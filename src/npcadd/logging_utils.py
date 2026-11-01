"""Structured logging with a stable run identifier.

Two sinks are configured by :func:`configure_logging`:

* a human-readable stream on stderr, for the person watching the run;
* a machine-readable JSON Lines file under the run directory, for audit.

Every record carries the ``run_id`` so that interleaved logs from parallel
workers can be attributed. The run ID is derived deterministically from the
config hash plus the run start timestamp, so it is unique per run but traceable
to the configuration that produced it.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

__all__ = ["RunContext", "configure_logging", "get_logger", "new_run_id"]

_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class RunContext(logging.Filter):
    """Injects the run ID into every record."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line, with any ``extra=`` fields preserved."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "unset"),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "run_id":
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    value = repr(value)
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def new_run_id(config_hash: str, started_at: float | None = None) -> str:
    """Build a run ID of the form ``<utc-timestamp>-<config-hash-prefix>``."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at or time.time()))
    return f"{stamp}-{config_hash[:8]}"


def configure_logging(
    run_id: str,
    log_file: Path | None = None,
    level: int | str = logging.INFO,
) -> logging.Logger:
    """Configure the ``npcadd`` logger. Idempotent: existing handlers are replaced."""
    logger = logging.getLogger("npcadd")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    context = RunContext(run_id)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(run_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    stream.addFilter(context)
    logger.addHandler(stream)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        jsonl = logging.FileHandler(log_file, encoding="utf-8")
        jsonl.setFormatter(JsonLinesFormatter())
        jsonl.addFilter(context)
        logger.addHandler(jsonl)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child of the ``npcadd`` logger."""
    return logging.getLogger(f"npcadd.{name}")
