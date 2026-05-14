"""Tests for the Google Trends traffic provider + the /traffic route.

`pytrends` actually scrapes google.com. All tests mock at the
`_pytrends_lookup_sync` boundary so no real Google traffic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

# ---- Pure helpers ------------------------------------------------------------------------------


def test_domain_to_keyword_strips_scheme_and_tld() -> None:
    from src.upstreams.traffic.google_trends import _domain_to_keyword

    assert _domain_to_keyword("stripe.com") == "stripe"
    assert _domain_to_keyword("https://stripe.com") == "stripe"
    assert _domain_to_keyword("https://www.stripe.com/pricing") == "stripe"
    assert _domain_to_keyword("Stripe.com") == "stripe"
    assert _domain_to_keyword("api.stripe.com") == "api"  # leftmost label wins


def test_classify_trend_handles_short_series() -> None:
    from src.upstreams.traffic.google_trends import _classify_trend

    assert _classify_trend([]) == "unknown"
    assert _classify_trend([1, 2, 3]) == "unknown"  # too short


def test_classify_trend_growing() -> None:
    from src.upstreams.traffic.google_trends import _classify_trend

    # Tail >> head
    series = [10] * 6 + [50] * 6
    assert _classify_trend(series) == "growing"


def test_classify_trend_declining() -> None:
    from src.upstreams.traffic.google_trends import _classify_trend

    series = [50] * 6 + [10] * 6
    assert _classify_trend(series) == "declining"


def test_classify_trend_flat() -> None:
    from src.upstreams.traffic.google_trends import _classify_trend

    series = [50] * 12
    assert _classify_trend(series) == "flat"


def test_classify_trend_zero_baseline_growing() -> None:
    """If the head averages 0 and the tail has any value → growing."""
    from src.upstreams.traffic.google_trends import _classify_trend

    series = [0] * 8 + [10] * 8
    assert _classify_trend(series) == "growing"


# ---- Provider unit tests -----------------------------------------------------------------------


async def test_lookup_happy_path() -> None:
    from src.upstreams.traffic.google_trends import GoogleTrendsProvider

    provider = GoogleTrendsProvider()
    fake_raw = {
        "top_keywords": ["stripe payments", "stripe checkout", "stripe atlas"],
        "growth_indicator": "growing",
        "notes": [],
    }
    with patch("src.upstreams.traffic.google_trends._pytrends_lookup_sync", return_value=fake_raw):
        result = await provider.lookup("stripe.com")
    assert result.domain == "stripe.com"
    assert result.top_keywords == ["stripe payments", "stripe checkout", "stripe atlas"]
    assert result.growth_indicator == "growing"
    assert result.monthly_visits_estimate is None  # Trends doesn't give absolute visits
    assert result.provider == "google_trends"


async def test_lookup_propagates_notes() -> None:
    from src.upstreams.traffic.google_trends import GoogleTrendsProvider

    provider = GoogleTrendsProvider()
    fake_raw = {
        "top_keywords": [],
        "growth_indicator": "unknown",
        "notes": ["no interest-over-time data returned (low search volume?)"],
    }
    with patch("src.upstreams.traffic.google_trends._pytrends_lookup_sync", return_value=fake_raw):
        result = await provider.lookup("obscure-startup.io")
    assert result.growth_indicator == "unknown"
    assert any("no interest-over-time" in n for n in result.notes)


async def test_lookup_rejects_empty_domain() -> None:
    from src.upstreams.traffic.google_trends import GoogleTrendsProvider

    provider = GoogleTrendsProvider()
    with pytest.raises(ValueError, match="non-empty"):
        await provider.lookup("")


async def test_lookup_wraps_pytrends_error_as_runtime_error() -> None:
    """When pytrends breaks (Google anti-scrape), surface as RuntimeError → 502."""
    from src.upstreams.traffic.google_trends import GoogleTrendsProvider, _PytrendsError

    provider = GoogleTrendsProvider()
    with patch(
        "src.upstreams.traffic.google_trends._pytrends_lookup_sync",
        side_effect=_PytrendsError("google_trends payload build failed (likely Google anti-scrape)"),
    ):
        with pytest.raises(RuntimeError, match="anti-scrape"):
            await provider.lookup("stripe.com")


async def test_lookup_times_out() -> None:
    """A wedged pytrends call → timeout RuntimeError, not a hung event loop."""
    import time

    from src.upstreams.traffic.google_trends import GoogleTrendsProvider

    provider = GoogleTrendsProvider()

    def slow_lookup(_keyword: str) -> dict[str, Any]:
        time.sleep(60)
        return {"top_keywords": [], "growth_indicator": "unknown", "notes": []}

    with patch("src.upstreams.traffic.google_trends.TIMEOUT_S", 0.05):
        with patch("src.upstreams.traffic.google_trends._pytrends_lookup_sync", side_effect=slow_lookup):
            with pytest.raises(RuntimeError, match="timed out"):
                await provider.lookup("stripe.com")


# ---- /traffic route tests ----------------------------------------------------------------------


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


async def test_traffic_route_happy_path(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    fake = {
        "top_keywords": ["stripe payments"],
        "growth_indicator": "growing",
        "notes": [],
    }
    with patch("src.upstreams.traffic.google_trends._pytrends_lookup_sync", return_value=fake):
        resp = await client.post(
            "/traffic",
            json={"domain": "stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["domain"] == "stripe.com"
    assert body["growth_indicator"] == "growing"
    assert body["provider"] == "google_trends"


async def test_traffic_route_rejects_invalid_domain(
    client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/traffic",
        json={"domain": "not a domain at all"},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_traffic_route_502_on_provider_error(
    client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod
    from src.upstreams.traffic.google_trends import _PytrendsError

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    with patch(
        "src.upstreams.traffic.google_trends._pytrends_lookup_sync",
        side_effect=_PytrendsError("google rate-limited us"),
    ):
        resp = await client.post(
            "/traffic",
            json={"domain": "stripe.com"},
            headers={"Authorization": f"Bearer {env_token}"},
        )
    assert resp.status_code == 502
    assert "rate-limited" in resp.json()["detail"]
