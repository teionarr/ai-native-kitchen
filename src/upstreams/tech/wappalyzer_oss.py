"""Wappalyzer OSS tech provider — free, no API key, pure-Python.

Uses the python-Wappalyzer library (PyPI: python-Wappalyzer). Sync under the hood,
wrapped in asyncio.to_thread so we don't block the event loop while it fetches the
URL + applies fingerprints.

Trade-off worth knowing: python-Wappalyzer's fingerprint database isn't actively
maintained anymore (last release 2023). It still recognizes the common stack
(React/Vue/Svelte, AWS/GCP/Cloudflare, Postgres/Mongo, Auth0/Clerk, Stripe, etc.)
but may miss the very latest frameworks. When the staleness becomes a problem,
swap to wappalyzer-next (Playwright-based, fresh fingerprints, much heavier
container) by writing a sibling module + flipping providers.yaml — no route
changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import HttpUrl

from src.upstreams import register
from src.upstreams.tech._base import Technology, TechProvider, TechResult

log = logging.getLogger("kitchen.upstreams.wappalyzer_oss")

# Run the sync analyze inside this many seconds — past it we kill the future.
# The library's own internal timeout is shorter, but defense-in-depth.
ANALYZE_TIMEOUT_S = 30.0


@register("tech", "wappalyzer_oss")
class WappalyzerOSSProvider(TechProvider):
    name = "wappalyzer_oss"

    def __init__(self) -> None:
        # Defer import so a missing optional dep doesn't crash the registry import-time.
        # If python-Wappalyzer isn't installed, raise here → route returns 503.
        try:
            from Wappalyzer import Wappalyzer  # noqa: F401  imported for availability check
        except ImportError as e:
            raise ValueError(
                "python-Wappalyzer is not installed. Add 'python-Wappalyzer' to dependencies and reinstall."
            ) from e

    async def lookup(self, url: str) -> TechResult:
        if not url:
            raise ValueError("url must be non-empty")

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_analyze_sync, url), timeout=ANALYZE_TIMEOUT_S)
        except TimeoutError as e:
            raise RuntimeError(f"wappalyzer_oss timed out after {ANALYZE_TIMEOUT_S}s") from e
        except _AnalyzeError as e:
            # Re-raise as RuntimeError so the route's catch-block maps to 502 with the
            # user-facing message, not the internal exception type.
            raise RuntimeError(str(e)) from e

        techs = [_to_technology(name, info) for name, info in raw.items()]
        return TechResult(url=HttpUrl(url), technologies=techs, provider=self.name)


# ---- Sync helpers (run in a thread) -------------------------------------------------------------


class _AnalyzeError(Exception):
    """Internal: any failure inside the sync analyze. Wrapped + re-raised as RuntimeError."""


def _analyze_sync(url: str) -> dict[str, Any]:
    """Synchronous Wappalyzer call. MUST be run in a thread (uses requests under the hood).

    Returns: {tech_name: {"versions": [...], "categories": [...], "confidence": int}}
    """
    # Local imports keep cold-import cost off the hot path of routes that don't use this.
    try:
        from Wappalyzer import Wappalyzer, WebPage  # type: ignore[import-not-found]
    except ImportError as e:
        raise _AnalyzeError(f"python-Wappalyzer import failed at runtime: {e}") from e

    try:
        wapp = Wappalyzer.latest()
    except Exception as e:  # library uses generic Exception in places
        raise _AnalyzeError(f"could not load wappalyzer fingerprints: {e}") from e

    try:
        page = WebPage.new_from_url(url, verify=True, timeout=15)
    except Exception as e:
        raise _AnalyzeError(f"could not fetch {url!r}: {e}") from e

    try:
        # analyze_with_versions_and_categories returns the richest output
        return wapp.analyze_with_versions_and_categories(page)
    except Exception as e:
        raise _AnalyzeError(f"wappalyzer analyze failed: {e}") from e


def _to_technology(name: str, info: Any) -> Technology:
    """Map a single library entry to our Technology pydantic model."""
    versions: list[str] = []
    categories: list[str] = []
    if isinstance(info, dict):
        v = info.get("versions") or []
        if isinstance(v, list):
            versions = [str(x) for x in v if isinstance(x, str | int | float)]
        c = info.get("categories") or []
        if isinstance(c, list):
            categories = [str(x) for x in c if isinstance(x, str)]

    return Technology(
        name=name,
        # Take the FIRST category if multiple — keeps the response shape predictable.
        category=categories[0] if categories else None,
        # Take the FIRST version if multiple. The library lists multiple when fingerprint
        # patterns match different historical versions; the freshest is usually first.
        version=versions[0] if versions else None,
        # python-Wappalyzer doesn't expose per-tech confidence reliably; leave None.
        confidence=None,
    )
