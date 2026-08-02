from __future__ import annotations

"""Build auditable chart metadata with the Codex conversation model.

The module is intentionally offline.  It never resolves a provider, sends a
prompt to AstrBot, or exposes a background model job.  Codex reviews the
deterministic feature evidence in this conversation and this module persists
that review together with the exact local chart source.
"""

import hashlib
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ... import Root
from .constants import (
    ALLOWED_TAGS,
    DIFFICULTY_NAMES,
    TAG_CATEGORIES,
    TAG_RULE_VERSION,
    TAG_WEIGHTS,
)
from .local.maidata_parser import MaidataChart, NoteEvent, parse_maidata
from .rule_tags import filter_allowed_tags, select_final_tags
from .storage import write_json_atomic
from .local.structure_tagger import analyze_chart_tags, extract_features

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_LEVELS_PATH = "static/Levels"
MIN_DS = 12.6
SAMPLE_SIZE = 100
SAMPLE_SEED = 2026080202
PROGRESS_INTERVAL_SECONDS = 300

DATASET_FILE = Root / "static" / "chart_tag_dataset.jsonl"
SAMPLE_MANIFEST_FILE = Root / "static" / "chart_tag_sample_manifest.json"
PROGRESS_FILE = Root / "static" / "chart_tag_progress.json"
REVIEW_FILE = Root / "static" / "chart_tag_review.json"
REPORT_FILE = Root / "CHART_TAG_REPORT.md"

REFERENCE_SOURCES = [
    "https://www.bilibili.com/opus/978826006029664264",
    "https://www.bilibili.com/opus/912886214932037655",
    "https://www.bilibili.com/opus/29693067423444838",
    "https://w.atwiki.jp/simai/pages/1002.html",
]
PRE_ENTRY_SECONDS = -0.05
HARD_END_SECONDS = 0.15
POST_ENTRY_SECONDS = 0.20
MIN_EFFECTIVE_DELTA_SECONDS = 1e-6

TAG_WINDOW_HINTS: dict[str, tuple[str, ...]] = {
    "双押": ("/", "*"),
    "管子": ("h[",),
    "留尾": ("[",),
    "定位": ("-", ">", "<", "V", "v"),
    "飞手": ("/", "-", ">", "<"),
    "协调": ("/", "-", ">", "<"),
    "交互": (),
    "轴交互": (),
    "爬梯交互": (),
    "纵连": (),
    "死镰": ("-", ">", "<"),
    "如龙": ("-", ">", "<"),
    "撞尾": (),
}


def now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _round(value: Any, digits: int = 6) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def resolve_levels_directory(raw: str | Path | None = None) -> Path:
    value = str(raw or DEFAULT_LEVELS_PATH).strip()
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


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    text = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _event_payload(index: int, event: NoteEvent) -> dict[str, Any]:
    return {
        "index": index,
        "time": _round(event.time),
        "kind": event.kind,
        "buttons": list(event.buttons),
        "shape": event.shape,
        "duration": _round(event.duration),
        "is_break": bool(event.is_break),
        "is_ex": bool(event.is_ex),
        "raw": event.raw,
        "bpm": _round(event.bpm, 3),
        "path": list(event.path),
    }


def _event_groups(events: list[tuple[int, NoteEvent]]) -> list[tuple[float, list[tuple[int, NoteEvent]]]]:
    groups: dict[int, list[tuple[int, NoteEvent]]] = {}
    for index, event in sorted(events, key=lambda item: item[1].time):
        groups.setdefault(round(float(event.time) * 1000), []).append((index, event))
    return [(stamp / 1000.0, group) for stamp, group in sorted(groups.items())]


