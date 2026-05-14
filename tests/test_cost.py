"""Tests for the cost-telemetry module.

asyncpg's connection-pool API is too rich to mock by hand, so we wrap the small
piece of it we use (`pool.acquire() -> conn.execute / conn.fetchval`) and patch
asyncpg.create_pool to return a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

import src.cost as cost_mod


class _FakeConn:
    def __init__(self, recorder: list[tuple[str, tuple]]) -> None:
        self._recorder = recorder
        self.fetchval_returns: Any = 0.0

    async def execute(self, sql: str, *args: Any) -> str:
        self._recorder.append(("execute", (sql.strip(), args)))
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._recorder.append(("fetchval", (sql.strip(), args)))
        return self.fetchval_returns


class _FakePool:
    def __init__(self) -> None:
        self.recorder: list[tuple[str, tuple]] = []
        self.conn = _FakeConn(self.recorder)
        self.closed = False

    def acquire(self) -> _AcquireCM:
        return _AcquireCM(self.conn)

    async def close(self) -> None:
        self.closed = True


class _AcquireCM:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.fixture
async def fake_pool(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_FakePool]:
    """Replace asyncpg.create_pool with one that returns our fake pool."""
    pool = _FakePool()

    async def fake_create_pool(*_a: Any, **_kw: Any) -> _FakePool:
        return pool

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(cost_mod.settings, "postgres_dsn", "postgresql://x@y/z")
    await cost_mod.reset_for_tests()
    yield pool
    await cost_mod.reset_for_tests()


# ---- record() ----------------------------------------------------------------------------------


async def test_record_inserts_row_with_all_fields(fake_pool: _FakePool) -> None:
    await cost_mod.record(
        skill_id="research-company",
        endpoint="/funding",
        status_code=200,
        duration_ms=42,
        cache_hit=True,
        upstream_cost_usd=0.123,
        request_id="req-abc",
    )
    # First op is "execute" with the migration SQL on init; second is our INSERT.
    inserts = [(op, args) for op, args in fake_pool.recorder if op == "execute" and "INSERT INTO cost_log" in args[0]]
    assert len(inserts) == 1
    args = inserts[0][1][1]
    assert args == ("research-company", "/funding", 200, 42, True, 0.123, "req-abc")


async def test_record_silent_when_postgres_dsn_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_mod.settings, "postgres_dsn", None)
    await cost_mod.reset_for_tests()
    # Should not raise even though no Postgres is configured
    await cost_mod.record(skill_id="x", endpoint="/y", status_code=200, duration_ms=10)


async def test_record_silent_when_pool_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """First connect attempt fails → cache disabled → record() silently no-ops."""
    import asyncpg

    async def failing_create(*_a: Any, **_kw: Any) -> Any:
        raise asyncpg.PostgresConnectionError("connection refused")

    monkeypatch.setattr("asyncpg.create_pool", failing_create)
    monkeypatch.setattr(cost_mod.settings, "postgres_dsn", "postgresql://x@y/z")
    await cost_mod.reset_for_tests()

    await cost_mod.record(skill_id="x", endpoint="/y", status_code=200, duration_ms=10)
    assert cost_mod._pool_attempted is True
    assert cost_mod._pool is None


# ---- daily_total() -----------------------------------------------------------------------------


async def test_daily_total_returns_postgres_value(fake_pool: _FakePool) -> None:
    fake_pool.conn.fetchval_returns = 1.234
    total = await cost_mod.daily_total("research-company")
    assert total == 1.234
    fetchvals = [args for op, args in fake_pool.recorder if op == "fetchval"]
    assert len(fetchvals) == 1
    assert "SUM(upstream_cost_usd)" in fetchvals[0][0]
    assert fetchvals[0][1][0] == "research-company"  # skill_id parameter


async def test_daily_total_returns_zero_when_postgres_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cost_mod.settings, "postgres_dsn", None)
    await cost_mod.reset_for_tests()
    assert await cost_mod.daily_total("x") == 0.0


async def test_daily_total_returns_zero_on_query_error(fake_pool: _FakePool) -> None:
    """Fail-open: query errors return 0.0 so requests aren't blocked by a Postgres hiccup."""
    import asyncpg

    fake_pool.conn.fetchval = AsyncMock(side_effect=asyncpg.PostgresError("boom"))
    total = await cost_mod.daily_total("research-company")
    assert total == 0.0


async def test_daily_total_returns_zero_when_sum_is_null(fake_pool: _FakePool) -> None:
    """No rows for this skill yet → SUM returns NULL → COALESCE wraps to 0; we still
    coerce defensively."""
    fake_pool.conn.fetchval_returns = None
    total = await cost_mod.daily_total("never-recorded")
    assert total == 0.0


# ---- migrations --------------------------------------------------------------------------------


async def test_migration_sql_runs_on_first_connect(fake_pool: _FakePool) -> None:
    await cost_mod._get_pool()  # forces init
    executes = [args[0] for op, args in fake_pool.recorder if op == "execute"]
    assert any("CREATE TABLE IF NOT EXISTS cost_log" in sql for sql in executes)
    assert any("CREATE INDEX IF NOT EXISTS cost_log_skill_ts" in sql for sql in executes)


# ---- Pool reuse --------------------------------------------------------------------------------


async def test_pool_is_reused_across_calls(fake_pool: _FakePool) -> None:
    pool1 = await cost_mod._get_pool()
    pool2 = await cost_mod._get_pool()
    assert pool1 is pool2
