"""Allowlisted JSON log fields. Never serialize request objects or exception values."""

import json
import logging
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if task_id := getattr(record, "task_id", None):
            data["task_id"] = task_id
        return json.dumps(data)


def configure_logging() -> None:
    logger = logging.getLogger("zelo")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
