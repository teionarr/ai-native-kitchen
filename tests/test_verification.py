"""Tests for the verification module — pure-function checks + the /verify route.

URL-liveness checks are exercised against httpx's in-process MockTransport so we
don't hit the network in CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from src.verification import (
    Insight,
    Source,
    verify_insights,
)
from src.verification._contradictions import find_cross_domain_contradictions
from src.verification._diversity import check_source_diversity, downgrade_for_diversity
from src.verification._sanity import check_sanity, load_bounds

# ---- Helpers -----------------------------------------------------------------------------------


def _make_insight(
    *,
    id: str = "test-1",
    domain: str = "sales",
    headline: str = "Default headline",
    evidence: str = "Default evidence",
    sources: list[Source] | None = None,
    confidence: str = "medium",
    raw_facts: list[str] | None = None,
) -> Insight:
    return Insight(
        id=id,
        domain=domain,
        headline=headline,
        evidence=evidence,
        sources=sources if sources is not None else [Source(title="ex", url="https://example.com/x")],
        confidence=confidence,  # type: ignore[arg-type]
        raw_facts=raw_facts or [],
    )


# ---- Source diversity --------------------------------------------------------------------------


def test_diversity_no_sources_is_an_error() -> None:
    ins = _make_insight(sources=[])
    issues = check_source_diversity(ins)
    assert any(i.code == "no_sources" and i.severity == "error" for i in issues)


def test_diversity_high_confidence_with_one_domain_downgrades() -> None:
    ins = _make_insight(
        confidence="high",
        sources=[
            Source(title="a", url="https://stripe.com/a"),
            Source(title="b", url="https://stripe.com/b"),
        ],
    )
    issues = check_source_diversity(ins)
    assert any(i.code == "single_source_domain" for i in issues)
    assert downgrade_for_diversity(ins, issues) == "medium"


def test_diversity_high_confidence_with_two_domains_passes() -> None:
    ins = _make_insight(
        confidence="high",
        sources=[
            Source(title="a", url="https://stripe.com"),
            Source(title="b", url="https://techcrunch.com"),
        ],
    )
    issues = check_source_diversity(ins)
    assert all(i.code != "single_source_domain" for i in issues)
    assert downgrade_for_diversity(ins, issues) == "high"


def test_diversity_medium_confidence_with_one_domain_does_not_flag() -> None:
    """The single-domain rule only kicks in for high-confidence insights."""
    ins = _make_insight(
        confidence="medium",
        sources=[Source(title="a", url="https://stripe.com")],
    )
    issues = check_source_diversity(ins)
    assert all(i.code != "single_source_domain" for i in issues)


def test_diversity_self_citation_only_for_high() -> None:
    ins = _make_insight(
        confidence="high",
        sources=[
            Source(title="a", url="https://stripe.com"),
            Source(title="b", url="https://blog.stripe.com"),
        ],
    )
    issues = check_source_diversity(ins, target_domain="stripe.com")
    assert any(i.code == "self_citation_only" for i in issues)
    assert downgrade_for_diversity(ins, issues) == "medium"


def test_diversity_strips_www_and_subdomain() -> None:
    """blog.stripe.com + www.stripe.com both register as stripe.com."""
    ins = _make_insight(
        confidence="high",
        sources=[
            Source(title="a", url="https://www.stripe.com/a"),
            Source(title="b", url="https://blog.stripe.com/b"),
        ],
    )
    issues = check_source_diversity(ins)
    assert any(i.code == "single_source_domain" for i in issues)


# ---- Sanity bounds -----------------------------------------------------------------------------


def test_sanity_loads_default_bounds() -> None:
    bounds = load_bounds()
    assert "headcount" in bounds
    assert bounds["headcount"]["min"] == 1


def test_sanity_flags_impossible_headcount() -> None:
    ins = _make_insight(
        raw_facts=["headcount ~50,000,000"],
    )
    issues = check_sanity(ins)
    assert any(i.code == "headcount_out_of_bounds" for i in issues)


def test_sanity_passes_realistic_headcount() -> None:
    ins = _make_insight(raw_facts=["headcount ~8000"])
    issues = check_sanity(ins)
    assert not any(i.code == "headcount_out_of_bounds" for i in issues)


def test_sanity_parses_funding_with_units() -> None:
    ins = _make_insight(
        evidence="Stripe raised $14B in their Series H last year",
    )
    issues = check_sanity(ins)
    # $14B is fine (under $100B cap); should pass
    assert not any(i.code == "funding_round_usd_out_of_bounds" for i in issues)


def test_sanity_flags_impossibly_low_funding() -> None:
    ins = _make_insight(
        evidence="They raised $1 last quarter according to the press release",
    )
    issues = check_sanity(ins)
    assert any(i.code == "funding_round_usd_out_of_bounds" for i in issues)


def test_sanity_flags_old_founding_year() -> None:
    ins = _make_insight(
        raw_facts=["founded in 1234"],
    )
    issues = check_sanity(ins)
    assert any(i.code == "founding_year_out_of_bounds" for i in issues)


def test_sanity_handles_no_numbers_quietly() -> None:
    ins = _make_insight(
        evidence="Stripe is a payment processor based in California.",
    )
    assert check_sanity(ins) == []


# ---- Cross-domain contradictions ---------------------------------------------------------------


def test_contradictions_finds_headcount_disagreement() -> None:
    insights = [
        _make_insight(id="people-1", domain="people", raw_facts=["headcount ~120"]),
        _make_insight(id="money-1", domain="money", raw_facts=["headcount ~200"]),
    ]
    contradictions = find_cross_domain_contradictions(insights)
    assert len(contradictions) == 1
    c = contradictions[0]
    assert c.metric == "headcount"
    assert {c.fact_a_domain, c.fact_b_domain} == {"people", "money"}


def test_contradictions_ignores_same_domain_disagreement() -> None:
    """Two facts within the same domain isn't a cross-domain contradiction."""
    insights = [
        _make_insight(id="p1", domain="people", raw_facts=["headcount ~120"]),
        _make_insight(id="p2", domain="people", raw_facts=["headcount ~200"]),
    ]
    assert find_cross_domain_contradictions(insights) == []


