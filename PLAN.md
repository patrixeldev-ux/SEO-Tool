# PLAN: Search OS — autonomous SEO / AEO / GEO platform (web + desktop)
Created: 2026-10-11 · Planner: Opus 5.5 · Executor: Sonnet 5.5
Status: READY (execute one phase at a time; Phase 0 first)

## Goal
A platform where the owner adds a website and the system **plans the SEO/AEO/GEO work itself and carries it out**:
it observes the site (crawl, Search Console, Bing, AI answer engines), diagnoses problems and opportunities,
writes a prioritised action plan, deploys the safe fixes, asks approval for risky ones, measures whether each
change actually helped, rolls back what hurt, and learns which fixes work. Web app + desktop app (Tauri), one account.

"Done" for the whole product = Phases 0–8 below shipped. Each phase is independently usable and testable.

## Positioning (why this can win — read before building)
- Search Atlas OTTO already sells "SEO autopilot" (crawl → fix → deploy). Semrush/Ahrefs have AI-visibility modules.
  We will NOT out-data them (they own crawl/backlink indexes; we rent data from DataForSEO).
- Our edge, in priority order:
  1. **Proof, not claims.** Every deployed change is an experiment against matched control pages, with a confidence
     interval and a won / lost / inconclusive verdict. Losers auto-roll back. Competitors report single numbers.
  2. **Honest AI-visibility numbers.** AI answers vary run to run; every AI metric carries sample size + 95% CI and
     shows "insufficient data" instead of a fake score.
  3. **Server-side deployment only.** No JavaScript pixel: GPTBot, ClaudeBot and PerplexityBot do not execute JS, so
     JS-injected fixes are invisible to them. We deploy via Cloudflare edge, WordPress plugin, or GitHub PR.
     (Not unique on its own: OTTO also ships via Cloudflare/CMS alongside its pixel. It matters combined with 1 and 2.)
  4. **Agency-first:** multi-client orgs, white-label reports, Agency OS / Telegram control.

## Context the executor needs
- Owner: Darshit Patel (Patrixel, Vadodara). Dev machine: Windows + Docker Desktop. Local path:
  `D:\Patrixel Projects\search-os`. GitHub: `patrixeldev-ux/search-os` (owner creates the empty repo).
- Sister project Agency OS (`patrixeldev-ux/agency-os`) is integrated only in Phase 8, via public API + webhooks.
  Do not import its code.
- Precision is the product. Every scoring/statistics function must be a pure function with unit tests on known inputs.
- Things that look wrong but are intentional:
  - Web is a Vite SPA, not Next.js — so the same build wraps cleanly in Tauri.
  - `llms.txt` check is info-only (weight 0): no major engine has confirmed using it.
  - Blocking *training* bots (GPTBot, Google-Extended, ClaudeBot) is reported as info, not an error — it can be a
    legitimate owner choice. Blocking *search/retrieval* bots is high severity.

## Decisions already made (do not revisit)
- **Name/repo:** working name "Search OS", repo `search-os`, monorepo.
- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, PostgreSQL 16, Redis 7,
  Celery 5 + celery beat. Package manager `uv`. Lint `ruff`, types `mypy --strict` on `searchos/stats` and
  `searchos/audit`, tests `pytest` + `pytest-asyncio`.
- **Crawling/parsing:** `httpx` (async, HTTP/2), `protego` (robots.txt with wildcards), `selectolax` (HTML),
  `trafilatura` (main-content text), `extruct` (JSON-LD/microdata), Playwright (Chromium) only for the JS-dependence check.
- **Stats:** `numpy`, `scipy`. No pandas in hot paths.
- **Frontend:** React + TypeScript + Vite, Tailwind, shadcn/ui, TanStack Query + TanStack Router, Recharts.
  Latest stable versions at install time, pinned by lockfile.
- **Desktop:** Tauri 2 wrapping `web/` build; API base URL configurable; refresh token stored in OS keychain via the
  Rust `keyring` crate behind a Tauri command. Desktop is a client of the same cloud API (no local crawler in v1).
