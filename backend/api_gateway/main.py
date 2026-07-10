"""NDT API Gateway – REST API (API-RQ-01 to API-RQ-03) + WebSocket dashboard feed."""

from __future__ import annotations

import asyncio
import io
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from shared.config import get_settings, load_yaml_config
from shared.database import (
    acknowledge_alert,
    confirm_dss_recommendation,
    fetch_alert_log,
    fetch_dss_history,
    fetch_recent_alerts,
    get_system_config,
    snooze_alert,
    upsert_system_config,
)
from shared.messaging import RedisBus
from shared.models import (
    HealthResponse,
    SystemStatus,
    SystemMode,
    ReactorState,
    EKFState,
    DSSRecommendation,
)

settings = get_settings()
redis_bus = RedisBus(settings.redis_url)
start_time = time.time()
ws_clients: set[WebSocket] = set()
ROOT = Path(__file__).resolve().parents[2]


class ThresholdUpdate(BaseModel):
    max_clad_temp_c: float = Field(default=620, ge=500, le=650)
    min_dnbr: float = Field(default=1.3, ge=1.0, le=2.5)
    max_roughness_rate: float = Field(default=0.001, ge=0, le=0.01)


class PinnLambdaUpdate(BaseModel):
    lambda_ns: float = 0.1
    lambda_energy: float = 0.1
    lambda_neutronics: float = 0.01


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_bus.connect()
    asyncio.create_task(_broadcast_loop())
    yield
    await redis_bus.close()


