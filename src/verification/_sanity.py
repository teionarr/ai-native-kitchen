"""Numerical sanity check.

Parses numbers out of `raw_facts` (and the headline / evidence) and validates them
against bounds in references/sanity-bounds.yaml. Catches hallucinations + unit errors.

Examples we want to flag:
  - "headcount: 50,000,000" (50M; way too high — ranges allow up to 5M)
  - "Series A raised $1" (too low; min 1000)
  - "founded in year 47" (too low; min 1700)

We intentionally do NOT flag merely-improbable values inside the bounds — that's
business judgment, not verification.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.verification.schemas import Insight, VerificationIssue

log = logging.getLogger("kitchen.verification.sanity")

_DEFAULT_BOUNDS_PATH = Path(__file__).resolve().parent.parent.parent / "references" / "sanity-bounds.yaml"

# A simple set of patterns: (regex, metric_key, value_extractor)
# The value extractor takes the regex match and returns a float (or raises ValueError).
_NUMBER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "headcount ~120", "headcount: 5000", "headcount 8,000+"
    (re.compile(r"\bheadcount[\s~:]+\$?(\d[\d,]*)\b", re.IGNORECASE), "headcount"),
    # "founded 2010", "founded in 2010"
    (re.compile(r"\bfounded(?:\s+in)?\s+(\d{4})\b", re.IGNORECASE), "founding_year"),
    # "$X raised", "raised $X" — captures number + optional unit (M/B/T)
    (re.compile(r"\braised\s+\$([\d,.]+)\s*([MBT]?)\b", re.IGNORECASE), "funding_round_usd"),
    (re.compile(r"\$([\d,.]+)\s*([MBT])\s+raise\b", re.IGNORECASE), "funding_round_usd"),
    # "$X valuation", "valuation $X"
    (re.compile(r"valuation[\s:]+\$([\d,.]+)\s*([MBT]?)\b", re.IGNORECASE), "valuation_usd"),
    (re.compile(r"\$([\d,.]+)\s*([MBT])\s+valuation\b", re.IGNORECASE), "valuation_usd"),
    # "ARR $X"
    (re.compile(r"\bARR[\s:]+\$([\d,.]+)\s*([MBT]?)\b", re.IGNORECASE), "arr_usd"),
    (re.compile(r"\$([\d,.]+)\s*([MBT])\s+ARR\b", re.IGNORECASE), "arr_usd"),
]

_UNIT_MULTIPLIERS = {"": 1.0, "M": 1_000_000.0, "B": 1_000_000_000.0, "T": 1_000_000_000_000.0}


def load_bounds(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_BOUNDS_PATH
    if not p.exists():
        log.warning("sanity-bounds.yaml not found at %s; sanity checks disabled", p)
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        log.exception("sanity-bounds.yaml is malformed; treating as empty")
        return {}


def check_sanity(insight: Insight, *, bounds: dict[str, Any] | None = None) -> list[VerificationIssue]:
    bounds = bounds if bounds is not None else load_bounds()
    if not bounds:
        return []

    text = " ".join([insight.headline, insight.evidence, *insight.raw_facts])

    issues: list[VerificationIssue] = []
    seen: set[tuple[str, float]] = set()  # dedup so the same number isn't flagged twice

    for pattern, metric in _NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = _extract_value(match)
            except ValueError:
                continue
            key = (metric, value)
            if key in seen:
                continue
            seen.add(key)
            issue = _check_one(metric, value, bounds, insight.id)
            if issue is not None:
                issues.append(issue)

    return issues


# ---- Internals ----------------------------------------------------------------------------


def _extract_value(match: re.Match[str]) -> float:
    """Parse the numeric value (with optional M/B/T unit) from a regex match."""
    raw = match.group(1).replace(",", "")
    n = float(raw)
    unit = match.group(2).upper() if match.lastindex and match.lastindex >= 2 else ""
    return n * _UNIT_MULTIPLIERS.get(unit, 1.0)


def _check_one(metric: str, value: float, bounds: dict[str, Any], insight_id: str) -> VerificationIssue | None:
    rules = bounds.get(metric)
    if not isinstance(rules, dict):
        return None

    # Special case: founding_year max bound is "today" — derive dynamically rather
    # than relying on the YAML which goes stale.
    if metric == "founding_year":
        rules = dict(rules)
        rules["max"] = max(int(rules.get("max", 1700)), datetime.now(UTC).year)

    lo = rules.get("min")
    hi = rules.get("max")
    if lo is not None and value < lo:
        return VerificationIssue(
            severity="warning",
            code=f"{metric}_out_of_bounds",
            message=f"insight {insight_id} mentions {metric}={_humanize(value)} which is below sanity floor ({_humanize(lo)})",
        )
    if hi is not None and value > hi:
        return VerificationIssue(
            severity="warning",
            code=f"{metric}_out_of_bounds",
            message=f"insight {insight_id} mentions {metric}={_humanize(value)} which is above sanity ceiling ({_humanize(hi)})",
        )
    return None


def _humanize(n: float) -> str:
    """Format a number compactly for the issue message."""
    if n >= 1e12:
        return f"{n / 1e12:.2g}T"
    if n >= 1e9:
        return f"{n / 1e9:.2g}B"
    if n >= 1e6:
        return f"{n / 1e6:.2g}M"
    if n >= 1e3:
        return f"{n / 1e3:.2g}K"
    return f"{n:g}"
