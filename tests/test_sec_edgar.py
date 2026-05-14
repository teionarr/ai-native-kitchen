"""Tests for the SEC EDGAR funding provider.

httpx is mocked via pytest-httpx-style monkeypatching of httpx.AsyncClient.get so we
don't hit sec.gov in CI. (We could use respx but it's another dep; the manual mock
is small and clear.)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from src.upstreams.funding.sec_edgar import (
    INTERESTING_FORMS,
    SECEdgarProvider,
    _filing_url,
    _parse_date,
)

# ---- Helpers -----------------------------------------------------------------------------------


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


def _make_client(responses: dict[str, _FakeResponse]):
    """Patch httpx.AsyncClient so .get(url) returns the matching fake response."""

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


# ---- Pure helpers ------------------------------------------------------------------------------


def test_parse_date_handles_iso_format() -> None:
    d = _parse_date("2025-03-14")
    assert d.year == 2025 and d.month == 3 and d.day == 14


def test_parse_date_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        _parse_date("not a date")


def test_filing_url_strips_dashes_from_accession() -> None:
    url = _filing_url(
        cik=320193,
        accession_number="0000320193-24-000123",
        primary_doc="aapl-20240928.htm",
    )
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"


def test_interesting_forms_includes_core_filings() -> None:
    for f in ("10-K", "10-Q", "8-K", "S-1"):
        assert f in INTERESTING_FORMS


# ---- Provider behaviour -----------------------------------------------------------------------


@pytest.fixture
def provider() -> SECEdgarProvider:
    return SECEdgarProvider()


@pytest.fixture
def stripe_tickers_payload() -> dict:
    return {
        "0": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
        "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    }


@pytest.fixture
def aapl_submissions_payload() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-K", "8-K", "DEF 14A", "SD", "10-Q"],
                "filingDate": ["2024-11-01", "2024-10-15", "2024-09-01", "2024-08-01", "2024-08-15"],
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000111",
                    "0000320193-24-000099",
                    "0000320193-24-000088",
                    "0000320193-24-000077",
                ],
                "primaryDocument": [
                    "aapl-20240928.htm",
                    "aapl-8k.htm",
                    "aapl-proxy.htm",
                    "aapl-sd.htm",
                    "aapl-10q.htm",
                ],
            }
        }
    }


async def test_lookup_finds_company_by_ticker(
    provider: SECEdgarProvider,
    stripe_tickers_payload: dict,
    aapl_submissions_payload: dict,
) -> None:
    responses = {
        "company_tickers.json": _FakeResponse(200, stripe_tickers_payload),
        "submissions/CIK": _FakeResponse(200, aapl_submissions_payload),
    }
    with _make_client(responses):
        result = await provider.lookup("AAPL")
    assert result.is_public is True
    assert result.cik == "320193"
    assert result.ticker == "AAPL"
    assert result.company == "Apple Inc."
    assert result.provider == "sec_edgar"
    # Filings filtered to interesting forms only — SD should be excluded
    forms = [f.form_type for f in result.sec_filings]
    assert "10-K" in forms
    assert "8-K" in forms
    assert "DEF 14A" in forms
    assert "10-Q" in forms
    assert "SD" not in forms


async def test_lookup_finds_company_by_exact_name(
    provider: SECEdgarProvider,
    stripe_tickers_payload: dict,
    aapl_submissions_payload: dict,
) -> None:
    responses = {
        "company_tickers.json": _FakeResponse(200, stripe_tickers_payload),
        "submissions/CIK": _FakeResponse(200, aapl_submissions_payload),
    }
    with _make_client(responses):
        result = await provider.lookup("Apple Inc.")
    assert result.is_public is True
    assert result.cik == "320193"


async def test_lookup_substring_matches_pick_shortest(
    provider: SECEdgarProvider,
    aapl_submissions_payload: dict,
) -> None:
    tickers = {
        "0": {"cik_str": 1, "ticker": "X", "title": "Foo Holdings International Plc"},
        "1": {"cik_str": 2, "ticker": "Y", "title": "Foo Inc"},
        "2": {"cik_str": 3, "ticker": "Z", "title": "Foo Bar Capital Management LLC"},
    }
    responses = {
        "company_tickers.json": _FakeResponse(200, tickers),
        "submissions/CIK": _FakeResponse(200, aapl_submissions_payload),
    }
    with _make_client(responses):
        result = await provider.lookup("foo")
    assert result.cik == "2"  # shortest match wins
    assert result.company == "Foo Inc"


async def test_lookup_returns_empty_for_unknown_company(
    provider: SECEdgarProvider, stripe_tickers_payload: dict
) -> None:
    with _make_client({"company_tickers.json": _FakeResponse(200, stripe_tickers_payload)}):
        result = await provider.lookup("ThisCompanyDoesNotExist Inc")
    assert result.is_public is False
    assert result.sec_filings == []
    assert any("no SEC EDGAR match" in n for n in result.notes)


async def test_lookup_handles_404_on_submissions(provider: SECEdgarProvider, stripe_tickers_payload: dict) -> None:
    responses = {
        "company_tickers.json": _FakeResponse(200, stripe_tickers_payload),
        "submissions/CIK": _FakeResponse(404, None),
    }
    with _make_client(responses):
        result = await provider.lookup("AAPL")
    assert result.is_public is True
    assert result.sec_filings == []


async def test_lookup_caps_filings_at_20(provider: SECEdgarProvider, stripe_tickers_payload: dict) -> None:
    # 30 8-Ks, all interesting form
    big_payload = {
        "filings": {
            "recent": {
                "form": ["8-K"] * 30,
                "filingDate": ["2024-01-01"] * 30,
                "accessionNumber": [f"0000320193-24-{i:06d}" for i in range(30)],
                "primaryDocument": [f"doc{i}.htm" for i in range(30)],
            }
        }
    }
    responses = {
        "company_tickers.json": _FakeResponse(200, stripe_tickers_payload),
        "submissions/CIK": _FakeResponse(200, big_payload),
    }
    with _make_client(responses):
        result = await provider.lookup("AAPL")
    assert len(result.sec_filings) == 20


async def test_lookup_rejects_empty_company(provider: SECEdgarProvider) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        await provider.lookup("")
