"""Tests for the actual route surface.

- /funding wires through to SEC EDGAR (which we mock via httpx.AsyncClient patching)
- /people, /tech, /traffic return 503 with structured "signal_unconfigured" body
  because no providers are wired in providers.yaml yet
- /providers GET reflects what's registered + active
- Pydantic strict mode rejects unknown fields with 422
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def env_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-token-rc"
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", token)
    return token


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- /providers --------------------------------------------------------------------------------


async def test_providers_lists_all_signals(client: AsyncClient) -> None:
    resp = await client.get("/providers")
    assert resp.status_code == 200
    body = resp.json()
    signals = {s["signal"] for s in body["signals"]}
    assert signals == {"search", "scraping", "traffic", "funding", "people", "tech"}


async def test_providers_shows_funding_active_as_sec_edgar(client: AsyncClient) -> None:
    resp = await client.get("/providers")
    funding = next(s for s in resp.json()["signals"] if s["signal"] == "funding")
    assert funding["active"] == "sec_edgar"
    assert "sec_edgar" in funding["registered"]


async def test_providers_shows_unconfigured_signals_as_null(client: AsyncClient) -> None:
    """Pick whichever signal is still unconfigured at this point in the build (traffic
    until the Google Trends provider lands)."""
    resp = await client.get("/providers")
    traffic = next(s for s in resp.json()["signals"] if s["signal"] == "traffic")
    assert traffic["active"] is None


# ---- /funding (wired to SEC EDGAR) ---------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


def _patch_httpx(responses: dict[str, _FakeResponse]):
    @asynccontextmanager
    async def fake_async_client(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        class FakeClient:
            async def get(self, url: str, **_: Any) -> _FakeResponse:
                for k, resp in responses.items():
                    if k in url:
                        return resp
                raise AssertionError(f"unexpected URL: {url}")

        yield FakeClient()

    return patch("httpx.AsyncClient", fake_async_client)


_TICKERS_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}
_AAPL_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-K"],
            "filingDate": ["2024-11-01"],
            "accessionNumber": ["0000320193-24-000123"],
            "primaryDocument": ["aapl-20240928.htm"],
        }
    }
}


async def test_funding_returns_result_for_known_company(client: AsyncClient, env_token: str) -> None:
    responses = {
        "company_tickers.json": _FakeResponse(200, _TICKERS_PAYLOAD),
        "submissions/CIK": _FakeResponse(200, _AAPL_SUBMISSIONS),
    }
    with _patch_httpx(responses):
        resp = await client.post(
            "/funding",
            json={"company": "AAPL"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_public"] is True
    assert body["ticker"] == "AAPL"
    assert body["provider"] == "sec_edgar"
    assert len(body["sec_filings"]) == 1


async def test_funding_rejects_extra_fields(client: AsyncClient, env_token: str) -> None:
    """pydantic strict mode — extra fields → 422."""
    resp = await client.post(
        "/funding",
        json={"company": "AAPL", "extra_field": "should-be-rejected"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_funding_rejects_empty_company(client: AsyncClient, env_token: str) -> None:
    resp = await client.post(
        "/funding",
        json={"company": ""},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_funding_rejects_oversized_company(client: AsyncClient, env_token: str) -> None:
    resp = await client.post(
        "/funding",
        json={"company": "x" * 250},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


# ---- Unconfigured signals → 503 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path,body",
    [
        ("/people", {"company": "Apple Inc."}),
        ("/tech", {"primary_url": "https://example.com"}),
        ("/traffic", {"domain": "example.com"}),
    ],
)
async def test_unconfigured_signal_returns_503(client: AsyncClient, env_token: str, path: str, body: dict) -> None:
    resp = await client.post(path, json=body, headers={"Authorization": f"Bearer {env_token}"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "signal_unconfigured"
    assert detail["signal"] in {"people", "tech", "traffic"}


async def test_traffic_rejects_invalid_domain(client: AsyncClient, env_token: str) -> None:
    """Pydantic regex catches obviously-wrong domains before reaching the provider."""
    resp = await client.post(
        "/traffic",
        json={"domain": "not a domain at all"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_tech_rejects_non_http_url(client: AsyncClient, env_token: str) -> None:
    resp = await client.post(
        "/tech",
        json={"primary_url": "javascript:alert(1)"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422
