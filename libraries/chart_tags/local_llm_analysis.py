from __future__ import annotations

"""Use the AstrBot conversation model to review local Levels charts.

The parser supplies compact, two-measure windows so the model can judge the
player-facing definitions without sending an entire maidata file as prompt.
Only tags from ``ALLOWED_TAGS`` are accepted and the raw model result is kept
with the chart for later review.
"""

import asyncio
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ... import Root, log
from ..roast.llm_client import resolve_roast_provider_id
from .constants import ALLOWED_TAGS, TAG_CATEGORIES, TAG_RULE_VERSION, TAG_WEIGHTS
from .local.maidata_parser import MaidataChart, NoteEvent, parse_maidata
from .local.pipeline import DEFAULT_LEVELS_DIR, iter_levels_files
from .rule_tags import filter_allowed_tags, select_final_tags, tag_weight
from .storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic

CN_TZ = timezone(timedelta(hours=8))
LOCAL_LLM_STATE_FILE = Root / "static" / "maimai_levels_llm_job.json"
LOCAL_LLM_SAMPLE_MANIFEST_FILE = Root / "static" / "chart_tag_llm_sample_manifest.json"
LOCAL_LLM_RULE_VERSION = 3
DEFAULT_RELATIVE_PATH = "static/Levels"
MAX_WINDOWS = 8
MAX_SEQUENCE_CHARS = 850
DEFAULT_MAX_CONCURRENT_MODEL_CALLS = 4
DEFAULT_MODEL_TIMEOUT_SECONDS = 150
MAX_MODEL_TAGS = 5
ZHUANGWEI_THRESHOLD_SECONDS = 0.2

ANALYSIS_SYSTEM_PROMPT = """你是 maimai DX 谱面结构分析器。你只能根据输入的谱面结构摘要判断标签，不能凭歌曲名、定数或泛化印象猜测。

必须遵守以下定义和打标原则：
- 所有局部配置必须在连续至少两小节窗口内成立；按窗口内实际 BPM 折算两小节时长。变速时按窗口起始 BPM 分段。
- 标签表示这张谱面的主要难点，而不是“谱面里出现过这种配置”。只有当配置在多个窗口反复出现、占据明显游玩时间，或直接构成谱面的核心处理压力时才打标；单个孤立配置、短暂点缀、普通过渡和仅由歌曲名/定数推断的配置都不能打标。
- 一张谱面最多返回 5 个主要难点，按重要性排序；没有足够证据的标签宁可不返回。手速、爆发、底力、节奏、交互、定位等泛化标签只有在它们本身是谱面核心难点时才返回，不能因为存在高速音符、交替、位移或节奏变化就自动返回。
- 双押：同一时刻两键组成一组；两小节内同时击 onset 占比至少 75%。
- 管子：只指 Hold，不是 Slide；短 Hold 高密、Hold 间隔极短，或连续 Hold 长度/间隔不一造成节奏型怪异，且需连续覆盖两小节并成为主要处理压力。
- 定位：短时间高密、大位移、卡手；快速且跨度大的 Slide 也可归入定位，但必须是持续性核心难点，不是一次大跨度移动。
- 留尾：只指 Slide 出张跨度大、速度快且容易卡手的尾部/出张，不表示普通 Hold 重叠；需要在谱面中反复或长段构成主要难点。
- 跳拍：只识别 Swing/Shuffle 或连续附点；普通切分、连续切分和重音错觉不算。
- 死镰：连续约 3~4 个相邻 Tap（含星头）与对向 Slide 同时处理，且 Slide 启动方向与 Tap 迭代方向相反。
- 如龙：双押或隔半拍引导换手的同侧扫；普通曲线 Slide 不能单独触发。
- 协调：难协调键型，包括大位移交互、二纵/三纵和连续短纵；可与定位、交互并存。
- 轴交互：快速交替中固定重复的轴键，例如 a,b,a,c,a,b,a,c。
- 爬梯交互：键位沿连续方向逐步扩展或收缩，允许旋转和镜像。
- 普通交互：没有轴、爬梯或协调特征的快速交替；细分标签成立时仍可保留交互。
- 拆谱是协调的旧别名；模型输出协调，不输出拆谱。

仅输出一个 JSON 对象，不要 Markdown；`tags` 只能包含 0 至 5 个主要难点标签：
{"tags":["允许标签"],"confidence":0.0,"summary":"一句话依据","evidence":[{"window":"窗口编号或时间","tags":["允许标签"],"reason":"具体键位/节奏证据"}]}

允许标签：""" + ", ".join(ALLOWED_TAGS)

