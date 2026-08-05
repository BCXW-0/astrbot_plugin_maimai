from __future__ import annotations

"""Direct runtime chart tag lookup.

This module intentionally has no JSON tag-library reader.  Each command call
resolves the requested song/difficulty to a local Levels file and invokes the
trained local model.
"""

from typing import Any

from .auto_tagger import AutoTagJob
from .constants import MAX_FINAL_TAGS
from .rule_tags import filter_allowed_tags, sort_tags_by_weight, tag_weight

_RUNTIME_JOB = AutoTagJob()


def chart_key(song_id: Any, level_index: Any) -> str:
    return f"{song_id}:{level_index}"


def analyze_chart_runtime(song_id: Any, level_index: Any) -> dict[str, Any]:
    key = chart_key(song_id, level_index)
    return _RUNTIME_JOB.analyze_key(key, fresh=False) or {}


def get_chart_tag_scores(song_id: Any, level_index: Any) -> dict[str, float]:
    item = _RUNTIME_JOB.analyze_key(
        chart_key(song_id, level_index),
        include_evidence=False,
    ) or {}
    raw = item.get("model_scores") if isinstance(item.get("model_scores"), dict) else {}
    result: dict[str, float] = {}
    for tag, value in raw.items():
        cleaned = filter_allowed_tags([str(tag)])
        if not cleaned:
            continue
        try:
            result[cleaned[0]] = float(value)
        except (TypeError, ValueError):
            result[cleaned[0]] = tag_weight(cleaned[0])
    if result:
        return result
    return {tag: tag_weight(tag) for tag in get_chart_tags(song_id, level_index)}


def get_chart_tags(song_id: Any, level_index: Any) -> list[str]:
    item = _RUNTIME_JOB.analyze_key(
        chart_key(song_id, level_index),
        include_evidence=False,
    ) or {}
    tags = item.get("final_tags") or item.get("model_tags") or []
    if not isinstance(tags, list):
        return []
    scores = item.get("tag_scores") if isinstance(item.get("tag_scores"), dict) else None
    return sort_tags_by_weight(filter_allowed_tags(str(tag) for tag in tags), scores)


def format_chart_tags(song_id: Any, level_index: Any, max_tags: int = MAX_FINAL_TAGS) -> str:
    tags = get_chart_tags(song_id, level_index)
    if not tags:
        return ""
    return " 标签:" + "/".join(tags[:max_tags])


__all__ = [
    "analyze_chart_runtime",
    "chart_key",
    "format_chart_tags",
    "get_chart_tag_scores",
    "get_chart_tags",
]
