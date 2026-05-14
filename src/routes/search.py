"""POST /search — wraps the active search provider (today: Exa)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src import cache
from src.auth import require_skill
from src.routes._unconfigured import raise_signal_unconfigured
from src.upstreams import get_active_provider
from src.upstreams.search._base import SearchProvider, SearchResult

log = logging.getLogger("kitchen.routes.search")

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=25)


@router.post("/search", response_model=SearchResult)
async def search(
    request: Request,
    body: SearchRequest,
    skill_id: str = Depends(require_skill),
) -> SearchResult:
    try:
        provider = get_active_provider("search")
    except (ValueError, LookupError, FileNotFoundError):
        raise_signal_unconfigured("search")

    if not isinstance(provider, SearchProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="search provider misconfigured (wrong type)",
        )

    cache_payload = {"query": body.query, "limit": body.limit}
    cached = await cache.get("search", provider.name, cache_payload)
    if cached is not None:
        log.info("search cache hit", extra={"skill_id": skill_id, "query": body.query[:80]})
        try:
            return SearchResult.model_validate(cached)
        except Exception:
            log.exception("cached search payload failed validation; refetching")

    log.info("search lookup", extra={"skill_id": skill_id, "query": body.query[:80], "limit": body.limit})
    try:
        result = await provider.search(body.query, limit=body.limit)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except RuntimeError as e:
        log.warning("search provider error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream search provider failed: {e}",
        ) from e
    except Exception:
        log.exception("search lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream search provider failed",
        ) from None

    # Search results change daily-ish (news + recency) — fact TTL (24h)
    await cache.set("search", provider.name, cache_payload, result.model_dump(mode="json"), ttl_kind="fact")
    return result
