"""Tests for the Wappalyzer OSS tech provider + the /tech route.

The `python-Wappalyzer` library actually fetches URLs at analyze time, so all tests
mock the sync `_analyze_sync` helper at the module level — no real HTTP, no
fingerprint download.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

# ---- Provider unit tests -----------------------------------------------------------------------


async def test_lookup_happy_path_returns_tech_result() -> None:
    from src.upstreams.tech.wappalyzer_oss import WappalyzerOSSProvider

    provider = WappalyzerOSSProvider()
    fake_raw = {
        "React": {"versions": ["18.2.0"], "categories": ["JavaScript Frameworks"]},
        "Cloudflare": {"versions": [], "categories": ["CDN", "DNS"]},
        "Postgres": {"versions": ["16.0"], "categories": ["Databases"]},
    }
    with patch("src.upstreams.tech.wappalyzer_oss._analyze_sync", return_value=fake_raw):
        result = await provider.lookup("https://example.com")
    assert str(result.url) == "https://example.com/"
    assert result.provider == "wappalyzer_oss"
    by_name = {t.name: t for t in result.technologies}
    assert by_name["React"].category == "JavaScript Frameworks"
    assert by_name["React"].version == "18.2.0"
    assert by_name["Cloudflare"].category == "CDN"  # first category wins
    assert by_name["Cloudflare"].version is None
    assert by_name["Postgres"].version == "16.0"


async def test_lookup_handles_empty_result() -> None:
    """Site with no detectable tech → empty technologies list, not an error."""
    from src.upstreams.tech.wappalyzer_oss import WappalyzerOSSProvider

    provider = WappalyzerOSSProvider()
    with patch("src.upstreams.tech.wappalyzer_oss._analyze_sync", return_value={}):
        result = await provider.lookup("https://example.com")
    assert result.technologies == []


async def test_lookup_rejects_empty_url() -> None:
    from src.upstreams.tech.wappalyzer_oss import WappalyzerOSSProvider

    provider = WappalyzerOSSProvider()
    with pytest.raises(ValueError, match="non-empty"):
        await provider.lookup("")


async def test_lookup_wraps_analyze_error() -> None:
    """Library exceptions get wrapped to RuntimeError so the route maps to 502 cleanly."""
    from src.upstreams.tech.wappalyzer_oss import WappalyzerOSSProvider, _AnalyzeError

    provider = WappalyzerOSSProvider()
    with patch(
        "src.upstreams.tech.wappalyzer_oss._analyze_sync",
        side_effect=_AnalyzeError("could not fetch URL"),
    ):
        with pytest.raises(RuntimeError, match="could not fetch URL"):
            await provider.lookup("https://example.com")


async def test_lookup_times_out() -> None:
    """If analyze takes too long, we kill it and surface a timeout RuntimeError."""
    import asyncio
    import time

    from src.upstreams.tech.wappalyzer_oss import WappalyzerOSSProvider

    provider = WappalyzerOSSProvider()

    def slow_analyze(_url: str) -> dict[str, Any]:
        time.sleep(60)  # longer than ANALYZE_TIMEOUT_S
        return {}

    # Patch the timeout to something short for the test, then verify timeout behaviour
    with patch("src.upstreams.tech.wappalyzer_oss.ANALYZE_TIMEOUT_S", 0.05):
        with patch("src.upstreams.tech.wappalyzer_oss._analyze_sync", side_effect=slow_analyze):
            with pytest.raises(RuntimeError, match="timed out"):
                await asyncio.wait_for(provider.lookup("https://example.com"), timeout=10.0)


def test_to_technology_handles_partial_input() -> None:
    """Garbage / partial info dicts should produce a Technology, not raise."""
    from src.upstreams.tech.wappalyzer_oss import _to_technology

    # Just a string instead of a dict
    t1 = _to_technology("Mystery", "this is not a dict")
    assert t1.name == "Mystery"
    assert t1.category is None
    assert t1.version is None

    # Dict with non-list versions
    t2 = _to_technology("X", {"versions": "not a list", "categories": ["Cat"]})
    assert t2.version is None
    assert t2.category == "Cat"


# ---- /tech route tests -------------------------------------------------------------------------


@pytest.fixture
def env_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", "tok")
    return "tok"


@pytest.fixture
async def client() -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _async_return(value: Any) -> Any:
    async def _inner(*_a: Any, **_kw: Any) -> Any:
        return value

    return _inner


def _async_noop() -> Any:
    async def _inner(*_a: Any, **_kw: Any) -> None:
        return None

    return _inner


async def test_tech_route_happy_path(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    fake = {"React": {"versions": ["18.2.0"], "categories": ["JavaScript Frameworks"]}}
    with patch("src.upstreams.tech.wappalyzer_oss._analyze_sync", return_value=fake):
        resp = await client.post(
            "/tech",
            json={"primary_url": "https://stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "wappalyzer_oss"
    assert body["technologies"][0]["name"] == "React"


async def test_tech_route_rejects_javascript_url(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/tech",
        json={"primary_url": "javascript:alert(1)"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_tech_route_502_on_provider_error(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod
    from src.upstreams.tech.wappalyzer_oss import _AnalyzeError

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    with patch(
        "src.upstreams.tech.wappalyzer_oss._analyze_sync",
        side_effect=_AnalyzeError("could not fetch https://nonexistent.example"),
    ):
        resp = await client.post(
            "/tech",
            json={"primary_url": "https://nonexistent.example"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 502
    assert "could not fetch" in resp.json()["detail"]


async def test_tech_route_caches_result(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two identical /tech requests → analyze called exactly once (cache hit on the second)."""
    import fakeredis.aioredis

    import src.cache as cache_mod
    import src.cost as cost_mod

    # Wire cache to use fakeredis
    server = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await cache_mod.reset_for_tests()
    cache_mod._client = server
    cache_mod._client_attempted = True

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    call_count = 0

    def counting_analyze(_url: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {"React": {"versions": ["18.2.0"], "categories": ["JavaScript Frameworks"]}}

    with patch("src.upstreams.tech.wappalyzer_oss._analyze_sync", side_effect=counting_analyze):
        r1 = await client.post(
            "/tech",
            json={"primary_url": "https://stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
        r2 = await client.post(
            "/tech",
            json={"primary_url": "https://stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert call_count == 1  # second response served from cache
    await cache_mod.reset_for_tests()
