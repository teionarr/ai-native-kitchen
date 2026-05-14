"""Cross-domain numerical contradiction detection.

Scans `raw_facts` across all insights for the same metric appearing with different
values from different domains. Example: People says "headcount ~120", Money says
"headcount ~200" — emit a CrossDomainContradiction.

Thresholds are in references/sanity-bounds.yaml under `disagreement_thresholds`.
"""

from __future__ import annotations

from typing import Any

from src.verification._sanity import _NUMBER_PATTERNS, _extract_value
from src.verification.schemas import CrossDomainContradiction, Insight


def find_cross_domain_contradictions(
    insights: list[Insight], *, bounds: dict[str, Any] | None = None
) -> list[CrossDomainContradiction]:
    if not insights:
        return []
    if bounds is None:
        from src.verification._sanity import load_bounds

        bounds = load_bounds()
    thresholds = bounds.get("disagreement_thresholds", {}) if isinstance(bounds, dict) else {}

    # Collect: {metric: [(domain, value, raw_fact_text)]}
    by_metric: dict[str, list[tuple[str, float, str]]] = {}
    for insight in insights:
        text = " ".join([insight.headline, insight.evidence, *insight.raw_facts])
        for pattern, metric in _NUMBER_PATTERNS:
            for match in pattern.finditer(text):
                try:
                    value = _extract_value(match)
                except ValueError:
                    continue
                by_metric.setdefault(metric, []).append((insight.domain, value, match.group(0)))

    contradictions: list[CrossDomainContradiction] = []
    for metric, observations in by_metric.items():
        if len(observations) < 2:
            continue
        # Group by domain; a single domain repeating itself isn't a contradiction
        for i in range(len(observations)):
            for j in range(i + 1, len(observations)):
                a_dom, a_val, a_text = observations[i]
                b_dom, b_val, b_text = observations[j]
                if a_dom == b_dom:
                    continue
                if not _disagree(metric, a_val, b_val, thresholds):
                    continue
                contradictions.append(
                    CrossDomainContradiction(
                        fact_a=a_text,
                        fact_a_domain=a_dom,
                        fact_b=b_text,
                        fact_b_domain=b_dom,
                        metric=metric,
                        magnitude=_describe_magnitude(a_val, b_val),
                    )
                )
    return contradictions


# ---- Internals ----------------------------------------------------------------------------


def _disagree(metric: str, a: float, b: float, thresholds: dict[str, Any]) -> bool:
    """True if the two values disagree more than the metric's threshold."""
    if a == 0 and b == 0:
        return False

    if metric == "headcount":
        pct = thresholds.get("headcount_pct", 20)
        return _pct_diff(a, b) > pct
    if metric in {"revenue_usd"}:
        mult = thresholds.get("revenue_multiplier", 2)
        return _multiplier_diff(a, b) > mult
    if metric in {"arr_usd"}:
        mult = thresholds.get("arr_multiplier", 2)
        return _multiplier_diff(a, b) > mult
    if metric in {"funding_round_usd", "valuation_usd"}:
        mult = thresholds.get("funding_multiplier", 1.1)
        return _multiplier_diff(a, b) > mult
    # founding_year, founder_count: any difference is suspicious
    return a != b


def _pct_diff(a: float, b: float) -> float:
    if a == 0 or b == 0:
        return 100.0
    return abs(a - b) / max(a, b) * 100.0


def _multiplier_diff(a: float, b: float) -> float:
    if a == 0 or b == 0:
        return float("inf")
    hi, lo = max(a, b), min(a, b)
    return hi / lo


def _describe_magnitude(a: float, b: float) -> str:
    if a == 0 or b == 0:
        return "zero vs non-zero"
    hi, lo = max(a, b), min(a, b)
    return f"{hi / lo:.2g}x discrepancy"
