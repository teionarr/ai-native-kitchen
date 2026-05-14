"""Top-level verify_insights() — composes all the deterministic checks."""

from __future__ import annotations

from src.verification._contradictions import find_cross_domain_contradictions
from src.verification._diversity import check_source_diversity, downgrade_for_diversity
from src.verification._sanity import check_sanity, load_bounds
from src.verification._url import head_check_many
from src.verification.schemas import (
    Insight,
    InsightVerification,
    VerificationIssue,
    VerificationReport,
)


async def verify_insights(
    insights: list[Insight],
    *,
    target_domain: str | None = None,
    skip_url_check: bool = False,
) -> VerificationReport:
    """Run all deterministic checks. Returns a VerificationReport.

    `target_domain` (e.g. "stripe.com") triggers the self-citation rule for
    high-confidence insights. Pass None to skip that specific check.

    `skip_url_check` is for tests / dev where we don't want network I/O.
    """
    bounds = load_bounds()

    # Network: HEAD-check every distinct URL across all insights, in parallel.
    url_alive: dict[str, bool] = {}
    if not skip_url_check:
        urls = [str(s.url) for ins in insights for s in ins.sources]
        url_alive = await head_check_many(urls)

    verifications: list[InsightVerification] = []
    for ins in insights:
        issues: list[VerificationIssue] = []
        # Diversity rules
        issues.extend(check_source_diversity(ins, target_domain=target_domain))
        # Sanity bounds
        issues.extend(check_sanity(ins, bounds=bounds))
        # URL liveness
        for src in ins.sources:
            if str(src.url) in url_alive and not url_alive[str(src.url)]:
                issues.append(
                    VerificationIssue(
                        severity="warning",
                        code="url_dead",
                        message=f"source URL {src.url} did not respond at verification time",
                    )
                )

        suggested = downgrade_for_diversity(ins, issues)
        verifications.append(
            InsightVerification(
                insight_id=ins.id,
                suggested_confidence=suggested,
                issues=issues,
            )
        )

    contradictions = find_cross_domain_contradictions(insights, bounds=bounds)

    summary = {
        "insights_verified": len(verifications),
        "issues_total": sum(len(v.issues) for v in verifications),
        "issues_by_severity_warning": sum(1 for v in verifications for i in v.issues if i.severity == "warning"),
        "issues_by_severity_error": sum(1 for v in verifications for i in v.issues if i.severity == "error"),
        "cross_domain_contradictions": len(contradictions),
    }

    return VerificationReport(
        verifications=verifications,
        cross_domain_contradictions=contradictions,
        summary=summary,
    )
