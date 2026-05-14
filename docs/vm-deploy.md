# VM deployment — operator safety guide

> **The VM has other stuff on it. The whole point of this doc is "don't break that other stuff."** Detection-first deployment. Namespace everything. Reuse existing reverse proxy. Backup before any change.

## Pre-deployment reconnaissance (do this FIRST, before installing anything)

Run these read-only commands on the VM and capture output to a private notes file (NOT in this repo):

```bash
# What containers are already running?
docker ps -a
docker network ls
docker volume ls

# What's bound to which ports?
ss -tlnp     # TCP listeners
ss -ulnp     # UDP listeners

# What's running outside Docker?
systemctl list-units --type=service --state=running
ps auxf | head -50

# Existing reverse proxy?
which nginx caddy traefik haproxy
ls /etc/nginx/sites-enabled/ 2>/dev/null
ls /etc/caddy/ 2>/dev/null
docker ps --format '{{.Names}} {{.Image}}' | grep -iE 'nginx|caddy|traefik|haproxy'

# Disk + memory headroom
df -h /
free -h

# Existing users / what owns common dirs
ls -la /opt /srv /var/lib | head
id

# Firewall rules — make sure we don't fight ufw/firewalld
ufw status verbose 2>/dev/null
firewall-cmd --list-all 2>/dev/null
iptables -L -n 2>/dev/null | head -30

# DNS — what your subdomains currently point to
dig +short api.<your-domain>
dig +short briefs.<your-domain>
```

Save the output. Decisions documented in this PR before any compose file is written.

## Hard isolation rules

### 1. Namespace everything

Every path / container / volume / network owned by the kitchen carries the `ai-native-kitchen` prefix:

| What | Path / name |
|---|---|
| Code | `/opt/ai-native-kitchen/` |
| Config | `/etc/ai-native-kitchen/` (mode 0750, owned by `ai-native-kitchen` user) |
| Data | `/var/lib/ai-native-kitchen/` (Postgres volume, Redis volume, brief-host static dir) |
| Logs | `/var/log/ai-native-kitchen/` (or journald with `SyslogIdentifier=ai-native-kitchen`) |
| Containers | `kitchen-service`, `kitchen-redis`, `kitchen-postgres`, `kitchen-caddy` |
| Docker network | `kitchen-net` (isolated bridge — never `default`, never `host`) |
| Docker volumes | `kitchen-redis-data`, `kitchen-postgres-data`, `kitchen-briefs` |

### 2. Dedicated system user

Create a non-root user `ai-native-kitchen` (UID/GID 1000+). Owns only `/opt/ai-native-kitchen/` and `/var/lib/ai-native-kitchen/`. Containers run as this UID via `user:` in compose. **Root is never used to run kitchen services.**

### 3. Port allocation — high range, loopback only

Default proposal:
- `kitchen-service`: `127.0.0.1:18080` (exposed to internet only via existing reverse proxy)
- `kitchen-postgres`: `127.0.0.1:18432` (loopback only)
- `kitchen-redis`: not exposed on host (docker-network-internal only)

**Verify port availability before deploying:**

```bash
ss -tlnp | grep -E ':(18080|18432)'
```

If occupied, pick the next free port. Don't fight existing services.

### 4. Reverse proxy — integrate, don't replace

Whatever's on the VM today handles `*.your-domain` traffic. We add to it; we never replace it.

**If nginx is running:**
```bash
sudo cp /etc/nginx/sites-available/ai-native-kitchen.conf.example /etc/nginx/sites-available/ai-native-kitchen.conf
# Edit subdomain names in the file
sudo ln -s /etc/nginx/sites-available/ai-native-kitchen.conf /etc/nginx/sites-enabled/
sudo nginx -t  # MUST be green before reload
sudo systemctl reload nginx
```

**If Caddy is running:**
```bash
echo "import /etc/ai-native-kitchen/caddy/*.caddy" | sudo tee -a /etc/caddy/Caddyfile
sudo cp deploy/caddy/*.caddy /etc/ai-native-kitchen/caddy/
sudo caddy reload --config /etc/caddy/Caddyfile
```

