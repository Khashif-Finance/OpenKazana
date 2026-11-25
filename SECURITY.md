# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in OpenKhazana (such as authentication bypass, SQL/SSRF injection, cross-tenant data leakage, or XSS), please **do not open a public GitHub issue**.

Instead, report it privately via GitHub Security Advisories on `https://github.com/Khashif-Finance/OpenKhazana/security/advisories/new` with:
- A clear description of the vulnerability and affected files/endpoints.
- Steps to reproduce or a proof-of-concept request.
- Any suggested remediation.

## Built-In Production Security Controls

- **Fail-fast production check**: `ENVIRONMENT=production` with `AUTH_MODE=multiuser` refuses to start if `SECRET_KEY` is shorter than 32 characters or uses an insecure default prefix.
- **HttpOnly SameSite session cookies** with `COOKIE_SECURE=true` enforced in `docker-compose.prod.yml` behind Caddy automatic HTTPS.
- **Sliding-window rate limiting** on authentication, PDF download proxy, and live scraper refresh routes.
- **SSRF-safe allowlisted PDF proxy**: `GET /api/v1/market/downloads/proxy` strictly restricts outbound fetches to verified PSX, Pakistani AMC, and Pakistani brokerage hostnames.
- **Multi-tenant data isolation**: Portfolios, transactions, watchlists, alerts, and custom research bookmarks are scoped per `user_id` with PostgreSQL RLS context hooks.
- **Admin RBAC**: All scraper triggers, PDF/FMR ingestion endpoints, and `/api/v1/admin/*` routes enforce admin privileges or `X-Admin-Key`.
