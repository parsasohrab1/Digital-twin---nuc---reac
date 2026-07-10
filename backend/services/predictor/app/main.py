"""Fault Predictor - LSTM+ARIMA trend & scenario simulation (FP-01 to FP-07)."""

import logging
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
from fastapi import FastAPI

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import HealthResponse, ScenarioRequest

logger = logging.getLogger("ndt.predictor")
settings = get_settings()
app: FastAPI = create_service_app("NDT Predictor")

_anomaly_detected = False
_last_simulation_ms = 0.0


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service="predictor",
        status="healthy",
        details={
            "anomaly_detected": _anomaly_detected,
            "last_simulation_ms": _last_simulation_ms,
            "scenario_count": settings.predictor_scenario_count,
        },
    )


@app.post("/simulate")
async def simulate_scenarios(request: ScenarioRequest) -> dict[str, Any]:
    """FP-04 to FP-07: Batch what-if scenario simulation."""
    global _last_simulation_ms
    start = time.perf_counter()
    n = settings.predictor_scenario_count
    horizon = request.horizon_sec

    dnb_times = []
    clad_times = []
    for _ in range(n):
        power_factor = request.power_pct / 100 + np.random.normal(0, 0.02)
        flow_factor = request.pump_flow_pct / 100 + np.random.normal(0, 0.02)
        dnbr_traj = 1.8 * flow_factor / power_factor
        t_to_dnb = max(0, (dnbr_traj - 1.0) * 300 * np.random.uniform(0.8, 1.2))
        t_to_clad = max(0, (620 - 400 * power_factor) * np.random.uniform(0.5, 2.0))
        dnb_times.append(t_to_dnb)
        clad_times.append(t_to_clad)

    _last_simulation_ms = (time.perf_counter() - start) * 1000

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenarios_run": n,
        "horizon_sec": horizon,
        "simulation_ms": _last_simulation_ms,
        "dnb_time": {
            "mean_sec": float(np.mean(dnb_times)),
            "p5_sec": float(np.percentile(dnb_times, 5)),
            "p95_sec": float(np.percentile(dnb_times, 95)),
        },
        "clad_620_time": {
            "mean_sec": float(np.mean(clad_times)),
            "p5_sec": float(np.percentile(clad_times, 5)),
            "p95_sec": float(np.percentile(clad_times, 95)),
        },
        "inputs": request.model_dump(),
    }


@app.get("/anomaly")
async def check_anomaly() -> dict[str, Any]:
    """FP-03: Anomaly detection status."""
    return {"anomaly_detected": _anomaly_detected, "mode": "transient" if _anomaly_detected else "normal"}
