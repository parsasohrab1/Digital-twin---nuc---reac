"""PINN Core Service - Physics-Informed Neural Network (PINN-01 to PINN-06)."""

import logging
import time
from datetime import datetime, timezone
from typing import Any

import torch
import torch.nn as nn
from fastapi import FastAPI
from pydantic import BaseModel, Field

from shared.app_factory import create_service_app
from shared.config import get_settings
from shared.models import HealthResponse, SystemMode

logger = logging.getLogger("ndt.pinn-core")
settings = get_settings()
app: FastAPI = create_service_app("NDT PINN Core")

device = torch.device("cpu" if settings.pinn_use_cpu else ("cuda" if torch.cuda.is_available() else "cpu"))


class Swish(nn.Module):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * torch.sigmoid(x)


class PINNModel(nn.Module):
    """PINN-01: 6 hidden layers, 128 neurons, Swish activation."""

    INPUT_DIM = 9   # r, theta, z, t, T_in, P_in, m_dot, Q_total, theta_phys
    OUTPUT_DIM = 4  # T_coolant, P, v_z, T_fuel_surface

    def __init__(self, hidden_layers: int = 6, neurons: int = 128) -> None:
        super().__init__()
        layers: list[nn.Module] = [nn.Linear(self.INPUT_DIM, neurons), Swish()]
        for _ in range(hidden_layers - 1):
            layers.extend([nn.Linear(neurons, neurons), Swish()])
        layers.append(nn.Linear(neurons, self.OUTPUT_DIM))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class PredictRequest(BaseModel):
    r: float = Field(default=0.5, ge=0, le=1)
    theta: float = Field(default=0.0, ge=0, le=6.28)
    z: float = Field(default=1.83, ge=0, le=3.66)
    t: float = Field(default=0.0, ge=0)
    t_in: float = Field(default=290.0)
    p_in: float = Field(default=15.5)
    m_dot: float = Field(default=18500.0)
    q_total: float = Field(default=3000.0)
    theta_phys: float = Field(default=0.05)


_model = PINNModel(settings.pinn_hidden_layers, settings.pinn_neurons).to(device)
_model.eval()
_last_inference_ms: float = 0.0


@app.on_event("startup")
async def load_model() -> None:
    import os
    path = settings.pinn_model_path
    if os.path.exists(path):
        _model.load_state_dict(torch.load(path, map_location=device))
        logger.info("Loaded PINN model from %s", path)
    else:
        logger.warning("No trained model at %s - using random weights", path)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service="pinn-core",
        status="healthy",
        details={
            "device": str(device),
            "last_inference_ms": _last_inference_ms,
            "lambda_ns": settings.pinn_lambda_ns,
            "lambda_energy": settings.pinn_lambda_energy,
            "lambda_neutronics": settings.pinn_lambda_neutronics,
        },
    )


@app.post("/predict")
async def predict(req: PredictRequest) -> dict[str, Any]:
    """PINN-02/03: Predict thermal-hydraulic fields."""
    global _last_inference_ms
    x = torch.tensor(
        [[req.r, req.theta, req.z, req.t, req.t_in, req.p_in, req.m_dot, req.q_total, req.theta_phys]],
        dtype=torch.float32,
        device=device,
    )
    start = time.perf_counter()
    with torch.no_grad():
        out = _model(x).cpu().numpy()[0]
    _last_inference_ms = (time.perf_counter() - start) * 1000

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "T_coolant": float(out[0]),
        "P": float(out[1]),
        "v_z": float(out[2]),
        "T_fuel_surface": float(out[3]),
        "inference_ms": _last_inference_ms,
    }


@app.get("/config")
async def get_config() -> dict[str, Any]:
    return {
        "hidden_layers": settings.pinn_hidden_layers,
        "neurons": settings.pinn_neurons,
        "lambda_ns": settings.pinn_lambda_ns,
        "lambda_energy": settings.pinn_lambda_energy,
        "lambda_neutronics": settings.pinn_lambda_neutronics,
    }