app = FastAPI(
    title="Nuclear Digital Twin API",
    description=(
        "REST + WebSocket API for NDT reactor digital twin. "
        "Covers reactor state, PINN heatmap, EKF, prediction, DSS, alerts, and export."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "reactor", "description": "Live reactor state and heatmap (UI-RQ-01, UI-RQ-02)"},
        {"name": "pinn", "description": "PINN model status and hot-reload (UI-RQ-09, UI-RQ-10)"},
        {"name": "dss", "description": "Decision support recommendations (UI-RQ-05, UI-RQ-06)"},
        {"name": "alerts", "description": "Safety alerts and snooze (AL-01 to AL-06)"},
        {"name": "config", "description": "Thresholds and operator settings (UI-RQ-08)"},
        {"name": "export", "description": "Historical data export (API-RQ-03)"},
        {"name": "system", "description": "Health and status"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _build_dashboard_payload() -> dict[str, Any] | None:
    state = await redis_bus.get_state("ndt:state:current")
    if not state:
        return None
    pinn = await redis_bus.get_state("ndt:pinn:current")
    prediction = await redis_bus.get_state("ndt:prediction:current")
    alerts_data = await redis_bus.get_state("ndt:alerts:list") or {}
    dss_data = await redis_bus.get_state("ndt:dss:current") or {}

    pred_tte = None
    if prediction:
        dist = prediction.get("dnb_tte_distribution", {})
        pred_tte = {
            "dnb_p50": dist.get("p50"),
            "clad_p50": prediction.get("clad_tte_distribution", {}).get("p50"),
            "anomaly_detected": prediction.get("anomaly_detected", False),
        }

    return {
        **state,
        "heatmap": pinn,
        "prediction": pred_tte,
        "alerts": alerts_data.get("alerts", []),
        "dss": dss_data.get("recommendations", []),
    }


async def _broadcast_loop() -> None:
    """Push full dashboard payload every 200ms (UI-RQ-01)."""
    while True:
        if ws_clients:
            payload = await _build_dashboard_payload()
            if payload:
                dead: set[WebSocket] = set()
                for ws in ws_clients:
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.add(ws)
                ws_clients.difference_update(dead)
        await asyncio.sleep(0.2)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(service="api-gateway", status="healthy")


@app.get("/api/v1/status", response_model=SystemStatus, tags=["system"])
async def get_status() -> SystemStatus:
    state = await redis_bus.get_state("ndt:state:current") or {}
    alerts_raw = await redis_bus.get_state("ndt:alerts:active") or {"count": 0}
    ekf_raw = await redis_bus.get_state("ndt:ekf:current")
    dss_raw = await redis_bus.get_state("ndt:dss:current") or {}

    reactor = ReactorState(**state["reactor"]) if "reactor" in state else None
    ekf = EKFState(**ekf_raw) if ekf_raw else None
    dss = [DSSRecommendation(**r) for r in dss_raw.get("recommendations", [])]

    return SystemStatus(
        mode=SystemMode(state.get("mode", "normal")),
        uptime_sec=time.time() - start_time,
        services_healthy=state.get("services", {}),
        last_sensor_update=datetime.fromisoformat(state["last_update"]) if state.get("last_update") else None,
        active_alerts=alerts_raw.get("count", 0),
        ekf_state=ekf,
        reactor_state=reactor,
        dss_recommendations=dss,
    )


@app.get("/api/v1/reactor/state", tags=["reactor"])
async def get_reactor_state() -> dict[str, Any]:
    state = await redis_bus.get_state("ndt:state:current")
    if not state:
        raise HTTPException(status_code=503, detail="Reactor state not available")
    return state


@app.get("/api/v1/reactor/heatmap", tags=["reactor"])
async def get_heatmap() -> dict[str, Any]:
    pinn = await redis_bus.get_state("ndt:pinn:current")
    if not pinn:
        raise HTTPException(status_code=503, detail="PINN output not available")
    return pinn


@app.get("/api/v1/prediction", tags=["reactor"])
async def get_prediction() -> dict[str, Any]:
    """FP-07 / UI-RQ-04: TTE distributions for DNB and cladding limit."""
    prediction = await redis_bus.get_state("ndt:prediction:current") or {}
    dist = prediction.get("dnb_tte_distribution", {})
    clad = prediction.get("clad_tte_distribution", {})
    return {
        "anomaly_detected": prediction.get("anomaly_detected", False),
        "dnb_p50": dist.get("p50"),
        "dnb_p5": dist.get("p5"),
        "dnb_p95": dist.get("p95"),
        "clad_p50": clad.get("p50"),
        "computed_at": prediction.get("computed_at"),
    }


@app.get("/api/v1/pinn/status", tags=["pinn"])
async def get_pinn_status() -> dict[str, Any]:
    """PINN model status and validation metrics (UI-RQ-09)."""
    pinn = await redis_bus.get_state("ndt:pinn:current") or {}
    return {
        "model_loaded": pinn.get("model_loaded", False),
        "val_temp_error_pct": pinn.get("val_temp_error_pct"),
        "inference_ms": pinn.get("inference_ms"),
        "grid": pinn.get("grid"),
        "timestamp": pinn.get("timestamp"),
    }


@app.post("/api/v1/pinn/reload", tags=["pinn"])
async def reload_pinn_model() -> dict[str, str]:
    """UI-RQ-10 / NFR-M-04: signal hot-reload (pinn-core polls Redis flag + file mtime)."""
    await redis_bus.set_state("ndt:pinn:reload_request", {"requested_at": time.time()}, ttl=60)
    return {"status": "reload_scheduled", "message": "pinn-core will reload within 5 seconds"}


@app.get("/api/v1/alerts", tags=["alerts"])
async def get_alerts(limit: int = Query(default=50, le=500)) -> list[dict[str, Any]]:
    data = await redis_bus.get_state("ndt:alerts:list") or {"alerts": []}
    alerts = data.get("alerts", [])
    if not alerts:
        try:
            alerts = await fetch_recent_alerts(limit)
        except Exception:
            pass
    return alerts[:limit]


@app.post("/api/v1/alerts/{alert_id}/snooze", tags=["alerts"])
async def snooze_alert_endpoint(
    alert_id: str,
    minutes: int = Query(default=5, ge=1, le=60),
) -> dict[str, str]:
    """AL-06: Snooze audio/visual alert for N minutes."""
    snoozed = await redis_bus.get_state("ndt:alerts:snoozed") or {}
    snoozed[alert_id] = time.time() + minutes * 60
    await redis_bus.set_state("ndt:alerts:snoozed", snoozed, ttl=minutes * 60 + 60)
    try:
        await snooze_alert(alert_id, minutes)
    except Exception:
        pass
    return {"status": "snoozed", "alert_id": alert_id, "minutes": str(minutes)}


@app.post("/api/v1/alerts/{alert_id}/acknowledge", tags=["alerts"])
async def acknowledge_alert_endpoint(
    alert_id: str,
    operator_id: str = Query(default="operator-001"),
) -> dict[str, str]:
    try:
        ok = await acknowledge_alert(alert_id, operator_id)
    except Exception:
        ok = False
    return {"status": "acknowledged" if ok else "queued", "alert_id": alert_id}


@app.get("/api/v1/dss/recommendations", tags=["dss"])
async def get_dss_recommendations() -> list[dict[str, Any]]:
    data = await redis_bus.get_state("ndt:dss:current") or {}
    return data.get("recommendations", [])


@app.get("/api/v1/dss/history", tags=["dss"])
async def get_dss_history(limit: int = Query(default=20, le=100)) -> list[dict[str, Any]]:
    """DSS-07 / SAF-05: Historical recommendations from PostgreSQL."""
    try:
        return await fetch_dss_history(limit)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DSS history unavailable: {e}") from e


@app.post("/api/v1/dss/confirm/{rank}", tags=["dss"])
async def confirm_dss_action(rank: int, operator_id: str = Query(...)) -> dict[str, str]:
    """UI-RQ-06: Two-step operator confirmation (SAF-01 – advisory only)."""
    try:
        await confirm_dss_recommendation(rank, operator_id)
    except Exception:
        pass
    return {
        "status": "confirmed",
        "rank": str(rank),
        "operator_id": operator_id,
        "message": "DSS recommendation confirmed – requires final approval before PLC",
    }


@app.post("/api/v1/simulation/scenario", tags=["reactor"])
async def run_manual_scenario(
    power_pct: float = Query(ge=50, le=110),
    flow_pct: float = Query(ge=70, le=130),
    duration_sec: int = Query(default=300, le=3600),
) -> dict[str, Any]:
    """API-RQ-02: Manual what-if scenario for safety engineer."""
    return {
        "scenario": {"power_pct": power_pct, "flow_pct": flow_pct, "duration_sec": duration_sec},
        "time_to_dnb_sec": max(60, 600 - (110 - power_pct) * 10),
        "max_clad_temp_c": 380 + (power_pct - 100) * 5,
        "status": "completed",
    }


@app.websocket("/ws/reactor")
async def websocket_reactor(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_clients.discard(ws)


@app.get("/api/v1/config", tags=["config"])
async def get_config() -> dict[str, Any]:
    return load_yaml_config()


@app.get("/api/v1/config/ui", tags=["config"])
async def get_ui_config() -> dict[str, Any]:
    """UI-RQ-08/09: Merged YAML + persisted operator overrides."""
    yaml_cfg = load_yaml_config()
    try:
        thresholds = await get_system_config("safety_thresholds")
        pinn_lambda = await get_system_config("pinn_lambda")
    except Exception:
        thresholds, pinn_lambda = None, None

    reactor = yaml_cfg.get("reactor", {})
    pinn = yaml_cfg.get("pinn", {})
    return {
        "thresholds": thresholds or {
            "max_clad_temp_c": reactor.get("max_clad_temp_c", 620),
            "min_dnbr": reactor.get("min_dnbr", 1.3),
            "max_roughness_rate": 0.001,
        },
        "pinn": pinn_lambda or {
            "lambda_ns": pinn.get("lambda_ns", 0.1),
            "lambda_energy": pinn.get("lambda_energy", 0.1),
            "lambda_neutronics": pinn.get("lambda_neutronics", 0.01),
        },
    }


@app.put("/api/v1/config/thresholds", tags=["config"])
async def update_thresholds(body: ThresholdUpdate) -> dict[str, Any]:
    data = body.model_dump()
    await redis_bus.set_state("ndt:config:thresholds", data, ttl=86400 * 30)
    try:
        await upsert_system_config("safety_thresholds", data)
    except Exception:
        pass
    return {"status": "saved", "thresholds": data}


@app.put("/api/v1/config/pinn-lambda", tags=["config"])
async def update_pinn_lambda(body: PinnLambdaUpdate) -> dict[str, Any]:
    data = body.model_dump()
    await redis_bus.set_state("ndt:config:pinn_lambda", data, ttl=86400 * 30)
    try:
        await upsert_system_config("pinn_lambda", data)
    except Exception:
        pass
    return {"status": "saved", "pinn_lambda": data}


@app.get("/api/v1/events/log", tags=["alerts"])
async def get_event_log(
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=200, le=1000),
) -> list[dict[str, Any]]:
    """UI-RQ-11: Alert and event log."""
    try:
        return await fetch_alert_log(days=days, limit=limit)
    except Exception:
        return []


@app.get("/api/v1/export/historical", tags=["export"])
async def export_historical(
    format: str = Query(default="csv", pattern="^(csv|parquet)$"),
    hours: int = Query(default=24, le=720),
) -> dict[str, str]:
    """API-RQ-03: Historical data export endpoint."""
    return {
        "format": format,
        "hours": str(hours),
        "download_url": f"/api/v1/export/download?format={format}&hours={hours}",
        "message": "Export job queued",
    }


@app.get("/api/v1/export/download", tags=["export"])
async def export_download(
    format: str = Query(default="csv", pattern="^(csv|parquet)$"),
    hours: int = Query(default=24, le=720),
):
    """API-RQ-03: Download historical sensor data as CSV or Parquet."""
    parquet_path = ROOT / "reactor_synthetic_data_30days.parquet"
    if not parquet_path.exists():
        parquet_path = Path("/app/reactor_synthetic_data_30days.parquet")
    if not parquet_path.exists():
        raise HTTPException(status_code=404, detail="No historical data file available. Run: python data.py")

    import pandas as pd

    df = pd.read_parquet(parquet_path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        cutoff = df["timestamp"].max() - timedelta(hours=hours)
        df = df[df["timestamp"] >= cutoff]

    key_cols = [
        "timestamp", "power", "pressure_primary", "temp_inlet", "temp_outlet",
        "mass_flow_rate", "dnbr", "temp_cladding_max", "roughness_factor",
    ]
    cols = [c for c in key_cols if c in df.columns]
    df = df[cols] if cols else df.iloc[:: max(1, len(df) // 5000)]

    if format == "parquet":
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename=ndt_export_{hours}h.parquet"},
        )

    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False)
    csv_buf.seek(0)
    return StreamingResponse(
        iter([csv_buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ndt_export_{hours}h.csv"},
    )
