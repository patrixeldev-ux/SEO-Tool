"""Development helper: run a real audit of the bundled fixture website against a site in your database.

    python -m searchos.dev_crawl_fixture --email demo@example.com             # queue it for the Celery worker
    python -m searchos.dev_crawl_fixture --email demo@example.com --inline    # run it right here (Docker)

It starts the fixture server (fixtures/site, on 127.0.0.1), creates (or reuses) a site called
"fixture.example" in the user's first organisation, points a crawl at the fixture server, waits for it to
finish and prints the score. Open the Audit page for "Fixture site" in the web app to see the same result.

The real API can only crawl a site's own domain, so this is the one way to exercise the whole pipeline
without a public website. It refuses to run when ENVIRONMENT=production. With the Celery worker, the worker
must be able to reach 127.0.0.1 (same machine); in Docker use --inline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import sqlalchemy as sa

from searchos.core.auth import normalize_email
from searchos.core.config import get_settings
from searchos.core.db import dispose_engine, get_engine, get_sessionmaker
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import AuditSnapshot, Crawl, Job, JobStatus, Membership, Site, User
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from searchos.worker.launch import create_crawl, enqueue_crawl

FIXTURE_DOMAIN = "fixture.example"
FIXTURE_NAME = "Fixture site (Harbor & Pine Coffee)"
# A fixed port keeps page URLs identical between runs, so "changes since the last audit" make sense.
FIXTURE_PORT = 18765


def open_fixture_server() -> FixtureServer:
    try:
        return FixtureServer(port=FIXTURE_PORT)
    except OSError:  # port taken: any free one will do, only the change comparison suffers
        return FixtureServer()


async def run(email: str, page_limit: int, inline: bool, js_check: bool, wait_seconds: float) -> int:
    async with get_sessionmaker()() as session:
        user = await session.scalar(sa.select(User).where(User.email == normalize_email(email)))
        if user is None:
            print(f"No user {email}. Register in the app first (or run: python -m searchos.seed_demo).")
            return 1
        membership = await session.scalar(
            sa.select(Membership)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at, Membership.id)
            .limit(1)
        )
        if membership is None:
            print("This user does not belong to an organisation.")
            return 1
        site = await session.scalar(
            sa.select(Site).where(Site.org_id == membership.org_id, Site.domain == FIXTURE_DOMAIN)
        )
        if site is None:
            site = Site(org_id=membership.org_id, domain=FIXTURE_DOMAIN, display_name=FIXTURE_NAME)
            session.add(site)
            await session.commit()

        with open_fixture_server() as server:
            print(f"Fixture website serving at {server.base_url}")
            crawl, job = await create_crawl(
                session,
                site,
                created_by=user.id,
                page_limit=page_limit,
                start_url=server.base_url + "/",
                payload_extra={
                    "allowed_hosts": [server.netloc],
                    "allow_private_hosts": True,
                    "rate_per_host": 50,
                    "js_check": js_check,
                },
            )
            crawl_id, job_id = crawl.id, job.id
            if inline:
                print("Running the audit in this process...")
                await run_job(job_id, crawl_handler, engine=get_engine())
            else:
                if not await enqueue_crawl(session, crawl, job):
                    print("Could not reach the job queue (is Redis running?).")
                    return 1
                print("Queued for the Celery worker; waiting...")
                deadline = time.monotonic() + wait_seconds
                last = -1
                while time.monotonic() < deadline:
                    async with get_sessionmaker()() as poll:
                        row = (
                            await poll.execute(sa.select(Job.status, Job.progress).where(Job.id == job_id))
                        ).one()
                    if row.progress != last:
                        print(f"  {row.status.value} {row.progress}%")
                        last = row.progress
                    if row.status in (JobStatus.done, JobStatus.failed, JobStatus.cancelled):
                        break
                    await asyncio.sleep(0.5)
                else:
                    print("Timed out waiting for the worker.")
                    return 1

    async with get_sessionmaker()() as session:
        done = await session.get(Crawl, crawl_id)
        snapshot = await session.scalar(sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl_id))
    if done is None or done.status.value != "done" or snapshot is None:
        print(f"The audit did not finish: {done.status.value if done else '?'} {done.error if done else ''}")
        return 1
    total = snapshot.issue_counts["total"]
    score = "not available" if snapshot.score is None else f"{snapshot.score:.1f}"
    print(f"Done: {done.pages_crawled} pages, score {score}, {total} issues")
    js = (done.site_info or {}).get("js_check") or {}
    if js.get("disabled"):
        print("JS rendering check: switched off")
    elif js.get("available"):
        print(f"JS rendering check: ran on {js.get('pages_rendered', 0)} pages")
    else:
        print("JS rendering check: unavailable (no browser)")
    for category, value in snapshot.subscores.items():
        print(f"  {category:16} {'-' if value is None else round(value)}")
    print(f"Audit id: {crawl_id}  (site {site.id})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--page-limit", type=int, default=500)
    parser.add_argument("--inline", action="store_true", help="run in this process instead of the worker")
    parser.add_argument("--no-js-check", action="store_true", help="skip the browser rendering check")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    if get_settings().is_production:
        print("Refusing to run when ENVIRONMENT=production.", file=sys.stderr)
        return 1

    async def go() -> int:
        try:
            return await run(args.email, args.page_limit, args.inline, not args.no_js_check, args.timeout)
        finally:
            await dispose_engine()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
