"""ai-native-kitchen FastAPI app — entry point.

Today this only exposes /health. Routes for /discover, /domain/{slug}, /traffic,
/funding, /people, /tech, /briefs land in PRs 2.3 / 2.4.

Design notes:
- Async from the start. Future routes fan out to upstream HTTP APIs (Perplexity,
  Firecrawl, etc.) via httpx.AsyncClient and asyncio.gather. Sync would block the
  event loop on every upstream call.
- Single FastAPI app instance. Routers from src/routes/ get mounted as they land.
- /docs is OFF by default — set KITCHEN_ENABLE_DOCS=true in dev only.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response

from src.config import settings
from src.version import VERSION

log = logging.getLogger("kitchen")

# Module-level start timestamp. Set on first import — that's effectively
# process-start since the module is imported once. Avoids the FastAPI lifespan
# hook, which doesn't fire under httpx ASGITransport without extra plumbing.
_STARTED_AT = time.monotonic()


app = FastAPI(
    title="ai-native-kitchen",
    version=VERSION,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)


log.info(
    "kitchen module loaded",
    extra={"version": VERSION, "port": settings.port, "enable_docs": settings.enable_docs},
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Tag every request with a UUIDv4 (or echo client-provided X-Request-Id).

    The id ends up in response headers and (once structured logging lands) every
    log line for the request. Useful when debugging which call hit which upstream.
    """
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/health", tags=["meta"])
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe.

    Reports the running version + uptime so an operator can confirm the container
    actually got the new image after a restart. Public — bearer auth NOT required
    on this endpoint (Caddy / docker healthcheck need to hit it).
    """
    del request  # unused; kept in signature for future per-request logging
    return {
        "status": "ok",
        "version": VERSION,
        "uptime_s": int(time.monotonic() - _STARTED_AT),
    }
