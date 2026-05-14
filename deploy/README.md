# Deployment snippets

Reverse-proxy configs for the kitchen, designed to be added to whatever's already running on your VM. **Pick the one that matches your existing reverse proxy. Never install a second one.**

| Your VM has… | Use this |
|---|---|
| Caddy already running | [`caddy/ai-native-kitchen.caddy.example`](caddy/ai-native-kitchen.caddy.example) |
| Nginx already running | [`nginx/ai-native-kitchen.conf.example`](nginx/ai-native-kitchen.conf.example) |
| Traefik already running | (TODO — open an issue if you need this) |
| Nothing — bare VM | Run Caddy in a kitchen container; instructions in `docs/vm-deploy.md` |

Both snippets:
- Touch only `api.YOUR_DOMAIN_HERE` and `briefs.YOUR_DOMAIN_HERE` — never the apex domain
- Reverse-proxy to `127.0.0.1:18080` (the loopback port `docker-compose.yml` exposes)
- Set strict security headers (HSTS, CSP, X-Content-Type-Options, Referrer-Policy)
- Assume existing Let's Encrypt automation handles certs (don't install a second cert manager)
- Log to stdout / your existing nginx log paths

Read [`../docs/vm-deploy.md`](../docs/vm-deploy.md) for the full operator checklist before touching the VM. The snippets are step 4 of an 8-step staged deploy.
