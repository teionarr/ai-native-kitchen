"""Tests for the Redis cache wrapper.

Uses fakeredis (in-process Redis-compatible server) so no real Redis daemon
is needed for CI. We swap the underlying client via the existing module-level
caching hook (reset_for_tests + monkeypatch on _client).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

import src.cache as cache_mod


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Replace the cache module's client with a fresh fakeredis for each test."""
    server = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await cache_mod.reset_for_tests()
    # Mark as "already attempted" so _get_client() doesn't try to read settings.redis_url
    cache_mod._client = server
    cache_mod._client_attempted = True
    yield server
    await cache_mod.reset_for_tests()


# ---- _hash_key behaviour -----------------------------------------------------------------------


def test_hash_key_is_stable_for_dicts() -> None:
    k1 = cache_mod._hash_key("funding", "sec_edgar", {"company": "Apple", "limit": 5})
    k2 = cache_mod._hash_key("funding", "sec_edgar", {"limit": 5, "company": "Apple"})
    assert k1 == k2


def test_hash_key_changes_with_signal_or_provider() -> None:
    p = {"company": "X"}
    assert cache_mod._hash_key("funding", "sec_edgar", p) != cache_mod._hash_key("funding", "crunchbase", p)
    assert cache_mod._hash_key("funding", "sec_edgar", p) != cache_mod._hash_key("people", "sec_edgar", p)


def test_hash_key_does_not_leak_company_name_into_key() -> None:
    """A redis-cli KEYS dump shouldn't reveal what companies were looked up."""
    key = cache_mod._hash_key("funding", "sec_edgar", {"company": "Acme Corporation"})
    assert "Acme" not in key
    assert "acme" not in key.lower()


def test_hash_key_string_payload_works() -> None:
    key = cache_mod._hash_key("funding", "sec_edgar", "just-a-string")
    assert key.startswith("funding:sec_edgar:")


# ---- Cache behaviour with fakeredis ------------------------------------------------------------


