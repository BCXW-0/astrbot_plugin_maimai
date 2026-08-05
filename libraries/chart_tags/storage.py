from __future__ import annotations

import gzip
import json
import os
import threading
from pathlib import Path
from typing import Any

from ... import static

TAGS_DIR = static
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


def write_json_gzip_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_tags_dir()
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _JSON_LOCK:
        with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        temp_path.replace(path)
