"""Structured JSON logging via structlog. All logs carry job_id/domain context."""
from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                logging.getLevelName(level.upper())),
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        )
    except ImportError:  # pragma: no cover
        logging.basicConfig(level=level.upper(), stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
