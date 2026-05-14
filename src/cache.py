"""Async Redis cache wrapper with TTL and SHA-256-hashed keys.

Cache key shape: `<signal>:<provider>:<sha256(payload)[:32]>`. The hashing matters
for two reasons:
  1. A `redis-cli KEYS *` dump never reveals which target companies a user looked up.
  2. JSON-encoded payloads can be large; the digest is constant-size.

Two TTL kinds:
  - "fact" (24h): for things that change daily — recent news, hiring, headcount
  - "static" (7d): for things that change rarely — funding history, founders, tech stack

Graceful degradation: if Redis is unset OR unreachable OR temporarily failing, every
operation becomes a no-op (cache.get returns None; cache.set is silent). The route
still calls the provider and serves the request. Cache is an optimization, never a
correctness boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from src.config import settings

log = logging.getLogger("kitchen.cache")

TtlKind = Literal["fact", "static"]
FACT_TTL_S: int = 24 * 60 * 60
STATIC_TTL_S: int = 7 * 24 * 60 * 60

_CONNECT_TIMEOUT_S = 2.0
_OP_TIMEOUT_S = 1.0

# Connection-multiplexing client. None when Redis isn't configured / reachable.
_client: redis_asyncio.Redis | None = None
_client_attempted = False


def _hash_key(signal: str, provider: str, payload: dict[str, Any] | str) -> str:
    if isinstance(payload, dict):
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    else:
        encoded = str(payload).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:32]
    return f"{signal}:{provider}:{digest}"


async def _get_client() -> redis_asyncio.Redis | None:
    """Lazy-initialize the Redis connection. None means cache is disabled for this process."""
    global _client, _client_attempted
    if _client is not None:
        return _client
    if _client_attempted:
        return None  # we already tried once and failed; don't retry every request
    _client_attempted = True
    if not settings.redis_url:
        log.info("KITCHEN_REDIS_URL not set — cache disabled")
        return None
    try:
        client = redis_asyncio.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT_S,
            socket_timeout=_OP_TIMEOUT_S,
        )
        await client.ping()
    except (RedisError, OSError) as e:
        log.warning("Redis unreachable at startup; cache disabled for this process: %s", e)
        return None
    _client = client
    log.info("Redis connected; cache enabled")
    return _client


async def reset_for_tests() -> None:
    """Drop the cached client + reset the attempted flag. Tests use this to swap
    between fakeredis instances cleanly."""
    global _client, _client_attempted
    if _client is not None:
        try:
            await _client.aclose()
        except RedisError:
            pass
    _client = None
    _client_attempted = False


async def get(signal: str, provider: str, payload: dict[str, Any] | str) -> Any | None:
    """Return the cached JSON-decoded value, or None on miss / Redis failure."""
    client = await _get_client()
    if client is None:
        return None
    key = _hash_key(signal, provider, payload)
    try:
        raw = await client.get(key)
    except (RedisError, OSError) as e:
        log.warning("Redis GET failed: %s", e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("corrupt cache entry at key=%s; deleting", key)
        try:
            await client.delete(key)
        except (RedisError, OSError):
            pass
        return None


async def set(
    signal: str,
    provider: str,
    payload: dict[str, Any] | str,
    value: Any,
    *,
    ttl_kind: TtlKind = "fact",
) -> None:
    """Cache the value with the appropriate TTL. Silent on Redis failure."""
    client = await _get_client()
    if client is None:
        return
    ttl = STATIC_TTL_S if ttl_kind == "static" else FACT_TTL_S
    key = _hash_key(signal, provider, payload)
    try:
        encoded = json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as e:
        log.warning("cache value not JSON-serializable for key=%s: %s", key, e)
        return
    try:
        await client.setex(key, ttl, encoded)
    except (RedisError, OSError) as e:
        log.warning("Redis SETEX failed: %s", e)


async def delete(signal: str, provider: str, payload: dict[str, Any] | str) -> bool:
    """Delete an entry. Returns True if it existed, False otherwise (or on failure)."""
    client = await _get_client()
    if client is None:
        return False
    key = _hash_key(signal, provider, payload)
    try:
        return bool(await client.delete(key))
    except (RedisError, OSError) as e:
        log.warning("Redis DELETE failed: %s", e)
        return False
