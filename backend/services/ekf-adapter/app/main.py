"""EKF Adaptive Unit - Real-time parameter estimation (AD-01 to AD-07)."""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

import numpy as np
import redis.asyncio as aioredis
from fastapi import FastAPI

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import HealthResponse

logger = logging.getLogger("ndt.ekf-adapter")
settings = get_settings()
app: FastAPI = create_service_app("NDT EKF Adapter")

# AD-01: State vector x = [ε_fuel, R_f, K_spacer, T_coolant_out]
_state = np.array([0.05, 0.0001, 1.0, 330.0])
_P = np.eye(4) * 0.01
_last_valid = _state.copy()
_innovation_norm = 0.0
_buffer: deque[dict[str, Any]] = deque(maxlen=settings.redis_ekf_buffer_size)
_redis: aioredis.Redis | None = None
_ekf_task: asyncio.Task | None = None


def ekf_update(measurements: np.ndarray) -> tuple[np.ndarray, float]:
    """Extended Kalman Filter update step."""
    global _state, _P, _innovation_norm, _last_valid

    Q = np.eye(4) * 0.001
    R = np.eye(len(measurements)) * 0.05

    x_pred = _state
    P_pred = _P + Q

    H = np.array([[0, 0, 0, 1], [0, 0, 1, 0]])[: len(measurements)]
    y = measurements - H @ x_pred
    S = H @ P_pred @ H.T + R
    K = P_pred @ H.T @ np.linalg.inv(S)

    innovation = y
    _innovation_norm = float(np.linalg.norm(innovation / (np.diag(S) + 1e-9)))

    if _innovation_norm > settings.ekf_innovation_threshold:
        _state = _last_valid.copy()  # AD-07
        logger.warning("EKF divergence detected (innovation=%.2f), reset", _innovation_norm)
    else:
        _state = x_pred + K @ y
        _P = (np.eye(4) - K @ H) @ P_pred
        _last_valid = _state.copy()

    return _state, _innovation_norm


async def ekf_loop() -> None:
    while True:
        measurements = np.array([330.0 + np.random.normal(0, 0.5), 1.0 + np.random.normal(0, 0.01)])
        state, innov = ekf_update(measurements)

        record = {
            "epsilon_fuel": float(state[0]),
            "r_f": float(state[1]),
            "k_spacer": float(state[2]),
            "t_coolant_out": float(state[3]),
            "innovation_norm": innov,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _buffer.append(record)

        if _redis:
            await _redis.set("ekf:state", json.dumps(record))
            await _redis.lpush("ekf:buffer", json.dumps(record))
            await _redis.ltrim("ekf:buffer", 0, settings.redis_ekf_buffer_size - 1)

        await asyncio.sleep(settings.ekf_update_interval_sec)


@app.on_event("startup")
async def startup() -> None:
    global _redis, _ekf_task
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    _ekf_task = asyncio.create_task(ekf_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _ekf_task:
        _ekf_task.cancel()
    if _redis:
        await _redis.close()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service="ekf-adapter",
        status="healthy",
        details={"buffer_size": len(_buffer), "innovation_norm": _innovation_norm},
    )


@app.get("/state")
async def get_state() -> dict[str, Any]:
    return {
        "epsilon_fuel": float(_state[0]),
        "r_f": float(_state[1]),
        "k_spacer": float(_state[2]),
        "t_coolant_out": float(_state[3]),
        "innovation_norm": _innovation_norm,
    }


@app.get("/history")
async def get_history(limit: int = 100) -> dict[str, Any]:
    items = list(_buffer)[-limit:]
    return {"count": len(items), "records": items}
