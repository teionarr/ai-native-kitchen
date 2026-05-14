"""Tests for the Firecrawl scraping provider + the /scrape route.

httpx is mocked at the AsyncClient level so we don't hit api.firecrawl.dev in CI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from src.config import settings

# ---- Provider unit tests -----------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self) -> Any:
        return self._payload


def _patch_httpx_post(response: _FakeResponse | Exception):
    """Patch httpx.AsyncClient so .post(...) returns the given response (or raises)."""

    @asynccontextmanager
    async def fake_async_client(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        class C:
            async def post(self, _url: str, **_kw: Any) -> _FakeResponse:
                if isinstance(response, Exception):
                    raise response
                return response

        yield C()

    return patch("httpx.AsyncClient", fake_async_client)


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "firecrawl_api_key", "fc-test-key")
    return "fc-test-key"


def test_provider_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructor refuses if KITCHEN_FIRECRAWL_API_KEY isn't set — surfaces as 503 in the route."""
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    monkeypatch.setattr(settings, "firecrawl_api_key", None)
    with pytest.raises(ValueError, match="FIRECRAWL_API_KEY"):
        FirecrawlProvider()


async def test_scrape_happy_path_returns_result(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    payload = {
        "success": True,
        "data": {
            "markdown": "# Stripe\n\nStripe is a payment processing platform.",
            "metadata": {"title": "Stripe — payments infrastructure", "language": "en"},
        },
    }
    with _patch_httpx_post(_FakeResponse(200, payload)):
        result = await provider.scrape("https://stripe.com")
    assert str(result.url) == "https://stripe.com/"
    assert "Stripe" in result.text
    assert result.title == "Stripe — payments infrastructure"
    assert result.provider == "firecrawl"


async def test_scrape_rejects_empty_url(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    with pytest.raises(ValueError, match="non-empty"):
        await provider.scrape("")


async def test_scrape_401_raises_with_clear_message(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    with _patch_httpx_post(_FakeResponse(401, text="unauthorized")):
        with pytest.raises(RuntimeError, match="rejected auth"):
            await provider.scrape("https://stripe.com")


async def test_scrape_402_quota_exceeded(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    with _patch_httpx_post(_FakeResponse(402)):
        with pytest.raises(RuntimeError, match="quota exceeded"):
            await provider.scrape("https://stripe.com")


async def test_scrape_429_rate_limited(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    with _patch_httpx_post(_FakeResponse(429)):
        with pytest.raises(RuntimeError, match="rate-limited"):
            await provider.scrape("https://stripe.com")


async def test_scrape_network_error(with_api_key: str) -> None:
    import httpx

    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    with _patch_httpx_post(httpx.NetworkError("connection refused")):
        with pytest.raises(RuntimeError, match="unreachable"):
            await provider.scrape("https://stripe.com")


async def test_scrape_unexpected_payload_shape(with_api_key: str) -> None:
    from src.upstreams.scraping.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    # success=False in the response
    with _patch_httpx_post(_FakeResponse(200, {"success": False, "error": "blocked"})):
        with pytest.raises(RuntimeError, match="unexpected payload"):
            await provider.scrape("https://stripe.com")


# ---- /scrape route tests -----------------------------------------------------------------------


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


async def test_scrape_route_happy_path(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bypass cap + telemetry
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    payload = {
        "success": True,
        "data": {
            "markdown": "# Stripe page content",
            "metadata": {"title": "Stripe"},
        },
    }
    with _patch_httpx_post(_FakeResponse(200, payload)):
        resp = await client.post(
            "/scrape",
            json={"url": "https://stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Stripe"
    assert "Stripe page content" in body["text"]
    assert body["provider"] == "firecrawl"


async def test_scrape_route_503_when_api_key_missing(
    client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No KITCHEN_FIRECRAWL_API_KEY → provider __init__ raises → route returns 503."""
    import src.cost as cost_mod

    monkeypatch.setattr(settings, "firecrawl_api_key", None)
    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/scrape",
        json={"url": "https://stripe.com"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "signal_unconfigured"
    assert resp.json()["detail"]["signal"] == "scraping"


async def test_scrape_route_rejects_non_http_url(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/scrape",
        json={"url": "javascript:alert(1)"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_scrape_route_rejects_extra_fields(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/scrape",
        json={"url": "https://stripe.com", "rogue_field": "x"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_scrape_route_502_on_provider_error(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    with _patch_httpx_post(_FakeResponse(402)):  # quota exceeded
        resp = await client.post(
            "/scrape",
            json={"url": "https://stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 502
    assert "quota exceeded" in resp.json()["detail"]


# ---- helpers ----------------------------------------------------------------------------------


def _async_return(value: Any) -> Any:
    async def _inner(*_a: Any, **_kw: Any) -> Any:
        return value

    return _inner


def _async_noop() -> Any:
    async def _inner(*_a: Any, **_kw: Any) -> None:
        return None

    return _inner