ZHUANGWEI_SYSTEM_PROMPT = """你是 maimai DX 谱面中的“撞尾”专项判定器。只判断是否存在“撞尾”，不得输出其它标签。

定义：Slide 按 simai/Maidata 语法从起点沿实际经过的区域移动。对每个经过区域，计算：
经过时间 = Slide 开始时间 + time 比例 × Slide 持续时间。
若 Tap、Hold 或其它 Slide 的起点与该经过区域相同，且 0 < |目标时间 - 经过时间| < 0.2 秒，则构成撞尾。时间差等于 0 不算；等于 0.2 秒不算。目标 Tap 的原始语法含 x（Ex，例如 1x）时不算。Slide 自身的起点不作为自己的目标；普通 Tap、Break、Hold 和其它 Slide 起点可以作为目标。

输入中的 zhuangwei_candidates 已按谱面书写语法解析了 Slide 的经过区域和时间，必须只根据这些候选及原始谱面结构判断，不能凭歌曲名、定数或印象猜测。候选不成立时返回空标签。只输出严格 JSON，不要 Markdown：
{"tags":[],"confidence":0.0,"summary":"一句话依据","evidence":[{"candidate_id":"候选编号","tags":[],"reason":"说明Slide经过区域、目标音符、时间差和是否排除Ex"}]}
其中 tags 只能是 [] 或 ["撞尾"]，evidence 中只引用实际成立的候选编号。"""


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _read_state() -> dict[str, Any]:
    if not LOCAL_LLM_STATE_FILE.exists():
        return {}
    try:
        value = json.loads(LOCAL_LLM_STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict[str, Any]) -> None:
    write_json_atomic(LOCAL_LLM_STATE_FILE, state)


def resolve_levels_directory(raw: str | Path | None = None) -> Path:
    """Resolve a user path relative to the plugin root and prevent escape."""
    value = str(raw or DEFAULT_RELATIVE_PATH).strip()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Root / candidate
    candidate = candidate.resolve()
    root = Root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("谱面目录必须位于插件根目录内") from exc
    if not candidate.is_dir():
        raise FileNotFoundError(f"谱面目录不存在: {candidate}")
    return candidate


def _event_group(events: list[NoteEvent]) -> list[tuple[float, list[NoteEvent]]]:
    groups: dict[int, list[NoteEvent]] = {}
    for event in sorted(events, key=lambda item: item.time):
        groups.setdefault(round(event.time * 1000), []).append(event)
    return [(stamp / 1000.0, group) for stamp, group in sorted(groups.items())]


def _button_distance(a: str, b: str) -> int:
    try:
        distance = abs(int(a) - int(b)) % 8
    except (TypeError, ValueError):
        return 0
    return min(distance, 8 - distance)


def _window_score(events: list[NoteEvent]) -> float:
    groups = _event_group(events)
    multi = sum(1 for _, group in groups if len({str(b) for event in group for b in event.buttons if str(b).isdigit()}) >= 2)
    holds = sum(1 for event in events if event.kind == "hold")
    slides = sum(1 for event in events if event.kind == "slide")
    jumps = 0
    singles = [event for event in events if event.kind in {"tap", "break"} and len(event.buttons) == 1]
    for previous, current in zip(singles, singles[1:]):
        if 0 < current.time - previous.time <= 0.24 and _button_distance(previous.buttons[0], current.buttons[0]) >= 3:
            jumps += 1
    return len(groups) + multi * 3.0 + holds * 2.0 + slides * 1.2 + jumps * 2.0


