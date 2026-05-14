"""SEC EDGAR provider — free, no API key, US public companies only.

Endpoints used:
- https://www.sec.gov/files/company_tickers.json
    Static JSON dump mapping ticker → CIK → company name. Cheap & cacheable.
- https://data.sec.gov/submissions/CIK{cik:010d}.json
    Recent filings for a given CIK.

SEC's fair-use policy requires a User-Agent identifying the requester. We send our
project URL + a contact line. They throttle aggressive callers; one company lookup
typically uses 2 HTTP calls so we're well within the 10-req/sec cap.

For non-US companies and pre-IPO startups, this provider returns an empty FundingResult
(is_public=False). Other providers (OpenCorporates / Crunchbase) cover those.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import HttpUrl

from src.upstreams import register
from src.upstreams.funding._base import FundingProvider, FundingResult, SECFiling

log = logging.getLogger("kitchen.upstreams.sec_edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TPL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# SEC fair-use policy: include contact information in the User-Agent.
# Update if the project moves to a new repo or ownership changes.
USER_AGENT = "ai-native-kitchen/0.1 contact:teionarr@github.com"

# Forms we surface in the result. Everything else (ownership, prospectus addenda,
# correspondence) is too noisy for an interview prep brief.
INTERESTING_FORMS = frozenset({"10-K", "10-Q", "8-K", "S-1", "S-1/A", "20-F", "DEF 14A"})

REQUEST_TIMEOUT_S = 15.0


@register("funding", "sec_edgar")
class SECEdgarProvider(FundingProvider):
    name = "sec_edgar"

    async def lookup(self, company: str) -> FundingResult:
        company = company.strip()
        if not company:
            raise ValueError("company must be non-empty")

        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            cik_info = await self._find_cik(client, company)
            if cik_info is None:
                return FundingResult(
                    company=company,
                    is_public=False,
                    provider=self.name,
                    notes=[f"no SEC EDGAR match for {company!r} — likely private or non-US"],
                )
            cik, ticker, official_name = cik_info
            filings = await self._fetch_filings(client, cik)

        return FundingResult(
            company=official_name,
            is_public=True,
            cik=str(cik),
            ticker=ticker,
            sec_filings=filings,
            provider=self.name,
            notes=[] if filings else ["company found in SEC EDGAR but no recent filings of interesting form types"],
        )

    async def _find_cik(self, client: httpx.AsyncClient, company: str) -> tuple[int, str, str] | None:
        """Look up a company in the SEC's tickers dump. Returns (cik, ticker, official_name) or None.

        Match strategy:
        1. Exact ticker match (case-insensitive)
        2. Exact company-name match (case-insensitive)
        3. Substring match on company name (case-insensitive)

        We don't fuzzy-match; that's a job for the synthesis layer / agent prompt.
        """
        resp = await client.get(TICKERS_URL)
        resp.raise_for_status()
        tickers = resp.json()
        if not isinstance(tickers, dict):
            log.warning("unexpected SEC tickers payload shape: %s", type(tickers).__name__)
            return None

        needle = company.casefold()
        candidates: list[tuple[int, str, str]] = []
        for entry in tickers.values():
            if not isinstance(entry, dict):
                continue
            try:
                cik = int(entry["cik_str"])
                ticker = str(entry["ticker"]).upper()
                name = str(entry["title"])
            except (KeyError, ValueError, TypeError):
                continue
            if ticker.casefold() == needle:
                return (cik, ticker, name)
            if name.casefold() == needle:
                return (cik, ticker, name)
            if needle in name.casefold():
                candidates.append((cik, ticker, name))

        if not candidates:
            return None
        # Prefer the shortest matching name (heuristic: less specific = more likely the canonical entry).
        candidates.sort(key=lambda c: len(c[2]))
        return candidates[0]

    async def _fetch_filings(self, client: httpx.AsyncClient, cik: int) -> list[SECFiling]:
        resp = await client.get(SUBMISSIONS_URL_TPL.format(cik=cik))
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []

        forms = recent.get("form", []) or []
        dates = recent.get("filingDate", []) or []
        accession_numbers = recent.get("accessionNumber", []) or []
        primary_documents = recent.get("primaryDocument", []) or []

        filings: list[SECFiling] = []
        for form, filed, accession, primary_doc in zip(
            forms, dates, accession_numbers, primary_documents, strict=False
        ):
            if form not in INTERESTING_FORMS:
                continue
            try:
                filed_at = _parse_date(filed)
            except ValueError:
                continue
            url = _filing_url(cik, accession, primary_doc)
            filings.append(
                SECFiling(
                    form_type=form,
                    filed_at=filed_at,
                    url=HttpUrl(url),
                    accession_number=accession,
                )
            )
            if len(filings) >= 20:  # cap at 20 — one filing per quarter for ~5 years
                break
        return filings


def _parse_date(s: Any) -> date:
    return datetime.strptime(str(s), "%Y-%m-%d").date()


def _filing_url(cik: int, accession_number: str, primary_doc: str) -> str:
    """Build a stable EDGAR URL pointing at the filing's primary document."""
    accession_clean = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{primary_doc}"