**If Traefik is running:** add labels to `docker-compose.yml` so Traefik auto-discovers (router + service labels). No host-level config touched.

**If nothing is running:** install Caddy in our own container on the kitchen network. Don't touch the host system.

**TLS:** if the existing reverse proxy already has Let's Encrypt automation for the apex domain, just add the subdomain. **Never run a second cert manager** on the same machine — the two will fight.

### 5. Subdomain isolation

Touch only `api.<your-domain>` and `briefs.<your-domain>`. **Never the apex domain.** Add DNS A records (Cloudflare or wherever); confirm propagation with `dig` before reverse-proxy work.

### 6. Firewall

Don't open new external ports — everything routes through the existing reverse proxy on 80/443. If `ufw` is active, no rule changes needed. Confirm:

```bash
sudo ufw status verbose
```

### 7. Backup before any destructive change

**Before first deploy:**
```bash
sudo tar czf ~/vm-snapshot-pre-ai-native-kitchen-$(date +%F).tgz \
    /etc /opt /var/lib --exclude='/var/lib/docker'
# Then scp this off-VM
```

**Before any subsequent change to existing services:**
```bash
sudo cp <file> <file>.bak.$(date +%F)
```

Document the rollback step in the same PR that introduces the change.

### 8. Staged deploy — never go straight from compose to public

| Stage | Command | What you verify |
|---|---|---|
| 1. Dry-run | `docker compose --dry-run up` | Compose file valid, no side effects |
| 2. Loopback up | `docker compose up -d` | `curl http://127.0.0.1:18080/health` returns 200; no public exposure yet |
| 3. Reverse-proxy wire | Add subdomain config; `nginx -t && reload` (or equivalent) | `curl https://api.<your-domain>/health` returns 200 with bearer token |
| 4. Brief-host enable | Add `briefs.<your-domain>` block | First brief upload smoke test passes |

### 9. SSH access — don't use root

Create a dedicated `deploy` user on the VM with sudo for ONLY:
- `docker`
- `systemctl reload nginx` (or caddy)

Add your existing public key (the one in your `~/.ssh/`) to `/home/deploy/.ssh/authorized_keys`. **All deploys go through `ssh deploy@<your-vm>` resolved via `~/.ssh/config`. This repo never references `root@<ip>`.**

### 10. Observability — don't fight the existing stack

If the VM already runs Prometheus / Grafana / Loki:
- Expose the kitchen's `/metrics` endpoint
- Ship logs to journald (Loki picks up via promtail)

If nothing's there: just journald (`SyslogIdentifier=ai-native-kitchen`). Don't install a new observability stack alongside whatever the VM already has.

### 11. Removal plan — reversibility is the test

A clean uninstall must remove EVERYTHING the kitchen added, leaving the VM in its pre-deployment state:

```bash
# Bring containers down + remove volumes
docker compose -f /opt/ai-native-kitchen/docker-compose.yml down -v

# Remove our paths
sudo rm -rf /opt/ai-native-kitchen \
    /etc/ai-native-kitchen \
    /var/lib/ai-native-kitchen \
    /var/log/ai-native-kitchen

# Remove our system user
sudo userdel ai-native-kitchen 2>/dev/null

# Remove reverse-proxy config
sudo rm /etc/nginx/sites-enabled/ai-native-kitchen.conf
sudo rm /etc/nginx/sites-available/ai-native-kitchen.conf
sudo nginx -t && sudo systemctl reload nginx

# Remove DNS records (manually, in your DNS provider)
# Remove Doppler service token (in Doppler dashboard)
```

If any step touches anything outside that list, our isolation has been violated. **That's the test.**

## What the operator should NOT do

- SSH in as root
- Modify any file outside `/opt/ai-native-kitchen/`, `/etc/ai-native-kitchen/`, `/var/lib/ai-native-kitchen/`, `/var/log/ai-native-kitchen/`
- Change DNS records without confirmation in the PR
- Modify firewall rules without confirmation
- Install host-level packages (everything in containers when possible)
- Run a second cert manager / reverse proxy alongside the existing one
- Use `sudo cp` to overwrite existing config files without `.bak.<date>` backup
