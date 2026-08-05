from __future__ import annotations

"""Full local chart annotation and auditable metadata generation."""

import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ... import Root
from .constants import (
    ALLOWED_TAGS,
    DIFFICULTY_CAPS,
    DIFFICULTY_NAMES,
    MAX_TAG_DS,
    MIN_TAG_DS,
    RULE_ENGINE,
    RULE_SPEC_SOURCE,
    TAG_RULE_VERSION,
)
from .local.maidata_parser import MaidataChart, NoteEvent, parse_maidata, parse_maidata_metadata
from .local.structure_tagger import _ring_distance, analyze_chart_tags
from .rule_tags import filter_allowed_tags, select_final_tags, sort_tags_by_weight, tag_weight
from .storage import write_json_atomic, write_json_gzip_atomic

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_LEVELS_PATH = "static/Levels"
DATASET_FILE = Root / "static" / "chart_tag_dataset.jsonl.gz"
MANIFEST_FILE = Root / "static" / "chart_tag_manifest.json"
PROGRESS_FILE = Root / "static" / "chart_tag_progress.json"
AUDIT_FILE = Root / "static" / "chart_tag_audit.json.gz"
REPORT_FILE = Root / "CHART_TAG_REPORT.md"
PROGRESS_INTERVAL_SECONDS = 300

# The UI uses these hints only to select a compact display window.  The real
# decision is made by structure_tagger and the stored evidence positions.
TAG_WINDOW_HINTS: dict[str, tuple[str, ...]] = {
    "节奏": (),
    "延迟星星": ("[",),
    "拆弹": ("[",),
    "管子": ("h[",),
    "定位": ("-", ">", "<", "V", "v"),
    "散打": (),
    "飞手": ("/",),
    "防蹭": ("[",),
    "留尾": ("[",),
    "爆发": (),
    "底力": (),
    "交互": (),
    "轴交互": (),
    "爬梯交互": (),
    "定拍": (),
    "双押": ("/",),
    "扫键": (),
    "死镰": ("-", ">", "<"),
    "错位": ("[",),
    "手速": (),
    "纵连": (),
    "跳拍": (),
    "如龙": ("-", ">", "<"),
    "协调": ("/", "-", ">", "<"),
    "撞尾": (),
}


def now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _round(value: Any, digits: int = 6) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def resolve_levels_directory(raw: str | Path | None = None) -> Path:
    value = str(raw or DEFAULT_LEVELS_PATH).strip()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(Root.resolve())
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


def _bpm_segments(chart: MaidataChart) -> list[dict[str, Any]]:
    events = sorted(chart.events, key=lambda item: item.time)
    if not events:
        return []
    segments: list[dict[str, Any]] = []
    for event in events:
        bpm = float(event.bpm or chart.bpm or 120.0)
        if segments and abs(float(segments[-1]["bpm"]) - bpm) < 1e-6:
            continue
        if segments:
            segments[-1]["end"] = _round(event.time)
        segments.append({"start": _round(event.time), "end": None, "bpm": _round(bpm, 3)})
    if segments:
        segments[-1]["end"] = _round(events[-1].time)
    return segments


