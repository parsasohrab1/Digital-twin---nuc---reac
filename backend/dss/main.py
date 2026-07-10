"""Nuclear DSS – NSGA-II + safety-first ranking + PostgreSQL (DSS-01 to DSS-07)."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

import numpy as np
import structlog
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import Problem
from pymoo.optimize import minimize
from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting

from dss.ranking import build_recommendations, rank_pareto_solutions
from shared.config import get_settings, load_yaml_config
from shared.database import close_pool, insert_dss_recommendations
from shared.messaging import RedisBus, RabbitMQBus
from shared.models import DSSRecommendation

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()


class ReactorDSSProblem(Problem):
    """DSS-01, DSS-02: three objectives, three decision variables."""

    def __init__(self, base_state: dict):
        dss_cfg = config.get("dss", {})
        dv = dss_cfg.get("decision_variables", {})
        xl = np.array([
            dv.get("reactor_power_pct", [50, 100])[0],
            dv.get("main_pump_flow_pct", [80, 120])[0],
            dv.get("boron_injection_ppm_min", [0, 100])[0],
        ])
        xu = np.array([
            dv.get("reactor_power_pct", [50, 100])[1],
            dv.get("main_pump_flow_pct", [80, 120])[1],
            dv.get("boron_injection_ppm_min", [0, 100])[1],
        ])
        super().__init__(n_var=3, n_obj=3, n_constr=0, xl=xl, xu=xu)
        self.base = base_state

    def _evaluate(self, x, out, *args, **kwargs):
        power = x[:, 0]
        flow = x[:, 1]
        base_temp = self.base.get("temp_cladding_max_c", 380)
        f1 = base_temp * (power / 100) / np.maximum(flow / 100, 0.5)
        f2 = np.abs(power - 100) * 0.5
        f3 = np.abs(power - 100)
        out["F"] = np.column_stack([f1, f2, f3])


class DSSService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        dss_cfg = config.get("dss", {})
        self.population = dss_cfg.get("population", 100)
        self.max_runtime_ms = dss_cfg.get("max_runtime_ms", 500)
        self.pareto_max = dss_cfg.get("pareto_max_solutions", 10)
        self.top_n = dss_cfg.get("top_recommendations", 3)
        self.w1 = dss_cfg.get("safety_weights", {}).get("w1", 0.6)
        self.w2 = dss_cfg.get("safety_weights", {}).get("w2", 0.4)
        self._last_run_ms = 0.0

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        log.info("dss_started", population=self.population)
        asyncio.create_task(self._optimize_loop())

    def _run_nsga2(self, base_state: dict) -> tuple[list[DSSRecommendation], float, int]:
        t0 = time.perf_counter()
        problem = ReactorDSSProblem(base_state)
        algorithm = NSGA2(pop_size=self.population)
        # Fewer generations to stay within 500 ms budget (DSS-04)
        result = minimize(problem, algorithm, ("n_gen", 6), verbose=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        recommendations: list[DSSRecommendation] = []
        pareto_count = 0

        if result.X is not None and result.F is not None:
            nds = NonDominatedSorting()
            fronts = nds.do(result.F)
            pareto_idx = fronts[0][: self.pareto_max]
            pareto_x = result.X[pareto_idx]
            pareto_count = len(pareto_x)

            ranked = rank_pareto_solutions(
                pareto_x, base_state, top_n=self.top_n, w1=self.w1, w2=self.w2
            )
            recommendations = build_recommendations(ranked, base_state)

        if elapsed_ms > self.max_runtime_ms:
            log.warning("dss_slow_optimization", elapsed_ms=round(elapsed_ms, 1))

        self._last_run_ms = elapsed_ms
        return recommendations, elapsed_ms, pareto_count

    async def _optimize_loop(self) -> None:
        while True:
            try:
                state = await self.redis.get_state("ndt:state:current")
                prediction = await self.redis.get_state("ndt:prediction:current")

                if state and "reactor" in state:
                    reactor = state["reactor"]
                    anomaly = prediction and prediction.get("anomaly_detected", False)
                    trigger = anomaly or reactor.get("dnbr", 2) < 1.5 or state.get("mode") == "transient"

                    if trigger:
                        recs, elapsed_ms, pareto_n = self._run_nsga2(reactor)
                        rec_dicts = [r.model_dump(mode="json") for r in recs]
                        context = {
                            "dnbr": reactor.get("dnbr"),
                            "anomaly": anomaly,
                            "pareto_solutions": pareto_n,
                            "elapsed_ms": elapsed_ms,
                        }

                        try:
                            await insert_dss_recommendations(rec_dicts, context)
                        except Exception as db_err:
                            log.warning("dss_db_insert_failed", error=str(db_err))

                        payload = {
                            "recommendations": rec_dicts,
                            "pareto_count": pareto_n,
                            "optimization_ms": elapsed_ms,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        await self.redis.set_state("ndt:dss:current", payload, ttl=60)
                        await self.rabbit.publish("dss.recommendation", payload)
                        log.info(
                            "dss_recommendations_published",
                            count=len(recs),
                            pareto=pareto_n,
                            ms=round(elapsed_ms, 1),
                        )

            except Exception as e:
                log.error("dss_error", error=str(e))

            await asyncio.sleep(5)


async def main() -> None:
    service = DSSService()
    await service.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
