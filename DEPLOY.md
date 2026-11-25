# Deploying OpenKhazana to a VPS or Cloud Server (Production SaaS Guide)

OpenKhazana ships with a turnkey **Docker Compose + Caddy 2 Automatic HTTPS + PostgreSQL 16 + Redis 7 + ARQ Worker** production stack (`docker-compose.prod.yml`) ready for any Ubuntu/Debian VPS (\$5–\$12/mo Hetzner, DigitalOcean, Vultr, Contabo, AWS Lightsail).

---

## Minimum Recommended VPS Specs

- **CPU**: 2 vCPU (recommended for Pandas analytics, PDF parsing & Next.js build)
- **RAM**: 2 GB (4 GB recommended if running full historical EOD/PDF backfills)
- **Disk**: 20 GB SSD
- **OS**: Ubuntu 22.04 / 24.04 LTS with Docker Engine + Docker Compose v2

---

## Step 1 — Point Your Domain & Provision Firewall

1. Create an **A record** at your DNS provider pointing `openkhazana.yourdomain.com` → your VPS public IPv4 address.
2. Open only SSH, HTTP, and HTTPS ports:
   ```bash
   sudo ufw allow OpenSSH
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 443/udp
   sudo ufw enable
   ```
   *(PostgreSQL 5432 and Redis 6379 are never exposed publicly in `docker-compose.prod.yml`.)*

---

## Step 2 — Clone & Configure `.env`

```bash
git clone https://github.com/Khashif-Finance/OpenKhazana.git /opt/openkhazana
cd /opt/openkhazana
cp .env.example .env
```

Generate strong secrets:
```bash
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "ADMIN_API_KEY=$(openssl rand -hex 24)"
```

Edit `.env` (`nano .env`) and set:
```env
ENVIRONMENT=production
AUTH_MODE=multiuser
ALLOW_PUBLIC_REGISTRATION=true
RATE_LIMIT_ENABLED=true

APP_DOMAIN=openkhazana.yourdomain.com
POSTGRES_USER=openkhazana
POSTGRES_PASSWORD=<generated-pg-password>
POSTGRES_DB=openkhazana

SECRET_KEY=<generated-64-char-hex-secret>
ADMIN_API_KEY=<generated-admin-key>
CORS_ORIGINS=https://openkhazana.yourdomain.com
COOKIE_SECURE=true
```

---

## Step 3 — Launch with Automatic Free HTTPS

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

What happens automatically on boot:
1. PostgreSQL 16 & Redis 7 start inside a private internal Docker network.
2. FastAPI automatically runs **`alembic upgrade head`** (creating all tables up to `0005_v4_to_v1_0_complete_schema`).
3. Caddy 2 automatically provisions and renews a **free Let's Encrypt TLS certificate** for `APP_DOMAIN`, enabling HTTP/3 + HSTS + single-domain `/api/*` routing.
4. ARQ background worker begins scheduled PSX, MUFAP, PBS CPI, SBP EasyData, FX, Gold, Crypto, News, IPO & 5-Brokerage Research syncs.

Verify health:
```bash
curl https://openkhazana.yourdomain.com/health
# {"status":"ok","database":"connected","service":"openkhazana-api","version":"1.0.0","auth_mode":"multiuser"}
```

---

## Step 4 — Claim Your Admin Account & Lock Down Signups (Optional)

1. Visit `https://openkhazana.yourdomain.com/auth` and register your account. **The very first registered account automatically receives `Admin` privileges.**
2. Visit **`/status`** to access the **Instance Health & SaaS Admin Control** panel:
   - Trigger initial full market sync (`Sync All Live Market Feeds Now`).
   - Toggle **Public Signups (`OPEN` / `INVITE ONLY`)** live without restarting containers (great for a private personal/family VPS!).
   - Manage registered users (Active / Disabled / Admin roles).

---

## Step 5 — Automated Daily PostgreSQL Backups

Make the bundled backup script executable and add a daily cron job:
```bash
chmod +x /opt/openkhazana/scripts/backup-postgres.sh
crontab -e
```
Add:
```cron
30 3 * * * /opt/openkhazana/scripts/backup-postgres.sh >> /var/log/openkhazana-backup.log 2>&1
```
Backups are gzip-compressed in `/opt/openkhazana/backups/` with 14-day automatic retention.

To restore a backup:
```bash
gunzip -c /opt/openkhazana/backups/openkhazana_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T postgres psql -U openkhazana -d openkhazana
```

---

## Upgrading OpenKhazana on Your VPS

```bash
cd /opt/openkhazana
git pull origin main
docker compose -f docker-compose.prod.yml up -d --build
```
Alembic migrations run automatically before Uvicorn starts.
