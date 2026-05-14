"""POST /funding — wraps the active funding provider (today: SEC EDGAR)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src import cache
from src.auth import require_skill
from src.upstreams import get_active_provider
from src.upstreams.funding._base import FundingProvider, FundingResult

log = logging.getLogger("kitchen.routes.funding")

router = APIRouter(tags=["funding"])


class FundingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=200)


@router.post("/funding", response_model=FundingResult)
async def funding(
    request: Request,
    body: FundingRequest,
    skill_id: str = Depends(require_skill),
) -> FundingResult:
    try:
        provider = get_active_provider("funding")
    except (ValueError, LookupError, FileNotFoundError) as e:
        log.warning("funding provider unconfigured: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="funding provider not configured (see config/providers.yaml)",
        ) from e

    if not isinstance(provider, FundingProvider):
        log.error("funding provider %r is not a FundingProvider subclass", type(provider).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="funding provider misconfigured (wrong type)",
        )

    cache_payload = {"company": body.company}
    cached = await cache.get("funding", provider.name, cache_payload)
    if cached is not None:
        log.info("funding cache hit", extra={"skill_id": skill_id, "company": body.company})
        try:
            return FundingResult.model_validate(cached)
        except Exception:
            log.exception("cached funding payload failed validation; refetching")

    log.info("funding lookup", extra={"skill_id": skill_id, "company": body.company})
    try:
        result = await provider.lookup(body.company)
    except ValueError as e:
        # Provider rejected the input — treat as 422.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except Exception:
        log.exception("funding lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream funding provider failed; try again or check logs",
        ) from None

    # Funding data is static-ish — filings come quarterly, founders rarely change.
    await cache.set("funding", provider.name, cache_payload, result.model_dump(mode="json"), ttl_kind="static")
    return result
