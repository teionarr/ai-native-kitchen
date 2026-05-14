"""Search signal — shape every search provider must return.

The kitchen's `/search` (or generic /domain handlers) call into the active SearchProvider.
Every concrete implementation (Exa, Perplexity, etc.) returns a `SearchResult`.
"""

from abc import abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.upstreams._base import UpstreamProvider


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: HttpUrl
    snippet: str
    published_at: datetime | None = None


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[SearchHit] = Field(default_factory=list)
    provider: str  # name of the provider that produced this result


class SearchProvider(UpstreamProvider):
    @abstractmethod
    async def search(self, query: str, *, limit: int = 5) -> SearchResult: ...