def _window_payload(chart: MaidataChart) -> list[dict[str, Any]]:
    events = sorted(chart.events, key=lambda item: item.time)
    if not events:
        return []
    candidates: list[tuple[float, float, list[NoteEvent]]] = []
    for event in events:
        bpm = float(event.bpm or chart.bpm or 120.0)
        # simai 的四拍为一小节；模型窗口固定覆盖连续两小节（八拍）。
        window = 960.0 / max(bpm, 1.0)
        selected = [item for item in events if event.time <= item.time < event.time + window]
        if len(selected) >= 4:
            candidates.append((_window_score(selected), event.time, selected))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[tuple[float, float, list[NoteEvent]]] = []
    for score, start, selected in candidates:
        if any(abs(start - old_start) < max((960.0 / max(float(selected[0].bpm or chart.bpm or 120.0), 1.0)) * 0.45, 0.05) for _, old_start, _ in chosen):
            continue
        chosen.append((score, start, selected))
        if len(chosen) >= MAX_WINDOWS:
            break

    result: list[dict[str, Any]] = []
    for index, (score, start, selected) in enumerate(chosen, start=1):
        bpm = float(selected[0].bpm or chart.bpm or 120.0)
        end = start + 960.0 / max(bpm, 1.0)
        groups = _event_group(selected)
        tokens: list[str] = []
        for time_value, group in groups:
            relative_beat = (time_value - start) * bpm / 60.0
            raw = "/".join(event.raw for event in group if event.raw)
            tokens.append(f"{relative_beat:.2f}:{raw}")
        sequence = "; ".join(tokens)
        result.append({
            "id": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "bpm": round(bpm, 3),
            "event_count": len(selected),
            "onset_count": len(groups),
            "score": round(score, 3),
            "sequence": sequence[:MAX_SEQUENCE_CHARS],
        })
    return result


def build_chart_prompt_payload(path: Path, chart: MaidataChart) -> dict[str, Any]:
    from .local.structure_tagger import extract_features

    features = extract_features(chart)
    candidates = _zhuangwei_candidates(chart)
    return {
        "source_file": path.name,
        "song_id": path.name.split("_", 1)[0],
        "title": path.stem.split("_", 1)[1] if "_" in path.stem else path.stem,
        "difficulty_id": chart.diff_id,
        "level_index": chart.level_index,
        "ds": chart.ds,
        "designer": chart.designer,
        "whole_bpm": chart.bpm,
        "features": {
            key: round(float(value), 4) for key, value in features.items()
            if isinstance(value, (int, float))
        },
        "two_measure_windows": _window_payload(chart),
        "zhuangwei_candidate_count": len(candidates),
        "zhuangwei_candidates": candidates,
    }


def _zhuangwei_candidates(chart: MaidataChart) -> list[dict[str, Any]]:
    """Enumerate strict, auditable Slide-path collisions for the LLM to review."""
    candidates: list[dict[str, Any]] = []
    target_kinds = {"tap", "break", "hold", "slide"}
    for slide_index, slide in enumerate(chart.events):
        if slide.kind != "slide" or slide.duration <= 0:
            continue
        path = tuple(str(value) for value in (slide.path or slide.buttons) if str(value).isdigit())
        if len(path) < 2:
            continue
        for path_index, area in enumerate(path[1:], start=1):
            ratio = path_index / max(len(path) - 1, 1)
            pass_time = float(slide.time) + ratio * float(slide.duration)
            for target_index, target in enumerate(chart.events):
                if target_index == slide_index or target.kind not in target_kinds:
                    continue
                target_area = str(target.buttons[0]) if target.buttons else ""
                if target_area != area or target.is_ex:
                    continue
                delta = abs(float(target.time) - pass_time)
                if not (0.0 < delta < ZHUANGWEI_THRESHOLD_SECONDS):
                    continue
                candidates.append({
                    "candidate_id": f"s{slide_index}:p{path_index}:t{target_index}",
                    "slide_event_index": slide_index,
                    "slide_raw": slide.raw,
                    "slide_start": round(float(slide.time), 6),
                    "slide_duration": round(float(slide.duration), 6),
                    "slide_path": list(path),
                    "path_index": path_index,
                    "area": area,
                    "time_ratio": round(ratio, 6),
                    "passed_time": round(pass_time, 6),
                    "target_event_index": target_index,
                    "target_kind": target.kind,
                    "target_raw": target.raw,
                    "target_time": round(float(target.time), 6),
                    "delta": round(delta, 6),
                    "target_is_ex": bool(target.is_ex),
                })
    return candidates


def _extract_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", value)
        if not match:
            raise ValueError("模型没有返回 JSON")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    return parsed


