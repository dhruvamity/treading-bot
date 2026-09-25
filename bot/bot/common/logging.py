"""Structured JSON logs plus a separate decision log (B3.8), with secret redaction (B3.9, A2.6).

Every record carries: ts, level, component, venue, market, session, event, reason, data.
The decision log is its own file stream and records every mode change, parameter change, order intent
and cancel reason, so the account's behaviour is explainable to a reviewer (A2.3).
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import orjson

from bot.common.time import now_us

# Long hex strings (keys, signatures), Telegram bot tokens, JWT-ish strings, "0x" + 64 hex.
_REDACT_PATTERNS = [
    re.compile(r"0x[0-9a-fA-F]{64}"),
    re.compile(r"\b[0-9a-fA-F]{64,}\b"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),  # telegram bot token
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
_SECRET_KEYS = re.compile(r"(private|secret|password|token|signature|seed|mnemonic|auth)", re.I)
REDACTED = "«redacted»"
_EXTRA_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    """Exact-match redaction for known secret values (called by the secrets store on load)."""
    if value and len(value) >= 8:
        _EXTRA_SECRETS.add(value)


def redact_str(s: str) -> str:
    for secret in _EXTRA_SECRETS:
        if secret in s:
            s = s.replace(secret, REDACTED)
    for p in _REDACT_PATTERNS:
        s = p.sub(REDACTED, s)
    return s


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_str(obj)
    if isinstance(obj, dict):
        return {k: (REDACTED if isinstance(k, str) and _SECRET_KEYS.search(k) and v else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact(x) for x in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": getattr(record, "ts_us", None) or now_us(),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "venue": getattr(record, "venue", None),
            "market": getattr(record, "market", None),
            "session": getattr(record, "session", None),
            "event": getattr(record, "event", None) or record.getMessage(),
            "reason": getattr(record, "reason", None),
            "data": getattr(record, "data", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return orjson.dumps(redact(payload), default=str).decode()


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_str(record.msg)
        if record.args:
            record.args = tuple(redact(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


_configured = False


def setup_logging(log_dir: Path | str = "logs", level: str = "INFO", to_stdout: bool = True) -> None:
    global _configured
    if _configured:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("bot")
    root.setLevel(level)
    fmt = JsonFormatter()
    filt = RedactingFilter()
    fh = TimedRotatingFileHandler(log_dir / "bot.jsonl", when="midnight", backupCount=30, utc=True)
    fh.setFormatter(fmt)
    fh.addFilter(filt)
    root.addHandler(fh)
    if to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.addFilter(filt)
        root.addHandler(sh)
    dec = logging.getLogger("bot.decision")
    dh = TimedRotatingFileHandler(log_dir / "decisions.jsonl", when="midnight", backupCount=365, utc=True)
    dh.setFormatter(fmt)
    dh.addFilter(filt)
    dec.addHandler(dh)
    dec.setLevel("INFO")
    _configured = True


class Log:
    """Thin structured logger: `log.info("order_placed", venue=..., market=..., reason=..., data={...})`."""

    def __init__(self, component: str) -> None:
        self._l = logging.getLogger(f"bot.{component}")
        self.component = component

    def _emit(self, level: int, event: str, **kw: Any) -> None:
        exc_info = kw.pop("exc_info", None)
        extra = {
            "component": self.component,
            "event": event,
            "venue": kw.pop("venue", None),
            "market": kw.pop("market", None),
            "session": kw.pop("session", None),
            "reason": kw.pop("reason", None),
            "data": kw.pop("data", None) or (kw or None),
        }
        self._l.log(level, event, extra=extra, exc_info=exc_info)

    def debug(self, event: str, **kw: Any) -> None:
        self._emit(logging.DEBUG, event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._emit(logging.INFO, event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._emit(logging.WARNING, event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._emit(logging.ERROR, event, **kw)

    def critical(self, event: str, **kw: Any) -> None:
        self._emit(logging.CRITICAL, event, **kw)


class DecisionLog:
    """Every decision with its reason (A2.3). Also kept in memory for tests and the daily report."""

    def __init__(self, keep_last: int = 10_000) -> None:
        self._l = logging.getLogger("bot.decision")
        self.records: list[dict[str, Any]] = []
        self._keep = keep_last

    def record(
        self,
        kind: str,
        reason: str,
        *,
        venue: str | None = None,
        market: str | None = None,
        session: str | None = None,
        ts_us: int | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        if not reason:
            raise ValueError(f"decision {kind!r} logged without a reason")
        rec = {"ts": ts_us or now_us(), "kind": kind, "reason": reason, "venue": venue, "market": market,
               "session": session, "data": data or None}
        self.records.append(rec)
        if len(self.records) > self._keep:
            del self.records[: len(self.records) - self._keep]
        self._l.info(kind, extra={"component": "decision", "event": kind, "venue": venue, "market": market,
                                  "session": session, "reason": reason, "data": data or None, "ts_us": rec["ts"]})
        return rec
