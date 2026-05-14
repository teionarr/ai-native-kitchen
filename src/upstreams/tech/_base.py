"""Tech signal — what's in their stack."""

from abc import abstractmethod

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.upstreams._base import UpstreamProvider


class Technology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. "React", "PostgreSQL", "Cloudflare"
    category: str | None = None  # e.g. "JavaScript Framework", "Database", "CDN"
    version: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class TechResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    technologies: list[Technology] = Field(default_factory=list)
    provider: str


class TechProvider(UpstreamProvider):
    @abstractmethod
    async def lookup(self, url: str) -> TechResult: ...