def _window_payload(chart: MaidataChart, limit: int = 10) -> list[dict[str, Any]]:
    indexed = list(enumerate(sorted(chart.events, key=lambda item: item.time)))
    if not indexed:
        return []
    candidates: list[tuple[float, float, list[tuple[int, NoteEvent]], float]] = []
    for _, anchor in indexed:
        bpm = float(anchor.bpm or chart.bpm or 120.0)
        duration = 960.0 / max(bpm, 1.0)
        selected = [(index, event) for index, event in indexed if anchor.time <= event.time < anchor.time + duration]
        if len(selected) < 4:
            continue
        groups = _event_groups(selected)
        multi = sum(1 for _, group in groups if len(group) >= 2)
        holds = sum(1 for _, event in selected if event.kind == "hold")
        slides = sum(1 for _, event in selected if event.kind == "slide")
        score = len(groups) + multi * 3.0 + holds * 2.0 + slides * 1.2
        candidates.append((score, anchor.time, selected, duration))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[tuple[float, float, list[tuple[int, NoteEvent]], float]] = []
    for item in candidates:
        if any(abs(item[1] - old[1]) < 0.2 for old in chosen):
            continue
        chosen.append(item)
        if len(chosen) >= limit:
            break

    result: list[dict[str, Any]] = []
    for window_id, (score, start, selected, duration) in enumerate(chosen, start=1):
        bpm = float(selected[0][1].bpm or chart.bpm or 120.0)
        tokens: list[str] = []
        for timestamp, group in _event_groups(selected):
            beat = (timestamp - start) * bpm / 60.0
            tokens.append(f"{beat:.3f}:{'/'.join(event.raw for _, event in group if event.raw)}")
        result.append({
            "id": window_id,
            "start": _round(start, 3),
            "end": _round(start + duration, 3),
            "bpm": _round(bpm, 3),
            "event_indexes": [index for index, _ in selected],
            "event_count": len(selected),
            "onset_count": len(_event_groups(selected)),
            "score": _round(score, 3),
            "sequence": "; ".join(tokens)[:1200],
        })
    return result


def _bpm_segments(chart: MaidataChart) -> list[dict[str, Any]]:
    events = sorted(chart.events, key=lambda item: item.time)
    segments: list[dict[str, Any]] = []
    for event in events:
        bpm = float(event.bpm or chart.bpm or 120.0)
        if segments and abs(float(segments[-1]["bpm"]) - bpm) < 1e-6:
            segments[-1]["end"] = _round(event.time)
            continue
        segments.append({"start": _round(event.time), "end": _round(event.time), "bpm": _round(bpm, 3)})
    return segments


