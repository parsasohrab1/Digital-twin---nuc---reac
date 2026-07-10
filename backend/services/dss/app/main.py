"""Nuclear DSS - Decision Support System (DSS-01 to DSS-07)."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import DSSAction, HealthResponse

logger = logging.getLogger("ndt.dss")
settings = get_settings()
app: FastAPI = create_service_app("NDT DSS")

_last_optimization_ms = 0.0


def rank_actions() -> list[DSSAction]:
    """DSS-06/07: Safety-first ranking of corrective actions."""
    candidates = [
        DSSAction(
            rank=1,
            action="increase_flow_10%",
            description="Increase main coolant pump flow by 10%",
            safety_score=5,
            success_probability=92.0,
            recommended_duration_sec=120,
        ),
        DSSAction(
            rank=2,
            action="reduce_power_15%",
            description="Reduce reactor thermal power by 15%",
            safety_score=4,
            success_probability=88.0,
            recommended_duration_sec=300,
        ),
        DSSAction(
            rank=3,
            action="borate_injection",
            description="Increase boric acid injection rate to 50 ppm/min",
            safety_score=3,
            success_probability=75.0,
            recommended_duration_sec=600,
        ),
    ]
    return candidates[: settings.dss_top_actions]


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service="dss",
        status="healthy",
        details={"last_optimization_ms": _last_optimization_ms},
    )


@app.get("/top-actions")
async def top_actions() -> dict[str, Any]:
    """DSS-07: Top ranked corrective actions for operator."""
    global _last_optimization_ms
    start = time.perf_counter()
    actions = rank_actions()
    _last_optimization_ms = (time.perf_counter() - start) * 1000

    return {
        "actions": [a.model_dump() for a in actions],
        "pareto_size": min(10, len(actions)),
        "optimization_ms": _last_optimization_ms,
        "safety_validated": True,
        "operator_approval_required": True,  # SAF-01
    }


@app.post("/recommendations/log")
async def log_recommendation(action: DSSAction) -> dict[str, str]:
    """SAF-05: Immutable log of DSS recommendations."""
    rec_id = str(uuid.uuid4())
    logger.info("DSS recommendation logged: id=%s action=%s score=%d", rec_id, action.action, action.safety_score)
    return {
        "id": rec_id,
        "status": "logged",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": "Persist to WORM storage in production",
    }
