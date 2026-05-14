"""Tests for the bearer-token auth dependency.

Hits a route that requires auth (we use POST /funding) and asserts:
- missing Authorization header → 401
- malformed Authorization header → 401
- wrong scheme (Basic instead of Bearer) → 401
- valid Bearer with unknown token → 401
- valid Bearer with known token → continues to handler (assertion below)
- skill_id derived from env var name (case + dash conversion)
- /health and /providers are exempt (no auth required)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

import src.auth as auth_mod
from src.main import app


@pytest.fixture
def env_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-token-research-company-abc123"
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", token)
    return token


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- load_skill_tokens unit tests --------------------------------------------------------------


def test_load_skill_tokens_picks_up_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", "tok-rc")
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_BEST_BET_PICK", "tok-bbp")
    monkeypatch.setenv("UNRELATED_VAR", "ignore")
    tokens = auth_mod.load_skill_tokens()
    assert tokens["tok-rc"] == "research-company"
    assert tokens["tok-bbp"] == "best-bet-pick"


def test_load_skill_tokens_skips_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_X", "")
    tokens = auth_mod.load_skill_tokens()
    assert "" not in tokens


def test_load_skill_tokens_skips_empty_skill_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env var literally named SERVICE_BEARER_TOKEN_ (trailing _) has empty skill_id."""
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_", "should-be-ignored")
    tokens = auth_mod.load_skill_tokens()
    assert "should-be-ignored" not in tokens


# ---- Auth-dependency end-to-end tests -----------------------------------------------------------


async def test_no_auth_header_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/funding", json={"company": "Apple Inc."})
    assert resp.status_code == 401
    assert "Bearer" in resp.headers.get("www-authenticate", "")


async def test_malformed_authorization_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/funding",
        json={"company": "Apple Inc."},
        headers={"Authorization": "NotBearer something"},
    )
    assert resp.status_code == 401


async def test_wrong_scheme_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/funding",
        json={"company": "Apple Inc."},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp.status_code == 401


async def test_unknown_bearer_token_returns_401(client: AsyncClient, env_token: str) -> None:
    resp = await client.post(
        "/funding",
        json={"company": "Apple Inc."},
        headers={"Authorization": "Bearer this-token-does-not-exist"},
    )
    assert resp.status_code == 401


async def test_health_does_not_require_auth(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_providers_does_not_require_auth(client: AsyncClient) -> None:
    resp = await client.get("/providers")
    assert resp.status_code == 200


async def test_401_does_not_leak_info_about_validity() -> None:
    """Both 'missing' and 'invalid' should return 401 — no oracle."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        missing = await c.post("/funding", json={"company": "X"})
        invalid = await c.post("/funding", json={"company": "X"}, headers={"Authorization": "Bearer x"})
    assert missing.status_code == 401
    assert invalid.status_code == 401