def _slide_collision_candidates(chart: MaidataChart) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    excluded_ex: list[dict[str, Any]] = []
    target_kinds = {"tap", "break", "hold"}
    for slide_index, slide in enumerate(chart.events):
        if slide.kind != "slide" or slide.duration <= 0:
            continue
        path = tuple(str(value) for value in (slide.path or slide.buttons) if str(value).isdigit())
        if len(path) < 2:
            continue
        for path_index, area in enumerate(path[1:], start=1):
            ratio = path_index / max(len(path) - 1, 1)
            passed_time = float(slide.time) + ratio * float(slide.duration)
            is_last = path_index == len(path) - 1
            slide_end = float(slide.time) + float(slide.duration)
            upper = POST_ENTRY_SECONDS
            if is_last:
                upper = max(upper, slide_end - passed_time + POST_ENTRY_SECONDS)
            for target_index, target in enumerate(chart.events):
                if target_index == slide_index or target.kind not in target_kinds:
                    continue
                if not is_last or target.kind == "slide":
                    continue
                if len(path) < 3:
                    continue
                target_area = str(target.buttons[0]) if target.buttons else ""
                if target_area != area:
                    continue
                delta = float(target.time) - passed_time
                if not PRE_ENTRY_SECONDS <= delta <= upper:
                    continue
                # Intermediate path areas are useful evidence only for a
                # multi-area slide.  A one-step Slide crossing a Hold head is
                # normal chart grammar, not automatically 撞尾 evidence.
                if target.kind == "hold" and delta < 0.0:
                    continue
                candidate = {
                    "candidate_id": f"s{slide_index}:p{path_index}:t{target_index}",
                    "slide_event_index": slide_index,
                    "slide_raw": slide.raw,
                    "slide_start": _round(slide.time),
                    "slide_duration": _round(slide.duration),
                    "slide_path": list(path),
                    "path_index": path_index,
                    "area": area,
                    "area_role": "last" if is_last else "intermediate",
                    "time_ratio": _round(ratio),
                    "passed_time": _round(passed_time),
                    "target_event_index": target_index,
                    "target_kind": target.kind,
                    "target_raw": target.raw,
                    "target_time": _round(target.time),
                    "delta": _round(delta),
                    "target_is_ex": bool(target.is_ex),
                    "path_length": len(path),
                    "timing_class": (
                        "absolute" if abs(delta) < MIN_EFFECTIVE_DELTA_SECONDS
                        else "hard" if 0.0 < delta <= HARD_END_SECONDS
                        else "soft"
                    ),
                    "special_context": {
                        "is_last_area": is_last,
                        "slide_ends_at": _round(slide_end),
                        "slide_shape": slide.shape,
                        "slide_anchor_count": len(slide.buttons),
                        "slide_is_break": bool(slide.is_break),
                    },
                }
                if target.is_ex:
                    excluded_ex.append({
                        "candidate_id": candidate["candidate_id"],
                        "slide_raw": candidate["slide_raw"],
                        "area": area,
                        "target_raw": candidate["target_raw"],
                        "target_time": candidate["target_time"],
                        "delta": candidate["delta"],
                        "reason": "目标原始语法含 x 的 Ex 音符不计入撞尾",
                    })
                else:
                    candidates.append(candidate)
    return candidates, excluded_ex


