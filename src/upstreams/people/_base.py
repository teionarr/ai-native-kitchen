"""People signal — org size, leadership, recent senior hires."""

from abc import abstractmethod

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.upstreams._base import UpstreamProvider


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None
    profile_url: HttpUrl | None = None
    tenure_months: int | None = None
    joined_from: str | None = None  # previous employer if known


class PeopleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    headcount_estimate: int | None = None
    leadership: list[Person] = Field(default_factory=list)
    recent_senior_hires: list[Person] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    provider: str


class PeopleProvider(UpstreamProvider):
    @abstractmethod
    async def lookup(self, company: str) -> PeopleResult: ...
