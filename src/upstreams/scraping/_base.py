"""Scraping signal — fetch + clean a single URL into structured text."""

from abc import abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from src.upstreams._base import UpstreamProvider


class ScrapeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    text: str  # cleaned, scraper-renderer-text
    title: str | None = None
    fetched_at: datetime
    provider: str
    # raw_html is intentionally omitted from the contract — too easy to leak server-rendered
    # secrets or chunked auth tokens into responses. Concrete providers can keep it locally
    # for their own processing but should not include it in the returned model.


class ScrapingProvider(UpstreamProvider):
    @abstractmethod
    async def scrape(self, url: str) -> ScrapeResult: ...