def _review_collision(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep repeated or hard timing conflicts and reject isolated soft edges."""
    hard = [item for item in candidates if item.get("timing_class") in {"absolute", "hard"}]
    soft = [item for item in candidates if item.get("timing_class") == "soft"]
    hard_slides = {item.get("slide_event_index") for item in hard}
    soft_slides = {item.get("slide_event_index") for item in soft}
    # One slide can create several area candidates.  Repetition must be on
    # separate Slides so a normal path is not promoted by its own geometry.
    hard_positive = [item for item in hard if item.get("timing_class") == "hard"]
    if len(hard) >= 3 and len(hard_slides) >= 3 and len(hard_positive) >= 2:
        return hard
    soft_times = sorted(float(item.get("target_time", 0.0) or 0.0) for item in soft)
    separated_soft = sum(
        1 for index in range(1, len(soft_times)) if soft_times[index] - soft_times[index - 1] >= 0.25
    )
    if len(soft) >= 5 and len(soft_slides) >= 4 and separated_soft >= 3:
        return soft
    return []


def build_chart_prompt_payload(path: Path, chart: MaidataChart) -> dict[str, Any]:
    candidates, excluded_ex = _slide_collision_candidates(chart)
    features = extract_features(chart)
    return {
        "source_file": path.name,
        "source_path": _relative_path(path),
        "song_id": path.name.split("_", 1)[0],
        "title": path.stem.split("_", 1)[1] if "_" in path.stem else path.stem,
        "difficulty_id": chart.diff_id,
        "level_index": chart.level_index,
        "ds": chart.ds,
        "designer": chart.designer,
        "bpm": chart.bpm,
        "features": {key: _round(value, 6) for key, value in features.items() if isinstance(value, (int, float))},
        "two_measure_windows": _window_payload(chart),
        "collision_candidate_count": len(candidates),
        "collision_candidates": candidates,
        "collision_exclusions": excluded_ex,
        "collision_rule": {
            "pre_entry_seconds": PRE_ENTRY_SECONDS,
            "hard_end_seconds": HARD_END_SECONDS,
            "post_entry_seconds": POST_ENTRY_SECONDS,
            "last_area_extends_to_slide_end": True,
            "ex_target_excluded": True,
        },
        "reference_sources": REFERENCE_SOURCES,
    }


def collect_eligible_chart_refs(directory: str | Path = DEFAULT_LEVELS_PATH, min_ds: float = MIN_DS) -> list[dict[str, Any]]:
    levels_dir = resolve_levels_directory(directory)
    refs: list[dict[str, Any]] = []
    for path in sorted(levels_dir.glob("*.txt")):
        if not path.is_file():
            continue
        try:
            song = parse_maidata(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        song_id = song.short_id or path.name.split("_", 1)[0]
        for chart in song.charts.values():
            if float(chart.ds or 0.0) < min_ds:
                continue
            refs.append({
                "key": f"{song_id}:{chart.level_index}",
                "path": str(path.resolve()),
                "source_path": _relative_path(path),
                "file": path.name,
                "title": song.title or path.stem,
                "artist": song.artist,
                "level_index": chart.level_index,
                "diff_id": chart.diff_id,
                "difficulty": DIFFICULTY_NAMES.get(chart.level_index, str(chart.level_index)),
                "ds": float(chart.ds),
                "bpm": float(chart.bpm or song.whole_bpm or 0.0),
                "whole_bpm": float(song.whole_bpm or chart.bpm or 0.0),
                "designer": chart.designer,
            })
    return refs


def create_sample_manifest(
    directory: str | Path = DEFAULT_LEVELS_PATH,
    min_ds: float = MIN_DS,
    sample_size: int = SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
) -> dict[str, Any]:
    refs = collect_eligible_chart_refs(directory, min_ds)
    if len(refs) < sample_size:
        raise ValueError(f"有效难度不足 {sample_size}: {len(refs)}")
    selected = random.Random(seed).sample(refs, sample_size)
    manifest = {
        "manifest_version": 3,
        "analysis_engine": "codex_conversation_model",
        "call_mode": "in_conversation",
        "created_at": now(),
        "directory": str(Path(directory).as_posix()),
        "resolved_directory": str(resolve_levels_directory(directory)),
        "min_ds": min_ds,
        "eligible_pool_count": len(refs),
        "sample_size_requested": sample_size,
        "sample_size_selected": len(selected),
        "random_seed": seed,
        "force_recompute": True,
        "progress_interval_seconds": PROGRESS_INTERVAL_SECONDS,
        "charts": selected,
        "reference_sources": REFERENCE_SOURCES,
    }
    write_json_atomic(SAMPLE_MANIFEST_FILE, manifest)
    return manifest


def _chart_from_ref(ref: dict[str, Any]) -> MaidataChart:
    path = Path(str(ref["path"]))
    song = parse_maidata(path.read_text(encoding="utf-8-sig"))
    chart = song.charts.get(int(ref["level_index"]))
    if chart is None:
        raise ValueError(f"谱面难度不存在: {ref.get('key')}")
    return chart


def _window_matches_tag(window: dict[str, Any], tag: str) -> bool:
    sequence = str(window.get("sequence", ""))
    hints = TAG_WINDOW_HINTS.get(tag, ())
    return bool(hints and any(hint in sequence for hint in hints))


def _pure_tap_onsets(chart: MaidataChart) -> list[tuple[float, str, float]]:
    groups: dict[int, tuple[list[str], float]] = {}
    for event in chart.events:
        if event.kind not in {"tap", "break"} or len(event.buttons) != 1:
            continue
        button = str(event.buttons[0])
        if not button.isdigit():
            continue
        stamp = round(float(event.time) * 1000)
        if stamp not in groups:
            groups[stamp] = ([], float(event.bpm or chart.bpm or 120.0))
        groups[stamp][0].append(event.raw)
    return [
        (stamp / 1000.0, "/".join(raws), bpm)
        for stamp, (raws, bpm) in sorted(groups.items())
    ]


def _strict_rhythm_windows(chart: MaidataChart) -> list[dict[str, Any]]:
    """Find local swing/shuffle or dotted runs, excluding generic uneven IOI."""
    points = _pure_tap_onsets(chart)
    if len(points) < 10:
        return []
    result: list[dict[str, Any]] = []
    for start in range(len(points) - 9):
        sample = points[start:start + 10]
        beat = 60.0 / max(float(sample[0][2] or chart.bpm or 120.0), 1.0)
        two_measures = 480.0 / max(float(sample[0][2] or chart.bpm or 120.0), 1.0)
        if sample[-1][0] - sample[0][0] > two_measures:
            continue
        intervals = [sample[index + 1][0] - sample[index][0] for index in range(9)]
        if any(value <= 0 for value in intervals):
            continue
        swing_pairs = 0
        dotted_pairs = 0
        for index in range(0, 8, 2):
            left, right = intervals[index], intervals[index + 1]
            ratio = max(left, right) / min(left, right)
            pair_sum = left + right
            if 1.6 <= ratio <= 2.6 and 0.72 * beat <= pair_sum <= 1.28 * beat:
                swing_pairs += 1
            if 1.7 <= ratio <= 3.4 and 0.72 * beat <= pair_sum <= 1.32 * beat:
                dotted_pairs += 1
        if swing_pairs >= 4:
            result.append({
                "kind": "swing_shuffle",
                "start": _round(sample[0][0], 3),
                "end": _round(sample[-1][0], 3),
                "sequence": "; ".join(raw for _, raw, _ in sample),
            })
        elif dotted_pairs >= 4:
            result.append({
                "kind": "continuous_dotted",
                "start": _round(sample[0][0], 3),
                "end": _round(sample[-1][0], 3),
                "sequence": "; ".join(raw for _, raw, _ in sample),
            })
    # Overlapping starts describe one run. Keep one representative per local
    # region so raw usage reflects patterns, not window overlap.
    compact: list[dict[str, Any]] = []
    for item in result:
        if compact and float(item["start"]) - float(compact[-1]["start"]) < 0.25:
            continue
        compact.append(item)
    return compact


def _strict_structure_review(chart: MaidataChart, structure: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Apply Codex review gates to rule candidates before they become labels."""
    features = structure.get("features") if isinstance(structure.get("features"), dict) else {}
    source_tags = filter_allowed_tags(structure.get("tags") or [])
    accepted = set(source_tags)
    rejected: list[str] = []
    two_measures = max(float(features.get("two_measure_sec", 2.0) or 2.0), 0.5)

    guanzi = (
        float(features.get("hold_dense_run", 0.0) or 0.0) >= two_measures
        and float(features.get("hold_dense_peak", 0.0) or 0.0) >= 0.5
        and float(features.get("hold_dense_hot", 0.0) or 0.0) >= 4
        and float(features.get("hold_count", 0.0) or 0.0) >= 16
    ) or (
        float(features.get("hold_chain_max", 0.0) or 0.0) >= 6
        and float(features.get("hold_chain_span_max", 0.0) or 0.0) >= two_measures * 0.85
        and float(features.get("short_hold_count", 0.0) or 0.0) >= 16
    )
    double = (
        float(features.get("dual_dense_run", 0.0) or 0.0) >= two_measures
        and float(features.get("dual_dense_peak", 0.0) or 0.0) >= 0.75
        and float(features.get("dual_dense_hot", 0.0) or 0.0) >= 4
    )
    rulong = (
        float(features.get("rulong_double_hits", 0.0) or 0.0) >= 2
        or (
            float(features.get("rulong_half_hits", 0.0) or 0.0) >= 6
            and float(features.get("rulong_hits", 0.0) or 0.0) >= 6
        )
    )
    ladder = float(features.get("ladder_hits", 0.0) or 0.0) >= 1
    coordination = (
        float(features.get("coord_disp", 0.0) or 0.0) >= 1
        or (
            float(features.get("short_stack_hot", 0.0) or 0.0) >= 4
            and float(features.get("short_stack_distinct_hot", 0.0) or 0.0) >= 4
        )
    )
    death = float(features.get("death_scythe_hits", 0.0) or 0.0) >= 3
    rhythm_windows = _strict_rhythm_windows(chart)
    rhythm = bool(rhythm_windows)
    gates = {
        "管子": guanzi,
        "双押": double,
        "如龙": rulong,
        "爬梯交互": ladder,
        "协调": coordination,
        "死镰": death,
        "跳拍": rhythm,
    }
    for tag, passed in gates.items():
        if tag in accepted and not passed:
            accepted.remove(tag)
            rejected.append(tag)
    # Keep 交互 as a compatibility label only when there is enough ordinary
    # alternating evidence and no specific interaction pattern explains it.
    if "交互" in accepted and (
        "轴交互" in accepted or "爬梯交互" in accepted or "协调" in accepted
    ) and float(features.get("trill", 0.0) or 0.0) < 24:
        accepted.remove("交互")
        rejected.append("交互")
    return filter_allowed_tags(accepted), filter_allowed_tags(rejected), rhythm_windows


def _tag_reason(tag: str, features: dict[str, Any]) -> str:
    reasons = {
        "管子": "连续两小节 Hold 占比或 Hold 链/异节奏证据达到局部阈值",
        "双押": "同一时间点双键组在连续两小节局部窗口占比达到 75%",
        "定位": "局部高密度、大位移或快速大跨度 Slide 形成卡手处理压力",
        "留尾": "大跨度且快速的 Slide 出张构成持续尾部处理压力",
        "死镰": "连续相邻 Tap 与方向相反的 Slide 同时成立，符合经典死镰关系",
        "如龙": "双押或半拍引导后出现同侧连续扫键，并重复构成换手压力",
        "协调": "短纵、大位移交互或难协调键型在局部重复出现",
        "轴交互": "交替序列中固定重复键形成轴结构",
        "爬梯交互": "键位按连续方向逐步扩展或收缩形成爬梯结构",
        "交互": "快速交替存在，但没有被轴、爬梯或协调结构完全解释",
        "跳拍": "仅在 Swing/Shuffle 或连续附点节奏证据成立时标记",
        "撞尾": "Slide 沿 simai 路径进入目标区域的时序冲突经重复性复核成立",
    }
    return reasons.get(tag, "局部结构特征达到该标签的判定条件")


def _tag_evidence(
    tag: str,
    chart: MaidataChart,
    windows: list[dict[str, Any]],
    features: dict[str, Any],
    accepted_collisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if tag == "撞尾":
        return [
            {
                "kind": "collision_candidate",
                "candidate_id": item["candidate_id"],
                "event_indexes": [item["slide_event_index"], item["target_event_index"]],
                "raw": f"{item['slide_raw']} -> {item['target_raw']}",
                "position": {
                    "slide_time": item["slide_start"],
                    "passed_time": item["passed_time"],
                    "target_time": item["target_time"],
                    "area": item["area"],
                    "delta": item["delta"],
                },
                "reason": f"{item['timing_class']}：{_tag_reason(tag, features)}",
            }
            for item in accepted_collisions
        ]
    matching = [window for window in windows if _window_matches_tag(window, tag)]
    if not matching:
        matching = windows[:1]
    return [
        {
            "kind": "two_measure_window",
            "window_id": window["id"],
            "event_indexes": window["event_indexes"],
            "raw": window["sequence"],
            "position": {"start": window["start"], "end": window["end"], "bpm": window["bpm"]},
            "reason": _tag_reason(tag, features),
        }
        for window in matching[:3]
    ]


def review_chart_with_codex(path: Path, ref: dict[str, Any], chart: MaidataChart) -> dict[str, Any]:
    features = extract_features(chart)
    structure = analyze_chart_tags(chart)
    strict_tags, rejected_tags, rhythm_windows = _strict_structure_review(chart, structure)
    structure["tags"] = strict_tags
    payload = build_chart_prompt_payload(path, chart)
    collisions = payload["collision_candidates"]
    accepted_collisions = _review_collision(collisions)
    scores = {
        str(tag): float(value)
        for tag, value in (structure.get("tag_scores") or {}).items()
        if tag in strict_tags and isinstance(value, (int, float))
    }
    if accepted_collisions:
        scores["撞尾"] = max(1.0, min(1.45, 0.92 + len(accepted_collisions) / 20.0))
    raw_tags = filter_allowed_tags([*(structure.get("tags") or []), *(["撞尾"] if accepted_collisions else [])])
    final_tags, tag_scores = select_final_tags(scores)
    if accepted_collisions and "撞尾" not in final_tags:
        final_tags = ["撞尾", *final_tags[:4]]
        tag_scores["撞尾"] = scores["撞尾"]
    final_tags = filter_allowed_tags(final_tags)[:5]
    windows = payload["two_measure_windows"]
    tag_positions = {
        tag: _tag_evidence(tag, chart, windows, features, accepted_collisions)
        for tag in final_tags
    }
    if "跳拍" in tag_positions and rhythm_windows:
        tag_positions["跳拍"] = [
            {
                "kind": item["kind"],
                "event_indexes": [],
                "raw": item["sequence"],
                "position": {"start": item["start"], "end": item["end"], "bpm": chart.bpm},
                "reason": _tag_reason("跳拍", features),
            }
            for item in rhythm_windows[:3]
        ]
    summary = (
        f"Codex 审阅通过：{', '.join(final_tags)}；"
        f"有效两小节窗口 {len(windows)} 个，撞尾候选 {len(accepted_collisions)} 个。"
        if final_tags
        else f"Codex 审阅完成：未发现足以成为主难点的连续标签；有效两小节窗口 {len(windows)} 个。"
    )
    raw_text = path.read_text(encoding="utf-8-sig")
    return {
        "record_version": 2,
        "record_key": ref["key"],
        "analysis_engine": "codex_conversation_model",
        "call_mode": "in_conversation",
        "model_call_status": "success",
        "analysis_status": "completed",
        "reviewed_at": now(),
        "reference_sources": REFERENCE_SOURCES,
        "source": {
            "file": ref["file"],
            "path": ref["source_path"],
            "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "difficulty_id": ref["diff_id"],
            "level_index": ref["level_index"],
            "difficulty": ref["difficulty"],
            "title": ref["title"],
            "artist": ref["artist"],
            "designer": ref["designer"],
            "ds": ref["ds"],
            "bpm": ref["bpm"],
            "whole_bpm": ref["whole_bpm"],
        },
        "chart": {
            "inote": chart.raw,
            "events": [_event_payload(index, event) for index, event in enumerate(chart.events)],
            "bpm_segments": _bpm_segments(chart),
        },
        "features": {key: _round(value, 6) for key, value in features.items()},
        "two_measure_windows": windows,
        "collision": {
            "rule": payload["collision_rule"],
            "candidates": collisions,
            "excluded_ex": payload["collision_exclusions"],
            "accepted_candidate_ids": [item["candidate_id"] for item in accepted_collisions],
        },
        "raw_tags": raw_tags,
        "final_tags": final_tags,
        "tag_scores": {tag: _round(tag_scores.get(tag, scores.get(tag, 0.0)), 6) for tag in final_tags},
        "tag_positions": tag_positions,
        "summary": summary,
        "codex_review": {
            "accepted_structure_tags": strict_tags,
            "rejected_structure_tags": rejected_tags,
            "rhythm_windows": rhythm_windows,
            "collision_candidates_reviewed": len(collisions),
            "accepted_collision_count": len(accepted_collisions),
        },
        "formal_pipeline_enabled": False,
    }


def _progress(processed: int, total: int, *, status: str, current: str = "", error: str = "") -> None:
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": "codex_conversation_model",
        "status": status,
        "processed": processed,
        "total": total,
        "succeeded": processed if not error else max(0, processed - 1),
        "failed": 1 if error else 0,
        "current": current,
        "error": error,
        "updated_at": now(),
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
    })


