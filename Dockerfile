# syntax=docker/dockerfile:1.7

# Pinned to the digest of python:3.12-slim resolved at the time this PR landed.
# Renovate / Dependabot will bump this weekly via a PR, so we always know exactly
# what bytes ran in production and can roll back to the previous digest in seconds.
FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

LABEL org.opencontainers.image.source="https://github.com/teionarr/ai-native-kitchen" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="ai-native-kitchen — shared FastAPI service for the AI-lead skills ecosystem"

# Doppler CLI for runtime secret injection. Install via Doppler's official apt repo
# (more deterministic than the shell installer, which has issues with the gpg binary
# name on Debian 13). Purge the install toolchain after — curl + gnupg should not be
# reachable inside the running container.
# hadolint ignore=DL3008,DL3015
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gpg gnupg ca-certificates \
 && curl -sLf --retry 3 --tlsv1.2 --proto "=https" \
        https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key \
        | gpg --dearmor -o /usr/share/keyrings/doppler-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" \
        > /etc/apt/sources.list.d/doppler-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends doppler \
 && apt-get purge -y curl gpg gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Dedicated non-root user. Matches docs/vm-deploy.md namespace rules — UID/GID 1000
# is the same as the system user we'll create on the VM, so bind-mounted volumes
# don't end up root-owned on the host.
RUN groupadd -g 1000 kitchen \
 && useradd -u 1000 -g 1000 -m -s /bin/bash kitchen

WORKDIR /opt/ai-native-kitchen

# Copy source and install. Doing the install in one COPY+RUN means rebuilds are
# slower (no layer cache for deps), but the image is simpler and there's only ~10
# Python packages — not worth a multi-stage build at this size.
COPY --chown=kitchen:kitchen pyproject.toml /opt/ai-native-kitchen/
COPY --chown=kitchen:kitchen src/ /opt/ai-native-kitchen/src/

# pip install runs as root so site-packages is owned by root; the running user
# only needs read access. Cache disabled to keep image small.
RUN pip install --no-cache-dir -e .

USER kitchen
EXPOSE 8000

# Healthcheck uses stdlib urllib so we don't have to install curl in the final image.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Doppler-aware entrypoint. If DOPPLER_TOKEN is set, use `doppler run` to inject
# secrets. Otherwise fall back to plain uvicorn so the image is still useful in
# CI / local dev where Doppler may not be set up.
CMD ["sh", "-c", "if [ -n \"$DOPPLER_TOKEN\" ]; then exec doppler run -- uvicorn src.main:app --host 0.0.0.0 --port 8000; else exec uvicorn src.main:app --host 0.0.0.0 --port 8000; fi"]
