"""NDT API Gateway - REST API & WebSocket hub (API-RQ-01 to API-RQ-03)."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import (
    Alert,
    AlertLevel,
    DSSAction,
    EKFState,
    HealthResponse,
    ReactorState,
    ScenarioRequest,
    SystemMode,
    SystemStatus,
)

logger = logging.getLogger("ndt.api-gateway")
settings = get_settings()
app: FastAPI = create_service_app("NDT API Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICE_ROUTES = {
    "data-ingestion": settings.data_ingestion_url,
    "pinn-core": settings.pinn_core_url,
    "ekf-adapter": settings.ekf_adapter_url,
    "predictor": settings.predictor_url,
    "dss": settings.dss_url,
    "alerts": settings.alerts_url,
}


async def proxy_get(client: httpx.AsyncClient, service: str, path: str) -> dict[str, Any]:
    base = SERVICE_ROUTES.get(service)
    if not base:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    try:
        resp = await client.get(f"{base}{path}", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Proxy error %s%s: %s", base, path, exc)
        raise HTTPException(status_code=502, detail=f"Service {service} unavailable") from exc


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    return HealthResponse(service="api-gateway", status="healthy")


@app.get("/api/v1/status", response_model=SystemStatus, tags=["System"])
async def system_status() -> SystemStatus:
    """API-RQ-01: Aggregated system status."""
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient() as client:
        reactor_data = await _safe_fetch(client, "data-ingestion", "/state")
        ekf_data = await _safe_fetch(client, "ekf-adapter", "/state")
        dss_data = await _safe_fetch(client, "dss", "/top-actions")
        alerts_data = await _safe_fetch(client, "alerts", "/active")

    reactor = ReactorState(
        timestamp=now,
        power_mwth=reactor_data.get("power_mwth", 3000.0),
        pressure_mpa=reactor_data.get("pressure_mpa", 15.5),
        temp_inlet_c=reactor_data.get("temp_inlet_c", 290.0),
        temp_outlet_c=reactor_data.get("temp_outlet_c", 330.0),
        mass_flow_kg_s=reactor_data.get("mass_flow_kg_s", 18500.0),
        dnbr=reactor_data.get("dnbr", 1.8),
        temp_cladding_max_c=reactor_data.get("temp_cladding_max_c", 380.0),
        mode=SystemMode(reactor_data.get("mode", "normal")),
    )

    ekf = None
    if ekf_data:
        ekf = EKFState(
            epsilon_fuel=ekf_data.get("epsilon_fuel", 0.05),
            r_f=ekf_data.get("r_f", 0.0001),
            k_spacer=ekf_data.get("k_spacer", 1.0),
            t_coolant_out=ekf_data.get("t_coolant_out", 330.0),
            timestamp=now,
            innovation_norm=ekf_data.get("innovation_norm", 0.0),
        )

    actions = [
        DSSAction(**a) for a in dss_data.get("actions", [])[: settings.dss_top_actions]
    ] if dss_data else []

    active_alerts = [
        Alert(**a) for a in alerts_data.get("alerts", [])
    ] if alerts_data else []

    return SystemStatus(
        mode=reactor.mode,
        reactor=reactor,
        ekf=ekf,
        dnbr=reactor.dnbr,
        tte_sec=alerts_data.get("min_tte_sec") if alerts_data else None,
        top_dss_actions=actions,
        active_alerts=active_alerts,
        sensor_health=reactor_data.get("sensor_health", {}),
    )


@app.post("/api/v1/scenarios/simulate", tags=["Scenarios"])
async def simulate_scenario(request: ScenarioRequest) -> dict[str, Any]:
    """API-RQ-02: Manual what-if scenario simulation."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.predictor_url}/simulate",
            json=request.model_dump(),
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@app.get("/api/v1/history/export", tags=["Data"])
async def export_history(format: str = "csv", hours: int = 24) -> dict[str, str]:
    """API-RQ-03: Historical data export."""
    if format not in ("csv", "parquet"):
        raise HTTPException(status_code=400, detail="format must be csv or parquet")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.data_ingestion_url}/export",
            params={"format": format, "hours": hours},
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@app.get("/api/v1/services", tags=["System"])
async def list_services() -> dict[str, Any]:
    results = {}
    async with httpx.AsyncClient() as client:
        for name, url in SERVICE_ROUTES.items():
            try:
                r = await client.get(f"{url}/health", timeout=3.0)
                results[name] = {"url": url, "status": r.json().get("status", "unknown")}
            except httpx.HTTPError:
                results[name] = {"url": url, "status": "unreachable"}
    return {"services": results}


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time dashboard updates (UI-RQ-01: ≤200ms refresh)."""
    await manager.connect(websocket)
    try:
        while True:
            status = await system_status()
            await websocket.send_json(status.model_dump(mode="json"))
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def _safe_fetch(client: httpx.AsyncClient, service: str, path: str) -> dict[str, Any]:
    try:
        return await proxy_get(client, service, path)
    except HTTPException:
        return {}
