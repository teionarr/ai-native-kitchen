"""Cost-telemetry middleware.

Wraps every request, times it, and after the response is produced records one row
in cost_log keyed on `request.state.skill_id` (which the auth dependency sets).

Why a middleware (vs an inline call in each route):
  - Catches every route automatically — no risk of forgetting to record one
  - Captures status_code AFTER the route runs (needed for partial-failure analytics)
  - Times the full request including FastAPI's overhead, not just the handler

Skips telemetry when:
  - request.state has no skill_id (request didn't pass auth — /health, /providers, 401s)
  - The request returned 401 itself (auth failure — already logged separately)

Cache-hit signal: routes set response.headers["X-Cache-Hit"] = "true" when they
served from cache. Surfaced into the cost_log row for hit-rate analytics.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from src import cost

log = logging.getLogger("kitchen.middleware.cost")


async def cost_telemetry_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)

    skill_id = getattr(request.state, "skill_id", None)
    if skill_id is None:
        # Unauthenticated route (or auth rejected before setting state) — nothing to record.
        return response

    # Routes can opt into reporting cache hits via this header.
    cache_hit = response.headers.get("x-cache-hit", "").lower() == "true"
    request_id = getattr(request.state, "request_id", None)

    try:
        await cost.record(
            skill_id=skill_id,
            endpoint=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            cache_hit=cache_hit,
            upstream_cost_usd=0.0,  # populated when paid providers wire in their per-call cost
            request_id=request_id,
        )
    except Exception:
        # cost.record is supposed to be silent — if it raises, log loudly but don't
        # affect the response (we already sent it).
        log.exception("cost telemetry middleware failed unexpectedly")

    return response