def _window_payload(chart: MaidataChart, limit: int = 20, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    analysis = analysis or analyze_chart_tags(chart)
    result: list[dict[str, Any]] = []
    for window_id, window in enumerate(analysis.get("windows") or [], start=1):
        item = dict(window)
        item["id"] = window_id
        result.append(item)
    return result[:limit]


def _slide_collision_candidates(
    chart: MaidataChart,
    *,
    include_details: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    excluded_ex: list[dict[str, Any]] = []
    targets = {"tap", "break", "hold"}
    targets_by_area: dict[str, list[tuple[int, NoteEvent]]] = defaultdict(list)
    for target_index, target in enumerate(chart.events):
        if target.kind not in targets or not target.buttons:
            continue
        targets_by_area[str(target.buttons[0])].append((target_index, target))
    for slide_index, slide in enumerate(chart.events):
        if slide.kind != "slide" or slide.duration <= 0:
            continue
        path = tuple(str(value) for value in (slide.path or slide.buttons) if str(value).isdigit())
        if len(path) < 2:
            continue
        area = path[-1]
        slide_end = float(slide.time) + float(slide.duration)
        segment_lengths = [
            _ring_distance(path[index - 1], path[index])
            for index in range(1, len(path))
        ]
        path_length = max(sum(segment_lengths), 1)
        terminal_length = max(segment_lengths[-1], 1)
        # The terminal area starts before the nominal Slide end.  This is a
        # conservative geometry estimate for simai's final path zone; the
        # actual timing comparison remains anchored to the Slide end so the
        # absolute/hard/soft classes match the reference articles.
        terminal_ratio = min(0.50, max(0.05, terminal_length / path_length))
        terminal_start = slide_end - float(slide.duration) * terminal_ratio
        for target_index, target in targets_by_area.get(area, ()):
            if target_index == slide_index:
                continue
            delta = float(target.time) - slide_end
            if not -0.05 <= delta <= 0.20:
                continue
            if float(target.time) < terminal_start - 1e-6:
                continue
            # A negative overlap with an ordinary Hold head is regular simai
            # grammar, not a tail collision.  Ex targets remain audit-only.
            if target.kind == "hold" and delta < 0.0:
                continue
            candidate_id = f"s{slide_index}:p{len(path) - 1}:t{target_index}"
            timing_class = "absolute" if abs(delta) < 1e-6 else "hard" if 0.0 < delta <= 0.15 else "soft"
            candidate = {
                "candidate_id": candidate_id,
                "slide_event_index": slide_index,
                "target_event_index": target_index,
                "delta": _round(delta),
                "timing_class": timing_class,
            }
            if include_details:
                candidate.update({
                    "slide_raw": slide.raw,
                    "target_raw": target.raw,
                    "slide_start": _round(slide.time),
                    "slide_duration": _round(slide.duration),
                    "slide_path": list(path),
                    "terminal_start": _round(terminal_start),
                    "terminal_ratio": _round(terminal_ratio),
                    "slide_end": _round(slide_end),
                    "target_time": _round(target.time),
                    "target_kind": target.kind,
                    "area": area,
                    "target_is_ex": bool(target.is_ex),
                    "rule": "Slide末端路径区，目标相对Slide结束时间[-0.05s,+0.20s]",
                })
            if target.is_ex:
                if include_details:
                    excluded_ex.append({**candidate, "reason": "目标原始语法含 x 的 Ex 音符不计入撞尾"})
            else:
                candidates.append(candidate)
    return candidates, excluded_ex


def _review_collision(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # A single absolute/hard collision is sufficient. Soft boundaries need
    # repetition across distinct Slide events, as required by the XLS.
    accepted: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("timing_class") in {"absolute", "hard"}:
            accepted.append(candidate)
        else:
            soft.append(candidate)
    distinct_soft_slides = {candidate.get("slide_event_index") for candidate in soft}
    if len(distinct_soft_slides) >= 2:
        accepted.extend(soft)
    return accepted


def build_chart_audit_payload(path: Path, chart: MaidataChart, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    analysis = analysis or analyze_chart_tags(chart)
    candidates, excluded_ex = _slide_collision_candidates(chart)
    accepted = _review_collision(candidates)
    return {
        "source_file": path.name,
        "source_path": _relative_path(path),
        "difficulty_id": chart.diff_id,
        "level_index": chart.level_index,
        "ds": chart.ds,
        "designer": chart.designer,
        "bpm": chart.bpm,
        "features": {key: _round(value, 6) for key, value in (analysis.get("features") or {}).items() if isinstance(value, (int, float))},
        "two_measure_windows": _window_payload(chart, analysis=analysis),
        "collision_candidate_count": len(candidates),
        "collision_accepted_count": len(accepted),
        "collision_candidates": candidates,
        "collision_accepted": accepted,
        "collision_exclusions": excluded_ex,
        "collision_rule": {
            "pre_entry_seconds": -0.05,
            "post_entry_seconds": 0.20,
            "hard_post_entry_seconds": 0.15,
            "soft_requires_distinct_slides": True,
            "terminal_path_zone_estimated": True,
            "last_area_only": True,
            "ex_target_excluded": True,
        },
        "rule_engine": RULE_ENGINE,
    }


def collect_eligible_chart_refs(
    directory: str | Path = DEFAULT_LEVELS_PATH,
    min_ds: float = MIN_TAG_DS,
    max_ds: float = MAX_TAG_DS,
) -> list[dict[str, Any]]:
    levels_dir = resolve_levels_directory(directory)
    refs: list[dict[str, Any]] = []
    for path in sorted(levels_dir.glob("*.txt")):
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            song = parse_maidata_metadata(raw.decode("utf-8-sig"))
        except Exception:
            continue
        if not song.short_id or not song.title:
            continue
        if str(song.title).lstrip().startswith("[") or "宴" in str(song.meta.get("genre", "")):
            continue
        source_hash = _sha256(raw)
        for level_index, chart in sorted(song.charts.items()):
            ds = float(chart.ds or 0.0)
            if level_index not in {2, 3, 4} or not min_ds <= ds <= max_ds:
                continue
            refs.append({
                "key": f"{song.short_id}:{level_index}",
                "path": str(path.resolve()),
                "source_path": _relative_path(path),
                "file": path.name,
                "source_sha256": source_hash,
                "song_id": str(song.short_id),
                "shortid": str(song.short_id),
                "title": song.title,
                "artist": song.artist,
                "genre": song.meta.get("genre", ""),
                "version": song.version,
                "level_index": level_index,
                "diff_id": chart.diff_id,
                "difficulty": DIFFICULTY_NAMES.get(level_index, str(level_index)),
                "level": song.meta.get(f"lv_{chart.diff_id}", str(ds)),
                "ds": ds,
                "bpm": float(chart.bpm or song.whole_bpm or 0.0),
                "whole_bpm": float(song.whole_bpm or chart.bpm or 0.0),
                "designer": chart.designer,
            })
    return sorted(refs, key=lambda item: (str(item["song_id"]), int(item["level_index"])))


def _manifest_ref(ref: dict[str, Any]) -> dict[str, Any]:
    item = dict(ref)
    item["path"] = item.get("source_path", "")
    return item


def _progress(processed: int, total: int, *, status: str, task: str = "annotation", current: str = "", error: str = "", **extra: Any) -> None:
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": RULE_ENGINE,
        "task": task,
        "status": status,
        "processed": processed,
        "total": total,
        "current": current,
        "error": error,
        "updated_at": now(),
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
        **extra,
    })


def _record_summary(record: dict[str, Any]) -> str:
    source = record["source"]
    raw = "、".join(record.get("raw_tags") or []) or "无候选标签"
    final = "、".join(record.get("final_tags") or []) or "无难点标签"
    return f"{source.get('title', '')} {source.get('difficulty', '')}：候选 {raw}；最终 {final}。"


def _tag_cap(tag: str, ds: float) -> float | None:
    if tag == "错位":
        return DIFFICULTY_CAPS["错位_at_least_13_6"] if ds >= 13.6 else DIFFICULTY_CAPS["错位_below_13_6"]
    return DIFFICULTY_CAPS.get(tag)


def _apply_difficulty_caps(records: list[dict[str, Any]]) -> None:
    """Apply the XLS same-constant prevalence ceilings to final labels."""
    for record in records:
        scores = record.get("candidate_scores") if isinstance(record.get("candidate_scores"), dict) else {}
        training_tags = record.get("training_tags")
        if not isinstance(training_tags, list):
            training_tags = record.get("difficulty_tags") or []
        difficulty = filter_allowed_tags(training_tags)
        selected, selected_scores = select_final_tags({tag: scores.get(tag, tag_weight(tag)) for tag in difficulty})
        record["pre_final_tags"] = selected
        record["pre_final_scores"] = selected_scores
        record["final_tags"] = selected

    by_ds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_ds[f"{float(record['source'].get('ds', 0.0)):.1f}"].append(record)
    cap_stats: dict[str, dict[str, int]] = {}
    for group in by_ds.values():
        total = len(group)
        for tag in ALLOWED_TAGS:
            candidates = [record for record in group if tag in record.get("final_tags", [])]
            if not candidates:
                continue
            ds = float(candidates[0]["source"].get("ds", 0.0) or 0.0)
            cap = _tag_cap(tag, ds)
            if cap is None:
                continue
            allowed = math.floor(total * cap)
            candidates.sort(key=lambda item: (-float(item.get("pre_final_scores", {}).get(tag, 0.0)), str(item.get("record_key", ""))))
            removed = candidates[allowed:]
            for record in removed:
                record["final_tags"] = [value for value in record["final_tags"] if value != tag]
            cap_stats.setdefault(tag, {"groups": 0, "capped_groups": 0, "max_allowed": 0})
            cap_stats[tag]["groups"] += 1
            cap_stats[tag]["max_allowed"] = max(cap_stats[tag]["max_allowed"], allowed)
            if removed:
                cap_stats[tag]["capped_groups"] += 1
    for record in records:
        record["final_tags"] = sort_tags_by_weight(record.get("final_tags") or [], record.get("pre_final_scores") or {})[:5]
        record["tag_scores"] = {
            tag: _round((record.get("pre_final_scores") or {}).get(tag, tag_weight(tag)), 6)
            for tag in record["final_tags"]
        }
        evidence = record.get("tag_evidence") if isinstance(record.get("tag_evidence"), dict) else {}
        training_tags = filter_allowed_tags(record.get("training_tags") or [])
        weak_tags = filter_allowed_tags([
            *(record.get("raw_tags") or []),
            *(record.get("difficulty_tags") or []),
        ])
        record["training_tag_positions"] = {
            tag: evidence.get(tag, []) for tag in training_tags
        }
        record["weak_candidate_positions"] = {
            tag: evidence.get(tag, []) for tag in weak_tags
        }
        record["tag_positions"] = {tag: evidence.get(tag, []) for tag in record["final_tags"]}
        record["summary"] = _record_summary(record)
    for record in records:
        record["difficulty_cap_stats"] = cap_stats


def _event_count(chart: MaidataChart) -> dict[str, int]:
    counts = {"tap": 0, "hold": 0, "slide": 0, "touch": 0, "break": 0}
    for event in chart.events:
        if event.kind in counts:
            counts[event.kind] += 1
    counts["total"] = sum(counts.values())
    return counts


def _make_record(ref: dict[str, Any], raw: str, chart: MaidataChart) -> dict[str, Any]:
    analysis = analyze_chart_tags(chart)
    payload = build_chart_audit_payload(Path(ref["path"]), chart, analysis)
    candidates = payload["collision_candidates"]
    accepted = payload["collision_accepted"]
    excluded_ex = payload["collision_exclusions"]
    accepted_ids = [item["candidate_id"] for item in accepted]
    raw_tags = filter_allowed_tags(analysis.get("raw_tags") or [])
    difficulty_tags = filter_allowed_tags(analysis.get("difficulty_tags") or [])
    candidate_scores = dict(analysis.get("candidate_scores") or {})
    difficulty_scores = dict(analysis.get("difficulty_scores") or {})
    tag_evidence = dict(analysis.get("tag_evidence") or {})
    if accepted:
        raw_tags = filter_allowed_tags([*raw_tags, "撞尾"])
        difficulty_tags = filter_allowed_tags([*difficulty_tags, "撞尾"])
        candidate_scores["撞尾"] = max(float(candidate_scores.get("撞尾", 0.0) or 0.0), 1.0)
        difficulty_scores["撞尾"] = max(float(difficulty_scores.get("撞尾", 0.0) or 0.0), 1.0)
        tag_evidence["撞尾"] = [
            {
                "kind": "slide_collision",
                "event_indexes": [item.get("slide_event_index"), item.get("target_event_index")],
                "raw": f"{item.get('slide_raw', '')} -> {item.get('target_raw', '')}",
                "position": {
                    "slide_time": item.get("slide_start"),
                    "terminal_start": item.get("terminal_start"),
                    "target_time": item.get("target_time"),
                    "delta": item.get("delta"),
                    "timing_class": item.get("timing_class"),
                },
                "reason": "通过绝对/硬撞尾或多个不同 Slide 的软撞尾复核",
            }
            for item in accepted[:3]
        ]
    return {
        "record_version": 5,
        "record_key": ref["key"],
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "analysis_status": "completed",
        "source": {
            **{key: ref.get(key) for key in ("file", "source_path", "source_sha256", "song_id", "shortid", "title", "artist", "genre", "version", "difficulty", "level_index", "diff_id", "level", "designer", "ds", "bpm", "whole_bpm")},
        },
        "mapping": {
            "mapping_version": 3,
            "mapping_type": "runtime_levels_to_model",
            "source_file": ref.get("source_path", ""),
            "source_file_name": ref.get("file", ""),
            "source_sha256": ref.get("source_sha256", ""),
            "shortid": ref.get("shortid", ref.get("song_id", "")),
            "diff_id": ref.get("diff_id"),
            "level_index": ref.get("level_index"),
            "chart_section": f"inote_{ref.get('diff_id', '')}",
            "chart_info": {key: ref.get(key) for key in ("title", "artist", "genre", "version", "level", "ds", "bpm", "whole_bpm", "designer")},
        },
        "chart": {
            "inote": chart.raw,
            "events": [_event_payload(index, event) for index, event in enumerate(chart.events)],
            "bpm_segments": _bpm_segments(chart),
            "note_counts": _event_count(chart),
        },
        "features": {key: _round(value, 6) for key, value in (analysis.get("features") or {}).items() if isinstance(value, (int, float))},
        "two_measure_windows": payload["two_measure_windows"],
        "collision": {
            "rule": payload["collision_rule"],
            "candidates": candidates,
            "excluded_ex": excluded_ex,
            "accepted_candidate_ids": accepted_ids,
        },
        "raw_tags": raw_tags,
        "difficulty_tags": difficulty_tags,
        "candidate_scores": candidate_scores,
        "difficulty_scores": difficulty_scores,
        "tag_evidence": tag_evidence,
        "training_tag_positions": {},
        "weak_candidate_positions": {},
        "tag_positions": {},
        "final_tags": [],
        "tag_scores": {},
        "summary": "",
        "rule_version": TAG_RULE_VERSION,
    }


def _write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(temp, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temp.replace(path)


def _usage(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts = {tag: 0 for tag in ALLOWED_TAGS}
    for record in records:
        for tag in record.get(field) or []:
            if tag in counts:
                counts[tag] += 1
    total = max(len(records), 1)
    return [{"tag": tag, "count": counts[tag], "rate": round(counts[tag] / total, 6)} for tag in ALLOWED_TAGS]


def build_report(records: list[dict[str, Any]], training: dict[str, Any]) -> str:
    raw_usage = _usage(records, "raw_tags")
    final_usage = _usage(records, "final_tags")
    final_by_tag = {row["tag"]: row for row in final_usage}
    lines = [
        "# 谱面标签全量重算与本地模型报告",
        "",
        f"- 生成时间：{now()}",
        f"- 规则来源：`{RULE_SPEC_SOURCE}`；规则版本：`{TAG_RULE_VERSION}`；分析引擎：`{RULE_ENGINE}`",
        f"- 范围：`{MIN_TAG_DS:.1f}-{MAX_TAG_DS:.1f}`，Expert / Master / Re:Master；完整记录：{len(records)}",
        f"- 数据集：`{DATASET_FILE.relative_to(Root)}`；每条记录含完整 `inote`、事件、BPM 段、两小节窗口、撞尾候选和标签位置",
        "- 说明：候选标签按 XLS 候选特征生成；最终标签按难点特征、最多 5 个标签及同定数占比上限筛选。",
        "",
        "## 标签使用率",
        "",
        "| 标签 | 原始次数 | 原始使用率 | 最终次数 | 最终使用率 |",
        "|:--|--:|--:|--:|--:|",
    ]
    for row in raw_usage:
        final = final_by_tag[row["tag"]]
        lines.append(f"| {row['tag']} | {row['count']} | {row['rate']:.2%} | {final['count']} | {final['rate']:.2%} |")
    lines.extend([
        "",
        "## 难点灵敏度",
        "",
        "| 标签 | 同定数占比上限 |",
        "|:--|--:|",
    ])
    for tag, cap in DIFFICULTY_CAPS.items():
        label = tag.replace("_below_13_6", "（定数<13.6）").replace("_at_least_13_6", "（定数≥13.6）")
        lines.append(f"| {label} | {cap:.0%} |")
    lines.extend([
        "",
        "## 逐谱面标注",
        "",
        "| # | Key | 定数 | BPM | 候选标签 | 难点标签 | 最终标签 | 撞尾证据 |",
        "|--:|:--|--:|--:|:--|:--|:--|--:|",
    ])
    for index, record in enumerate(records, start=1):
        source = record["source"]
        collision_count = len((record.get("collision") or {}).get("accepted_candidate_ids") or [])
        lines.append(
            f"| {index} | `{record['record_key']}` {source.get('title', '')} {source.get('difficulty', '')} | "
            f"{float(source.get('ds', 0.0)):.1f} | {float(source.get('bpm', 0.0)):.1f} | "
            f"{'、'.join(record.get('raw_tags') or []) or '无'} | "
            f"{'、'.join(record.get('difficulty_tags') or []) or '无'} | "
            f"{'、'.join(record.get('final_tags') or []) or '无'} | {collision_count} |")
    lines.extend([
        "",
        "## 训练结果",
        "",
        f"- 模型：`{training['model_file']}`；元数据：`{training['model_meta']}`；Loss：`{training['loss_file']}`。",
        f"- 训练/验证：{training['train_records']} / {training['valid_records']}；特征数：{training['feature_count']}；最佳 epoch：{training['best_epoch']}；最佳验证 Loss：{training['best_valid_loss']}",
        "- 模型为本地多标签分类器；运行时只读取 Levels 对应难度并调用本地模型。",
        "",
        "## 数据文件",
        "",
        f"- 全量清单：`{MANIFEST_FILE.relative_to(Root)}`",
        f"- 全量审计：`{AUDIT_FILE.relative_to(Root)}`",
        "- 运行时映射：由谱面文件 `shortid_title.txt`、`inote_<diff_id>`、文件 SHA-256 和定数实时构建，不持久化谱面标签库。",
    ])
    return "\n".join(lines) + "\n"


def run_full_annotation(directory: str | Path = DEFAULT_LEVELS_PATH) -> dict[str, Any]:
    refs = collect_eligible_chart_refs(directory)
    if not refs:
        raise ValueError("没有找到 12.6-15.0 的有效谱面")
    resolved_directory = resolve_levels_directory(directory)
    manifest = {
        "manifest_version": 4,
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "created_at": now(),
        "directory": _relative_path(resolved_directory),
        "resolved_directory": _relative_path(resolved_directory),
        "min_ds": MIN_TAG_DS,
        "max_ds": MAX_TAG_DS,
        "eligible_pool_count": len(refs),
        "charts": [_manifest_ref(ref) for ref in refs],
        "progress_interval_seconds": PROGRESS_INTERVAL_SECONDS,
        "reference_sources": ["maimai.xls", "https://w.atwiki.jp/simai/pages/1002.html"],
    }
    write_json_atomic(MANIFEST_FILE, manifest)
    _progress(0, len(refs), status="running", current="开始全量解析")
    records: list[dict[str, Any]] = []
    for index, ref in enumerate(refs, start=1):
        path = Path(ref["path"])
        raw = path.read_text(encoding="utf-8-sig")
        song = parse_maidata(raw)
        chart = song.charts.get(int(ref["level_index"]))
        if chart is None:
            raise ValueError(f"谱面难度不存在: {ref['key']}")
        records.append(_make_record(ref, raw, chart))
        _progress(index, len(refs), status="running", current=ref["key"], raw_tags=sum(len(item.get("raw_tags") or []) for item in records), final_tags=sum(len(item.get("final_tags") or []) for item in records))
    _apply_difficulty_caps(records)
    _write_jsonl_atomic(DATASET_FILE, records)
    write_json_gzip_atomic(AUDIT_FILE, {
        "version": 1,
        "generated_at": now(),
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "record_count": len(records),
        "raw_usage": _usage(records, "raw_tags"),
        "final_usage": _usage(records, "final_tags"),
        "records": records,
    })
    _progress(len(records), len(records), status="completed", current="标签重算完成", raw_tags=sum(len(item.get("raw_tags") or []) for item in records), final_tags=sum(len(item.get("final_tags") or []) for item in records))
    return {"ok": True, "records": len(records), "dataset": str(DATASET_FILE.relative_to(Root)), "manifest": str(MANIFEST_FILE.relative_to(Root)), "audit": str(AUDIT_FILE.relative_to(Root))}


def load_local_records(path: Path = DATASET_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


__all__ = [
    "AUDIT_FILE",
    "DATASET_FILE",
    "DIFFICULTY_CAPS",
    "MANIFEST_FILE",
    "MAX_TAG_DS",
    "MIN_TAG_DS",
    "PROGRESS_FILE",
    "REPORT_FILE",
    "TAG_WINDOW_HINTS",
    "_review_collision",
    "_slide_collision_candidates",
    "_window_payload",
    "build_chart_audit_payload",
    "build_report",
    "collect_eligible_chart_refs",
    "load_local_records",
    "now",
    "run_full_annotation",
]
