from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from ... import diffs
from ..maimaidx_music import mai
from .constants import ALLOWED_TAGS, DIFFICULTY_NAMES, MIN_TAG_DS, TAG_RULE_VERSION, TAG_WEIGHTS, TARGET_LEVEL_INDEXES
from .storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic

CN_TZ = timezone(timedelta(hours=8))


def _note_dict(chart: Any) -> dict[str, int]:
    notes = chart.notes
    data = {
        "tap": int(getattr(notes, "tap", 0) or 0),
        "hold": int(getattr(notes, "hold", 0) or 0),
        "slide": int(getattr(notes, "slide", 0) or 0),
        "touch": int(getattr(notes, "touch", 0) or 0),
        "break": int(getattr(notes, "brk", 0) or 0),
    }
    data["total"] = sum(data.values())
    return data


def _fit_diff(music: Any, level_index: int) -> float | None:
    if not music.stats or level_index >= len(music.stats):
        return None
    stats = music.stats[level_index]
    if not stats:
        return None
    value = getattr(stats, "fit_diff", None)
    return float(value) if value is not None else None


def _sort_key(music: Any) -> tuple[int, str]:
    try:
        return int(music.id), music.id
    except Exception:
        return 10**12, str(music.id)


def build_chart_tag_payload() -> dict[str, Any]:
    generated_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    old_data = read_chart_tags()
    old_charts = old_data.get("charts", {}) if isinstance(old_data, dict) else {}
    charts = {}
    for music in sorted(mai.total_list, key=_sort_key):
        for level_index in TARGET_LEVEL_INDEXES:
            if level_index >= len(music.charts) or level_index >= len(music.ds) or level_index >= len(music.level):
                continue
            ds = float(music.ds[level_index])
            if ds < MIN_TAG_DS:
                continue
            chart = music.charts[level_index]
            notes = _note_dict(chart)
            chart_key = f"{music.id}:{level_index}"
            item = {
                "song_id": str(music.id),
                "chart_id": int(music.cids[level_index]) if level_index < len(music.cids) else None,
                "title": music.title,
                "type": music.type,
                "difficulty": DIFFICULTY_NAMES.get(level_index, diffs[level_index] if level_index < len(diffs) else str(level_index)),
                "level_index": level_index,
                "level": music.level[level_index],
                "ds": ds,
                "fit_diff": _fit_diff(music, level_index),
                "bpm": int(music.basic_info.bpm),
                "artist": music.basic_info.artist,
                "genre": music.basic_info.genre,
                "version": music.basic_info.version,
                "is_new": bool(music.basic_info.is_new),
                "charter": chart.charter or "",
                "notes": notes,
                "tags": [],
                "manual_tags": [],
                "llm_tags": [],
                "final_tags": [],
                "tag_scores": {},
                "tag_categories": {},
                "evidence": [],
                "updated_at": generated_at,
            }
            old_item = old_charts.get(chart_key, {}) if isinstance(old_charts, dict) else {}
            if isinstance(old_item, dict):
                for field in (
                    "manual_tags",
                    "llm_tags",
                    "final_tags",
                    "tags",
                    "tag_scores",
                    "tag_categories",
                    "evidence",
                    "tag_status",
                    "tag_error",
                    "tag_rule_version",
                    "updated_at",
                ):
                    if field in old_item:
                        item[field] = old_item[field]
            charts[chart_key] = item
    return {
        "version": 1,
        "generated_at": generated_at,
        "sort": "song_id asc, level_index asc",
        "target_difficulties": [DIFFICULTY_NAMES[index] for index in TARGET_LEVEL_INDEXES],
        "min_ds": MIN_TAG_DS,
        "tag_rule_version": TAG_RULE_VERSION,
        "allowed_tags": ALLOWED_TAGS,
        "tag_weights": TAG_WEIGHTS,
        "charts": charts,
    }


def generate_chart_tags_file() -> dict[str, Any]:
    payload = build_chart_tag_payload()
    write_json_atomic(CHART_TAGS_FILE, payload)
    return {
        "path": str(CHART_TAGS_FILE),
        "chart_count": len(payload["charts"]),
        "generated_at": payload["generated_at"],
    }
