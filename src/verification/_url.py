"""URL liveness check — HEAD-request every distinct URL in parallel, return {url: alive}."""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("kitchen.verification.url")

HEAD_TIMEOUT_S = 5.0
HEAD_CONCURRENCY = 16


async def head_check_many(urls: list[str]) -> dict[str, bool]:
    """Return {url: alive} for every URL. Network failures = not alive (False)."""
    if not urls:
        return {}
    distinct = list(set(urls))
    sem = asyncio.Semaphore(HEAD_CONCURRENCY)

    async def _check_one(client: httpx.AsyncClient, url: str) -> tuple[str, bool]:
        async with sem:
            try:
                resp = await client.head(url, follow_redirects=True)
                return (url, 200 <= resp.status_code < 400)
            except (httpx.HTTPError, OSError):
                # Some servers reject HEAD outright; try a tiny GET as a fallback.
                try:
                    resp = await client.get(url, follow_redirects=True)
                    return (url, 200 <= resp.status_code < 400)
                except (httpx.HTTPError, OSError) as e:
                    log.debug("url dead: %s (%s)", url, e)
                    return (url, False)

    async with httpx.AsyncClient(timeout=HEAD_TIMEOUT_S) as client:
        results = await asyncio.gather(*[_check_one(client, u) for u in distinct])
    return dict(results)
