"""Operational logs: one JSON object per line (wiki: Stack > Logging).

The server writes to stdout and the hub to a rotated file; Grafana Alloy sends both to Loki.
A message is logged by its type and id, never with its payload: no images, base64, fingerprints,
secrets or cookies (C-05, C-10).
"""

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

CONTEXT_KEYS = ("run_id", "scan_id", "msg_id")


class JsonFormatter(logging.Formatter):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "mode": self.mode,
            "module": record.name.removeprefix("backend."),
            "msg": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                line[key] = value
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def setup(mode: str, log_file: str | None = None, max_mb: int = 10, keep_files: int = 5, level: int = logging.INFO):
    """Send the `backend` loggers to stdout (server) or to a rotated file (hub)."""
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_mb * 1024 * 1024, backupCount=keep_files
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(mode))
    root = logging.getLogger("backend")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    return root
