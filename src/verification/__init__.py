"""Verification module — deterministic checks over a list of insights.

Composes individual checks (URL liveness, source diversity, numerical sanity,
cross-domain contradictions) into a single `verify_insights()` entry point.

Why deterministic checks live in the kitchen instead of the plugin:
  - Centralized: all skills get the same verification, no copy-paste drift
  - Cacheable: URL liveness checks are network I/O; the kitchen's Redis cache
    can deduplicate them across skills
  - Tunable in one place: bounds and thresholds in references/sanity-bounds.yaml
    are operator-controlled, not skill-controlled

What the kitchen does NOT do (deliberately):
  - LLM-based verification (source paraphrase matching). That stays in the
    plugin's verifier agent — it's prompt+model-tier-specific and
    cost-sensitive per-skill.
  - Confidence rewriting. The verifier reports `suggested_confidence`; the
    plugin (or downstream caller) decides whether to apply it.
"""

from src.verification._api import verify_insights
from src.verification.schemas import (
    CrossDomainContradiction,
    Insight,
    InsightVerification,
    Source,
    VerificationIssue,
    VerificationReport,
)

__all__ = [
    "CrossDomainContradiction",
    "Insight",
    "InsightVerification",
    "Source",
    "VerificationIssue",
    "VerificationReport",
    "verify_insights",
]
