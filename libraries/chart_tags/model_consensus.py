from __future__ import annotations

"""Parse and run the model review used only during dataset creation."""

import asyncio
import json
import hashlib
from typing import Any

from .constants import ALLOWED_TAGS, MAX_FINAL_TAGS
from .rule_tags import filter_allowed_tags, normalize_tag

DEFAULT_ASTRBOT_PROVIDER_ID = "google_gemini/gemini-2.5-pro"
CURRENT_DIALOGUE_MODEL_ID = "dialogue_model"
CURRENT_DIALOGUE_MODEL_NAME = "5.6-Luna Max"
ASTRBOT_MODEL_NAME = "gemini-2.5-pro"
REQUIRED_MODEL_COUNT = 2
LEGACY_MODEL_COUNT = 3
MODEL_RETRY_DELAYS = (5, 15, 30, 60, 120)
_TOOL_CALL_MARKERS = (
    "custom_tool_call",
    "<tool_call>",
    "</tool_call>",
    "tool_calls",
    "function_call",
)

MODEL_SYSTEM_PROMPT = """
你是舞萌 DX 谱面结构审核员。只根据输入的完整谱面元数据、simai 语法、两小节窗口和候选特征判断配置标签。
严格遵守 maimai.xls：普通切分不算跳拍；普通曲线 Slide 不算如龙；普通 Hold 重叠不算留尾；撞尾必须依据 Slide 末端路径区和时序冲突；拆谱、手序、秒划仅作兼容输入，输出使用协调或留尾；每个谱面最多输出 5 个标签。
输出必须是一个 JSON 对象，不要 Markdown，不要解释文字，格式为 {"tags":["标签"],"evidence":[{"tag":"标签","window":"窗口或原始语法"}]}。
""".strip()


def build_chart_prompt(ref: dict[str, Any], chart: dict[str, Any], analysis: dict[str, Any]) -> str:
    payload = {
        "source": {
            "key": ref.get("key", ""),
            "title": ref.get("title", ""),
            "artist": ref.get("artist", ""),
            "difficulty": ref.get("difficulty", ""),
            "ds": ref.get("ds", 0.0),
            "bpm": ref.get("bpm", 0.0),
        },
        "chart": chart,
        "candidate_analysis": {
            "tags": analysis.get("tags") or [],
            "raw_tags": analysis.get("raw_tags") or [],
            "difficulty_tags": analysis.get("difficulty_tags") or [],
            "difficulty_scores": analysis.get("difficulty_scores") or {},
            "candidate_scores": analysis.get("candidate_scores") or {},
            "features": analysis.get("features") or {},
            "windows": (analysis.get("windows") or [])[:12],
            "tag_evidence": analysis.get("tag_evidence") or {},
        },
    }
    return "请审核下面这条谱面，只返回指定 JSON：\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    lowered = cleaned.casefold()
    if not cleaned or any(marker.casefold() in lowered for marker in _TOOL_CALL_MARKERS):
        return {}
    try:
        data = json.loads(cleaned)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_model_result(text: str, *, provider: str, requested_at: str = "") -> dict[str, Any]:
    data = _decode_json(text)
    raw_tags = data.get("tags")
    invalid_tags: list[str] = []
    if not isinstance(raw_tags, list) or len(raw_tags) > MAX_FINAL_TAGS:
        return {
            "provider": provider,
            "status": "invalid_response",
            "tags": [],
            "evidence": [],
            "response_digest": hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest(),
            "requested_at": requested_at,
            "error": "missing_or_excessive_tags",
        }
    for raw_tag in raw_tags:
        normalized = normalize_tag(str(raw_tag))
        if normalized not in ALLOWED_TAGS:
            invalid_tags.append(str(raw_tag))
    if invalid_tags:
        return {
            "provider": provider,
            "status": "invalid_response",
            "tags": [],
            "evidence": [],
            "response_digest": hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest(),
            "requested_at": requested_at,
            "error": "unknown_tag",
        }
    tags = filter_allowed_tags(str(tag) for tag in raw_tags)
    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    return {
        "provider": provider,
        "status": "completed",
        "tags": tags,
        "evidence": evidence[:MAX_FINAL_TAGS],
        "response_digest": hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest(),
        "requested_at": requested_at,
    }


