"""Exa search provider — free tier 1000 requests/month.

Wraps Exa's POST /search endpoint. Returns ranked results with title, URL,
published date, and (optional) snippet.

Why Exa over a generic Google-search API:
- Built for AI / LLM consumption — designed to return readable, dedupable URLs
- Neural-search type returns semantically-relevant pages (good for "what does
  company X actually do" type queries)
- Free tier covers our low-volume use; paid tier API is identical

Auth: x-api-key header. Key from settings.exa_api_key (KITCHEN_EXA_API_KEY).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import HttpUrl

from src.config import settings
from src.upstreams import register
from src.upstreams.search._base import SearchHit, SearchProvider, SearchResult

log = logging.getLogger("kitchen.upstreams.exa")

API_URL = "https://api.exa.ai/search"
REQUEST_TIMEOUT_S = 30.0
USER_AGENT = "ai-native-kitchen/0.1 (+https://github.com/teionarr/ai-native-kitchen)"

# Cap results — Exa accepts up to 100 but anything past 10-20 is rarely useful for
# the kind of "company brief" lookups the kitchen serves.
DEFAULT_LIMIT = 5
MAX_LIMIT = 25


@register("search", "exa")
class ExaProvider(SearchProvider):
    name = "exa"

    def __init__(self) -> None:
        self.api_key = settings.exa_api_key
        if not self.api_key:
            raise ValueError("ExaProvider requires KITCHEN_EXA_API_KEY (Doppler-injected env var)")

    async def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> SearchResult:
        if not query.strip():
            raise ValueError("query must be non-empty")
        # Clamp into [1, MAX_LIMIT]; the route already validates but be defensive.
        num = max(1, min(limit, MAX_LIMIT))

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        body = {
            "query": query,
            "numResults": num,
            "type": "neural",
            "useAutoprompt": True,
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            try:
                resp = await client.post(API_URL, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                raise RuntimeError(f"exa unreachable: {e}") from e

            if resp.status_code == 401:
                raise RuntimeError("exa rejected auth — check KITCHEN_EXA_API_KEY")
            if resp.status_code == 402:
                raise RuntimeError("exa quota exceeded for this billing period")
            if resp.status_code == 429:
                raise RuntimeError("exa rate-limited; retry after a few seconds")
            if resp.status_code >= 400:
                raise RuntimeError(f"exa error {resp.status_code}: {resp.text[:200]}")

            payload = resp.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"exa returned non-dict payload: {str(payload)[:200]}")

        raw_results = payload.get("results") or []
        if not isinstance(raw_results, list):
            raise RuntimeError(f"exa results not a list: {type(raw_results).__name__}")

        hits: list[SearchHit] = []
        for raw in raw_results:
            hit = _parse_hit(raw)
            if hit is not None:
                hits.append(hit)

        return SearchResult(query=query, results=hits, provider=self.name)


def _parse_hit(raw: Any) -> SearchHit | None:
    """Parse one Exa result into a SearchHit. Skips malformed entries silently."""
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    title = raw.get("title")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    if not isinstance(title, str) or not title.strip():
        # Exa occasionally returns results without a title — synthesize from URL
        title = url

    snippet = raw.get("text") or raw.get("snippet") or ""
    if not isinstance(snippet, str):
        snippet = ""

    published_at: datetime | None = None
    pub = raw.get("publishedDate")
    if isinstance(pub, str) and pub:
        try:
            # Exa returns ISO 8601 strings (e.g. "2024-11-01T00:00:00.000Z")
            published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            published_at = None

    try:
        return SearchHit(
            title=title.strip(),
            url=HttpUrl(url),
            snippet=snippet.strip()[:500],  # cap snippet length so cached entries stay reasonable
            published_at=published_at,
        )
    except Exception:
        # Pydantic validation failed (e.g. bad URL) — skip
        return None
