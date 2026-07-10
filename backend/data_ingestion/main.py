"""Data Ingestion Service – OPC UA client, filtering, InfluxDB write (DI-01 to DI-04)."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone

import structlog
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from shared.config import get_settings, load_yaml_config
from shared.messaging import RedisBus, RabbitMQBus, CHANNELS
from shared.models import ReactorState, SystemMode, SensorReading

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()


class MedianFilter:
    """DI-02: 5-sample median noise filter."""

    def __init__(self, window: int = 5):
        self._buffers: dict[str, deque[float]] = {}
        self.window = window

    def filter(self, sensor_id: str, value: float) -> float:
        if sensor_id not in self._buffers:
            self._buffers[sensor_id] = deque(maxlen=self.window)
        self._buffers[sensor_id].append(value)
        sorted_vals = sorted(self._buffers[sensor_id])
        mid = len(sorted_vals) // 2
        return sorted_vals[mid]


class DataIngestionService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        self.median = MedianFilter(window=config.get("sensors", {}).get("median_filter_window", 5))
        self.last_reading_time: datetime | None = None
        self._influx: InfluxDBClient | None = None
        self._write_api = None

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        self._influx = InfluxDBClient(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org,
        )
        self._write_api = self._influx.write_api(write_options=SYNCHRONOUS)
        log.info("data_ingestion_started", endpoint=settings.opcua_endpoint)
        await self._poll_loop()

    async def _read_sensors_opcua(self) -> dict[str, float]:
        """Read from OPC UA or fallback to synthetic data for development."""
        try:
            from asyncua import Client

            async with Client(url=settings.opcua_endpoint) as client:
                readings = {}
                for sensor in config.get("sensors", {}).get("points", []):
                    node = await client.get_node(sensor["tag"])
                    val = await node.read_value()
                    readings[sensor["id"]] = float(val)
                return readings
        except Exception as e:
            log.warning("opcua_fallback_synthetic", error=str(e))
            return self._synthetic_readings()

    def _synthetic_readings(self) -> dict[str, float]:
        t = time.time()
        return {
            "pressure_inlet": 15.5 + 0.05 * random.gauss(0, 1),
            "pressure_outlet": 15.3 + 0.05 * random.gauss(0, 1),
            "temp_inlet": 290 + 2 * random.gauss(0, 1),
            "temp_outlet": 330 + 2 * random.gauss(0, 1),
            "mass_flow": 18500 + 100 * random.gauss(0, 1),
            "power_thermal": 3000 * (1 + 0.02 * __import__("math").sin(t / 3600)),
            "dnbr_hot_channel": 1.8 - 0.001 * (t % 86400) / 86400,
        }

    def _validate_sensor(self, sensor_id: str, value: float) -> str:
        for sensor in config.get("sensors", {}).get("points", []):
            if sensor["id"] == sensor_id:
                if value < sensor["min"] or value > sensor["max"]:
                    return "bad"
        return "good"

    async def _write_influx(self, readings: list[SensorReading]) -> None:
        if not self._write_api:
            return
        points = []
        for r in readings:
            p = (
                Point("sensor")
                .tag("sensor_id", r.sensor_id)
                .field("value", r.value)
                .field("quality", r.quality)
                .time(r.timestamp)
            )
            points.append(p)
        self._write_api.write(bucket=settings.influxdb_bucket, record=points)

    async def _poll_loop(self) -> None:
        interval = 1.0 / settings.opcua_sample_rate_hz
        timeout = settings.opcua_sensor_timeout_sec

        while True:
            try:
                raw = await self._read_sensors_opcua()
                now = datetime.now(timezone.utc)
                self.last_reading_time = now

                readings: list[SensorReading] = []
                for sensor_id, value in raw.items():
                    filtered = self.median.filter(sensor_id, value)
                    quality = self._validate_sensor(sensor_id, filtered)
                    readings.append(
                        SensorReading(
                            sensor_id=sensor_id,
                            value=filtered,
                            unit="",
                            timestamp=now,
                            quality=quality,
                        )
                    )

                await self._write_influx(readings)

                reactor = ReactorState(
                    timestamp=now,
                    power_mwth=raw.get("power_thermal", 3000),
                    pressure_mpa=raw.get("pressure_inlet", 15.5),
                    temp_inlet_c=raw.get("temp_inlet", 290),
                    temp_outlet_c=raw.get("temp_outlet", 330),
                    mass_flow_kg_s=raw.get("mass_flow", 18500),
                    temp_hot_channel_c=raw.get("temp_outlet", 330) + 15,
                    temp_cladding_max_c=380,
                    dnbr=raw.get("dnbr_hot_channel", 1.8),
                    mode=SystemMode.NORMAL,
                )

                state_payload = {
                    "reactor": reactor.model_dump(mode="json"),
                    "readings": [r.model_dump(mode="json") for r in readings],
                    "last_update": now.isoformat(),
                    "mode": "normal",
                    "services": {"data-ingestion": True},
                }
                await self.redis.set_state("ndt:state:current", state_payload, ttl=10)
                await self.redis.publish(CHANNELS["sensor_data"], state_payload)
                await self.rabbit.publish("sensor.data", state_payload)

            except Exception as e:
                log.error("poll_error", error=str(e))

            # DI-03: Cut sensor -> Safe Mode (AL-09 / section 3.1)
            if self.last_reading_time:
                elapsed = (datetime.now(timezone.utc) - self.last_reading_time).total_seconds()
                if elapsed > timeout:
                    log.warning("cut_sensor_alert", elapsed_sec=elapsed)
                    safe_payload = {
                        **(await self.redis.get_state("ndt:state:current") or {}),
                        "mode": "safe",
                        "services": {"data-ingestion": False},
                        "safe_mode_reason": "sensor_cut",
                        "last_update": self.last_reading_time.isoformat(),
                    }
                    await self.redis.set_state("ndt:state:current", safe_payload, ttl=30)

            await asyncio.sleep(interval)


async def main() -> None:
    service = DataIngestionService()
    await service.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