def compare_model_results(
    first: dict[str, Any],
    second: dict[str, Any],
    third: dict[str, Any] | None = None,
) -> dict[str, Any]:
    models = (first, second, third) if third is not None else (first, second)
    tag_sets = [sorted(set(filter_allowed_tags(model.get("tags") or []))) for model in models]
    complete = all(model.get("status") == "completed" for model in models)
    consistent = bool(complete and all(tags == tag_sets[0] for tags in tag_sets[1:]))
    required_models = len(models)
    return {
        "required_models": required_models,
        "completed_models": sum(model.get("status") == "completed" for model in models),
        "first_tags": tag_sets[0],
        "second_tags": tag_sets[1],
        "third_tags": tag_sets[2] if len(tag_sets) == 3 else [],
        "consistent": consistent,
        "reason": "exact_tag_set_match" if consistent else "model_disagreement_or_invalid_response",
    }


def _completed_tags(model: dict[str, Any]) -> list[str]:
    return sorted(set(filter_allowed_tags(model.get("tags") or [])))


def is_accepted_model_review(
    review: dict[str, Any],
    *,
    allow_legacy_three: bool = True,
) -> bool:
    """Return whether a persisted review may enter external validation.

    New reviews must identify the current dialogue model and the configured
    Google provider. Historical three-model records are accepted only when
    their comparison explicitly records a completed, exact three-way match.
    """
    if not isinstance(review, dict):
        return False
    comparison = review.get("comparison") if isinstance(review.get("comparison"), dict) else {}
    required = int(comparison.get("required_models", 0) or 0)
    if not bool(comparison.get("consistent")):
        return False
    first = review.get("first") if isinstance(review.get("first"), dict) else {}
    second = review.get("second") if isinstance(review.get("second"), dict) else {}
    third = review.get("third") if isinstance(review.get("third"), dict) else {}
    if required == LEGACY_MODEL_COUNT:
        if not allow_legacy_three:
            return False
        models = (first, second, third)
        if any(model.get("status") != "completed" for model in models):
            return False
        tags = [_completed_tags(model) for model in models]
        return tags[0] == tags[1] == tags[2]
    if required != REQUIRED_MODEL_COUNT:
        return False
    if first.get("status") != "completed" or second.get("status") != "completed":
        return False
    first_provider = str(first.get("provider") or "").strip()
    second_provider = str(second.get("provider") or "").strip()
    if first_provider not in {
        CURRENT_DIALOGUE_MODEL_ID,
        CURRENT_DIALOGUE_MODEL_NAME,
        "current_dialogue_model",
    }:
        return False
    if second_provider not in {
        DEFAULT_ASTRBOT_PROVIDER_ID,
        f"astrbot_{DEFAULT_ASTRBOT_PROVIDER_ID.replace('/', '_')}",
    }:
        return False
    return _completed_tags(first) == _completed_tags(second)


async def call_context_model(context: Any, prompt: str, *, provider_id: str | None, provider_name: str) -> dict[str, Any]:
    if context is None or not hasattr(context, "llm_generate"):
        return {"provider": provider_name, "status": "unavailable", "tags": [], "evidence": [], "raw_response": ""}
    last: dict[str, Any] = {
        "provider": provider_name,
        "status": "unavailable",
        "tags": [],
        "evidence": [],
        "raw_response": "",
        "error": "retry_exhausted",
    }
    for attempt, delay in enumerate((0, *MODEL_RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            response = await context.llm_generate(
                chat_provider_id=provider_id,
                system_prompt=MODEL_SYSTEM_PROMPT,
                prompt=prompt,
            )
            text = getattr(response, "completion_text", "") if response else ""
            parsed = parse_model_result(text, provider=provider_name)
            if parsed.get("status") == "completed":
                return parsed
            last = parsed
            if attempt >= len(MODEL_RETRY_DELAYS):
                break
        except Exception as exc:
            last = {
                "provider": provider_name,
                "status": "unavailable",
                "tags": [],
                "evidence": [],
                "raw_response": "",
                "error": f"{type(exc).__name__}: {exc}"[:240],
            }
            if attempt >= len(MODEL_RETRY_DELAYS):
                break
    return last


__all__ = [
    "MODEL_SYSTEM_PROMPT",
    "DEFAULT_ASTRBOT_PROVIDER_ID",
    "CURRENT_DIALOGUE_MODEL_ID",
    "CURRENT_DIALOGUE_MODEL_NAME",
    "ASTRBOT_MODEL_NAME",
    "LEGACY_MODEL_COUNT",
    "REQUIRED_MODEL_COUNT",
    "build_chart_prompt",
    "call_context_model",
    "compare_model_results",
    "is_accepted_model_review",
    "parse_model_result",
]
