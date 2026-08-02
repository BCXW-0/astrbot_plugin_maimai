from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ... import static

TAGS_DIR = static
CHART_TAGS_FILE = TAGS_DIR / "maimaidx_chart_tags.json"
_JSON_LOCK = threading.RLock()


def ensure_tags_dir() -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_tags_dir()
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with _JSON_LOCK:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)


def read_chart_tags() -> dict[str, Any]:
    if not CHART_TAGS_FILE.exists():
        return {}
    try:
        with _JSON_LOCK:
            return json.loads(CHART_TAGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
