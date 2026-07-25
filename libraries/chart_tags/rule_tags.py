from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .constants import (
    ALLOWED_TAGS,
    GENERIC_TAGS,
    MAX_FINAL_TAGS,
    TAG_ALIASES,
    TAG_SCORE_ABSOLUTE_FLOOR,
    TAG_SCORE_RELATIVE_FLOOR,
    TAG_WEIGHTS,
)


def normalize_tag(tag: str) -> str:
    value = str(tag or "").strip()
    return TAG_ALIASES.get(value, value)


def filter_allowed_tags(tags: Iterable[str]) -> list[str]:
    allowed = set(ALLOWED_TAGS)
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized not in allowed or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def tag_weight(tag: str) -> float:
    normalized = normalize_tag(tag)
    if normalized in TAG_WEIGHTS:
        return float(TAG_WEIGHTS[normalized])
    if normalized in ALLOWED_TAGS:
        return 0.5
    return 0.0


def sort_tags_by_weight(tags: Iterable[str], scores: Mapping[str, float] | None = None) -> list[str]:
    cleaned = filter_allowed_tags(tags)
    if scores:
        return sorted(
            cleaned,
            key=lambda tag: (
                -float(scores.get(tag, tag_weight(tag)) or 0.0),
                -tag_weight(tag),
                tag,
            ),
        )
    return sorted(cleaned, key=lambda tag: (-tag_weight(tag), tag))


def select_final_tags(
    scores: Mapping[str, Any] | None,
    manual_tags: Iterable[str] | None = None,
    *,
    max_tags: int = MAX_FINAL_TAGS,
) -> tuple[list[str], dict[str, float]]:
    """
    按综合分选取最终标签。
    返回 (final_tags 降序, 选中标签的分数映射)。
    """
    manual = filter_allowed_tags(manual_tags or [])
    cleaned: dict[str, float] = {}
    for raw_tag, raw_score in (scores or {}).items():
        tag = normalize_tag(str(raw_tag))
        if tag not in ALLOWED_TAGS:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if score <= 0:
            continue
        cleaned[tag] = max(cleaned.get(tag, 0.0), score)

    for tag in manual:
        cleaned[tag] = max(cleaned.get(tag, 0.0), tag_weight(tag) * 1.25)

    if not cleaned:
        return [], {}

    max_score = max(cleaned.values())
    floor = max(TAG_SCORE_ABSOLUTE_FLOOR, max_score * TAG_SCORE_RELATIVE_FLOOR)
    candidates = [
        (tag, score)
        for tag, score in cleaned.items()
        if score >= floor or tag in manual
    ]
    candidates.sort(key=lambda item: (-item[1], -tag_weight(item[0]), item[0]))

    selected: list[str] = []
    distinctive = 0
    for tag, score in candidates:
        is_generic = tag in GENERIC_TAGS
        # 已有足够辨识难点时，弱泛化标签不再挤占名额
        if (
            is_generic
            and distinctive >= 2
            and tag not in manual
            and score < max_score * 0.55
        ):
            continue
        if tag in selected:
            continue
        selected.append(tag)
        if not is_generic:
            distinctive += 1
        if len(selected) >= max_tags:
            break

    for tag in manual:
        if tag not in selected:
            selected.append(tag)
    selected = selected[: max(1, max_tags)] if selected else []
    # 最终再按分数排序（manual 插入可能打乱）
    selected = sort_tags_by_weight(selected, cleaned)
    return selected, {tag: round(cleaned.get(tag, tag_weight(tag)), 4) for tag in selected}


def scores_from_tag_list(tags: Iterable[str], base_scale: float = 1.0) -> dict[str, float]:
    result: dict[str, float] = {}
    for tag in filter_allowed_tags(tags):
        result[tag] = round(tag_weight(tag) * base_scale, 4)
    return result
