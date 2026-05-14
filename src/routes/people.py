"""POST /people — wraps the active people provider (none configured yet → 503)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.auth import require_skill
from src.routes._unconfigured import raise_signal_unconfigured
from src.upstreams import get_active_provider
from src.upstreams.people._base import PeopleProvider, PeopleResult

log = logging.getLogger("kitchen.routes.people")

router = APIRouter(tags=["people"])


class PeopleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=200)


@router.post("/people", response_model=PeopleResult)
async def people(
    request: Request,
    body: PeopleRequest,
    skill_id: str = Depends(require_skill),
) -> PeopleResult:
    try:
        provider = get_active_provider("people")
    except (ValueError, LookupError, FileNotFoundError):
        raise_signal_unconfigured("people")

    if not isinstance(provider, PeopleProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="people provider misconfigured (wrong type)",
        )

    log.info("people lookup", extra={"skill_id": skill_id, "company": body.company})
    try:
        return await provider.lookup(body.company)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except Exception:
        log.exception("people lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream people provider failed",
        ) from None
