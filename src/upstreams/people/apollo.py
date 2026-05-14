"""Apollo people provider — free tier 60 credits/month.

Wraps Apollo.io's /v1/organizations/enrich endpoint to get headcount + basic
company facts. Each call costs 1 credit.

Why this is a thin people provider:
- /organizations/enrich gives headcount, founded year, primary domain, but NOT
  the leadership team or recent hires
- Apollo's /v1/mixed_people/search would give us those, but each match costs
  1 credit per person — easy to burn through 60/month researching one company
- For now we populate `headcount_estimate` + `notes` from the org enrich; the
  plugin's domain experts can fall back to LinkedIn MCP for leadership lookup
- When a paid Apollo tier is in play, this provider can be extended to also
  hit /mixed_people/search for execs

Auth: Apollo accepts api_key as a JSON body field (preferred) or x-api-key header.
We use the header form so the key never appears in request body logs.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import settings
from src.upstreams import register
from src.upstreams.people._base import PeopleProvider, PeopleResult

log = logging.getLogger("kitchen.upstreams.apollo")

API_URL = "https://api.apollo.io/api/v1/organizations/enrich"
REQUEST_TIMEOUT_S = 30.0
USER_AGENT = "ai-native-kitchen/0.1 (+https://github.com/teionarr/ai-native-kitchen)"


@register("people", "apollo")
class ApolloProvider(PeopleProvider):
    name = "apollo"

    def __init__(self) -> None:
        self.api_key = settings.apollo_api_key
        if not self.api_key:
            raise ValueError("ApolloProvider requires KITCHEN_APOLLO_API_KEY (Doppler-injected env var)")

    async def lookup(self, company: str) -> PeopleResult:
        company = company.strip()
        if not company:
            raise ValueError("company must be non-empty")

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache",
        }
        # Apollo's enrich accepts either domain or company name. We pass both as
        # `domain` (best signal) when the caller gives us a URL-shaped string,
        # otherwise as the q_organization_name search param.
        params = _build_query(company)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            try:
                resp = await client.get(API_URL, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                raise RuntimeError(f"apollo unreachable: {e}") from e

            if resp.status_code == 401:
                raise RuntimeError("apollo rejected auth — check KITCHEN_APOLLO_API_KEY")
            if resp.status_code == 402:
                raise RuntimeError("apollo quota exceeded for this billing period")
            if resp.status_code == 429:
                raise RuntimeError("apollo rate-limited; retry after a few seconds")
            if resp.status_code >= 400:
                raise RuntimeError(f"apollo error {resp.status_code}: {resp.text[:200]}")

            payload = resp.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"apollo returned non-dict payload: {str(payload)[:200]}")

        org = payload.get("organization")
        if not isinstance(org, dict):
            return PeopleResult(
                company=company,
                provider=self.name,
                notes=[f"apollo found no organization match for {company!r}"],
            )

        return PeopleResult(
            company=str(org.get("name") or company),
            headcount_estimate=_safe_int(org.get("estimated_num_employees")),
            leadership=[],  # paid-tier feature; deferred (see module docstring)
            recent_senior_hires=[],
            notes=_collect_notes(org),
            provider=self.name,
        )


def _build_query(company: str) -> dict[str, str]:
    """Build query params for /organizations/enrich.

    If `company` looks like a domain (has a dot, no spaces, no scheme), pass as
    domain — Apollo's strongest signal. Otherwise fall back to organization name.
    """
    if "." in company and " " not in company and "://" not in company:
        return {"domain": company.lower()}
    return {"q_organization_name": company}


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _collect_notes(org: dict[str, Any]) -> list[str]:
    """Surface useful facts that don't fit the structured fields, as plain-text notes."""
    notes: list[str] = []
    if year := org.get("founded_year"):
        notes.append(f"founded {year}")
    if industry := org.get("industry"):
        notes.append(f"industry: {industry}")
    if hq := org.get("primary_domain"):
        notes.append(f"domain: {hq}")
    if rev := org.get("estimated_annual_revenue"):
        notes.append(f"estimated annual revenue (apollo): {rev}")
    # Coalesce city/state/country into one note
    loc_parts = [p for p in (org.get("city"), org.get("state"), org.get("country")) if p]
    if loc_parts:
        notes.append(f"location: {', '.join(str(p) for p in loc_parts)}")
    return notes