def build_review_tag_library(records: list[dict[str, Any]]) -> dict[str, Any]:
    charts: dict[str, Any] = {}
    for record in records:
        source = record["source"]
        key = record["record_key"]
        tags = list(record.get("final_tags") or [])
        charts[key] = {
            "song_id": key.rsplit(":", 1)[0],
            "title": source.get("title", ""),
            "difficulty": source.get("difficulty", ""),
            "level_index": source.get("level_index"),
            "ds": source.get("ds"),
            "bpm": source.get("bpm"),
            "manual_tags": [],
            "codex_raw_tags": list(record.get("raw_tags") or []),
            "codex_tags": tags,
            "final_tags": [],
            "tags": [],
            "tag_scores": record.get("tag_scores") or {},
            "tag_categories": {tag: TAG_CATEGORIES[tag] for tag in tags if tag in TAG_CATEGORIES},
            "tag_positions": record.get("tag_positions") or {},
            "tag_status": "review_pending",
            "analysis_engine": record.get("analysis_engine"),
            "analysis_status": record.get("analysis_status"),
            "source_path": source.get("path", ""),
            "source_sha256": source.get("sha256", ""),
            "summary": record.get("summary", ""),
            "updated_at": record.get("reviewed_at", now()),
        }
    return {
        "version": 2,
        "generated_at": now(),
        "analysis_engine": "codex_conversation_model",
        "formal_pipeline_enabled": False,
        "review_status": "pending",
        "min_ds": MIN_DS,
        "tag_rule_version": TAG_RULE_VERSION,
        "allowed_tags": ALLOWED_TAGS,
        "tag_weights": TAG_WEIGHTS,
        "charts": charts,
    }


