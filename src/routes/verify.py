"""POST /verify — run deterministic checks over a list of insights."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.auth import require_skill
from src.verification import Insight, VerificationReport, verify_insights

log = logging.getLogger("kitchen.routes.verify")

router = APIRouter(tags=["verify"])


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: list[Insight] = Field(min_length=1, max_length=100)
    target_domain: str | None = Field(
        default=None,
        max_length=253,
        description="Target company's primary domain — triggers the self-citation rule",
    )
    skip_url_check: bool = Field(
        default=False,
        description="Skip the URL-liveness HEAD check (use for offline / dev runs)",
    )


@router.post("/verify", response_model=VerificationReport)
async def verify(
    request: Request,
    body: VerifyRequest,
    skill_id: str = Depends(require_skill),
) -> VerificationReport:
    log.info(
        "verify request",
        extra={
            "skill_id": skill_id,
            "insight_count": len(body.insights),
            "target_domain": body.target_domain,
            "skip_url_check": body.skip_url_check,
        },
    )
    try:
        return await verify_insights(
            body.insights,
            target_domain=body.target_domain,
            skip_url_check=body.skip_url_check,
        )
    except Exception:
        log.exception("verify failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="verification failed; check logs",
        ) from None
