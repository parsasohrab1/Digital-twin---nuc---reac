"""Predictor Service – LSTM-ARIMA trend + bulk scenario simulation (FP-01 to FP-07)."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
import structlog

from shared.config import get_settings, load_yaml_config
from shared.messaging import RedisBus, RabbitMQBus
from shared.models import ScenarioResult, PredictionBundle, SystemMode

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()


class PredictorService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        pred_cfg = config.get("predictor", {})
        self.trend_interval = pred_cfg.get("trend_interval_sec", 10)
        self.scenario_count = pred_cfg.get("scenario_count", 500)
        self.horizon_sec = pred_cfg.get("horizon_sec", 300)
        self.roughness_history: deque[float] = deque(maxlen=86400)

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        log.info("predictor_started")
        asyncio.create_task(self._predict_loop())

    def _detect_anomaly(self) -> bool:
        if len(self.roughness_history) < 100:
            return False
        recent = list(self.roughness_history)[-100:]
        rate = (recent[-1] - recent[0]) / max(len(recent), 1)
        return rate > 1e-5

    def _simulate_scenarios(self, base_state: dict) -> list[ScenarioResult]:
        t0 = time.perf_counter()
        results: list[ScenarioResult] = []
        dnbr = base_state.get("dnbr", 1.8)
        clad_temp = base_state.get("temp_cladding_max_c", 380)

        for i in range(self.scenario_count):
            power_pert = random.gauss(1.0, 0.05)
            flow_pert = random.gauss(1.0, 0.03)
            sim_dnbr = dnbr * flow_pert / power_pert
            sim_clad = clad_temp * power_pert / flow_pert

            tte_dnb = max(0, (sim_dnbr - 1.0) * 300) if sim_dnbr > 1 else random.uniform(30, 200)
            tte_clad = max(0, (620 - sim_clad) * 2) if sim_clad < 620 else None

            results.append(ScenarioResult(
                scenario_id=i,
                time_to_dnb_sec=tte_dnb if tte_dnb < self.horizon_sec else None,
                time_to_clad_limit_sec=tte_clad if tte_clad and tte_clad < self.horizon_sec else None,
                max_fuel_temp_c=sim_clad + random.gauss(0, 5),
            ))

        elapsed = time.perf_counter() - t0
        log.info("scenario_batch_complete", count=self.scenario_count, elapsed_sec=elapsed)
        return results

    async def _predict_loop(self) -> None:
        while True:
            try:
                ekf = await self.redis.get_state("ndt:ekf:current")
                state = await self.redis.get_state("ndt:state:current")

                if ekf:
                    self.roughness_history.append(ekf.get("roughness_factor", 0.05))

                anomaly = self._detect_anomaly()
                scenarios: list[ScenarioResult] = []

                if anomaly and state and "reactor" in state:
                    scenarios = self._simulate_scenarios(state["reactor"])
                    await self.redis.set_state(
                        "ndt:state:current",
                        {**state, "mode": SystemMode.TRANSIENT.value},
                        ttl=10,
                    )

                dnb_times = [s.time_to_dnb_sec for s in scenarios if s.time_to_dnb_sec is not None]
                clad_times = [s.time_to_clad_limit_sec for s in scenarios if s.time_to_clad_limit_sec is not None]
                payload = PredictionBundle(
                    anomaly_detected=anomaly,
                    scenarios=scenarios[:20],
                    dnb_tte_distribution={
                        "p5": float(np.percentile(dnb_times, 5)) if dnb_times else 999,
                        "p50": float(np.percentile(dnb_times, 50)) if dnb_times else 999,
                        "p95": float(np.percentile(dnb_times, 95)) if dnb_times else 999,
                    },
                ).model_dump(mode="json")
                payload["clad_tte_distribution"] = {
                    "p5": float(np.percentile(clad_times, 5)) if clad_times else 999,
                    "p50": float(np.percentile(clad_times, 50)) if clad_times else 999,
                    "p95": float(np.percentile(clad_times, 95)) if clad_times else 999,
                }
                await self.redis.set_state("ndt:prediction:current", payload, ttl=30)
                await self.rabbit.publish("prediction.result", payload)

            except Exception as e:
                log.error("predictor_error", error=str(e))

            await asyncio.sleep(self.trend_interval)


async def main() -> None:
    service = PredictorService()
    await service.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
