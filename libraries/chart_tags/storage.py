from __future__ import annotations

import gzip
import json
import os
import threading
from pathlib import Path
from typing import Any

_JSON_LOCK = threading.RLock()


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    _ensure_parent_dir(path)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with _JSON_LOCK:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)


def write_json_gzip_atomic(path: Path, data: dict[str, Any]) -> None:
    _ensure_parent_dir(path)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _JSON_LOCK:
        with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        temp_path.replace(path)
