# Contributing to OpenKhazana

Thank you for contributing to **OpenKhazana** — Pakistan's open-source institutional financial market terminal & multi-asset portfolio platform!

## Local Development Setup

### Backend (`apps/api`)
```bash
cd apps/api
python -m venv .venv
# Windows: .\.venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]" ruff aiosqlite
export DATABASE_URL="sqlite+aiosqlite:///./openkhazana_local.db"
export AUTH_MODE="local"
uvicorn app.main:app --reload --port 8000
```

### Frontend (`apps/web`)
```bash
cd apps/web
npm install
npm run dev
```

## Code Quality & Testing Checklist

Before opening a Pull Request, please verify all checks pass locally:

```bash
# Backend lint & tests
cd apps/api
ruff check --config pyproject.toml .
pytest tests -v

# Frontend typecheck & production build
cd apps/web
npx tsc --noEmit
npm run build
```

## Adding Database Schema Changes

Every SQLAlchemy model change **must** include an Alembic migration in `apps/api/alembic/versions/` so PostgreSQL production deployments stay in sync with SQLite local development.

## High-Impact Contribution Areas

- Additional Pakistani AMC SECP FMR PDF templates (MCB, NBP, UBL, Lakson, Faysal, Alfalah)
- Additional PSX broker CSV/XLSX trade-ledger presets
- FBR Finance Act annual CGT/WHT tax rule updates
- Unit tests for financial statement PDF edge cases & Shariah purification ratios
