from __future__ import annotations

"""Small in-memory log view for this plugin's WebUI."""

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .. import Root, log

_MAX_RECORDS = 500
_records: deque[dict[str, Any]] = deque(maxlen=_MAX_RECORDS)
_lock = threading.RLock()
_installed = False
_loguru_sink_id: int | None = None


def _timestamp(value: Any = None) -> str:
    if isinstance(value, datetime):
        current = value
    else:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone().isoformat(timespec="milliseconds")


def _append(*, level: str, message: str, logger_name: str = "", source: str = "") -> None:
    text = str(message or "").strip()
    if not text:
        return
    with _lock:
        _records.append({
            "timestamp": _timestamp(),
            "level": str(level or "INFO").upper(),
            "logger": str(logger_name or "astrbot_plugin_maimaidx"),
            "source": str(source or ""),
            "message": text,
        })


class _LoggingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            _append(
                level=record.levelname,
                message=message,
                logger_name=record.name,
                source=f"{record.pathname}:{record.lineno}",
            )
        except Exception:
            self.handleError(record)


def _is_plugin_loguru_record(record: dict[str, Any]) -> bool:
    name = str(record.get("name") or "")
    file_value = record.get("file")
    path = str(getattr(file_value, "path", "") or "")
    return name.startswith("astrbot_plugin_maimaidx") or path.startswith(str(Root))


def _loguru_sink(message: Any) -> None:
    record = getattr(message, "record", {}) or {}
    if not _is_plugin_loguru_record(record):
        return
    level = record.get("level")
    file_value = record.get("file")
    source = f"{getattr(file_value, 'path', '')}:{record.get('line', '')}"
    _append(
        level=getattr(level, "name", str(level or "INFO")),
        message=str(record.get("message", "")),
        logger_name=str(record.get("name") or "astrbot_plugin_maimaidx"),
        source=source,
    )


def install_plugin_log_capture(logger: Any = log) -> None:
    global _installed, _loguru_sink_id
    with _lock:
        if _installed:
            return
        if isinstance(logger, logging.Logger):
            handler = _LoggingHandler()
            handler.setLevel(logging.DEBUG)
            logger.addHandler(handler)
            logger.setLevel(min(logger.level or logging.INFO, logging.DEBUG))
        elif hasattr(logger, "add"):
            try:
                _loguru_sink_id = logger.add(_loguru_sink, level="DEBUG", enqueue=False)
            except Exception:
                _loguru_sink_id = None
        _installed = True


def get_plugin_logs(
    *,
    limit: int = 200,
    level: str = "",
    since: str = "",
    query: str = "",
) -> list[dict[str, Any]]:
    install_plugin_log_capture()
    limit = max(1, min(int(limit or 200), _MAX_RECORDS))
    wanted_level = str(level or "").strip().upper()
    text = str(query or "").strip().lower()
    with _lock:
        values = list(_records)
    result: list[dict[str, Any]] = []
    for item in reversed(values):
        if wanted_level and item["level"] != wanted_level:
            continue
        if since and item["timestamp"] <= since:
            continue
        if text and text not in f"{item['logger']} {item['source']} {item['message']}".lower():
            continue
        result.append(dict(item))
        if len(result) >= limit:
            break
    return result


def clear_plugin_logs() -> None:
    with _lock:
        _records.clear()


__all__ = ["clear_plugin_logs", "get_plugin_logs", "install_plugin_log_capture"]
