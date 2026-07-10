"""Alert & Reporting Service (AL-01 to AL-06)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException

from shared.app_factory import create_service_app
from shared.models import Alert, AlertLevel, HealthResponse

logger = logging.getLogger("ndt.alerts")
app: FastAPI = create_service_app("NDT Alerts")

_alerts: dict[str, Alert] = {}


def _seed_demo_alerts() -> None:
    now = datetime.now(timezone.utc)
    demo = [
        Alert(
            id=str(uuid.uuid4()),
            level=AlertLevel.MEDIUM,
            message="Abnormal physical parameter rate detected (roughness factor)",
            tte_sec=None,
            timestamp=now - timedelta(minutes=5),
        ),
    ]
    for a in demo:
        _alerts[a.id] = a


@app.on_event("startup")
async def startup() -> None:
    _seed_demo_alerts()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    active = sum(1 for a in _alerts.values() if not a.acknowledged)
    return HealthResponse(service="alerts", status="healthy", details={"active_count": active})


@app.get("/active")
async def active_alerts() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    active = [
        a for a in _alerts.values()
        if not a.acknowledged and (a.snoozed_until is None or a.snoozed_until < now)
    ]
    tte_values = [a.tte_sec for a in active if a.tte_sec is not None]
    return {
        "alerts": [a.model_dump(mode="json") for a in active],
        "min_tte_sec": min(tte_values) if tte_values else None,
    }


@app.post("/{alert_id}/acknowledge")
async def acknowledge(alert_id: str) -> dict[str, str]:
    if alert_id not in _alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    _alerts[alert_id].acknowledged = True
    return {"status": "acknowledged", "id": alert_id}


@app.post("/{alert_id}/snooze")
async def snooze(alert_id: str, minutes: int = 5) -> dict[str, str]:
    """AL-06: Snooze audio alerts for 5 minutes."""
    if alert_id not in _alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    _alerts[alert_id].snoozed_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"status": "snoozed", "id": alert_id, "minutes": str(minutes)}
