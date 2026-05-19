from __future__ import annotations

import json
from typing import Any

from ... import static

TAGS_DIR = static
CHART_TAGS_FILE = TAGS_DIR / "maimaidx_chart_tags.json"
JOB_STATE_FILE = TAGS_DIR / "maimaidx_chart_tags_job.json"


def ensure_tags_dir() -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_tags_dir()
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def read_chart_tags() -> dict[str, Any]:
    if not CHART_TAGS_FILE.exists():
        return {}
    try:
        return json.loads(CHART_TAGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_job_state() -> dict[str, Any]:
    if not JOB_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(JOB_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_job_state(data: dict[str, Any]) -> None:
    write_json_atomic(JOB_STATE_FILE, data)
