"""Shared configuration loader for NDT microservices."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ndt_env: str = "development"
    ndt_log_level: str = "INFO"
    service_name: str = "ndt-service"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "ndt"
    postgres_user: str = "ndt_user"
    postgres_password: str = "change_me_secure_password"

    influxdb_url: str = "http://influxdb:8086"
    influxdb_token: str = "ndt-dev-token-change-in-production"
    influxdb_org: str = "ndt"
    influxdb_bucket: str = "sensor_data"

    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str = "amqp://ndt:change_me_rabbit@rabbitmq:5672/"

    opcua_endpoint: str = "opc.tcp://opcua-simulator:4840/freeopcua/server/"
    opcua_sample_rate_hz: int = 10
    opcua_sensor_timeout_sec: int = 3

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or os.getenv("NDT_CONFIG", "/app/config/ndt.yaml"))
    if not config_path.exists():
        fallback = Path(__file__).resolve().parents[2] / "config" / "ndt.yaml"
        config_path = fallback if fallback.exists() else config_path
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
