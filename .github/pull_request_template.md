<!--
PR title must follow Conventional Commits:
  feat(scope): subject     — new user-facing capability
  fix(scope): subject      — bug fix
  refactor(scope): subject — internal change, no behavior change
  chore(scope): subject    — tooling / housekeeping
  docs(scope): subject     — docs only
  test(scope): subject     — tests only
  ci(scope): subject       — CI / GitHub Actions
  perf(scope): subject     — performance
  security(scope): subject — security-relevant change

Keep PRs SMALL. If this PR can't be reviewed in 15 minutes, split it.
-->

## What changed

<!-- One paragraph. What does this PR do? -->

## Why

<!-- The motivation. Which plan item / issue does this advance? -->

Closes #

## How tested

<!-- Concrete steps. "Ran X, saw Y." Not "tested locally." -->

- [ ] CI green
- [ ] Manually exercised
- [ ] Updated docs if behavior changed

## Risk & rollback

**Risk:**
**Rollback:**

## VM impact (deployment-affecting PRs only)

<!-- Skip if this PR doesn't touch VM-bound code. -->

- [ ] Read [`docs/vm-deploy.md`](docs/vm-deploy.md) and obeyed namespace rules
- [ ] All paths under `/opt/ai-native-kitchen/`, `/etc/ai-native-kitchen/`, `/var/lib/ai-native-kitchen/`, `/var/log/ai-native-kitchen/`
- [ ] No new external ports (everything routes through existing reverse proxy)
- [ ] Container hardening preserved: non-root, read-only root FS, cap_drop ALL, no-new-privileges
- [ ] No DNS / firewall changes without explicit confirmation in description

## Security checklist

- [ ] No secrets in code, commits, or logs (Doppler only)
- [ ] No new attack surface
- [ ] If adding a dependency: pinned + reviewed
- [ ] Pydantic validation on every new request body
