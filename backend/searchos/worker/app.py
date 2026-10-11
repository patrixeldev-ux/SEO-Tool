"""Celery application.

Run a worker:   celery -A searchos.worker.app worker --loglevel=info
Run the beat:   celery -A searchos.worker.app beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from searchos.core.config import get_settings

_settings = get_settings()

app = Celery("searchos", broker=_settings.redis_url, include=["searchos.worker.tasks"])
app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,  # job state lives in the `jobs` table, not the result backend
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "purge-expired-refresh-tokens": {
            "task": "searchos.maintenance.purge_refresh_tokens",
            "schedule": crontab(hour=3, minute=15),
        },
    },
)

celery_app = app  # alias