def _normalize_result(raw: dict[str, Any], target_tag: str | None = None) -> dict[str, Any]:
    tags = filter_allowed_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else [])
    if target_tag:
        tags = [target_tag] if target_tag in tags else []
    tags = tags[:MAX_MODEL_TAGS]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
    clean_evidence = []
    for item in evidence[:8]:
        if not isinstance(item, dict):
            continue
        clean_evidence.append({
            "window": str(item.get("window", ""))[:80],
            "candidate_id": str(item.get("candidate_id", item.get("candidate", "")))[:120],
            "tags": filter_allowed_tags(item.get("tags") if isinstance(item.get("tags"), list) else tags),
            "reason": str(item.get("reason", ""))[:500],
        })
        if target_tag:
            clean_evidence[-1]["tags"] = [target_tag] if target_tag in clean_evidence[-1]["tags"] else []
    return {
        "tags": tags,
        "confidence": round(confidence, 4),
        "summary": str(raw.get("summary", ""))[:800],
        "evidence": clean_evidence,
    }


class LocalLLMAnalysisJob:
    def __init__(self, context: Any | None = None, config: dict | None = None):
        self.context = context
        self.config = config or {}
        self.task: asyncio.Task | None = None
        self.stop_requested = False
        try:
            configured_concurrency = int(self.config.get("chart_tag_llm_concurrency", DEFAULT_MAX_CONCURRENT_MODEL_CALLS))
        except (TypeError, ValueError):
            configured_concurrency = DEFAULT_MAX_CONCURRENT_MODEL_CALLS
        self.max_concurrent_model_calls = max(1, min(configured_concurrency, 8))
        self.model_semaphore = asyncio.Semaphore(self.max_concurrent_model_calls)

    def status(self) -> dict[str, Any]:
        state = _read_state()
        state.setdefault("running", bool(self.task and not self.task.done()))
        state.setdefault("directory", DEFAULT_RELATIVE_PATH)
        state.setdefault("min_ds", 12.6)
        state.setdefault("processed", 0)
        state.setdefault("failed", 0)
        state.setdefault("charts_total", 0)
        state.setdefault("charts_updated", 0)
        state.setdefault("skipped", 0)
        state.setdefault("model_calls", 0)
        state.setdefault("files_ok", 0)
        state.setdefault("files_failed", 0)
        state["running"] = bool(self.task and not self.task.done())
        state["stop_requested"] = self.stop_requested
        state["state_path"] = str(LOCAL_LLM_STATE_FILE)
        state["tag_path"] = str(CHART_TAGS_FILE)
        return state

    async def start(
        self,
        *,
        directory: str = DEFAULT_RELATIVE_PATH,
        min_ds: float = 12.6,
        limit: int | None = None,
        chart_limit: int | None = None,
        sample_size: int | None = None,
        random_seed: int | None = None,
        target_tag: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if self.task and not self.task.done():
            return {"ok": True, "message": "本地谱面模型分析已在运行", **self.status()}
        levels_dir = resolve_levels_directory(directory)
        files = iter_levels_files(levels_dir)
        if limit is not None and limit > 0:
            files = files[:limit]
        selected_keys: set[str] | None = None
        sample_manifest: dict[str, Any] | None = None
        if sample_size is not None and sample_size > 0:
            candidates = self._collect_eligible_chart_refs(files, min_ds=min_ds)
            seed = int(random_seed if random_seed is not None else datetime.now(CN_TZ).timestamp())
            rng = random.Random(seed)
            selected = rng.sample(candidates, min(sample_size, len(candidates)))
            selected_keys = {str(item["key"]) for item in selected}
            files = [path for path in files if any(item["path"] == str(path) for item in selected)]
            sample_manifest = {
                "manifest_version": 1,
                "created_at": _now(),
                "directory": str(Path(directory).as_posix()),
                "resolved_directory": str(levels_dir),
                "min_ds": min_ds,
                "sample_size_requested": sample_size,
                "sample_size_selected": len(selected),
                "random_seed": seed,
                "force": force,
                "charts": selected,
            }
            write_json_atomic(LOCAL_LLM_SAMPLE_MANIFEST_FILE, sample_manifest)
        self.stop_requested = False
        state = {
            "running": True,
            "directory": str(Path(directory).as_posix()),
            "resolved_directory": str(levels_dir),
            "min_ds": min_ds,
            "chart_limit": chart_limit,
            "sample_size": sample_size,
            "sample_size_selected": len(selected_keys) if selected_keys is not None else 0,
            "random_seed": sample_manifest.get("random_seed") if sample_manifest else None,
            "target_tag": target_tag or "",
            "force": force,
            "files_total": len(files),
            "charts_total": 0,
            "charts_updated": 0,
            "skipped": 0,
            "model_calls": 0,
            "files_ok": 0,
            "files_failed": 0,
            "processed": 0,
            "failed": 0,
            "current_file": "",
            "current_chart": "",
            "last_error": "",
            "started_at": _now(),
            "completed_at": "",
            "message": "本地谱面模型分析已启动",
        }
        _write_state(state)
        self.task = asyncio.create_task(
            self._run(files, min_ds=min_ds, chart_limit=chart_limit, selected_keys=selected_keys, target_tag=target_tag, force=force),
            name="maimai-levels-llm",
        )
        return {"ok": True, "message": state["message"], **self.status()}

    async def stop(self) -> dict[str, Any]:
        self.stop_requested = True
        state = _read_state()
        state.update({"stop_requested": True, "message": "已请求停止，当前模型调用完成后停止"})
        _write_state(state)
        return {"ok": True, **self.status()}

    async def shutdown(self) -> None:
        self.stop_requested = True
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    async def _call_model(self, payload: dict[str, Any], target_tag: str | None = None) -> dict[str, Any]:
        if self.context is None or not hasattr(self.context, "llm_generate"):
            raise RuntimeError("当前插件运行环境没有可用的 AstrBot 对话模型")
        configured = str(self.config.get("chart_tag_llm_provider_id", "") or "").strip()
        if not configured:
            configured = str(self.config.get("roast_b50_provider_id", "") or "").strip()
        provider_id = configured or await resolve_roast_provider_id(self.context, self.config)
        system_prompt = ZHUANGWEI_SYSTEM_PROMPT if target_tag == "撞尾" else ANALYSIS_SYSTEM_PROMPT
        prompt_payload = payload
        if target_tag == "撞尾":
            prompt_payload = dict(payload)
            prompt_payload["zhuangwei_candidates"] = payload.get("zhuangwei_candidates", [])[:160]
        prompt = "请分析下面一张 maimai 谱面，只按 system 定义判断，返回严格 JSON。\n谱面结构：\n" + json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":"))
        try:
            timeout_seconds = int(self.config.get("chart_tag_llm_timeout_seconds", DEFAULT_MODEL_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_MODEL_TIMEOUT_SECONDS
        timeout_seconds = max(5, min(timeout_seconds, 600))
        async with self.model_semaphore:
            try:
                response = await asyncio.wait_for(
                    self.context.llm_generate(
                        chat_provider_id=provider_id,
                        system_prompt=system_prompt,
                        prompt=prompt,
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"模型调用超过 {timeout_seconds} 秒") from exc
        return _normalize_result(_extract_json(getattr(response, "completion_text", "") if response else ""), target_tag=target_tag)

    def _save_result(self, payload: dict[str, Any], result: dict[str, Any], path: Path, target_tag: str | None = None) -> None:
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        if not isinstance(charts, dict):
            charts = {}
        key = f"{payload['song_id']}:{payload['level_index']}"
        chart = charts.get(key) if isinstance(charts.get(key), dict) else {
            "song_id": payload["song_id"],
            "title": payload["title"],
            "level_index": payload["level_index"],
            "ds": payload["ds"],
            "bpm": payload["whole_bpm"],
            "manual_tags": [],
        }
        tags = result["tags"]
        scores = {tag: round(tag_weight(tag) * max(result["confidence"], 0.35), 4) for tag in tags}
        manual = [] if target_tag else filter_allowed_tags(chart.get("manual_tags", []))
        final_tags, tag_scores = select_final_tags(scores, manual)
        chart.update({
            "llm_tags": tags,
            "local_tags": tags,
            "local_tag_scores": scores,
            "local_confidence": result["confidence"],
            "local_source": "levels_llm",
            "local_source_path": str(path),
            "local_llm_rule_version": LOCAL_LLM_RULE_VERSION,
            "llm_analysis": result,
            "local_features": payload.get("features", {}),
            "zhuangwei_candidates": payload.get("zhuangwei_candidates", []),
            "analysis_target_tag": target_tag or "",
            "final_tags": final_tags,
            "tags": final_tags,
            "tag_scores": tag_scores,
            "tag_categories": {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES},
            "tag_status": "done",
            "tag_error": "",
            "tag_rule_version": max(int(chart.get("tag_rule_version", 0) or 0), TAG_RULE_VERSION),
            "updated_at": _now(),
        })
        charts[key] = chart
        data.update({
            "charts": charts,
            "tag_rule_version": TAG_RULE_VERSION,
            "allowed_tags": ALLOWED_TAGS,
            "tag_weights": TAG_WEIGHTS,
            "updated_at": _now(),
            "local_tag_engine": {
                "name": "levels_llm",
                "source_directory": str(path.parent),
                "min_ds": 12.6,
                "target_tag": target_tag or "",
                "updated_at": _now(),
            },
        })
        write_json_atomic(CHART_TAGS_FILE, data)

    def _save_failure(self, payload: dict[str, Any], path: Path, error: BaseException, target_tag: str | None = None) -> None:
        """Keep an auditable per-sample failure so retries cannot hide coverage gaps."""
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        if not isinstance(charts, dict):
            charts = {}
        key = f"{payload['song_id']}:{payload['level_index']}"
        charts[key] = {
            "song_id": payload["song_id"],
            "title": payload["title"],
            "level_index": payload["level_index"],
            "ds": payload["ds"],
            "bpm": payload["whole_bpm"],
            "manual_tags": [],
            "llm_tags": [],
            "local_tags": [],
            "final_tags": [],
            "tags": [],
            "local_confidence": 0.0,
            "local_source": "levels_llm",
            "local_source_path": str(path),
            "analysis_target_tag": target_tag or "",
            "analysis_status": "unavailable",
            "analysis_error": f"{type(error).__name__}: {error}"[:1000],
            "zhuangwei_candidates": payload.get("zhuangwei_candidates", []),
            "llm_analysis": {
                "tags": [],
                "confidence": 0.0,
                "summary": "模型调用失败，等待断点重试",
                "evidence": [],
            },
            "tag_status": "failed",
            "updated_at": _now(),
        }
        data.update({
            "charts": charts,
            "tag_rule_version": TAG_RULE_VERSION,
            "allowed_tags": ALLOWED_TAGS,
            "tag_weights": TAG_WEIGHTS,
            "updated_at": _now(),
        })
        write_json_atomic(CHART_TAGS_FILE, data)

    @staticmethod
    def _collect_eligible_chart_refs(files: list[Path], *, min_ds: float) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for path in files:
            try:
                song = parse_maidata(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for chart in song.charts.values():
                if chart.ds < min_ds:
                    continue
                refs.append({
                    "key": f"{song.short_id or path.name.split('_', 1)[0]}:{chart.level_index}",
                    "path": str(path),
                    "file": path.name,
                    "title": song.title or path.stem,
                    "level_index": chart.level_index,
                    "diff_id": chart.diff_id,
                    "ds": chart.ds,
                    "bpm": chart.bpm or song.whole_bpm,
                })
        return refs

    def _prepare_file(
        self,
        path: Path,
        *,
        min_ds: float,
        selected_keys: set[str] | None,
        force: bool,
    ) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8-sig")
        song = parse_maidata(text)
        pending: list[dict[str, Any]] = []
        skipped = 0
        eligible = 0
        for chart in song.charts.values():
            if chart.ds < min_ds:
                continue
            key = f"{song.short_id or path.name.split('_', 1)[0]}:{chart.level_index}"
            if selected_keys is not None and key not in selected_keys:
                continue
            eligible += 1
            existing = read_chart_tags().get("charts", {}).get(key, {})
            if not force and isinstance(existing, dict) and existing.get("local_llm_rule_version") == LOCAL_LLM_RULE_VERSION and existing.get("tag_status") == "done":
                skipped += 1
                continue
            pending.append(build_chart_prompt_payload(path, chart))
        return {"eligible": eligible, "skipped": skipped, "pending": pending}

    async def _run(
        self,
        files: list[Path],
        *,
        min_ds: float,
        chart_limit: int | None,
        selected_keys: set[str] | None,
        target_tag: str | None,
        force: bool,
    ) -> None:
        state = _read_state()
        batch_size = self.max_concurrent_model_calls
        selected_charts = 0
        try:
            for offset in range(0, len(files), batch_size):
                if self.stop_requested:
                    break
                batch = files[offset : offset + batch_size]
                state.update({
                    "current_file": ", ".join(path.name for path in batch),
                    "current_chart": "",
                    "message": f"正在解析文件批次（{len(batch)} 个）",
                })
                _write_state(state)

                prepared = await asyncio.gather(
                    *(asyncio.to_thread(self._prepare_file, path, min_ds=min_ds, selected_keys=selected_keys, force=force) for path in batch),
                    return_exceptions=True,
                )
                pending: list[tuple[Path, dict[str, Any]]] = []
                for path, result in zip(batch, prepared):
                    if isinstance(result, BaseException):
                        if isinstance(result, asyncio.CancelledError):
                            raise result
                        state["files_failed"] = int(state.get("files_failed", 0) or 0) + 1
                        state["last_error"] = f"{path.name} {type(result).__name__}: {result}"
                        log.error(f"本地谱面文件解析失败: {state['last_error']}")
                        continue
                    state["files_ok"] = int(state.get("files_ok", 0) or 0) + 1
                    file_pending = list(result.get("pending", []))
                    if chart_limit is None:
                        state["charts_total"] = int(state.get("charts_total", 0) or 0) + int(result.get("eligible", 0) or 0)
                        state["skipped"] = int(state.get("skipped", 0) or 0) + int(result.get("skipped", 0) or 0)
                        state["processed"] = int(state.get("processed", 0) or 0) + int(result.get("skipped", 0) or 0)
                    else:
                        remaining = max(chart_limit - selected_charts, 0)
                        file_pending = file_pending[:remaining]
                        selected_charts += len(file_pending)
                        state["charts_total"] = int(state.get("charts_total", 0) or 0) + len(file_pending)
                    pending.extend((path, payload) for payload in file_pending)

                if pending and not self.stop_requested:
                    state["model_calls"] = int(state.get("model_calls", 0) or 0) + len(pending)
                    first_path, first_payload = pending[0]
                    state.update({
                        "current_chart": f"{first_path.name} · {first_payload['level_index']}（并发 {len(pending)} 个）",
                        "message": f"正在并发调用 AstrBot 对话模型（上限 {self.max_concurrent_model_calls}）",
                    })
                    _write_state(state)
                    results = await asyncio.gather(
                        *(self._call_model(payload, target_tag=target_tag) for _, payload in pending),
                        return_exceptions=True,
                    )
                    for (path, payload), result in zip(pending, results):
                        try:
                            if isinstance(result, BaseException):
                                raise result
                            self._save_result(payload, result, path, target_tag=target_tag)
                            state["charts_updated"] = int(state.get("charts_updated", 0) or 0) + 1
                        except Exception as exc:
                            try:
                                self._save_failure(payload, path, exc, target_tag=target_tag)
                            except Exception as save_exc:
                                log.error(f"保存本地谱面失败审计记录失败: {type(save_exc).__name__}: {save_exc}")
                            state["failed"] = int(state.get("failed", 0) or 0) + 1
                            state["last_error"] = f"{path.name}:{payload['level_index']} {type(exc).__name__}: {exc}"
                            log.error(f"本地谱面模型分析失败: {state['last_error']}")
                        state["processed"] = int(state.get("processed", 0) or 0) + 1
                        _write_state(state)
                _write_state(state)
                if chart_limit is not None and selected_charts >= chart_limit:
                    break
            state.update({"running": False, "completed_at": _now(), "current_file": "", "current_chart": "", "message": "本地谱面模型分析完成" if not self.stop_requested else "本地谱面模型分析已停止"})
        except asyncio.CancelledError:
            state.update({"running": False, "completed_at": _now(), "message": "本地谱面模型分析已取消"})
            _write_state(state)
            raise
        except Exception as exc:
            state.update({"running": False, "completed_at": _now(), "last_error": f"{type(exc).__name__}: {exc}", "message": "本地谱面模型分析失败"})
            log.exception("本地谱面模型分析任务失败")
        finally:
            _write_state(state)
