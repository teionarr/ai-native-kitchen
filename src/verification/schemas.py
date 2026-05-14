"""Schemas for the verification module — input shape (insights to verify) +
output shape (per-insight issues + cross-domain contradictions + summary)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Confidence = Literal["low", "medium", "high"]
Severity = Literal["info", "warning", "error"]


class Source(BaseModel):
    """One supporting source for an insight."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: HttpUrl
    published_at: datetime | None = None


class Insight(BaseModel):
    """A single insight to verify. Designed to match the plugin's domain-expert JSON shape."""

    model_config = ConfigDict(extra="forbid")

    id: str
    domain: str  # "market" / "sales" / etc.
    headline: str
    evidence: str
    sources: list[Source] = Field(default_factory=list)
    confidence: Confidence = "medium"
    raw_facts: list[str] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    """One issue found during verification."""

    model_config = ConfigDict(extra="forbid")

    severity: Severity
    code: str  # stable machine-readable code (e.g. "url_dead", "self_citation_only")
    message: str  # human-readable explanation


class InsightVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    suggested_confidence: Confidence
    issues: list[VerificationIssue] = Field(default_factory=list)


class CrossDomainContradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_a: str
    fact_a_domain: str
    fact_b: str
    fact_b_domain: str
    metric: str  # "headcount" / "revenue" / etc.
    magnitude: str  # human-readable description of the discrepancy size


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verifications: list[InsightVerification] = Field(default_factory=list)
    cross_domain_contradictions: list[CrossDomainContradiction] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
