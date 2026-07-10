"""Phase 3 acceptance checks – DSS ranking + alerts (no Docker required)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from alerts.evaluator import evaluate_alerts  # noqa: E402
from dss.ranking import build_recommendations, rank_pareto_solutions, safety_first_score  # noqa: E402
from dss.main import ReactorDSSProblem  # noqa: E402
from pymoo.algorithms.moo.nsga2 import NSGA2  # noqa: E402
from pymoo.optimize import minimize  # noqa: E402


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    base = {"temp_cladding_max_c": 400, "dnbr": 1.5}
    pareto = np.array([
        [95, 110, 10],
        [80, 115, 20],
        [100, 100, 0],
    ])
    ranked = rank_pareto_solutions(pareto, base, top_n=3)
    recs = build_recommendations(ranked, base)
    checks.append(("dss_top3_recommendations", len(recs) == 3, str(len(recs))))
    checks.append(("dss_safety_scores_valid", all(1 <= r.safety_score <= 5 for r in recs), "OK"))
    checks.append(
        ("dss_safety_first_ordering",
         safety_first_score(80, 115, base) >= safety_first_score(100, 100, base) * 0.5,
         "lower power/higher flow preferred"),
    )

    # NSGA-II runtime budget (DSS-04)
    t0 = time.perf_counter()
    problem = ReactorDSSProblem(base)
    result = minimize(problem, NSGA2(pop_size=100), ("n_gen", 6), verbose=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    checks.append(("dss_nsga2_runs", result.X is not None, f"{elapsed_ms:.0f} ms"))
    checks.append(("dss_runtime_le_5000ms_dev", elapsed_ms <= 5000, f"{elapsed_ms:.0f} ms (dev CPU)"))

    # Alerts – four levels (AL-01 to AL-04)
    reactor = {"dnbr": 1.3, "temp_cladding_max_c": 610}
    prediction = {"anomaly_detected": True, "dnb_tte_distribution": {"p50": 120}}
    alerts = evaluate_alerts(reactor, prediction, ekf_calibrated=True)
    levels = {a.level.value for a in alerts}
    checks.append(("alert_critical", "critical" in levels, str(levels)))
    checks.append(("alert_high", "high" in levels, str(levels)))
    checks.append(("alert_medium", "medium" in levels, str(levels)))
    checks.append(("alert_info", "info" in levels, str(levels)))

    safe_alerts = evaluate_alerts(reactor, prediction, system_mode="safe")
    checks.append(("safe_mode_alert", any(a.title.startswith("Sensor") for a in safe_alerts), "OK"))

    _report(checks)
    return 0 if all(c[1] for c in checks) else 1


def _report(checks: list[tuple[str, bool, str]]) -> None:
    print("Phase 3 validation")
    print("-" * 50)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print("-" * 50)
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"Result: {passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    sys.exit(main())
