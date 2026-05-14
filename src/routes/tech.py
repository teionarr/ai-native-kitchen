"""POST /tech — wraps the active tech provider (today: Wappalyzer OSS)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src import cache
from src.auth import require_skill
from src.routes._unconfigured import raise_signal_unconfigured
from src.upstreams import get_active_provider
from src.upstreams.tech._base import TechProvider, TechResult

log = logging.getLogger("kitchen.routes.tech")

router = APIRouter(tags=["tech"])


class TechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_url: HttpUrl
    company: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/tech", response_model=TechResult)
async def tech(
    request: Request,
    body: TechRequest,
    skill_id: str = Depends(require_skill),
) -> TechResult:
    try:
        provider = get_active_provider("tech")
    except (ValueError, LookupError, FileNotFoundError):
        raise_signal_unconfigured("tech")

    if not isinstance(provider, TechProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="tech provider misconfigured (wrong type)",
        )

    cache_payload = {"primary_url": str(body.primary_url)}
    cached = await cache.get("tech", provider.name, cache_payload)
    if cached is not None:
        log.info("tech cache hit", extra={"skill_id": skill_id, "url": str(body.primary_url)})
        try:
            return TechResult.model_validate(cached)
        except Exception:
            log.exception("cached tech payload failed validation; refetching")

    log.info("tech lookup", extra={"skill_id": skill_id, "url": str(body.primary_url)})
    try:
        result = await provider.lookup(str(body.primary_url))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except RuntimeError as e:
        log.warning("tech provider error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream tech provider failed: {e}",
        ) from e
    except Exception:
        log.exception("tech lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream tech provider failed",
        ) from None

    # Tech stack changes rarely — static TTL (7d)
    await cache.set("tech", provider.name, cache_payload, result.model_dump(mode="json"), ttl_kind="static")
    return result
