"""Firecrawl scraping provider — free tier 1000 credits/month.

Wraps Firecrawl's /v1/scrape endpoint. Returns cleaned markdown + metadata.
A single scrape costs 1 credit (5 credits for premium features we don't use).

Why Firecrawl over a roll-our-own scraper:
- Handles JS-rendered pages out of the box (Playwright under the hood)
- Returns clean markdown, not raw HTML — saves us the scrubbing layer
- Built-in retry / proxy / rate-limit handling
- Free tier covers low-volume use; the paid tiers don't change the API

Auth: bearer header. API key comes from settings.firecrawl_api_key
(KITCHEN_FIRECRAWL_API_KEY env var).

If the key isn't configured at instantiation time, the provider raises ValueError —
which surfaces as a 503 "signal_unconfigured" via the route handler. This is the
same path as if no provider were registered for the signal at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from pydantic import HttpUrl

from src.config import settings
from src.upstreams import register
from src.upstreams.scraping._base import ScrapeResult, ScrapingProvider

log = logging.getLogger("kitchen.upstreams.firecrawl")

API_BASE = "https://api.firecrawl.dev/v1"
REQUEST_TIMEOUT_S = 60.0  # firecrawl can take a while on heavy pages

USER_AGENT = "ai-native-kitchen/0.1 (+https://github.com/teionarr/ai-native-kitchen)"


@register("scraping", "firecrawl")
class FirecrawlProvider(ScrapingProvider):
    name = "firecrawl"

    def __init__(self) -> None:
        self.api_key = settings.firecrawl_api_key
        if not self.api_key:
            # Caller (route handler) catches this and returns 503 with a clear message.
            raise ValueError("FirecrawlProvider requires KITCHEN_FIRECRAWL_API_KEY (Doppler-injected env var)")

    async def scrape(self, url: str) -> ScrapeResult:
        if not url:
            raise ValueError("url must be non-empty")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        # Request only markdown output — saves us a parse step + reduces response size.
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,  # strips nav / footer / sidebars
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            try:
                resp = await client.post(f"{API_BASE}/scrape", json=body, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                raise RuntimeError(f"firecrawl unreachable: {e}") from e

            if resp.status_code == 401:
                raise RuntimeError("firecrawl rejected auth — check KITCHEN_FIRECRAWL_API_KEY")
            if resp.status_code == 402:
                raise RuntimeError("firecrawl quota exceeded for this billing period")
            if resp.status_code == 429:
                raise RuntimeError("firecrawl rate-limited; retry after a few seconds")
            if resp.status_code >= 400:
                raise RuntimeError(f"firecrawl error {resp.status_code}: {resp.text[:200]}")

            payload = resp.json()
            if not isinstance(payload, dict) or not payload.get("success"):
                raise RuntimeError(f"firecrawl returned unexpected payload: {str(payload)[:200]}")

        data = payload.get("data") or {}
        markdown = data.get("markdown") or ""
        metadata = data.get("metadata") or {}
        title = metadata.get("title") if isinstance(metadata, dict) else None

        return ScrapeResult(
            url=HttpUrl(url),
            text=markdown,
            title=title if isinstance(title, str) else None,
            fetched_at=datetime.now(UTC),
            provider=self.name,
        )
