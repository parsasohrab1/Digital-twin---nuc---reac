"""DSS-06 safety-first ranking for Pareto solutions."""

from __future__ import annotations

import numpy as np

from shared.models import DSSRecommendation


def safety_first_score(
    power_pct: float,
    flow_pct: float,
    base_state: dict,
    w1: float = 0.6,
    w2: float = 0.4,
) -> float:
    """DSS-06: w1*(1 - T_fuel/620) + w2*DNBR (normalized)."""
    clad = base_state.get("temp_cladding_max_c", 380) * (power_pct / 100) / max(flow_pct / 100, 0.5)
    dnbr = base_state.get("dnbr", 1.8) * (flow_pct / 100) / max(power_pct / 100, 0.5)
    term1 = w1 * (1.0 - clad / 620.0)
    term2 = w2 * min(dnbr / 2.5, 1.0)
    return term1 + term2


def rank_pareto_solutions(
    pareto_x: np.ndarray,
    base_state: dict,
    top_n: int = 3,
    w1: float = 0.6,
    w2: float = 0.4,
) -> list[tuple[int, float, np.ndarray]]:
    """Return top-N indices sorted by safety-first score descending."""
    scores = []
    for i, x in enumerate(pareto_x):
        power, flow, _ = x
        s = safety_first_score(power, flow, base_state, w1, w2)
        scores.append((i, s, x))
    scores.sort(key=lambda t: t[1], reverse=True)
    return scores[:top_n]


def build_recommendations(
    ranked: list[tuple[int, float, np.ndarray]],
    base_state: dict,
) -> list[DSSRecommendation]:
    recommendations: list[DSSRecommendation] = []
    for rank, score, x in ranked:
        power, flow, boron = float(x[0]), float(x[1]), float(x[2])
        clad_temp = base_state.get("temp_cladding_max_c", 380) * (power / 100) / max(flow / 100, 0.5)
        safety = max(1, min(5, int(round(5 * max(0, min(1, score))))))
        success = max(50, min(99, 100 - abs(power - 95) - abs(flow - 100) * 0.3))

        parts: list[str] = []
        if power < 99:
            parts.append(f"reduce power to {power:.0f}%")
        elif power > 101:
            parts.append(f"increase power to {power:.0f}%")
        if flow > 101:
            parts.append(f"increase pump flow to {flow:.0f}%")
        elif flow < 99:
            parts.append(f"decrease pump flow to {flow:.0f}%")
        if boron > 1:
            parts.append(f"boron injection {boron:.0f} ppm/min")

        recommendations.append(DSSRecommendation(
            rank=rank,
            action=" + ".join(parts) or "continue normal operation",
            safety_score=safety,
            success_probability=float(success),
            execution_time_sec=30.0 + boron * 0.2,
            reactor_power_pct=power,
            pump_flow_pct=flow,
            boron_rate_ppm_min=boron,
        ))
    for i, rec in enumerate(recommendations, start=1):
        rec.rank = i
    return recommendations
