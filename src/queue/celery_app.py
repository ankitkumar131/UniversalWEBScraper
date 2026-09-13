"""Celery application for distributed mode (queue.mode=celery + Redis broker).

Importing this module requires celery+redis; the rest of the system works
without them (local runner mode)."""
from __future__ import annotations

import os

try:
    from celery import Celery
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "celery/redis not installed. Install with `pip install 'universal-web-scraper[queue]'` "
        "or run with queue.mode=local."
    ) from e

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "universal_web_scraper",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["src.queue.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,       # browser tasks are heavy: one at a time
    task_time_limit=1800,
    task_soft_time_limit=1500,
    task_default_queue="standard",
    task_routes={
        "uws.scrape.browser": {"queue": "browser"},
        "uws.scrape.lightweight": {"queue": "lightweight"},
    },
    broker_connection_retry_on_startup=True,
)
