from __future__ import annotations

from typing import Any

from .rule_tags import filter_allowed_tags, sort_tags_by_weight, tag_weight
from .storage import read_chart_tags


def chart_key(song_id: Any, level_index: Any) -> str:
    return f"{song_id}:{level_index}"


def _chart_item(song_id: Any, level_index: Any) -> dict[str, Any]:
    data = read_chart_tags()
    charts = data.get("charts", {}) if isinstance(data, dict) else {}
    item = charts.get(chart_key(song_id, level_index), {})
    return item if isinstance(item, dict) else {}


def get_chart_tag_scores(song_id: Any, level_index: Any) -> dict[str, float]:
    item = _chart_item(song_id, level_index)
    raw = item.get("tag_scores") if isinstance(item.get("tag_scores"), dict) else {}
    scores: dict[str, float] = {}
    for tag, value in raw.items():
        cleaned = filter_allowed_tags([str(tag)])
        if not cleaned:
            continue
        try:
            scores[cleaned[0]] = float(value)
        except (TypeError, ValueError):
            scores[cleaned[0]] = tag_weight(cleaned[0])
    if scores:
        return scores
    tags = get_chart_tags(song_id, level_index)
    return {tag: tag_weight(tag) for tag in tags}


def get_chart_tags(song_id: Any, level_index: Any) -> list[str]:
    item = _chart_item(song_id, level_index)
    tags = item.get("final_tags") or item.get("tags") or item.get("llm_tags") or []
    if not isinstance(tags, list):
        return []
    cleaned = filter_allowed_tags(str(tag) for tag in tags)
    scores = item.get("tag_scores") if isinstance(item.get("tag_scores"), dict) else None
    return sort_tags_by_weight(cleaned, scores)


def format_chart_tags(song_id: Any, level_index: Any, max_tags: int = 4) -> str:
    tags = get_chart_tags(song_id, level_index)
    if not tags:
        return ""
    return " 标签:" + "/".join(tags[:max_tags])
