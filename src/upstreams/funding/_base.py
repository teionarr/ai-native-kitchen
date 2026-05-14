"""Funding signal — money in, public-filing trail, runway hints.

The shape is deliberately wide: different providers give very different things.
SEC EDGAR returns filings (no rounds). Crunchbase returns rounds (no filings).
A provider populates only what it can; consumers handle missing fields.

(No `from __future__ import annotations` — pydantic v2 evaluates annotations at
class-creation time, and string-deferred annotations break HttpUrl / date | None
resolution in some Python versions.)
"""

from abc import abstractmethod
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.upstreams._base import UpstreamProvider


class FundingRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str  # "seed" / "series_a" / "series_b" / "ipo" / etc. (provider-specific)
    amount_usd: float | None = None
    round_date: date | None = None  # not "date" — collides with the type
    investors: list[str] = Field(default_factory=list)
    source: HttpUrl | None = None


class SECFiling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_type: str  # "10-K" / "10-Q" / "8-K" / "S-1" / etc.
    filed_at: date
    url: HttpUrl
    accession_number: str | None = None


class FundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    is_public: bool = False
    cik: str | None = None  # SEC Central Index Key — populated for US public companies
    ticker: str | None = None
    rounds: list[FundingRound] = Field(default_factory=list)
    total_raised_usd: float | None = None
    sec_filings: list[SECFiling] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    provider: str


class FundingProvider(UpstreamProvider):
    @abstractmethod
    async def lookup(self, company: str) -> FundingResult: ...
