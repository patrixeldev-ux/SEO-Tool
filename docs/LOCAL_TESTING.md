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

**Easiest way (one command):** with Docker Desktop running, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1
```

It checks Docker and ports, builds and starts everything, creates the demo account, waits until the app answers
and opens http://localhost:5173. If anything fails it writes `diagnose.txt` (logs of every container) in the
project folder. The manual steps below do the same thing by hand.


```powershell
docker compose -f docker-compose.dev.yml up --build
```

The first run builds the images and installs dependencies (a few minutes). When it settles you have:

| Service | URL / purpose |
|---|---|
| web | http://localhost:5173 (Vite dev server, hot reload) |
| api | http://localhost:8000 (interactive docs at http://localhost:8000/docs) |
| worker, beat | Celery worker and scheduler (no URL; watch their logs) |
| postgres, redis | `localhost:55432`, `localhost:56379` (bound to 127.0.0.1 only; unusual ports on purpose so they don't clash) |

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
6. AI Visibility, Experiments, Content and Reports show a "Coming in Phase N" card; they are built in
   later phases. **Audit** is live (see 5.4), so are **Search Console** (see 5.5) and **Actions** (see 5.6).

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

### 5.4 Audit a site (Phase 1)

The real **Run audit** button (on each site row, and on the **Audit** page) crawls the site's own domain, so it
needs a public website. To try the whole pipeline without one, audit the bundled test website
(`fixtures/site`, a small coffee-shop site with planted problems). It runs inside the worker container, which
also proves the browser used for the JavaScript check works in the image:

```powershell
docker compose -f docker-compose.dev.yml exec worker python -m searchos.dev_crawl_fixture --email demo@example.com --inline
```

It prints the score and the five category scores. Then open **Audit** in the app, pick **Fixture site
(Harbor & Pine Coffee)** and look at the score, the issues grouped by severity (click one to see why it matters,
how to fix it and the affected URLs; click a URL for the page details), the **Pages** tab and **Export CSV**.
Run the command a second time and the page shows **Changes since the last audit**.

The first build of the backend image is larger and slower than before because it now includes Chromium.

### 5.5 Search Console, opportunities, Bing and PageSpeed (Phase 2)

You can look at the whole Search Console area **without a Google account** by loading made-up but realistic data
(16 months of daily numbers, built so every kind of opportunity has something to find):

```powershell
docker compose -f docker-compose.dev.yml exec api python -m searchos.dev_seed_gsc --email demo@example.com
```

Open **Search Console**, pick **Harbor & Pine Coffee (demo search data)** and check:

- four line charts (clicks, impressions, CTR, position) with a 28 days / 3 months / 16 months switch; the note
  under them says the totals include queries Google hides, so the tables add up to less;
- **Top queries** / **Top pages**, and the **Real searches / All / Conversational only** switch (the
  conversational queries such as "yes" and "tell me more" are a heuristic and are labelled as one);
- the **Opportunities** tab with all four kinds (striking distance, click-through gap, losing clicks, pages
  competing), each with a plain explanation and an **Evidence** section;
- click a page for its queries, positions and CTR against your own curve;
- the **AI reports** tab: upload a file from `fixtures/ai_reports/` (these are made-up examples, not real exports).

Connecting a **real** property needs the one-time Google setup in [GOOGLE_SETUP.md](GOOGLE_SETUP.md) (also available
inside the app at Settings, Integrations, "Open the setup guide"). Without it the app says "Google isn't set up
yet" and everything else keeps working. After connecting, compare the totals on the Search Console page with
Search Console's own Performance report for the same dates: they should match within 1%.

### 5.6 The action plan (Phase 3)

The planner turns audit findings and Search Console opportunities into a ranked list of actions. It works **without
any AI key**: every action is created, and the ones that need written text (titles, descriptions, structured
data, anchors) show "Needs AI" with a banner that links to **Settings, AI**.

To see a full plan with made-up data and no key, load the fixture website's audit and matching search data, then
re-plan. `LLM_PROVIDER=fake` is a development-only stand-in that writes plausible text so you can click through
every state (it is refused when `ENVIRONMENT=production`):

```powershell
# in .env add:   LLM_PROVIDER=fake      then:  docker compose -f docker-compose.dev.yml up -d --force-recreate api worker
docker compose -f docker-compose.dev.yml exec worker python -m searchos.dev_crawl_fixture --email demo@example.com --inline
docker compose -f docker-compose.dev.yml exec api python -m searchos.dev_seed_gsc --email demo@example.com --site fixture
```

Open **Actions**, pick **Fixture site**, press **Re-plan** and check:

- summary cards (open actions, by risk, expected extra clicks per 28 days, split into measured and prior);
- each row: the page, the field, current against proposed text (removed words struck through, added words
  underlined), "Why this?", expected gain with a confidence, and effort 1 to 5;
- **Edit** a title to something far too long: the checks that fail are listed and nothing is saved;
- **Approve** and **Reject** (a reason is required), one at a time and with the checkboxes for several;
- the **Manual tasks** tab: checkboxes that stay ticked after a reload;
- **Export CSV** and **Developer handoff** (a text you can paste into a ticket);
- the autopilot switch only stores your choice and says "Deploying arrives in Phase 4";
- reject an action, press **Re-plan** again: it is not proposed again for 90 days unless its priority rises 50% or more.

For real AI text put `ANTHROPIC_API_KEY=...` (or `GEMINI_API_KEY=...` and `GEMINI_MODEL=...`) in `.env` and recreate
`api` and `worker`. **Settings, AI** then shows the provider and whether a key is present (never the key itself)
and lets an admin set the monthly token budget (default 2,000,000 per organisation).

### 5.7 Automated tests

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

### Deploy smoke run (no Cloudflare, WordPress or GitHub needed)

`uv run python -m searchos.dev_deploy_smoke` (from `backend\`, with PostgreSQL and Redis running) starts a throw-away
database, the real API and Celery worker, and a pretend website that behaves like the Cloudflare Worker. It then
approves a change, deploys it, lets the worker verify it in the page's raw HTML, rolls it back, runs the daily
autopilot task and rolls that back too. It prints one line per step and exits 0 when all pass. Ports 18080 and 18081
must be free. The ownership proof and the Worker test are marked as passed directly in its own database.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `port is already allocated` on 8000 / 5173 (often another project's containers, e.g. Agency OS) | `scripts\dev-up.ps1` names the container; stop it with `docker stop <name>`. For Postgres/Redis change `POSTGRES_PORT` / `REDIS_PORT` in `.env`. |
| Anything else | Run `powershell -ExecutionPolicy Bypass -File scripts\diagnose.ps1` and share `diagnose.txt`. |
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
