"""Tests for the Exa search provider + the /search route."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from src.config import settings

# ---- Helpers -----------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self) -> Any:
        return self._payload


def _patch_httpx_post(response: _FakeResponse | Exception):
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
    monkeypatch.setattr(settings, "exa_api_key", "exa-test-key")
    return "exa-test-key"


# ---- Provider unit tests -----------------------------------------------------------------------


def test_provider_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.upstreams.search.exa import ExaProvider

    monkeypatch.setattr(settings, "exa_api_key", None)
    with pytest.raises(ValueError, match="EXA_API_KEY"):
        ExaProvider()


async def test_search_happy_path(with_api_key: str) -> None:
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    payload = {
        "results": [
            {
                "title": "Stripe — payments infrastructure",
                "url": "https://stripe.com",
                "publishedDate": "2024-11-01T00:00:00.000Z",
                "text": "Stripe is a financial infrastructure platform.",
            },
            {
                "title": "Stripe Press",
                "url": "https://press.stripe.com",
                "text": "Books on technology.",
            },
        ]
    }
    with _patch_httpx_post(_FakeResponse(200, payload)):
        result = await provider.search("Stripe overview", limit=5)
    assert result.query == "Stripe overview"
    assert result.provider == "exa"
    assert len(result.results) == 2
    assert result.results[0].title == "Stripe — payments infrastructure"
    assert str(result.results[0].url) == "https://stripe.com/"
    assert result.results[0].published_at is not None
    assert result.results[0].published_at.year == 2024


async def test_search_rejects_empty_query(with_api_key: str) -> None:
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    with pytest.raises(ValueError, match="non-empty"):
        await provider.search("   ")


async def test_search_clamps_limit(with_api_key: str) -> None:
    """Provider clamps limit into [1, MAX_LIMIT] so a misuse can't ask for 10000 results."""
    from src.upstreams.search.exa import MAX_LIMIT, ExaProvider

    provider = ExaProvider()
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def capturing_client(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        class C:
            async def post(self, _url: str, **kw: Any) -> _FakeResponse:
                captured.update(kw.get("json") or {})
                return _FakeResponse(200, {"results": []})

        yield C()

    with patch("httpx.AsyncClient", capturing_client):
        await provider.search("X", limit=99999)
    assert captured["numResults"] == MAX_LIMIT


async def test_search_drops_malformed_results(with_api_key: str) -> None:
    """Bad URL / missing title fields are skipped silently, not raised."""
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    payload = {
        "results": [
            {"title": "Good", "url": "https://example.com", "text": "ok"},
            {"title": "Bad URL", "url": "not-a-url"},  # dropped
            {"url": "https://no-title.com", "text": "no title — uses url as title"},
            "this is not even a dict",  # dropped
        ]
    }
    with _patch_httpx_post(_FakeResponse(200, payload)):
        result = await provider.search("X")
    assert len(result.results) == 2
    titles = [r.title for r in result.results]
    assert "Good" in titles
    assert "https://no-title.com" in titles  # url-as-title fallback


async def test_search_401_raises(with_api_key: str) -> None:
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    with _patch_httpx_post(_FakeResponse(401)):
        with pytest.raises(RuntimeError, match="rejected auth"):
            await provider.search("X")


async def test_search_402_raises(with_api_key: str) -> None:
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    with _patch_httpx_post(_FakeResponse(402)):
        with pytest.raises(RuntimeError, match="quota exceeded"):
            await provider.search("X")


async def test_search_429_raises(with_api_key: str) -> None:
    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    with _patch_httpx_post(_FakeResponse(429)):
        with pytest.raises(RuntimeError, match="rate-limited"):
            await provider.search("X")


async def test_search_network_error(with_api_key: str) -> None:
    import httpx

    from src.upstreams.search.exa import ExaProvider

    provider = ExaProvider()
    with _patch_httpx_post(httpx.NetworkError("connection refused")):
        with pytest.raises(RuntimeError, match="unreachable"):
            await provider.search("X")


# ---- /search route tests -----------------------------------------------------------------------


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


async def test_search_route_happy_path(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    payload = {
        "results": [
            {"title": "A", "url": "https://a.com", "text": "snippet"},
        ]
    }
    with _patch_httpx_post(_FakeResponse(200, payload)):
        resp = await client.post(
            "/search",
            json={"query": "Stripe overview"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "Stripe overview"
    assert body["provider"] == "exa"
    assert len(body["results"]) == 1


async def test_search_route_503_when_no_api_key(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(settings, "exa_api_key", None)
    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/search",
        json={"query": "X"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["signal"] == "search"


async def test_search_route_rejects_empty_query(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/search",
        json={"query": ""},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_search_route_rejects_oversized_limit(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/search",
        json={"query": "X", "limit": 100},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_search_route_502_on_provider_error(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    with _patch_httpx_post(_FakeResponse(402)):
        resp = await client.post(
            "/search",
            json={"query": "X"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 502
    assert "quota exceeded" in resp.json()["detail"]
