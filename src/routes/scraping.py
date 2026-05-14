"""POST /scrape — wraps the active scraping provider (today: Firecrawl)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, HttpUrl

from src import cache
from src.auth import require_skill
from src.routes._unconfigured import raise_signal_unconfigured
from src.upstreams import get_active_provider
from src.upstreams.scraping._base import ScrapeResult, ScrapingProvider

log = logging.getLogger("kitchen.routes.scraping")

router = APIRouter(tags=["scraping"])


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


@router.post("/scrape", response_model=ScrapeResult)
async def scrape(
    request: Request,
    body: ScrapeRequest,
    skill_id: str = Depends(require_skill),
) -> ScrapeResult:
    try:
        provider = get_active_provider("scraping")
    except (ValueError, LookupError, FileNotFoundError):
        # Provider missing entirely OR provider __init__ raised (e.g. no API key)
        raise_signal_unconfigured("scraping")

    if not isinstance(provider, ScrapingProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="scraping provider misconfigured (wrong type)",
        )

    cache_payload = {"url": str(body.url)}
    cached = await cache.get("scraping", provider.name, cache_payload)
    if cached is not None:
        log.info("scraping cache hit", extra={"skill_id": skill_id, "url": str(body.url)})
        try:
            return ScrapeResult.model_validate(cached)
        except Exception:
            log.exception("cached scraping payload failed validation; refetching")

    log.info("scraping lookup", extra={"skill_id": skill_id, "url": str(body.url)})
    try:
        result = await provider.scrape(str(body.url))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except RuntimeError as e:
        # Provider's documented failure mode — auth/quota/rate-limit/network
        log.warning("scraping provider error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream scraping provider failed: {e}",
        ) from e
    except Exception:
        log.exception("scraping lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream scraping provider failed",
        ) from None

    # Pages change daily-ish — fact TTL (24h)
    await cache.set("scraping", provider.name, cache_payload, result.model_dump(mode="json"), ttl_kind="fact")
    return result
