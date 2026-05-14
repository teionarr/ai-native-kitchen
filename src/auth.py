"""Bearer-token auth for the kitchen.

Each consuming skill gets its own token. Tokens are configured as env vars (Doppler-
injected) named `SERVICE_BEARER_TOKEN_<SKILL_ID>` — for example,
`SERVICE_BEARER_TOKEN_RESEARCH_COMPANY=abc123...`.

The auth dependency:
1. Extracts the Bearer token from the Authorization header
2. Looks it up in the env-var-derived token map
3. Sets `request.state.skill_id` (used by cost telemetry + structured logs)
4. Returns the skill_id for direct use in routes

Adding a new consuming skill = adding one env var in Doppler. No code changes.

Token rotation: rotate in Doppler, restart container. Old tokens invalidate immediately
on container restart since the map is loaded fresh.

`/health` and `/providers` skip auth (operator-facing diagnostics).
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_BEARER_TOKEN_ENV_PREFIX = "SERVICE_BEARER_TOKEN_"  # noqa: S105 — env var name prefix, not a password

# `auto_error=False` so we control the 401 message and shape ourselves.
_bearer_scheme = HTTPBearer(auto_error=False)


def load_skill_tokens() -> dict[str, str]:
    """Return {token_value: skill_id} for every configured SERVICE_BEARER_TOKEN_<SKILL>.

    Read fresh from os.environ on every call. The cost is a linear scan of the env
    (small in containers); the benefit is that test fixtures monkeypatching env
    vars work without any cache-invalidation dance.
    """
    tokens: dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.startswith(_BEARER_TOKEN_ENV_PREFIX):
            continue
        if not value:
            continue
        skill_id = key[len(_BEARER_TOKEN_ENV_PREFIX) :].lower().replace("_", "-")
        if not skill_id:
            continue
        tokens[value] = skill_id
    return tokens


async def require_skill(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),  # noqa: B008 — FastAPI dependency pattern
) -> str:
    """FastAPI dependency: validate bearer token, set request.state.skill_id, return skill_id.

    Raises 401 if missing or invalid. Generic error message — don't leak whether a
    given token is "almost right" or whether the header was missing entirely.
    """
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tokens = load_skill_tokens()
    skill_id = tokens.get(credentials.credentials)
    if skill_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.skill_id = skill_id
    return skill_id
