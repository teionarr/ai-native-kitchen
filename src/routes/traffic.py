"""POST /traffic — wraps the active traffic provider (today: Google Trends)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src import cache
from src.auth import require_skill
from src.routes._unconfigured import raise_signal_unconfigured
from src.upstreams import get_active_provider
from src.upstreams.traffic._base import TrafficProvider, TrafficResult

log = logging.getLogger("kitchen.routes.traffic")

router = APIRouter(tags=["traffic"])


class TrafficRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3, max_length=253, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")


@router.post("/traffic", response_model=TrafficResult)
async def traffic(
    request: Request,
    body: TrafficRequest,
    skill_id: str = Depends(require_skill),
) -> TrafficResult:
    try:
        provider = get_active_provider("traffic")
    except (ValueError, LookupError, FileNotFoundError):
        raise_signal_unconfigured("traffic")

    if not isinstance(provider, TrafficProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="traffic provider misconfigured (wrong type)",
        )

    cache_payload = {"domain": body.domain}
    cached = await cache.get("traffic", provider.name, cache_payload)
    if cached is not None:
        log.info("traffic cache hit", extra={"skill_id": skill_id, "domain": body.domain})
        try:
            return TrafficResult.model_validate(cached)
        except Exception:
            log.exception("cached traffic payload failed validation; refetching")

    log.info("traffic lookup", extra={"skill_id": skill_id, "domain": body.domain})
    try:
        result = await provider.lookup(body.domain)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except RuntimeError as e:
        log.warning("traffic provider error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream traffic provider failed: {e}",
        ) from e
    except Exception:
        log.exception("traffic lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream traffic provider failed",
        ) from None

    # Trends data updates daily but slow to swing — fact TTL (24h)
    await cache.set("traffic", provider.name, cache_payload, result.model_dump(mode="json"), ttl_kind="fact")
    return result