def run_codex_annotation(
    directory: str | Path = DEFAULT_LEVELS_PATH,
    min_ds: float = MIN_DS,
    sample_size: int = SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
) -> dict[str, Any]:
    manifest = create_sample_manifest(directory, min_ds, sample_size, seed)
    refs = list(manifest["charts"])
    records: list[dict[str, Any]] = []
    _progress(0, len(refs), status="running")
    for index, ref in enumerate(refs, start=1):
        try:
            path = Path(ref["path"])
            chart = _chart_from_ref(ref)
            record = review_chart_with_codex(path, ref, chart)
            records.append(record)
            _progress(index, len(refs), status="running", current=ref["key"])
        except Exception as exc:
            _progress(index, len(refs), status="failed", current=ref.get("key", ""), error=f"{type(exc).__name__}: {exc}")
            raise
    if len(records) != sample_size or any(item.get("model_call_status") != "success" for item in records):
        raise RuntimeError(f"Codex 标注完整性校验失败: {len(records)} / {sample_size}")
    _write_jsonl(DATASET_FILE, records)
    # Conversation-review data is kept separate from the formal WebUI tag library.
    write_json_atomic(REVIEW_FILE, build_review_tag_library(records))
    _progress(len(records), len(records), status="completed")
    return {
        "ok": True,
        "records": len(records),
        "eligible_pool_count": manifest["eligible_pool_count"],
        "dataset": str(DATASET_FILE.relative_to(Root)),
        "manifest": str(SAMPLE_MANIFEST_FILE.relative_to(Root)),
        "review_file": str(REVIEW_FILE.relative_to(Root)),
    }


def load_codex_records(path: Path = DATASET_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def main() -> None:
    result = run_codex_annotation()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
