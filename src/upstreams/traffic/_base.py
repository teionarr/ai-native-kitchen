"""Traffic & demand signal — what the world is searching for / clicking on."""

from abc import abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.upstreams._base import UpstreamProvider

GrowthIndicator = Literal["growing", "flat", "declining", "unknown"]


class TrafficResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    monthly_visits_estimate: int | None = None
    top_keywords: list[str] = Field(default_factory=list)
    growth_indicator: GrowthIndicator = "unknown"
    notes: list[str] = Field(default_factory=list)
    provider: str


class TrafficProvider(UpstreamProvider):
    @abstractmethod
    async def lookup(self, domain: str) -> TrafficResult: ...
