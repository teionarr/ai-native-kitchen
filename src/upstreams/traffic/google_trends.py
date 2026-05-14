"""Google Trends traffic provider — free, no API key.

Wraps `pytrends` (community Python lib that scrapes Google Trends). Returns:
- top_keywords: queries Google Trends shows as "Related queries" for the company
- growth_indicator: "growing" / "flat" / "declining" / "unknown" derived from
  the 12-month interest-over-time slope
- monthly_visits_estimate: NOT populated (Trends gives relative interest, not
  absolute visit counts; SimilarWeb / Semrush would, but those are paid)

Two real caveats worth knowing:
  1. **pytrends is fragile**: Google rotates anti-scrape measures every few months.
     The library can break for a release cycle until upstream catches up. Wrap all
     calls in try/except and surface failures as RuntimeError → 502 cleanly.
  2. **Rate limits**: Google throttles unauthenticated traffic. Two requests/sec
     is the practical ceiling. The cache helps a lot here — same company within 24h
     never re-hits Google.

When pytrends gets too unreliable, the swap path is:
- Paid: DataForSEO or SerpAPI (real APIs, ~$20-50/mo)
- DIY: Bing Web Search API for query-volume estimates (different signal)

Either way, swap by writing a sibling provider + flipping providers.yaml.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from src.upstreams import register
from src.upstreams.traffic._base import TrafficProvider, TrafficResult

log = logging.getLogger("kitchen.upstreams.google_trends")

# Window for the interest-over-time query — 12 months gives enough signal for
# a growing/flat/declining call without being so long that Google complains.
TIMEFRAME = "today 12-m"
TIMEOUT_S = 30.0


@register("traffic", "google_trends")
class GoogleTrendsProvider(TrafficProvider):
    name = "google_trends"

    def __init__(self) -> None:
        try:
            from pytrends.request import TrendReq  # noqa: F401  availability check
        except ImportError as e:
            raise ValueError("pytrends is not installed. Add 'pytrends' to dependencies and reinstall.") from e

    async def lookup(self, domain: str) -> TrafficResult:
        if not domain:
            raise ValueError("domain must be non-empty")

        # pytrends searches by keyword (not by domain). Use the bare hostname-without-TLD
        # as the search term — for "stripe.com" that's "stripe", which Google maps to the
        # company in most cases.
        keyword = _domain_to_keyword(domain)

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_pytrends_lookup_sync, keyword), timeout=TIMEOUT_S)
        except TimeoutError as e:
            raise RuntimeError(f"google_trends timed out after {TIMEOUT_S}s") from e
        except _PytrendsError as e:
            raise RuntimeError(str(e)) from e

        return TrafficResult(
            domain=domain,
            monthly_visits_estimate=None,  # Trends gives relative interest, not absolute visits
            top_keywords=raw["top_keywords"],
            growth_indicator=raw["growth_indicator"],
            notes=raw["notes"],
            provider=self.name,
        )


# ---- Sync helpers (run in a thread) -------------------------------------------------------------


class _PytrendsError(Exception):
    """Internal: any failure inside the sync pytrends call."""


def _domain_to_keyword(domain: str) -> str:
    """Strip scheme + www + TLD: 'https://www.stripe.com' → 'stripe'."""
    s = domain.lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    if s.startswith("www."):
        s = s[4:]
    # Take the host (drop path) and the leftmost label
    host = s.split("/", 1)[0]
    return host.split(".", 1)[0] or host


def _pytrends_lookup_sync(keyword: str) -> dict[str, Any]:
    """Synchronous Google Trends lookup. MUST be run in a thread."""
    try:
        from pytrends.request import TrendReq  # type: ignore[import-not-found]
    except ImportError as e:
        raise _PytrendsError(f"pytrends import failed at runtime: {e}") from e

    notes: list[str] = []

    try:
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    except Exception as e:
        raise _PytrendsError(f"could not initialize pytrends: {e}") from e

    try:
        pytrends.build_payload([keyword], cat=0, timeframe=TIMEFRAME, geo="", gprop="")
    except Exception as e:
        raise _PytrendsError(f"google_trends payload build failed (likely Google anti-scrape): {e}") from e

    growth: Literal["growing", "flat", "declining", "unknown"] = "unknown"
    try:
        interest = pytrends.interest_over_time()
        if interest is not None and not interest.empty and keyword in interest.columns:
            growth = _classify_trend(list(interest[keyword]))
        else:
            notes.append("no interest-over-time data returned (low search volume?)")
    except Exception as e:
        notes.append(f"interest-over-time failed: {e}")

    top_keywords: list[str] = []
    try:
        related = pytrends.related_queries()
        # related is {keyword: {'top': df, 'rising': df}}
        if isinstance(related, dict) and keyword in related:
            inner = related[keyword] or {}
            top_df = inner.get("top")
            if top_df is not None and hasattr(top_df, "head"):
                top_keywords = [str(q) for q in top_df.head(10)["query"].tolist()]
    except Exception as e:
        notes.append(f"related queries failed: {e}")

    return {
        "top_keywords": top_keywords,
        "growth_indicator": growth,
        "notes": notes,
    }


def _classify_trend(values: list[float | int]) -> Literal["growing", "flat", "declining", "unknown"]:
    """Compare the trailing 25% of values against the leading 25% — simple, robust enough."""
    if not values or len(values) < 8:
        return "unknown"
    n = len(values)
    head = values[: n // 4]
    tail = values[-(n // 4) :]
    head_avg = sum(head) / len(head)
    tail_avg = sum(tail) / len(tail)
    if head_avg == 0:
        return "growing" if tail_avg > 0 else "unknown"
    delta_pct = (tail_avg - head_avg) / head_avg * 100
    if delta_pct > 20:
        return "growing"
    if delta_pct < -20:
        return "declining"
    return "flat"
