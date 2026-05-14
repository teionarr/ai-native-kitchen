# `ai-native-kitchen`

Shared infrastructure for the AI-lead skills ecosystem. Wraps premium APIs (Perplexity, Crunchbase, Apollo, Semrush, Wappalyzer paid) and free-tier sources (SEC EDGAR, OpenCorporates, Wikipedia via Firecrawl, Google Trends) behind one HTTP API. Adds a shared Redis cache, per-skill cost tracking, and a brief-host on `briefs.<your-domain>`.

> **Status: scaffold (v0.0.0).** No functional endpoints yet — the FastAPI app, Dockerfile, hardened docker-compose, upstream adapters, routes, cache, and cost telemetry land via PRs `feat/...`.

## What it serves

- **Plugin clients** (every skill in `~/Documents/Automation/AI-transformation-lead/skills/`) consume this service. Clients fall back to direct MCP/API calls when the service is unreachable, so adoption is opt-in per call.
- The first client is [`teionarr/research-company-plugin`](https://github.com/teionarr/research-company-plugin) (`/research-company` skill).
- Future clients (`/best-bet-*`, `/build-mvp-*`, `/feedback-*`, etc.) consume the same endpoints with their own scoped bearer tokens.

## Architecture

```
┌─ Your VM ─────────────────────────────────────────────┐
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Caddy (existing reverse proxy on this VM)      │  │
│  │  api.<your-domain>     → 127.0.0.1:18080        │  │
│  │  briefs.<your-domain>  → /var/lib/.../briefs/   │  │
│  └─────────────────────────────────────────────────┘  │
│                       │                               │
│  ┌────────────────────▼───────────────────────────┐   │
│  │  kitchen-service (FastAPI + Doppler CLI baked in)   │   │
│  │  /health   /discover   /domain/{slug}          │   │
│  │  /traffic  /funding    /people  /tech          │   │
│  │  /briefs   (POST upload, GET serve)            │   │
│  │                                                │   │
│  │  Upstream Strategy pattern:                    │   │
│  │    upstreams/search/     (exa, perplexity)     │   │
│  │    upstreams/scraping/   (firecrawl)           │   │
│  │    upstreams/traffic/    (semrush, etc.)       │   │
│  │    upstreams/funding/    (sec_edgar, etc.)     │   │
│  │    upstreams/people/     (linkedin_mcp, etc.)  │   │
│  │    upstreams/tech/       (wappalyzer)          │   │
│  └────────────────────────────────────────────────┘   │
│        │                          │                   │
│  ┌─────▼──────────┐      ┌────────▼────────────┐     │
│  │  kitchen-redis      │      │  kitchen-postgres        │     │
│  │  shared cache  │      │  cost telemetry     │     │
│  │  24h / 7d TTL  │      │  per-skill, 90-day  │     │
│  └────────────────┘      └─────────────────────┘     │
└───────────────────────────────────────────────────────┘
```

All containers: non-root, read-only root FS, `cap_drop ALL`, `no-new-privileges`, internal docker network. No new external ports — everything routes through the existing reverse proxy.

## Free-tier-first

The active provider for each signal is config (`config/providers.yaml`), not code. Default config picks free providers. Paid providers are skeleton implementations until you flip a config line — see [provider strategy](docs/CONTRIBUTING.md#provider-strategy).

## Secrets

Doppler is the only source of truth — see [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md). The container's only injected secret is `DOPPLER_TOKEN` (a service token scoped to one Doppler config); everything else flows from there.

## VM safety

Deployment is detection-first: nothing gets installed or modified on the VM before existing services + ports + reverse proxy are mapped. See [`docs/vm-deploy.md`](docs/vm-deploy.md) for the full operator checklist.

## Dev workflow

PR-driven against a protected `main`. CI gates: lint, schema, gitleaks, test. See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