- **Auth:** email + password (argon2id), JWT access token 15 min + rotating refresh token 30 days (web: httpOnly
  cookie; desktop: keychain). Roles: owner, admin, member, client_viewer. Multi-tenant from day 1: org → sites.
- **LLMs:** provider abstraction `searchos/llm/` with Anthropic (Claude) and Google (Gemini) adapters; model names in env.
  All LLM outputs that get deployed are validated by code (length, uniqueness, JSON-LD schema validity) before use.
- **Third-party data:** DataForSEO (keywords, SERP incl. AI Overviews, LLM Responses, LLM Mentions); Google Search
  Console API (OAuth, `webmasters.readonly`); PageSpeed Insights API (API key); Bing Webmaster API. GSC generative-AI
  report and Bing AI Performance report are **CSV import** (no confirmed API exposure).
- **Deployers (Phase 4):** Cloudflare Worker using HTMLRewriter (universal, any CMS on Cloudflare), WordPress plugin
  (`wp-plugin/searchos-connector`), GitHub PR (static/Next sites). **No JS pixel.**
- **Crawler etiquette:** UA `SearchOSBot/0.1 (+https://patrixel.com/searchos-bot)`, obey robots.txt, default 2 req/s
  per host, max 500 pages default (hard cap 10,000), 15 s timeout, max 5 redirects.
- **Score formula:** `score = 100 × (1 − Σ wᵢ·rᵢ / Σ wᵢ)` over applicable checks; `rᵢ` = share of applicable pages
  failing check i (site-level checks: 0 or 1). Weights: critical 10, high 5, medium 2, low 1, info 0. Same formula per
  category → subscores: Indexability, On-page, Structured data, Performance, AI-readiness.
- **Risk classes for actions:** `auto` (may deploy without approval when the site's autopilot is ON: titles, meta
  descriptions, image alt, JSON-LD add/fix, internal links ≤ 3 per page per week), `approve` (redirects, canonical
  changes, content edits, new pages, hreflang), `manual` (cannot be automated: earning links, PR, reviews,
  author expertise — shown as human tasks with instructions). **Never automated:** robots.txt Disallow changes,
  noindex, deleting pages, any link buying/exchange.

## Repo layout
```
search-os/
  backend/            # python package `searchos` (api, worker, audit, stats, planner, deploy, integrations, llm)
    searchos/
      api/            # FastAPI routers
      core/           # settings, db, security, deps
      models/         # SQLAlchemy models
      worker/         # celery app + tasks
      crawl/          # fetcher, robots, sitemap, parser
      audit/          # checks registry, catalog.yaml, scoring
      stats/          # pure statistics (wilson, bootstrap, did, ctr_curve, priors)
      integrations/   # gsc, bing, psi, dataforseo
      planner/        # action generation + prioritisation
      deploy/         # cloudflare, wordpress, github deployers
      geo/            # AI visibility sampling + metrics
      content/        # briefs, drafts, fact-check
      llm/            # provider adapters
    migrations/       # alembic
    tests/
  web/                # Vite React SPA
  desktop/            # Tauri 2 shell
  edge/               # Cloudflare Worker (TypeScript)
  wp-plugin/          # WordPress plugin (PHP)
  fixtures/site/      # static test site with planted defects + EXPECTED_ISSUES.json
  docs/               # LOCAL_TESTING.md, DEPLOY.md, CHECKS.md, METHODOLOGY.md
  docker-compose.dev.yml
```

## Execution protocol (applies to every phase)
1. Work on branch `phase/<n>-<slug>`. One phase per Sonnet session.
2. Tick steps in this file and append to the Progress log as you go.
3. A phase is done only when its Verification passes; then push the branch and open a PR to `main`.
4. Do not start the next phase in the same session.

---

