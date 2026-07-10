"""Alert evaluation logic (AL-01 to AL-04)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from shared.models import Alert, AlertLevel


def evaluate_alerts(
    reactor: dict,
    prediction: dict | None,
    system_mode: str = "normal",
    ekf_calibrated: bool = False,
) -> list[Alert]:
    alerts: list[Alert] = []
    now = datetime.now(timezone.utc)
    dnbr = reactor.get("dnbr", 2.0)
    clad_temp = reactor.get("temp_cladding_max_c", 380)

    # AL-09 / safe mode – sensor cut
    if system_mode == "safe":
        alerts.append(Alert(
            id=uuid.uuid4(),
            level=AlertLevel.CRITICAL,
            title="Sensor communication lost",
            description="Safe Mode active – continuing with last valid parameters (DI-03)",
            created_at=now,
        ))

    # AL-01: Critical – DNB within 300s
    if prediction:
        p50 = prediction.get("dnb_tte_distribution", {}).get("p50", 999)
        if p50 < 300:
            alerts.append(Alert(
                id=uuid.uuid4(),
                level=AlertLevel.CRITICAL,
                title="DNB risk (critical)",
                description=f"Estimated DNB within {p50:.0f}s (p50 scenario)",
                tte_sec=float(p50),
                created_at=now,
            ))

    # AL-02: High – cladding temp limit
    tte_clad = max(0.0, (620.0 - clad_temp) * 2.0)
    if tte_clad < 600:
        alerts.append(Alert(
            id=uuid.uuid4(),
            level=AlertLevel.HIGH,
            title="Cladding temperature limit approaching",
            description=f"Cladding temp: {clad_temp:.1f}C (limit 620C)",
            tte_sec=tte_clad,
            created_at=now,
        ))

    # AL-03: Medium – abnormal degradation
    if prediction and prediction.get("anomaly_detected"):
        alerts.append(Alert(
            id=uuid.uuid4(),
            level=AlertLevel.MEDIUM,
            title="Abnormal physical parameter drift",
            description="Degradation rate exceeds dynamic threshold (FP-03)",
            created_at=now,
        ))

    # Direct DNBR check
    if dnbr < 1.4:
        alerts.append(Alert(
            id=uuid.uuid4(),
            level=AlertLevel.CRITICAL,
            title="Low DNBR",
            description=f"Departure from nucleate boiling ratio: {dnbr:.2f}",
            tte_sec=float(dnbr * 200),
            created_at=now,
        ))

    # AL-04: Info – calibration completed
    if ekf_calibrated:
        alerts.append(Alert(
            id=uuid.uuid4(),
            level=AlertLevel.INFO,
            title="EKF auto-calibration completed",
            description="Physical parameters updated from sensor fusion",
            created_at=now,
        ))

    return alerts
