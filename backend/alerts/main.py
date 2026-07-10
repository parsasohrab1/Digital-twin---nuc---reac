"""Alerts Service – multi-level alerts, PostgreSQL, snooze (AL-01 to AL-06)."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import structlog

from alerts.evaluator import evaluate_alerts
from shared.config import get_settings, load_yaml_config
from shared.database import close_pool, insert_alert
from shared.messaging import RedisBus, RabbitMQBus
from shared.models import Alert

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()
SNOOZE_MINUTES = config.get("alerts", {}).get("snooze_minutes", 5)


class AlertsService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        self._published_titles: set[str] = set()
        self._ekf_seen = False
        self._calibration_announced = False

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        log.info("alerts_service_started")
        asyncio.create_task(self._monitor_loop())

    async def _get_snoozed_ids(self) -> set[str]:
        data = await self.redis.get_state("ndt:alerts:snoozed") or {}
        now = datetime.now(timezone.utc).timestamp()
        active = {k for k, v in data.items() if v > now}
        return active

    async def _persist_alert(self, alert: Alert) -> None:
        key = f"{alert.level}:{alert.title}"
        if key in self._published_titles:
            return
        self._published_titles.add(key)
        if len(self._published_titles) > 200:
            self._published_titles.clear()

        message = f"{alert.title} – {alert.description}"
        try:
            await insert_alert(
                level=alert.level.value,
                message=message,
                tte_sec=alert.tte_sec,
                alert_id=alert.id,
            )
        except Exception as e:
            log.warning("alert_db_insert_failed", error=str(e))

    async def _monitor_loop(self) -> None:
        while True:
            try:
                state = await self.redis.get_state("ndt:state:current")
                prediction = await self.redis.get_state("ndt:prediction:current")
                ekf = await self.redis.get_state("ndt:ekf:current")
                snoozed = await self._get_snoozed_ids()

                ekf_calibrated = False
                if ekf and not self._ekf_seen:
                    self._ekf_seen = True
                if ekf and self._ekf_seen and not self._calibration_announced:
                    if ekf.get("innovation_norm", 99) is not None and ekf.get("innovation_norm", 99) < 2:
                        ekf_calibrated = True
                        self._calibration_announced = True

                if state and "reactor" in state:
                    system_mode = state.get("mode", "normal")
                    new_alerts = evaluate_alerts(
                        state["reactor"],
                        prediction,
                        system_mode=system_mode,
                        ekf_calibrated=ekf_calibrated,
                    )

                    visible: list[dict] = []
                    for alert in new_alerts:
                        aid = str(alert.id) if alert.id else alert.title
                        if aid in snoozed:
                            continue
                        await self._persist_alert(alert)
                        d = alert.model_dump(mode="json")
                        d["id"] = str(alert.id) if alert.id else aid
                        visible.append(d)

                    await self.redis.set_state(
                        "ndt:alerts:active",
                        {"count": len(visible), "alerts": visible},
                        ttl=15,
                    )
                    await self.redis.set_state(
                        "ndt:alerts:list",
                        {"alerts": visible},
                        ttl=3600,
                    )

                    for alert in new_alerts:
                        await self.rabbit.publish(
                            "alert.new",
                            alert.model_dump(mode="json"),
                        )

            except Exception as e:
                log.error("alerts_error", error=str(e))

            await asyncio.sleep(2)


async def main() -> None:
    service = AlertsService()
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
