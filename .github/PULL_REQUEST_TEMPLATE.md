## Summary
Describe the changes and motivation in 2–4 bullets.

## Verification Checklist
- [ ] `ruff check --config pyproject.toml .` passes in `apps/api`
- [ ] `pytest tests -v` passes in `apps/api`
- [ ] `alembic upgrade head` succeeds cleanly (if models changed)
- [ ] `npx tsc --noEmit` & `npm run build` pass in `apps/web`
