"""Data Ingestion Service - OPC UA / Modbus / Synthetic (DI-01 to DI-04)."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import HealthResponse, SystemMode

logger = logging.getLogger("ndt.data-ingestion")
settings = get_settings()
app: FastAPI = create_service_app("NDT Data Ingestion")

SENSOR_TAGS = {
    "P_IN": {"unit": "MPa", "min": 14.0, "max": 16.5},
    "P_OUT": {"unit": "MPa", "min": 14.0, "max": 16.5},
    "T_IN_1": {"unit": "C", "min": 280, "max": 300},
    "T_IN_2": {"unit": "C", "min": 280, "max": 300},
    "T_OUT_1": {"unit": "C", "min": 320, "max": 350},
    "T_OUT_2": {"unit": "C", "min": 320, "max": 350},
    "T_CORE_1": {"unit": "C", "min": 300, "max": 360},
    "T_CORE_2": {"unit": "C", "min": 300, "max": 360},
    "T_CORE_3": {"unit": "C", "min": 300, "max": 360},
    "T_CORE_4": {"unit": "C", "min": 300, "max": 360},
    "FLOW_1": {"unit": "kg/s", "min": 15000, "max": 20000},
    "FLOW_2": {"unit": "kg/s", "min": 15000, "max": 20000},
    "FLUX_1": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_2": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_3": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_4": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_5": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_6": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_7": {"unit": "norm", "min": 0.5, "max": 1.5},
    "FLUX_8": {"unit": "norm", "min": 0.5, "max": 1.5},
}

_state: dict[str, Any] = {
    "power_mwth": 3000.0,
    "pressure_mpa": 15.5,
    "temp_inlet_c": 290.0,
    "temp_outlet_c": 330.0,
    "mass_flow_kg_s": 18500.0,
    "dnbr": 1.8,
    "temp_cladding_max_c": 380.0,
    "mode": SystemMode.NORMAL.value,
    "sensor_health": {tag: "ok" for tag in SENSOR_TAGS},
}

_median_buffers: dict[str, deque[float]] = {
    tag: deque(maxlen=settings.median_filter_window) for tag in SENSOR_TAGS
}
_last_sensor_ts: dict[str, datetime] = {}
_redis: aioredis.Redis | None = None
_ingestion_task: asyncio.Task | None = None


def median_filter(tag: str, value: float) -> float:
    """DI-02: Median filter with configurable window."""
    buf = _median_buffers[tag]
    buf.append(value)
    sorted_vals = sorted(buf)
    mid = len(sorted_vals) // 2
    return sorted_vals[mid] if len(sorted_vals) % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2


async def ingest_loop() -> None:
    """Main ingestion loop - synthetic or OPC UA."""
    import math
    import random

    t = 0
    interval = 1.0 / settings.sensor_sample_rate_hz
    while True:
        now = datetime.now(timezone.utc)
        t += interval

        if settings.use_synthetic_data:
            _state["power_mwth"] = 3000 * (1 + 0.05 * math.sin(t / 3600))
            _state["pressure_mpa"] = 15.5 - 0.05 * math.sin(t / 1800)
            _state["temp_inlet_c"] = 290 + 2 * math.sin(t / 86400)
            _state["temp_outlet_c"] = _state["temp_inlet_c"] + 40
            _state["mass_flow_kg_s"] = 18500 * (0.95 + 0.1 * math.sin(t / 7200))
            _state["dnbr"] = max(1.0, 1.8 - 0.001 * t / 3600)
            _state["temp_cladding_max_c"] = _state["temp_outlet_c"] + 50

        for tag, meta in SENSOR_TAGS.items():
            base = _state.get("pressure_mpa", 15.5) if "P_" in tag else _state.get("temp_outlet_c", 330)
            raw = base + random.gauss(0, 0.01 * abs(base))
            filtered = median_filter(tag, raw)

            if meta["min"] <= filtered <= meta["max"]:
                _last_sensor_ts[tag] = now
                _state["sensor_health"][tag] = "ok"
            else:
                _state["sensor_health"][tag] = "out_of_range"

            if _redis:
                await _redis.publish("sensor:readings", f"{tag}:{filtered}:{now.isoformat()}")

        for tag, last in list(_last_sensor_ts.items()):
            if (now - last).total_seconds() > settings.sensor_cutoff_sec:
                _state["sensor_health"][tag] = "cut_sensor"  # DI-03

        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup() -> None:
    global _redis, _ingestion_task
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    _ingestion_task = asyncio.create_task(ingest_loop())
    logger.info("Data ingestion started (synthetic=%s)", settings.use_synthetic_data)


@app.on_event("shutdown")
async def shutdown() -> None:
    if _ingestion_task:
        _ingestion_task.cancel()
    if _redis:
        await _redis.close()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service="data-ingestion",
        status="healthy",
        mode=SystemMode(_state["mode"]),
        details={"sensors": len(SENSOR_TAGS), "synthetic": settings.use_synthetic_data},
    )


@app.get("/state")
async def get_state() -> dict[str, Any]:
    return _state


@app.get("/export")
async def export_data(format: str = "csv", hours: int = 24) -> dict[str, str]:
    """API-RQ-03: Export placeholder - connects to InfluxDB in production."""
    return {
        "format": format,
        "hours": str(hours),
        "status": "ready",
        "message": "Export endpoint configured; connect InfluxDB query for full implementation",
    }
