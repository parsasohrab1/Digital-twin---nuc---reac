"""Async PostgreSQL helpers (DSS-07, AL-05, SAF-05)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

import asyncpg
import structlog

from shared.config import get_settings

log = structlog.get_logger()
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = await asyncpg.create_pool(
            host=s.postgres_host,
            port=s.postgres_port,
            user=s.postgres_user,
            password=s.postgres_password,
            database=s.postgres_db,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def insert_dss_recommendations(
    recommendations: list[dict[str, Any]],
    scenario_context: dict[str, Any],
) -> list[str]:
    ids: list[str] = []
    async with acquire() as conn:
        for rec in recommendations:
            row_id = await conn.fetchval(
                """
                INSERT INTO dss_recommendations
                    (action, description, safety_score, success_probability, scenario_context)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id
                """,
                rec.get("action", "")[:128],
                rec.get("action", ""),
                rec.get("safety_score"),
                rec.get("success_probability"),
                json.dumps({**scenario_context, "rank": rec.get("rank")}),
            )
            ids.append(str(row_id))
    return ids


async def confirm_dss_recommendation(rank: int, operator_id: str) -> bool:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE dss_recommendations
            SET operator_approved = TRUE,
                approved_at = NOW(),
                approved_by = $2
            WHERE id = (
                SELECT id FROM dss_recommendations
                WHERE operator_approved = FALSE
                ORDER BY created_at DESC
                OFFSET $1 LIMIT 1
            )
            RETURNING id
            """,
            max(0, rank - 1),
            operator_id,
        )
        return row is not None


async def insert_alert(
    level: str,
    message: str,
    tte_sec: float | None = None,
    alert_id: UUID | None = None,
) -> str:
    async with acquire() as conn:
        if alert_id:
            row_id = await conn.fetchval(
                """
                INSERT INTO alerts (id, level, message, tte_sec)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                alert_id,
                level,
                message,
                tte_sec,
            )
        else:
            row_id = await conn.fetchval(
                """
                INSERT INTO alerts (level, message, tte_sec)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                level,
                message,
                tte_sec,
            )
        return str(row_id) if row_id else ""


async def snooze_alert(alert_id: str, minutes: int = 5) -> bool:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE alerts
            SET snoozed_until = NOW() + ($2 || ' minutes')::interval
            WHERE id = $1::uuid
            RETURNING id
            """,
            alert_id,
            str(minutes),
        )
        return row is not None


async def acknowledge_alert(alert_id: str, operator_id: str) -> bool:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE alerts
            SET acknowledged = TRUE,
                acknowledged_at = NOW(),
                acknowledged_by = $2
            WHERE id = $1::uuid
            RETURNING id
            """,
            alert_id,
            operator_id,
        )
        return row is not None


async def fetch_recent_alerts(limit: int = 50) -> list[dict[str, Any]]:
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, level, message, tte_sec, created_at, acknowledged, snoozed_until
            FROM alerts
            WHERE (snoozed_until IS NULL OR snoozed_until < NOW())
              AND acknowledged = FALSE
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "level": r["level"],
                "title": r["message"].split(" – ")[0] if " – " in r["message"] else r["message"],
                "description": r["message"],
                "tte_sec": r["tte_sec"],
                "created_at": r["created_at"].isoformat(),
                "acknowledged": r["acknowledged"],
                "snoozed_until": r["snoozed_until"].isoformat() if r["snoozed_until"] else None,
            }
            for r in rows
        ]


async def fetch_dss_history(limit: int = 20) -> list[dict[str, Any]]:
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, action, description, safety_score, success_probability,
                   operator_approved, approved_by, created_at
            FROM dss_recommendations
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def fetch_alert_log(days: int = 30, limit: int = 500) -> list[dict[str, Any]]:
    """UI-RQ-11: Alert/event log for last N days."""
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, level, message, tte_sec, created_at, acknowledged
            FROM alerts
            WHERE created_at >= NOW() - ($1 || ' days')::interval
            ORDER BY created_at DESC
            LIMIT $2
            """,
            str(days),
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "level": r["level"],
                "message": r["message"],
                "title": r["message"].split(" – ")[0] if " – " in r["message"] else r["message"],
                "tte_sec": r["tte_sec"],
                "created_at": r["created_at"].isoformat(),
                "acknowledged": r["acknowledged"],
            }
            for r in rows
        ]


async def upsert_system_config(key: str, value: dict[str, Any]) -> None:
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO system_config (key, value, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = NOW()
            """,
            key,
            json.dumps(value),
        )


async def get_system_config(key: str) -> dict[str, Any] | None:
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM system_config WHERE key = $1",
            key,
        )
        if row and row["value"]:
            val = row["value"]
            return json.loads(val) if isinstance(val, str) else dict(val)
        return None
