"""Shared Pydantic models for inter-service communication."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class SystemMode(str, Enum):
    OFFLINE_TRAINING = "offline_training"
    CALIBRATION = "calibration"
    NORMAL = "normal"
    TRANSIENT = "transient"
    SAFE = "safe"


class SensorReading(BaseModel):
    sensor_id: str
    value: float
    unit: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    quality: str = "good"


class ReactorState(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    power_mwth: float
    pressure_mpa: float
    temp_inlet_c: float
    temp_outlet_c: float
    mass_flow_kg_s: float
    temp_hot_channel_c: float
    temp_cladding_max_c: float
    dnbr: float
    mode: SystemMode = SystemMode.NORMAL


class EKFState(BaseModel):
    roughness_factor: float
    fouling_resistance: float
    pressure_drop_coefficient: float
    temp_outlet_est: float
    innovation_norm: float | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PINNOutput(BaseModel):
    temp_coolant: list[list[float]]
    pressure_field: list[list[float]]
    velocity_z: list[list[float]]
    temp_fuel_surface: list[list[float]]
    inference_ms: float


class ScenarioResult(BaseModel):
    scenario_id: int
    time_to_dnb_sec: float | None
    time_to_clad_limit_sec: float | None
    max_fuel_temp_c: float


class PredictionBundle(BaseModel):
    anomaly_detected: bool
    scenarios: list[ScenarioResult]
    dnb_tte_distribution: dict[str, float]
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class DSSRecommendation(BaseModel):
    rank: int
    action: str
    safety_score: int = Field(ge=1, le=5)
    success_probability: float = Field(ge=0, le=100)
    execution_time_sec: float
    reactor_power_pct: float
    pump_flow_pct: float
    boron_rate_ppm_min: float


class Alert(BaseModel):
    id: UUID | None = None
    level: AlertLevel
    title: str
    description: str
    tte_sec: float | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SystemStatus(BaseModel):
    mode: SystemMode
    uptime_sec: float
    services_healthy: dict[str, bool]
    last_sensor_update: datetime | None
    active_alerts: int
    ekf_state: EKFState | None
    reactor_state: ReactorState | None
    dss_recommendations: list[DSSRecommendation] = []


class HealthResponse(BaseModel):
    service: str
    status: str
    version: str = "1.0.0"
    details: dict[str, Any] = {}
