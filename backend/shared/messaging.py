"""Redis pub/sub and RabbitMQ helpers."""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

import aio_pika
import redis.asyncio as aioredis
from pydantic import BaseModel

CHANNELS = {
    "sensor_data": "ndt:sensor:data",
    "reactor_state": "ndt:reactor:state",
    "ekf_update": "ndt:ekf:update",
    "pinn_output": "ndt:pinn:output",
    "prediction": "ndt:prediction",
    "dss_recommendation": "ndt:dss:recommendation",
    "alert": "ndt:alert",
}

EXCHANGES = {
    "ndt_events": "ndt.events",
}


class RedisBus:
    def __init__(self, url: str):
        self.url = url
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self.url, decode_responses=True)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def publish(self, channel: str, data: dict[str, Any] | BaseModel) -> None:
        assert self._client is not None
        payload = data.model_dump_json() if isinstance(data, BaseModel) else json.dumps(data)
        await self._client.publish(channel, payload)

    async def set_state(self, key: str, data: dict[str, Any] | BaseModel, ttl: int = 60) -> None:
        assert self._client is not None
        payload = data.model_dump_json() if isinstance(data, BaseModel) else json.dumps(data)
        await self._client.setex(key, ttl, payload)

    async def get_state(self, key: str) -> dict[str, Any] | None:
        assert self._client is not None
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None


class RabbitMQBus:
    def __init__(self, url: str):
        self.url = url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.Channel | None = None
        self._exchange: aio_pika.Exchange | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self.url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            EXCHANGES["ndt_events"], aio_pika.ExchangeType.TOPIC, durable=True
        )

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()

    async def publish(self, routing_key: str, data: dict[str, Any] | BaseModel) -> None:
        assert self._exchange is not None
        body = data.model_dump_json() if isinstance(data, BaseModel) else json.dumps(data)
        await self._exchange.publish(
            aio_pika.Message(body=body.encode(), content_type="application/json"),
            routing_key=routing_key,
        )

    async def subscribe(
        self,
        queue_name: str,
        routing_keys: list[str],
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        assert self._channel is not None and self._exchange is not None
        queue = await self._channel.declare_queue(queue_name, durable=True)
        for key in routing_keys:
            await queue.bind(self._exchange, routing_key=key)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    payload = json.loads(message.body.decode())
                    await handler(payload)
