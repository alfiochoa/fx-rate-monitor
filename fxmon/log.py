"""Structured (JSON-lines) logging."""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, rec):
        d = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": rec.levelname, "msg": rec.getMessage()}
        d.update(getattr(rec, "ctx", {}))
        return json.dumps(d, default=str)


def get_logger(name="fxmon"):
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


def event(log, msg, **ctx):
    log.info(msg, extra={"ctx": ctx})