## Phase 0 — Foundation
| File | Action | Purpose |
|---|---|---|
| backend/pyproject.toml, backend/searchos/** | create | app skeleton |
| backend/migrations/** | create | alembic |
| web/** | create | SPA shell |
| docker-compose.dev.yml, .env.example | create | local stack |
| .github/workflows/ci.yml | create | CI |
| docs/LOCAL_TESTING.md | create | how to run |

- [x] 0.1 Monorepo skeleton, README, .gitignore, .editorconfig, LICENSE placeholder (proprietary). — Done when: tree matches "Repo layout".
- [x] 0.2 Backend app factory, `pydantic-settings` config, `/health` and `/version`; ruff/mypy/pytest config. — Done when: `uv run pytest` passes, `/health` returns 200.
- [x] 0.3 Models + first migration: `orgs`, `users`, `memberships(role)`, `sites(org_id, domain, display_name, settings jsonb, autopilot_enabled bool default false)`, `jobs(type, status, progress, error, payload, result)`. — Done when: `alembic upgrade head` works on an empty DB.
- [x] 0.4 Auth: register (creates org + owner), login, refresh (rotation + reuse detection), logout; role dependency `require_role()`. — Done when: tests cover happy path, wrong password, expired/reused refresh token.
- [x] 0.5 Sites CRUD scoped to org; domain normalisation (lowercase, strip scheme/`www.` stored separately, punycode). — Done when: a user can't read another org's site (test).
- [x] 0.6 Celery worker + beat on Redis; `jobs` row updated by tasks; `GET /jobs/{id}`. — Done when: a demo task goes queued → running → done in a test.
- [ ] 0.7 `docker-compose.dev.yml`: postgres, redis, api (reload), worker, beat, web (vite). `.env.example` documented. — Done when: `docker compose -f docker-compose.dev.yml up` serves web on :5173 and API on :8000 on Windows.
- [ ] 0.8 Web shell: login/register, org switcher, sites list/create, empty nav for Audit, Search Console, AI Visibility, Actions, Experiments, Content, Reports, Settings; API client with silent refresh; light/dark. — Done when: register → create site works in browser.
- [ ] 0.9 CI: ruff, mypy, pytest (Postgres + Redis services), web typecheck + vitest + build. — Done when: CI green on the PR.
- [ ] 0.10 `docs/LOCAL_TESTING.md` (Windows + Docker Desktop steps, seed command `python -m searchos.seed_demo`). — Done when: following it from a clean clone works.

## Phase 1 — Observe 1: crawler + technical & AI-readiness audit
- [ ] 1.1 `fixtures/site/`: ~30 static pages, each planting specific defects (one per check) plus clean control pages; `EXPECTED_ISSUES.json` = exact set of (check_id, url). Served by a pytest fixture HTTP server. — Done when: fixture server serves it in tests.
- [ ] 1.2 Fetcher: robots via `protego`, rate limiter per host, UA, redirects recorded (chain + loop detection), timeouts, content-type filter, sitemap discovery (robots `Sitemap:`, `/sitemap.xml`, index + `.gz`). — Done when: unit tests for robots wildcards, redirect chain, sitemap index.
- [ ] 1.3 Parser: title, meta description, robots meta + `X-Robots-Tag`, canonical, h1–h3, hreflang, `lang`, viewport, links (internal/external, rel, anchor), images (src, alt, bytes when known), main text via `trafilatura`, word count, JSON-LD/microdata via `extruct`. — Done when: parser tests on fixture pages pass.
- [ ] 1.4 Crawl task: BFS with depth, dedupe by normalised URL, persist `crawls`, `pages`, `links`; live progress in `jobs`; cancel. — Done when: fixture crawl stores every fixture page with correct depth.
- [ ] 1.5 Checks engine: registry decorator; `audit/catalog.yaml` with id, category, severity, title, why_it_matters, how_to_fix, applies_to. — Done when: catalog loads and every registered check has a catalog entry (test).
- [ ] 1.6 Page checks: HTTP_4XX, HTTP_5XX, REDIRECT_CHAIN (>1 hop), REDIRECT_LOOP, BROKEN_INTERNAL_LINK, CANONICAL_MISSING, CANONICAL_TO_NON200, CANONICAL_MISMATCH, NOINDEX_IN_SITEMAP, TITLE_MISSING, TITLE_DUPLICATE, TITLE_LENGTH (<30 or >60 chars), META_DESC_MISSING, META_DESC_DUPLICATE, H1_MISSING, H1_MULTIPLE, THIN_CONTENT (<250 main-text words, indexable pages only), IMG_ALT_MISSING, VIEWPORT_MISSING, LANG_MISSING, HREFLANG_INVALID, MIXED_CONTENT, TTFB_SLOW (>800 ms), SCHEMA_PARSE_ERROR, SCHEMA_REQUIRED_PROP_MISSING (Organization, LocalBusiness, Article, Product, FAQPage, BreadcrumbList), ANSWER_BLOCK_MISSING (question-form H2/H3 not followed by a ≤80-word paragraph; labelled heuristic), AUTHOR_DATE_MISSING (article-like pages). — Done when: each check has a positive + negative unit test.
- [ ] 1.7 Site checks: HTTPS_MISSING, SITEMAP_MISSING, ROBOTS_MISSING, BLOCKED_IN_SITEMAP, ORPHAN_PAGE (in sitemap, zero internal inlinks), DEEP_PAGE (depth > 4), ORG_SCHEMA_MISSING (homepage), AI_SEARCH_BOT_BLOCKED (high) vs AI_TRAINING_BOT_BLOCKED (info) driven by `audit/data/ai_bots.json` (OAI-SearchBot, ChatGPT-User, GPTBot, Claude-SearchBot, Claude-User, ClaudeBot, PerplexityBot, Perplexity-User, Google-Extended, Bingbot, Applebot-Extended — verify names against each vendor's docs and record the source URL per bot in the JSON), LLMS_TXT (info). — Done when: tests pass incl. robots variants.
- [ ] 1.8 JS_DEPENDENT_CONTENT: Playwright renders up to 50 sampled indexable pages; flag when raw-HTML main text < 50% of rendered main text (AI crawlers that don't run JS see only raw HTML). — Done when: fixture CSR page flagged, SSR page not.
- [ ] 1.9 Scoring per the formula + category subscores; `audit_snapshots` table; diff vs previous crawl (new / fixed / persisting issues). — Done when: scoring unit tests with hand-computed expected values.
- [ ] 1.10 API + UI: start crawl (page limit), live progress, score + subscores, issues grouped by check with affected URLs, page detail, CSV export, "what changed since last crawl". — Done when: full flow works in browser on the fixture site.
- [ ] 1.11 Golden test: crawl fixture → produced (check_id, url) set == EXPECTED_ISSUES.json exactly (precision = recall = 1.0). `docs/CHECKS.md` generated from catalog. — Done when: golden test green in CI.

## Phase 2 — Observe 2: Search Console, Bing, PageSpeed + opportunity finder
- [ ] 2.1 Google OAuth (per site connection, encrypted token storage with Fernet key from env); property picker. — Done when: connect/disconnect works against a real property.
- [ ] 2.2 GSC daily ingest: `searchanalytics.query` by date × page × query × country × device with pagination; 16-month backfill job; nightly incremental (data lag ~2–3 days). Table `gsc_rows`. — Done when: row counts match the GSC UI totals within 1% for a sample date range (document check in progress log).
- [ ] 2.3 Site CTR curve: isotonic regression of CTR on rounded position from the site's own data (fallback: pooled curve across sites, flagged low-confidence). Pure function in `stats/ctr_curve.py`. — Done when: unit tests (monotonic, handles sparse positions).
- [ ] 2.4 Opportunities: STRIKING_DISTANCE (avg pos 4–20, impressions above site P75), CTR_GAP (actual CTR below curve's lower band with ≥ 200 impressions/28d), DECAY (28-day clicks vs prior 28 days, Poisson rate test p < 0.01), CANNIBALISATION (≥2 pages sharing a query each with ≥ 20% impressions). Each with evidence numbers. — Done when: unit tests on synthetic data.
- [ ] 2.5 Conversational-query tagging (AI Mode follow-ups like "yes", "go on"): regex heuristic, shown as a separate segment, excluded from CTR curve fitting. — Done when: tests on example strings.
- [ ] 2.6 CSV importers: GSC "Generative AI" report, Bing "AI Performance" report (citations, cited pages, grounding queries). — Done when: sample CSVs import and chart.
- [ ] 2.7 Bing Webmaster API (query/page stats) + PageSpeed Insights (field data preferred, lab fallback) for top 20 pages by impressions → checks CWV_LCP_POOR / CWV_INP_POOR / CWV_CLS_POOR added to audit. — Done when: values visible per page.
- [ ] 2.8 UI: Search Console dashboard, opportunities table, page drill-down (queries, positions, CTR vs curve). — Done when: works on a real connected site.

## Phase 3 — Plan: the autopilot action planner
- [ ] 3.1 `actions` table: site_id, type, target_url, field, current_value, proposed_value, evidence jsonb, expected_gain, confidence, effort, risk_class, priority, status (proposed/approved/deployed/rejected/rolled_back/manual_done), created_by (system/user).
- [ ] 3.2 Rules mapping: audit issues + opportunities → action types (e.g. TITLE_LENGTH + CTR_GAP → `rewrite_title`; SCHEMA_REQUIRED_PROP_MISSING → `fix_jsonld`; ORPHAN_PAGE → `add_internal_links`; JS_DEPENDENT_CONTENT → manual dev task with SSR guidance).
- [ ] 3.3 Value generation via LLM with hard validators: titles 30–60 chars, include the page's top GSC query when natural, unique across site; meta 120–155 chars; alt text ≤ 125 chars and descriptive; JSON-LD validated against our required-props table; internal-link suggestions only to indexable 200 pages with topical overlap (embedding similarity ≥ threshold). Rejected outputs retried ≤ 2 times then dropped with reason.
- [ ] 3.4 Expected gain: `impressions_28d × (curve_ctr(expected_pos) − actual_ctr)` for CTR-type actions; fixed small priors for technical fixes; confidence from data volume. Priority = expected_gain × prior_win_rate(type) / effort (prior = 0.5 until Phase 5 learns).
- [ ] 3.5 Manual tasks generator: link-earning, author expertise, reviews, PR — explained, never automated.
- [ ] 3.6 UI: action plan board (by priority), diff view current → proposed, approve / edit / reject in bulk, autopilot ON/OFF per site (default OFF).
- Done when (phase): on the fixture site + one real site, plan generates, every proposed value passes validators (test), approvals change status.

## Phase 4 — Execute: server-side deployers
- [ ] 4.1 Site ownership verification (DNS TXT or `/searchos-verify-<token>.txt`) required before any deploy.
- [ ] 4.2 `deployments` table: action_id, method, payload, before snapshot, after snapshot, deployed_at, reverted_at.
- [ ] 4.3 Cloudflare Worker (`edge/`): HTMLRewriter applies per-URL rules (title, meta, canonical, JSON-LD append, alt, link insertion) fetched from API and cached in KV; signed rules; kill-switch; zero-rule passthrough. Owner deploys the worker with Wrangler using our guide.
- [ ] 4.4 WordPress plugin: authenticated REST (application password) to set per-post title/meta/JSON-LD/alt; outputs tags in `wp_head`; integrates with Yoast / Rank Math via their title/description filters to avoid duplicate tags.
- [ ] 4.5 GitHub deployer: opens a PR with the changes for static/Next sites (GitHub App or fine-grained token); never pushes to default branch.
- [ ] 4.6 Post-deploy verification: re-fetch URL as a non-JS client, confirm change present in raw HTML; else mark failed.
- [ ] 4.7 Autopilot scheduler: when ON, deploys only `auto` actions, max N changes/site/week (default 20), and never more than one change per URL per 28 days (keeps experiments clean).
- [ ] 4.8 One-click rollback per deployment and per batch.
- Done when: on a test WordPress (docker) and a test Cloudflare zone, deploy → verify → rollback all pass.

## Phase 5 — Measure & learn: experiment engine
- [ ] 5.1 Experiment per deployment batch: treated pages vs matched controls (same URL-template cluster, unchanged in window, nearest neighbours on pre-period 28-day clicks and impressions trend, k = 5).
- [ ] 5.2 Estimator: difference-in-differences on log1p(daily clicks) (and impressions), 28 days pre vs 28 days post; 95% CI by page-level block bootstrap (B = 2000). Pure function `stats/did.py` with tests on simulated data with known effect.
- [ ] 5.3 Verdict: WON if CI lower bound > 0, LOST if upper bound < 0, else INCONCLUSIVE; minimum data guard (≥ 300 pre-period clicks across treated pages, otherwise "insufficient data").
- [ ] 5.4 Auto-rollback for LOST `auto` actions; notify owner.
- [ ] 5.5 Learning: Beta-binomial win rate per (site, action type), shrunk toward the global prior across all sites; feeds Phase 3 priority.
- [ ] 5.6 UI: experiments list with effect size ± CI, verdict, chart treated vs control. `docs/METHODOLOGY.md` explains the maths in plain language.
- Done when: simulation tests recover planted effects (coverage of 95% CI between 0.93 and 0.97 over 500 simulations).

## Phase 6 — AI visibility (GEO / AEO)
- [ ] 6.1 Prompt sets per site: branded vs unbranded, intent classes (informational, comparison, recommendation, problem-solving, transactional); suggested from GSC queries + LLM, owner-editable; prompt changes are dated (series breaks shown).
- [ ] 6.2 Engines via adapters: DataForSEO LLM Responses (ChatGPT, Claude, Gemini, Perplexity), DataForSEO SERP (Google AI Overview presence + cited URLs); optional direct provider APIs. Model/version logged per answer. Label in UI: "API answers approximate, not identical to, consumer apps".
- [ ] 6.3 Cost preflight + hard monthly budget cap per site; scheduler runs each prompt R times per period (default R = 5, weekly).
- [ ] 6.4 Extraction (deterministic first): brand mentions via alias regex with word boundaries; citations via domain match; list position when the answer is a list; competitor mentions likewise; sentiment via LLM only, stored separately.
- [ ] 6.5 Metrics with uncertainty: visibility rate, citation rate, share of voice, average list position. Wilson 95% CI per prompt × engine; aggregate CI via cluster bootstrap over prompts; change flagged only if the bootstrap CI of the period difference excludes 0 **and** |Δ| ≥ 3 points; "insufficient data" below 100 answers per engine per period. Engines never blended without a label.
- [ ] 6.6 Store raw answers; answer explorer UI; "who gets cited instead" view; GEO actions fed into Phase 3 (e.g. add answer blocks, comparison tables, FAQ schema on pages that competitors win with).
- [ ] 6.7 First-party fusion view: GSC generative-AI impressions + Bing AI citations next to sampled visibility.
- Done when: stats tests pass; a real prompt set of ≥ 30 prompts runs within budget and shows CIs.

## Phase 7 — Content engine
- [ ] 7.1 DataForSEO keyword ideas, volume, difficulty; clusters by SERP overlap (≥ 3 shared top-10 URLs = same cluster).
- [ ] 7.2 Brief: fetch top-10 pages (respect robots), headings, entities (LLM extraction + frequency across pages), questions, word-count range, AI Overview presence.
- [ ] 7.3 Coverage score: weighted share of brief entities present in draft (weight = number of top-10 pages containing entity). Labelled as a guide, not a ranking predictor.
- [ ] 7.4 Draft generation (Claude/Gemini) with required source list; fact-check pass flags unsupported claims; answer-first blocks + FAQ/Article JSON-LD.
- [ ] 7.5 Publishing is always `approve` class → WordPress draft post or GitHub PR. Content refresh actions for DECAY pages.
- Done when: brief → draft → approve → WordPress draft created on test WP.

## Phase 8 — Desktop, reports, Agency OS integration
- [ ] 8.1 Tauri 2 shell wrapping `web/` build; configurable API URL; keychain token storage; Windows installer (.msi) build in CI; macOS build optional.
- [ ] 8.2 White-label PDF reports (WeasyPrint): monthly per site — score trend, actions deployed, experiment verdicts with CIs, AI visibility with CIs. Scheduled email delivery.
- [ ] 8.3 Org API keys + outbound webhooks (crawl.completed, action.needs_approval, experiment.decided, visibility.changed).
- [ ] 8.4 Agency OS integration plan written as `docs/AGENCY_OS_INTEGRATION.md` (Telegram commands: /seo status, /seo approve, /seo report) — implemented in the agency-os repo under its own plan.
- [ ] 8.5 Deployment guide `docs/DEPLOY.md` (Oracle Always Free VM, same pattern as Agency OS; web on Cloudflare Pages).
- Done when: desktop app logs in and shows the same data as web; report PDF renders.

## Out of scope (do not build)
- Link building automation, link buying/exchange, PBNs, spam content at scale.
- JavaScript-pixel deployment.
- Local desktop crawler, mobile app, billing/subscriptions (separate plan once validated with real clients).
- Google Business Profile management.

## Verification (whole product, run per phase as relevant)
- `uv run ruff check . && uv run mypy searchos && uv run pytest` (backend) — all green.
- `pnpm -C web typecheck && pnpm -C web test && pnpm -C web build` — all green.
- Golden audit test precision = recall = 1.0 on fixtures.
- Stats simulation tests: CI coverage 0.93–0.97.
- Manual: register → add site → crawl → plan → approve → deploy (test WP) → verify raw HTML → rollback.

## Escalate to Opus if
- A library above can't do its job (protego, trafilatura, extruct, selectolax, Playwright) or a vendor API differs from this plan.
- A real-site crawl produces a false positive that the check definition can't distinguish from a true positive.
- GSC ingest totals differ from the GSC UI by more than 1%.
- Any statistical test in `stats/` fails its simulation coverage target.
- A deployer would need write access patterns beyond those listed (e.g. editing theme files).
- A step fails twice.

## Progress log
(Executor appends one line per completed step or deviation.)
- 2026-10-11 Opus: plan created.
- 2026-10-11 Sonnet: 0.1 done — monorepo tree, README, .gitignore, .editorconfig, .gitattributes (LF), proprietary LICENSE placeholder
- 2026-10-11 Sonnet: 0.2 done — FastAPI app factory (uvicorn --factory), pydantic-settings (secrets required, no defaults), /health /health/ready /version, ruff+mypy+pytest config
- 2026-10-11 Sonnet: 0.3 done — orgs/users/memberships/sites/jobs + refresh_tokens (needed by 0.4); async Alembic env; initial migration verified up/down/up and 'alembic check' test
- 2026-10-11 Sonnet: 0.4 done — register/login/refresh(rotation+family reuse detection)/logout/me; argon2id; hashed refresh tokens; require_role(); desktop body-token mode via X-Client-Type; 32 auth/role tests
- 2026-10-11 Sonnet: 0.5 done — sites CRUD via OrgContext (org scoping in dependency, 404 across orgs); domain normalisation incl. punycode; ports/IPs rejected; org list/create for the switcher
- 2026-10-11 Sonnet: 0.6 done — Celery worker+beat on Redis (celery -A searchos.worker.app), run_job lifecycle helper, GET /jobs/{id}, POST /jobs/demo (non-prod); test drives a real in-process worker queued->running->done; beat purges expired refresh tokens; seed_demo
