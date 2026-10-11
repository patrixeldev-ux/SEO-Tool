# Local testing (Windows + Docker Desktop)

This guide gets Search OS running on your Windows machine and shows how to check that it works.
All commands are **PowerShell**. Replace the path if your clone lives somewhere else.

## 0. What you need

| Tool | Notes |
|---|---|
| Docker Desktop | WSL 2 backend (the default). Give it at least 4 GB RAM. |
| Git for Windows | Line endings are forced to LF by `.gitattributes`; you do not need to configure anything. |

Optional, only if you want to run things **without** Docker (section 6): [uv](https://docs.astral.sh/uv/),
Node.js 22+, and pnpm (`npm install -g pnpm@10.28.0`).

## 1. Get the code

```powershell
cd "D:\Patrixel Projects"
git clone https://github.com/patrixeldev-ux/seo-tool.git SEO-Tool
cd "D:\Patrixel Projects\SEO-Tool"
# If the folder already exists as a clone of the repo, run instead:
#   cd "D:\Patrixel Projects\SEO-Tool"; git fetch origin; git checkout phase/0-foundation
#   (after the Phase 0 pull request is merged: git checkout main; git pull)
```

> Tip: bind mounts from a Windows drive are slower than from the WSL 2 filesystem. If file watching or page
> reloads feel slow, clone inside WSL instead (`\\wsl$\Ubuntu\home\<you>\SEO-Tool`). The compose file already
> turns on polling so reload works either way.

## 2. Configure

```powershell
Copy-Item .env.example .env
```

The defaults in `.env.example` are fine for local development. Nothing else needs editing.
`.env` is git-ignored. Never commit it, and use a real `JWT_SECRET` anywhere beyond your own machine
(the file shows how to generate one).

## 3. Start everything

```powershell
docker compose -f docker-compose.dev.yml up --build
```

The first run builds the images and installs dependencies (a few minutes). When it settles you have:

| Service | URL / purpose |
|---|---|
| web | http://localhost:5173 (Vite dev server, hot reload) |
| api | http://localhost:8000 (interactive docs at http://localhost:8000/docs) |
| worker, beat | Celery worker and scheduler (no URL; watch their logs) |
| postgres, redis | `localhost:5432`, `localhost:6379` (bound to 127.0.0.1 only) |

The `api` container runs `alembic upgrade head` on every start, so the database schema is always current.
Stop with `Ctrl+C`; `docker compose -f docker-compose.dev.yml down` removes the containers (data is kept) and
`down -v` also wipes the database.

## 4. Load demo data

In a **second** PowerShell window, in the same folder:

```powershell
docker compose -f docker-compose.dev.yml exec api python -m searchos.seed_demo
```

This is safe to run repeatedly. It prints the login:

- email `demo@example.com`, password `demo-password-123`
- workspace **Demo Agency** (you are owner, 2 sites) and **Demo Client Co** (you are admin, 1 site), so the
  organisation switcher has something to switch between.

## 5. Check that it works

### 5.1 In the browser (http://localhost:5173)

1. Click **Create an account**, fill in an organisation name, email and a password of 10+ characters. You land on
   **Sites** with an empty state.
2. **Add site**, enter `https://www.Example.com/blog`. It is listed as `www.example.com` (scheme and path
   stripped, `www.` stored separately).
3. Open the organisation switcher (top of the sidebar), choose **New organisation**, create one, and notice the
   site list is empty there: sites never leak between organisations.
4. Toggle light/dark with the moon/sun button. Reload: the choice is remembered.
5. Leave the page open for 15+ minutes (or just wait for the access token to expire) and click around: the app
   refreshes the session silently and you are not sent back to the login page.
6. The other sidebar entries (Audit, Search Console, AI Visibility, Actions, Experiments, Content, Reports) are
   intentionally empty placeholders in this phase.

### 5.2 API health

```powershell
Invoke-RestMethod http://localhost:8000/health        # status = ok
Invoke-RestMethod http://localhost:8000/health/ready  # status = ready (Postgres and Redis both answer)
Invoke-RestMethod http://localhost:8000/version
```

### 5.3 Background jobs (Celery)

Log in through the API and start the demo job, then poll it. You should see `queued`, then `running` with a rising
`progress`, then `done`:

```powershell
$login = Invoke-RestMethod -Method Post http://localhost:8000/auth/login -ContentType 'application/json' `
  -Body (@{ email = 'demo@example.com'; password = 'demo-password-123' } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }

$job = Invoke-RestMethod -Method Post http://localhost:8000/jobs/demo -Headers $headers `
  -ContentType 'application/json' -Body (@{ steps = 10; step_seconds = 0.5 } | ConvertTo-Json)

1..12 | ForEach-Object {
  $j = Invoke-RestMethod "http://localhost:8000/jobs/$($job.id)" -Headers $headers
  "{0,-8} {1,3}%" -f $j.status, $j.progress
  Start-Sleep -Milliseconds 600
}
```

The `worker` container log shows the task being received. The `beat` container schedules a nightly cleanup of
expired refresh tokens.

### 5.4 Automated tests

Backend (uses the Postgres and Redis containers; it creates and drops its own throw-away database and uses Redis
database 15, so your dev data is untouched):

```powershell
docker compose -f docker-compose.dev.yml exec api ruff check .
docker compose -f docker-compose.dev.yml exec api mypy searchos
docker compose -f docker-compose.dev.yml exec api pytest
```

Web:

```powershell
docker compose -f docker-compose.dev.yml exec web pnpm typecheck
docker compose -f docker-compose.dev.yml exec web pnpm test
docker compose -f docker-compose.dev.yml exec web pnpm build
```

## 6. Running without Docker for the apps (optional)

Handy for debugging with breakpoints. Keep Postgres and Redis in Docker:

```powershell
docker compose -f docker-compose.dev.yml up -d postgres redis
```

API (terminal 1). Settings are read from `.env` in the repo root, which already points at `localhost`:

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run python -m searchos.seed_demo
uv run uvicorn searchos.main:create_app --factory --reload
```

Worker (terminal 2). Celery's default pool does not work on Windows, hence `--pool=solo`:

```powershell
cd backend
uv run celery -A searchos.worker.app worker --loglevel=info --pool=solo
```

Beat (terminal 3, optional):

```powershell
cd backend
uv run celery -A searchos.worker.app beat --loglevel=info
```

Web (terminal 4):

```powershell
cd web
pnpm install
pnpm dev
```

Tests and linters from `backend\`: `uv run ruff check .`, `uv run mypy searchos`, `uv run pytest`.
From `web\`: `pnpm typecheck`, `pnpm test`, `pnpm build`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `port is already allocated` on 5432 / 6379 / 8000 / 5173 | Stop the other program, or set `POSTGRES_PORT` / `REDIS_PORT` in `.env`. |
| Web page loads but every request fails with a CORS error | `CORS_ORIGINS` in `.env` must contain the exact origin you browse to (default `http://localhost:5173`). Restart the stack after editing. |
| `JWT_SECRET ... at least 32 characters` or `Field required` on API start | `.env` is missing or incomplete. Re-copy it from `.env.example`. |
| You are logged out right after login | Browse via `http://localhost:5173` (not `127.0.0.1`) so the session cookie, which is set for `localhost`, is sent. |
| Code changes are not picked up | Polling is on by default; give it a few seconds. If you moved the clone, run `docker compose -f docker-compose.dev.yml up --build`. |
| Start from scratch | `docker compose -f docker-compose.dev.yml down -v` then `up --build`. |
| Python/Node packages look stale after pulling | `docker compose -f docker-compose.dev.yml up --build` (the web container also reinstalls on start). |

## 8. Security notes for local setups

- The refresh token lives in an `httpOnly` cookie scoped to `/auth` and is single use: every refresh issues a new
  one, and replaying an old one revokes that whole login session.
- In production (`ENVIRONMENT=production`) the cookie is `Secure`, API docs and demo endpoints are switched off, and
  the API refuses to start with the development `JWT_SECRET` placeholder.
- The seeded demo account has a well-known password. Never run `seed_demo` against a shared or production
  database (it refuses when `ENVIRONMENT=production`).
