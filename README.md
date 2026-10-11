# Search OS

Autonomous SEO / AEO / GEO platform (web + desktop). Add a website; the system observes it, plans the work,
deploys the safe fixes, asks approval for risky ones, measures whether each change helped, and rolls back losers.

The full build plan lives in [PLAN.md](PLAN.md). This repository is a monorepo:

| Folder | What |
|---|---|
| `backend/` | Python 3.12 / FastAPI / SQLAlchemy / Celery package `searchos` |
| `web/` | Vite + React + TypeScript SPA |
| `desktop/` | Tauri 2 shell (Phase 8) |
| `edge/` | Cloudflare Worker (Phase 4) |
| `wp-plugin/` | WordPress connector plugin (Phase 4) |
| `fixtures/site/` | Static test site with planted defects (Phase 1) |
| `docs/` | Local testing, deployment, checks catalogue, methodology |

## Quick start

See [docs/LOCAL_TESTING.md](docs/LOCAL_TESTING.md) (Windows + Docker Desktop instructions).

## Status

Phase 0 (foundation) is complete: auth + multi-tenant orgs/sites, background jobs, web shell, CI, local stack.
Next: Phase 1 (crawler and audit). Track progress in [PLAN.md](PLAN.md#progress-log).
