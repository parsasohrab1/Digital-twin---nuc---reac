"""EKF Adaptive Unit – Real-time parameter estimation (AD-01 to AD-07)."""

from __future__ import annotations

import asyncio
import sys
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
import structlog
from filterpy.kalman import ExtendedKalmanFilter

from shared.config import get_settings, load_yaml_config
from shared.messaging import RedisBus, RabbitMQBus
from shared.models import EKFState

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()


class EKFAdapterService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        ekf_cfg = config.get("ekf", {})
        self.buffer_size = ekf_cfg.get("buffer_size", 1000)
        self.innovation_threshold = ekf_cfg.get("innovation_threshold", 3.0)
        self.update_interval = ekf_cfg.get("update_interval_sec", 1)
        self.history: deque[EKFState] = deque(maxlen=self.buffer_size)
        self.ekf = self._init_ekf()
        self.last_valid = EKFState(
            roughness_factor=0.05,
            fouling_resistance=0.0001,
            pressure_drop_coefficient=1.0,
            temp_outlet_est=330.0,
        )

    def _init_ekf(self) -> ExtendedKalmanFilter:
        ekf = ExtendedKalmanFilter(dim_x=4, dim_z=2)
        ekf.x = np.array([0.05, 0.0001, 1.0, 330.0])
        ekf.P *= 0.01
        ekf.R = np.eye(2) * 0.01
        ekf.Q = np.eye(4) * 1e-5
        ekf.F = np.eye(4)
        ekf.H = np.array([[0, 0, 0, 1], [0, 0, 1, 0]])
        return ekf

    def _hx(self, x: np.ndarray) -> np.ndarray:
        return np.array([x[3], x[2]])

    def _HJacobian(self, x: np.ndarray) -> np.ndarray:
        return self.ekf.H

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        log.info("ekf_adapter_started")
        asyncio.create_task(self._update_loop())

    def _update(self, measurements: dict) -> EKFState:
        z = np.array([
            measurements.get("temp_outlet_c", 330),
            measurements.get("pressure_drop_coefficient", 1.0),
        ])
        self.ekf.predict()
        self.ekf.update(z, self._HJacobian, self._hx)

        innovation = z - self._hx(self.ekf.x)
        innovation_norm = float(np.linalg.norm(innovation))

        # AD-07: Reset on divergence
        if innovation_norm > self.innovation_threshold:
            log.warning("ekf_divergence_reset", innovation_norm=innovation_norm)
            self.ekf.x = np.array([
                self.last_valid.roughness_factor,
                self.last_valid.fouling_resistance,
                self.last_valid.pressure_drop_coefficient,
                self.last_valid.temp_outlet_est,
            ])
            innovation_norm = 0.0

        state = EKFState(
            roughness_factor=float(self.ekf.x[0]),
            fouling_resistance=float(self.ekf.x[1]),
            pressure_drop_coefficient=float(self.ekf.x[2]),
            temp_outlet_est=float(self.ekf.x[3]),
            innovation_norm=innovation_norm,
        )
        self.last_valid = state
        self.history.append(state)
        return state

    async def _update_loop(self) -> None:
        while True:
            t0 = time.perf_counter()
            try:
                state = await self.redis.get_state("ndt:state:current")
                if state and "reactor" in state:
                    ekf_state = self._update(state["reactor"])
                    payload = ekf_state.model_dump(mode="json")
                    await self.redis.set_state("ndt:ekf:current", payload, ttl=10)
                    await self.rabbit.publish("ekf.update", payload)
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    if elapsed_ms > 20:
                        log.warning("ekf_slow_update", ms=elapsed_ms)
            except Exception as e:
                log.error("ekf_update_error", error=str(e))
            await asyncio.sleep(self.update_interval)


async def main() -> None:
    service = EKFAdapterService()
    await service.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
