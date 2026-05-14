"""Per-request cost telemetry.

Every authenticated request inserts a row in `cost_log` with:
  (skill_id, endpoint, status_code, duration_ms, cache_hit, upstream_cost_usd, request_id, ts)

Two operations:
  - record(...): fire-and-forget INSERT after a request completes (used by the
    telemetry middleware)
  - daily_total(skill_id): SUM(upstream_cost_usd) for the last 24h (used by the
    daily-cap check in the auth dependency)

Graceful degradation: if KITCHEN_POSTGRES_DSN is unset OR Postgres is unreachable
on first connect attempt, every operation becomes a no-op:
  - record() silently drops the row (still log-warned on first failure)
  - daily_total() returns 0.0 (so the daily cap is never hit when telemetry is down)

The choice to return 0.0 on failure (vs failing closed at the cap) is deliberate:
we don't want a Postgres outage to take the kitchen down. Operators see the
warning logs and know the cap isn't being enforced until Postgres is restored.

Migration runner: SQL files in src/migrations/ are applied in lexicographic order
on first connect, idempotently (each migration uses CREATE IF NOT EXISTS).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from src.config import settings

log = logging.getLogger("kitchen.cost")

_CONNECT_TIMEOUT_S = 2.0
_OP_TIMEOUT_S = 1.0
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Connection pool — lazy-initialized.
_pool: asyncpg.Pool | None = None
_pool_attempted = False


async def _get_pool() -> asyncpg.Pool | None:
    """Lazy-init the asyncpg pool. None means telemetry is disabled for this process."""
    global _pool, _pool_attempted
    if _pool is not None:
        return _pool
    if _pool_attempted:
        return None
    _pool_attempted = True
    if not settings.postgres_dsn:
        log.info("KITCHEN_POSTGRES_DSN not set — cost telemetry disabled")
        return None
    try:
        pool = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=1,
            max_size=5,
            timeout=_CONNECT_TIMEOUT_S,
            command_timeout=_OP_TIMEOUT_S,
        )
        await _run_migrations(pool)
    except (asyncpg.PostgresError, OSError, TimeoutError) as e:
        log.warning("Postgres unreachable at startup; cost telemetry disabled: %s", e)
        return None
    _pool = pool
    log.info("Postgres connected; cost telemetry enabled")
    return _pool


async def _run_migrations(pool: asyncpg.Pool) -> None:
    """Apply every .sql file in src/migrations/ in lexicographic order. Idempotent."""
    if not _MIGRATIONS_DIR.exists():
        return
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        sql = f.read_text(encoding="utf-8")
        log.info("applying migration: %s", f.name)
        async with pool.acquire() as conn:
            await conn.execute(sql)


async def reset_for_tests() -> None:
    """Drop the cached pool + reset the attempted flag."""
    global _pool, _pool_attempted
    if _pool is not None:
        try:
            await _pool.close()
        except (asyncpg.PostgresError, OSError):
            pass
    _pool = None
    _pool_attempted = False


async def record(
    *,
    skill_id: str,
    endpoint: str,
    status_code: int,
    duration_ms: int,
    cache_hit: bool = False,
    upstream_cost_usd: float = 0.0,
    request_id: str | None = None,
) -> None:
    """Insert one row into cost_log. Silent on Postgres failure."""
    pool = await _get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cost_log
                    (skill_id, endpoint, status_code, duration_ms, cache_hit, upstream_cost_usd, request_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                skill_id,
                endpoint,
                status_code,
                duration_ms,
                cache_hit,
                upstream_cost_usd,
                request_id,
            )
    except (asyncpg.PostgresError, OSError, TimeoutError) as e:
        log.warning("cost.record INSERT failed: %s", e)


async def daily_total(skill_id: str) -> float:
    """SUM(upstream_cost_usd) for this skill in the last 24h.

    Returns 0.0 on any failure (telemetry down or query error). Fail-open: don't
    block requests because Postgres has a hiccup.
    """
    pool = await _get_pool()
    if pool is None:
        return 0.0
    cutoff = datetime.now(UTC) - timedelta(days=1)
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COALESCE(SUM(upstream_cost_usd), 0)::FLOAT FROM cost_log WHERE skill_id=$1 AND ts > $2",
                skill_id,
                cutoff,
            )
        return float(value) if value is not None else 0.0
    except (asyncpg.PostgresError, OSError, TimeoutError) as e:
        log.warning("cost.daily_total query failed: %s", e)
        return 0.0
