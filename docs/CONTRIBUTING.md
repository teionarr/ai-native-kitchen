# Contributing

The kitchen is shared infrastructure for the entire AI-lead skills ecosystem. Multiple plugins depend on its API contract, so changes here are higher-stakes than in individual skill repos. **Backwards compatibility is the default**; breaking changes need an explicit migration path documented in the PR.

PR-driven against a protected `main`. CI gates: lint, schema, test, gitleaks. Squash-merge only, linear history, no force-push.

## Branching

- Feature: `feat/<slug>` — new endpoint, new upstream, new capability
- Fix: `fix/<slug>` — bug fix
- Refactor: `refactor/<slug>` — internal change, no behavior change
- Chore / docs / ci: `chore/<slug>`, `docs/<slug>`, `ci/<slug>`
- Security: `security/<slug>` — anything that changes auth, secrets handling, or attack surface

## Conventional Commits

Same as any conventional-commit repo. The PR title becomes the squash-commit subject — make it a clean line.

`feat:` `fix:` `refactor:` `chore:` `docs:` `test:` `ci:` `perf:` `security:`.

## Provider strategy

The kitchen wraps every upstream behind a Strategy-pattern abstraction. Each *signal* (search, scraping, traffic, funding, people, tech) has its own subdirectory under `src/upstreams/<signal>/`:

```
src/upstreams/<signal>/
├── _base.py        # Abstract class declaring the JSON shape every provider must return
├── <free>.py       # Free-tier implementation (default)
├── <paid>.py       # Paid implementation (skeleton until configured)
└── ...
```

`config/providers.yaml` selects the active provider per signal:

```yaml
search: exa            # free tier, 1K req/mo
scraping: firecrawl    # free tier, 1K credits/mo
traffic: google_trends # free
funding: sec_edgar     # free
people: linkedin_mcp   # free, opt-in
tech: wappalyzer_oss   # free, local CLI
```

To add a new provider:
1. Implement the abstract methods in `src/upstreams/<signal>/<new>.py`
2. The route handler doesn't change — it calls `get_active_provider("<signal>").lookup(...)`
3. Flip the config line in `providers.yaml` to enable it

To upgrade from free to paid:
1. Confirm the paid provider implements the same `_base.py` JSON shape
2. Add the API key to Doppler under the right config (`dev` / `stg` / `prd`)
3. Edit `providers.yaml` to point at the paid provider
4. Restart the container

This pattern means we can swap any upstream without touching route code. It also means the kitchen is **provider-agnostic** — Anthropic / OpenAI / Bedrock model swaps work the same way via `config/models.yaml`.

## Secrets — Doppler only

Same policy as the plugin repo: Doppler is the source of truth, no `.env` files anywhere, OSS users can fall back to shell env vars but never to dotfiles. `gitleaks` runs on every PR.

The container runs `doppler run -- uvicorn ...` with `DOPPLER_TOKEN` injected at start time. That token is the only secret outside Doppler — it lives in `/etc/ai-native-kitchen/doppler-token` (mode 0400) on the VM, owned by the kitchen system user.

Rotate any secret in the Doppler dashboard → restart the container. Service tokens are revocable instantly.

## Lint / format / test

```bash
pip install -e '.[dev]'
ruff format .
ruff check . --fix
pytest -q
```

CI runs `ruff format --check` and `ruff check` (no auto-fix in CI).

## VM impact

Any PR that changes container config, ports, paths, networking, or anything else that lands on the VM must:

1. Read [`docs/vm-deploy.md`](vm-deploy.md) and obey the namespace rules
2. Tick the "VM impact" section of the PR template
3. Document rollback in the PR description

## Code review

Run `/review` on your own PR before asking for human review. It catches the obvious things cheaply. For PRs that change the API contract (new endpoint, changed JSON shape), run `/review` AND get a human eye — clients depend on you.
