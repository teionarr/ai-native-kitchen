"""Tests for the Apollo people provider + the /people route."""

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


def _patch_httpx_get(response: _FakeResponse | Exception):
    @asynccontextmanager
    async def fake_async_client(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        class C:
            async def get(self, _url: str, **_kw: Any) -> _FakeResponse:
                if isinstance(response, Exception):
                    raise response
                return response

        yield C()

    return patch("httpx.AsyncClient", fake_async_client)


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "apollo_api_key", "apollo-test-key")
    return "apollo-test-key"


# ---- Provider unit tests -----------------------------------------------------------------------


def test_provider_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    monkeypatch.setattr(settings, "apollo_api_key", None)
    with pytest.raises(ValueError, match="APOLLO_API_KEY"):
        ApolloProvider()


async def test_lookup_happy_path_with_org(with_api_key: str) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    payload = {
        "organization": {
            "name": "Stripe",
            "estimated_num_employees": 8500,
            "founded_year": 2010,
            "industry": "Financial Services",
            "primary_domain": "stripe.com",
            "city": "South San Francisco",
            "state": "California",
            "country": "United States",
        }
    }
    with _patch_httpx_get(_FakeResponse(200, payload)):
        result = await provider.lookup("stripe.com")
    assert result.company == "Stripe"
    assert result.headcount_estimate == 8500
    assert result.provider == "apollo"
    assert result.leadership == []
    assert any("founded 2010" in n for n in result.notes)
    assert any("Financial Services" in n for n in result.notes)
    assert any("stripe.com" in n for n in result.notes)
    assert any("South San Francisco" in n for n in result.notes)


async def test_lookup_handles_missing_organization(with_api_key: str) -> None:
    """Apollo returns no `organization` key when no match — provider returns empty result with note."""
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with _patch_httpx_get(_FakeResponse(200, {"some": "other shape"})):
        result = await provider.lookup("Nonexistent Co Inc")
    assert result.company == "Nonexistent Co Inc"
    assert result.headcount_estimate is None
    assert any("no organization match" in n for n in result.notes)


async def test_lookup_safe_int_drops_zero_and_garbage(with_api_key: str) -> None:
    """estimated_num_employees=0 or non-numeric → headcount_estimate=None, not a crash."""
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()

    # Zero should become None (Apollo sometimes returns 0 for unknown)
    with _patch_httpx_get(_FakeResponse(200, {"organization": {"name": "X", "estimated_num_employees": 0}})):
        result = await provider.lookup("X")
    assert result.headcount_estimate is None

    # Garbage value
    with _patch_httpx_get(
        _FakeResponse(200, {"organization": {"name": "X", "estimated_num_employees": "lots of people"}})
    ):
        result = await provider.lookup("X")
    assert result.headcount_estimate is None


async def test_lookup_rejects_empty_company(with_api_key: str) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with pytest.raises(ValueError, match="non-empty"):
        await provider.lookup("   ")


async def test_lookup_uses_domain_param_for_domain_input(with_api_key: str) -> None:
    """Domain-shaped input → query={"domain": ...}; name-shaped → q_organization_name."""
    from src.upstreams.people.apollo import _build_query

    assert _build_query("stripe.com") == {"domain": "stripe.com"}
    assert _build_query("Acme Corporation") == {"q_organization_name": "Acme Corporation"}
    # URL-shaped input falls through to name (no scheme stripping in this provider)
    assert _build_query("https://stripe.com") == {"q_organization_name": "https://stripe.com"}


async def test_lookup_401_raises(with_api_key: str) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with _patch_httpx_get(_FakeResponse(401)):
        with pytest.raises(RuntimeError, match="rejected auth"):
            await provider.lookup("stripe.com")


async def test_lookup_402_quota_exceeded(with_api_key: str) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with _patch_httpx_get(_FakeResponse(402)):
        with pytest.raises(RuntimeError, match="quota exceeded"):
            await provider.lookup("stripe.com")


async def test_lookup_429_rate_limited(with_api_key: str) -> None:
    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with _patch_httpx_get(_FakeResponse(429)):
        with pytest.raises(RuntimeError, match="rate-limited"):
            await provider.lookup("stripe.com")


async def test_lookup_network_error(with_api_key: str) -> None:
    import httpx

    from src.upstreams.people.apollo import ApolloProvider

    provider = ApolloProvider()
    with _patch_httpx_get(httpx.NetworkError("connection refused")):
        with pytest.raises(RuntimeError, match="unreachable"):
            await provider.lookup("stripe.com")


# ---- /people route tests -----------------------------------------------------------------------


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


async def test_people_route_happy_path(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    payload = {"organization": {"name": "Stripe", "estimated_num_employees": 8500}}
    with _patch_httpx_get(_FakeResponse(200, payload)):
        resp = await client.post(
            "/people",
            json={"company": "stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["company"] == "Stripe"
    assert body["headcount_estimate"] == 8500
    assert body["provider"] == "apollo"


async def test_people_route_503_when_no_api_key(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(settings, "apollo_api_key", None)
    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/people",
        json={"company": "Stripe"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["signal"] == "people"


async def test_people_route_502_on_quota(
    client: Any, env_token: str, with_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    with _patch_httpx_get(_FakeResponse(402)):
        resp = await client.post(
            "/people",
            json={"company": "stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 502
    assert "quota exceeded" in resp.json()["detail"]
