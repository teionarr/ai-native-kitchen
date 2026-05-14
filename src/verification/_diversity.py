"""Source diversity check.

Two rules:
  1. An insight at confidence=high must have ≥2 sources from distinct registrable
     domains (stripe.com + stripe.com/blog count as one). If not, downgrade to medium.
  2. An insight at confidence=high must have at least one source from a domain
     that is NOT the target company itself (passed in as `target_domain`). If
     all sources are self-citations, downgrade to medium.

Returns: list[VerificationIssue] for the insight (may be empty).
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.verification.schemas import Confidence, Insight, Severity, VerificationIssue


def check_source_diversity(insight: Insight, *, target_domain: str | None = None) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    if not insight.sources:
        issues.append(
            VerificationIssue(
                severity="error",
                code="no_sources",
                message="insight has no sources at all",
            )
        )
        return issues

    netlocs = {_registrable(_netloc(s.url)) for s in insight.sources}
    netlocs.discard("")

    if insight.confidence == "high" and len(netlocs) < 2:
        issues.append(
            _diversity_issue("warning", "single_source_domain", insight.confidence, len(netlocs)),
        )

    if insight.confidence == "high" and target_domain:
        target_reg = _registrable(target_domain.lower())
        if netlocs and netlocs <= {target_reg}:
            issues.append(
                VerificationIssue(
                    severity="warning",
                    code="self_citation_only",
                    message=(
                        f"all sources are from the target company ({target_reg!r}); "
                        "high confidence requires at least one third-party source"
                    ),
                )
            )

    return issues


def downgrade_for_diversity(insight: Insight, issues: list[VerificationIssue]) -> Confidence:
    """Map diversity issues to a suggested confidence. Most-restrictive wins."""
    codes = {i.code for i in issues}
    if "no_sources" in codes:
        return "low"
    if "single_source_domain" in codes or "self_citation_only" in codes:
        # Either rule downgrades high → medium; never goes lower (we don't punish twice)
        return "medium" if insight.confidence == "high" else insight.confidence
    return insight.confidence


# ---- Internals -------------------------------------------------------------------------------


def _netloc(url: object) -> str:
    """Extract netloc from a HttpUrl or str."""
    parsed = urlparse(str(url))
    return parsed.netloc.lower()


def _registrable(netloc: str) -> str:
    """Cheap registrable-domain extraction. 'blog.stripe.com:443' → 'stripe.com'.

    We strip port + 'www.' and take the last 2 labels. This isn't a full PSL lookup
    but is sufficient for our defense-in-depth use (the verification is advisory,
    not security-boundary). Multi-label TLDs (.co.uk, .com.au) will be slightly
    over-counted; acceptable for now.
    """
    netloc = netloc.split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    parts = netloc.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def _diversity_issue(severity: Severity, code: str, confidence: Confidence, n_domains: int) -> VerificationIssue:
    return VerificationIssue(
        severity=severity,
        code=code,
        message=(
            f"insight is {confidence}-confidence but cites only {n_domains} distinct "
            "domain(s); high confidence requires sources from ≥2 distinct domains"
        ),
    )