async def test_get_returns_none_for_missing(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    assert await cache_mod.get("funding", "sec_edgar", {"company": "missing"}) is None


async def test_set_then_get_roundtrip(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    payload = {"company": "Apple"}
    value = {"is_public": True, "ticker": "AAPL", "sec_filings": []}
    await cache_mod.set("funding", "sec_edgar", payload, value)
    got = await cache_mod.get("funding", "sec_edgar", payload)
    assert got == value


async def test_set_uses_fact_ttl_by_default(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    payload = {"company": "Apple"}
    await cache_mod.set("funding", "sec_edgar", payload, {"x": 1})
    key = cache_mod._hash_key("funding", "sec_edgar", payload)
    ttl = await fake_redis.ttl(key)
    assert 0 < ttl <= cache_mod.FACT_TTL_S


async def test_set_uses_static_ttl_when_requested(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    payload = {"company": "Apple"}
    await cache_mod.set("funding", "sec_edgar", payload, {"x": 1}, ttl_kind="static")
    key = cache_mod._hash_key("funding", "sec_edgar", payload)
    ttl = await fake_redis.ttl(key)
    assert cache_mod.FACT_TTL_S < ttl <= cache_mod.STATIC_TTL_S


async def test_corrupt_entry_treated_as_miss(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    payload = {"company": "Apple"}
    key = cache_mod._hash_key("funding", "sec_edgar", payload)
    # Inject malformed JSON directly
    await fake_redis.set(key, "not valid {{ json")
    got = await cache_mod.get("funding", "sec_edgar", payload)
    assert got is None
    # And the corrupt key should be cleaned up
    assert await fake_redis.get(key) is None


async def test_delete_removes_entry(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    payload = {"company": "Apple"}
    await cache_mod.set("funding", "sec_edgar", payload, {"x": 1})
    assert await cache_mod.delete("funding", "sec_edgar", payload) is True
    assert await cache_mod.get("funding", "sec_edgar", payload) is None


async def test_delete_returns_false_for_missing(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    assert await cache_mod.delete("funding", "sec_edgar", {"company": "never-cached"}) is False


async def test_circular_reference_does_not_raise(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """set() must never raise. A genuinely-unserializable value (circular ref) is
    swallowed and logged; nothing lands in the cache."""
    a: dict = {}
    a["self"] = a  # circular — json.dumps raises ValueError even with default=str

    # No exception
    await cache_mod.set("funding", "sec_edgar", {"company": "X"}, a)
    # And nothing landed
    assert await cache_mod.get("funding", "sec_edgar", {"company": "X"}) is None


async def test_set_serializes_datetime_via_default_str(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """The `default=str` arg in set() lets pydantic-flavored values (datetimes, etc.)
    serialize without callers having to model_dump(mode='json') everywhere."""
    from datetime import date

    payload = {"company": "X"}
    await cache_mod.set("funding", "sec_edgar", payload, {"d": date(2025, 1, 1)})
    got = await cache_mod.get("funding", "sec_edgar", payload)
    assert got == {"d": "2025-01-01"}


# ---- Graceful degradation when Redis isn't configured ------------------------------------------


async def test_no_redis_url_disables_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """When KITCHEN_REDIS_URL isn't set, every operation is a no-op."""
    monkeypatch.setattr(cache_mod.settings, "redis_url", None)
    await cache_mod.reset_for_tests()

    await cache_mod.set("funding", "sec_edgar", {"company": "X"}, {"v": 1})
    assert await cache_mod.get("funding", "sec_edgar", {"company": "X"}) is None
    assert await cache_mod.delete("funding", "sec_edgar", {"company": "X"}) is False


async def test_redis_unreachable_disables_cache_after_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Redis is configured but unreachable, the cache disables itself after the
    first failed ping — subsequent calls return None without retrying."""
    monkeypatch.setattr(cache_mod.settings, "redis_url", "redis://127.0.0.1:1/0")  # nothing on port 1
    await cache_mod.reset_for_tests()

    # First call attempts ping → fails → disables
    assert await cache_mod.get("funding", "sec_edgar", {"company": "X"}) is None
    # Second call short-circuits — _client_attempted is True
    assert cache_mod._client_attempted is True
    assert cache_mod._client is None
    assert await cache_mod.get("funding", "sec_edgar", {"company": "Y"}) is None


# ---- Integration with funding route -----------------------------------------------------------


async def test_funding_route_uses_cache_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Two identical /funding requests → provider lookup called exactly once."""
    from contextlib import asynccontextmanager
    from typing import Any
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient

    from src.main import app

    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", "tok")

    tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "filingDate": ["2024-11-01"],
                "accessionNumber": ["0000320193-24-000123"],
                "primaryDocument": ["aapl-20240928.htm"],
            }
        }
    }

    call_counter = {"tickers": 0, "submissions": 0}

    class _FakeResponse:
        def __init__(self, status_code: int, payload: Any) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self._payload

    @asynccontextmanager
    async def fake_client(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        class C:
            async def get(self, url: str, **__: Any) -> _FakeResponse:
                if "company_tickers.json" in url:
                    call_counter["tickers"] += 1
                    return _FakeResponse(200, tickers)
                if "submissions/CIK" in url:
                    call_counter["submissions"] += 1
                    return _FakeResponse(200, submissions)
                raise AssertionError(f"unexpected URL: {url}")

        yield C()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("httpx.AsyncClient", fake_client):
            r1 = await client.post(
                "/funding",
                json={"company": "AAPL"},
                headers={"Authorization": "Bearer tok"},
            )
            r2 = await client.post(
                "/funding",
                json={"company": "AAPL"},
                headers={"Authorization": "Bearer tok"},
            )
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both responses identical
    assert r1.json() == r2.json()
    # Provider was only hit once — second call served from cache
    assert call_counter["submissions"] == 1
    assert call_counter["tickers"] == 1
