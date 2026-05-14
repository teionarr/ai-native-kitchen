"""Process-wide settings, populated from environment variables.

In production the env is populated by Doppler at process start (`doppler run --`).
In dev it can come from Doppler OR directly from the user's shell. We never read
.env files — `pydantic-settings` would do so by default, so we explicitly disable
that with `env_file=None`.

Naming convention: every env var is prefixed `KITCHEN_` so the kitchen's settings
can't collide with anything else the VM is running.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KITCHEN_",
        case_sensitive=False,
        extra="ignore",
        env_file=None,  # explicit: never read .env files
    )

    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="HTTP port the service listens on inside the container.",
    )
    log_level: str = Field(
        default="info",
        description="Python logging level (debug / info / warning / error).",
    )
    enable_docs: bool = Field(
        default=False,
        description="Expose /docs OpenAPI UI. Off in production.",
    )
    redis_url: str | None = Field(
        default=None,
        description=(
            "redis://[:password@]host:port/db — when set, the cache layer uses this "
            "Redis instance to cache provider lookups. When unset (or unreachable), "
            "the cache is disabled and every request hits the upstream provider."
        ),
    )
    postgres_dsn: str | None = Field(
        default=None,
        description=(
            "postgresql://user:pass@host:port/db — when set, every authenticated "
            "request is recorded in the cost_log table for telemetry + daily-cap "
            "enforcement. When unset (or unreachable), telemetry silently no-ops."
        ),
    )
    max_daily_usd_per_skill: float = Field(
        default=20.0,
        ge=0,
        description=(
            "Per-skill daily USD cap (sum of upstream_cost_usd across all calls "
            "from a skill in the last 24h). Above this, requests get 429 until "
            "midnight rolls. Set to 0 to disable the cap (telemetry still records)."
        ),
    )
    firecrawl_api_key: str | None = Field(
        default=None,
        description=(
            "Firecrawl API key — get one at https://firecrawl.dev. Free tier is "
            "1000 credits/month. Required for the firecrawl scraping provider."
        ),
    )
    exa_api_key: str | None = Field(
        default=None,
        description=(
            "Exa API key — get one at https://exa.ai. Free tier is 1000 requests/"
            "month. Required for the exa search provider."
        ),
    )


settings = Settings()
