"""Handing follow-up work (verification) to Celery without making the caller depend on it."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

log = logging.getLogger(__name__)


def enqueue_verifications(deployment_ids: Iterable[uuid.UUID], countdown: int = 0) -> None:
    """Never raises: a down broker must not turn a successful deploy into an error. The periodic sweep
    (`searchos.deploy.sweep`) picks up anything that was missed."""
    from searchos.worker.tasks import deploy_verify_task

    for dep_id in deployment_ids:
        try:
            deploy_verify_task.apply_async(args=[str(dep_id)], countdown=countdown)
        except Exception:
            log.exception("could not queue verification of deployment %s", dep_id)