def test_contradictions_within_threshold_does_not_flag() -> None:
    """Headcount 120 vs 130 is a 10% diff — under the 20% threshold."""
    insights = [
        _make_insight(id="people-1", domain="people", raw_facts=["headcount ~120"]),
        _make_insight(id="money-1", domain="money", raw_facts=["headcount ~130"]),
    ]
    assert find_cross_domain_contradictions(insights) == []


def test_contradictions_finds_funding_disagreement() -> None:
    """Funding amounts should be exact — even small diffs flag (1.1x threshold)."""
    insights = [
        _make_insight(id="m1", domain="money", evidence="raised $14M Series A"),
        _make_insight(id="news-1", domain="market", evidence="raised $20M Series A"),
    ]
    contradictions = find_cross_domain_contradictions(insights)
    assert any(c.metric == "funding_round_usd" for c in contradictions)


# ---- verify_insights end-to-end (no URL check) -------------------------------------------------


async def test_verify_insights_skip_url_check_returns_full_report() -> None:
    insights = [
        _make_insight(
            id="a",
            domain="people",
            confidence="high",
            sources=[Source(title="x", url="https://stripe.com")],  # only 1 domain → downgrades
            raw_facts=["headcount ~120"],
        ),
        _make_insight(
            id="b",
            domain="money",
            sources=[
                Source(title="y", url="https://news.com"),
                Source(title="z", url="https://other.com"),
            ],
            raw_facts=["headcount ~200"],
        ),
    ]
    report = await verify_insights(insights, skip_url_check=True)
    assert len(report.verifications) == 2
    by_id = {v.insight_id: v for v in report.verifications}
    assert by_id["a"].suggested_confidence == "medium"  # downgraded from high
    assert by_id["b"].suggested_confidence == "medium"  # already medium
    assert len(report.cross_domain_contradictions) == 1
    assert report.summary["insights_verified"] == 2
    assert report.summary["cross_domain_contradictions"] == 1


# ---- URL liveness check (mock httpx) ----------------------------------------------------------


async def test_verify_url_liveness_flags_dead_links() -> None:
    insights = [
        _make_insight(
            id="x",
            sources=[
                Source(title="alive", url="https://example.com/a"),
                Source(title="dead", url="https://example.com/b"),
            ],
        ),
    ]

    # Patch head_check_many to return controlled liveness
    async def fake_head(urls: list[str]) -> dict[str, bool]:
        return {u: ("/a" in u) for u in urls}

    with patch("src.verification._api.head_check_many", side_effect=fake_head):
        report = await verify_insights(insights, skip_url_check=False)

    issues = report.verifications[0].issues
    dead_codes = [i for i in issues if i.code == "url_dead"]
    assert len(dead_codes) == 1
    assert "example.com/b" in dead_codes[0].message


# ---- /verify route ----------------------------------------------------------------------------


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


async def test_verify_route_happy_path(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    body = {
        "insights": [
            {
                "id": "a",
                "domain": "people",
                "headline": "Stripe headcount around 8000",
                "evidence": "Per LinkedIn",
                "sources": [
                    {"title": "LinkedIn", "url": "https://linkedin.com/company/stripe"},
                    {"title": "Press", "url": "https://stripe.com/press"},
                ],
                "confidence": "medium",
                "raw_facts": ["headcount ~8000"],
            },
        ],
        "skip_url_check": True,
    }
    resp = await client.post(
        "/verify",
        json=body,
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["verifications"]) == 1
    assert payload["verifications"][0]["suggested_confidence"] in {"low", "medium", "high"}
    assert payload["summary"]["insights_verified"] == 1


async def test_verify_route_rejects_empty_insights(
    client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/verify",
        json={"insights": [], "skip_url_check": True},
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422


async def test_verify_route_rejects_extra_fields(client: Any, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.cost as cost_mod

    monkeypatch.setattr(cost_mod, "daily_total", _async_return(0.0))
    monkeypatch.setattr(cost_mod, "record", _async_noop())

    resp = await client.post(
        "/verify",
        json={
            "insights": [
                {
                    "id": "a",
                    "domain": "people",
                    "headline": "x",
                    "evidence": "y",
                    "sources": [{"title": "s", "url": "https://example.com"}],
                }
            ],
            "skip_url_check": True,
            "rogue_field": "should be rejected",
        },
        headers={"Authorization": f"Bearer {env_token}"},
    )
    assert resp.status_code == 422
