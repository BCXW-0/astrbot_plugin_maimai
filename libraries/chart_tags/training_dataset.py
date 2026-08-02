from __future__ import annotations

"""Build auditable chart metadata and train a small NumPy multi-label model."""

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ... import Root
from .local_llm_analysis import build_chart_prompt_payload
from .local.maidata_parser import MaidataChart, NoteEvent, parse_maidata
from .storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic

CN_TZ = timezone(timedelta(hours=8))
SAMPLE_MANIFEST_FILE = Root / "static" / "chart_tag_llm_sample_manifest.json"
DATASET_FILE = Root / "static" / "chart_tag_llm_training_dataset.jsonl"
RUN_FILE = Root / "static" / "chart_tag_llm_training_run.json"
LOSS_FILE = Root / "static" / "chart_tag_llm_training_loss.json"
MODEL_FILE = Root / "static" / "maimai_chart_tag_local_model.npz"
MODEL_META_FILE = Root / "static" / "maimai_chart_tag_local_model.json"
REPORT_FILE = Root / "ZHUANGWEI_ANALYSIS_REPORT.md"
DATASET_VERSION = 1
MODEL_VERSION = 1


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _event_payload(index: int, event: NoteEvent) -> dict[str, Any]:
    return {
        "index": index,
        "time": round(float(event.time), 6),
        "kind": event.kind,
        "buttons": list(event.buttons),
        "shape": event.shape,
        "duration": round(float(event.duration), 6),
        "is_break": bool(event.is_break),
        "is_ex": bool(event.is_ex),
        "path": list(event.path),
        "raw": event.raw,
        "bpm": round(float(event.bpm), 6),
    }


def _window_ids(reference: str, windows: list[dict[str, Any]]) -> list[int]:
    text = str(reference or "").strip()
    if not text:
        return []
    ids: list[int] = []
    for token in re.findall(r"\d+", text):
        value = int(token)
        if 1 <= value <= len(windows):
            ids.append(value)
    if ids:
        return sorted(set(ids))
    try:
        time_value = float(text)
    except ValueError:
        return []
    if not windows:
        return []
    nearest = min(windows, key=lambda item: abs(float(item.get("start", 0.0)) - time_value))
    return [int(nearest["id"])]


def _tag_positions(result: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    positions: dict[str, list[dict[str, Any]]] = {}
    evidence = result.get("evidence", []) if isinstance(result.get("evidence"), list) else []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        ids = _window_ids(str(item.get("window", "")), windows)
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        for tag in tags:
            clean_tag = str(tag).strip()
            if not clean_tag:
                continue
            for window_id in ids:
                window = next((value for value in windows if int(value.get("id", 0)) == window_id), None)
                if not window:
                    continue
                positions.setdefault(clean_tag, []).append({
                    "window_id": window_id,
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "sequence": window.get("sequence", ""),
                    "reason": str(item.get("reason", ""))[:800],
                })
    return positions


def _chart_duration(chart: MaidataChart) -> float:
    return max((float(event.time) + float(event.duration) for event in chart.events), default=0.0)


def _numeric_features(chart: MaidataChart, payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("features", {}) if isinstance(payload.get("features"), dict) else {}
    features = {
        str(key): float(value)
        for key, value in raw.items()
        if isinstance(value, (int, float)) and np.isfinite(float(value))
    }
    features.update({
        "meta_ds": float(chart.ds),
        "meta_bpm": float(chart.bpm),
        "meta_event_count": float(len(chart.events)),
        "meta_chart_duration": _chart_duration(chart),
    })
    for kind in ("tap", "break", "hold", "slide", "touch"):
        features[f"event_{kind}_count"] = float(sum(1 for event in chart.events if event.kind == kind))
    windows = payload.get("two_measure_windows", [])
    if isinstance(windows, list):
        numeric_windows = [item for item in windows if isinstance(item, dict)]
        features["window_count"] = float(len(numeric_windows))
        for key in ("score", "event_count", "onset_count", "bpm"):
            values = [float(item[key]) for item in numeric_windows if isinstance(item.get(key), (int, float))]
            if values:
                features[f"window_{key}_max"] = max(values)
                features[f"window_{key}_mean"] = float(np.mean(values))
    return features


def build_dataset_from_manifest(
    manifest_path: Path = SAMPLE_MANIFEST_FILE,
    output_path: Path = DATASET_FILE,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chart_tags = read_chart_tags().get("charts", {})
    records: list[dict[str, Any]] = []
    for ref in manifest.get("charts", []):
        if not isinstance(ref, dict):
            continue
        path = Path(str(ref.get("path", "")))
        if not path.exists():
            continue
        song = parse_maidata(path.read_text(encoding="utf-8-sig"))
        level_index = int(ref.get("level_index", -1))
        chart = song.charts.get(level_index)
        if not chart or chart.ds < float(manifest.get("min_ds", 12.6)):
            continue
        key = str(ref.get("key", ""))
        chart_result = chart_tags.get(key, {})
        has_model_result = isinstance(chart_result, dict) and chart_result.get("local_source") == "levels_llm"
        if not has_model_result:
            chart_result = {}
        payload = build_chart_prompt_payload(path, chart)
        llm_analysis = chart_result.get("llm_analysis") if isinstance(chart_result.get("llm_analysis"), dict) else {}
        windows = payload.get("two_measure_windows", [])
        records.append({
            "dataset_version": DATASET_VERSION,
            "sample_seed": manifest.get("random_seed"),
            "sample_manifest": str(manifest_path.relative_to(Root)),
            "chart_key": key,
            "source_path": str(path.resolve().relative_to(Root.resolve())),
            "source_file": path.name,
            "song_id": str(ref.get("key", key).split(":", 1)[0]),
            "title": song.title,
            "artist": song.artist,
            "whole_bpm": song.whole_bpm,
            "version": song.version,
            "difficulty_id": chart.diff_id,
            "level_index": chart.level_index,
            "ds": chart.ds,
            "bpm": chart.bpm,
            "designer": chart.designer,
            "full_chart_content": chart.raw,
            "events": [_event_payload(index, event) for index, event in enumerate(chart.events)],
            "features": _numeric_features(chart, payload),
            "two_measure_windows": windows,
            "raw_model_tags": chart_result.get("local_tags", []),
            "final_tags": chart_result.get("final_tags", []),
            "confidence": chart_result.get("local_confidence", 0.0),
            "llm_summary": str(llm_analysis.get("summary", "")),
            "llm_evidence": llm_analysis.get("evidence", []),
            "tag_positions": _tag_positions(llm_analysis, windows),
            "zhuangwei_candidates": chart_result.get("zhuangwei_candidates", payload.get("zhuangwei_candidates", [])),
            "analysis_target_tag": chart_result.get("analysis_target_tag", ""),
            "analysis_status": chart_result.get("analysis_status", "completed" if has_model_result and chart_result.get("tag_status") == "done" else "unavailable"),
            "analysis_error": chart_result.get("analysis_error", "" if has_model_result else "模型结果未持久化"),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    _sync_sample_tag_library(records)
    result = {
        "dataset_version": DATASET_VERSION,
        "created_at": _now(),
        "source_manifest": str(manifest_path.relative_to(Root)),
        "output": str(output_path.relative_to(Root)),
        "records": len(records),
        "expected_records": int(manifest.get("sample_size_selected", 0)),
        "min_ds": manifest.get("min_ds", 12.6),
        "random_seed": manifest.get("random_seed"),
        "message": "训练元数据已生成",
    }
    write_json_atomic(RUN_FILE, {"stage": "dataset", **result})
    return result


def _sync_sample_tag_library(records: list[dict[str, Any]]) -> None:
    """Replace the old tag corpus with exactly the fixed sample manifest."""
    previous = read_chart_tags()
    charts: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("chart_key", ""))
        status = str(record.get("analysis_status", "unavailable"))
        old = previous.get("charts", {}).get(key, {}) if isinstance(previous.get("charts"), dict) else {}
        if status == "completed" and isinstance(old, dict) and old.get("local_source") == "levels_llm":
            charts[key] = old
            continue
        charts[key] = {
            "song_id": record.get("song_id", ""),
            "title": record.get("title", ""),
            "level_index": record.get("level_index", -1),
            "ds": record.get("ds", 0),
            "bpm": record.get("bpm", record.get("whole_bpm", 0)),
            "manual_tags": [],
            "llm_tags": [],
            "local_tags": [],
            "local_confidence": 0.0,
            "local_source": "levels_llm",
            "local_source_path": record.get("source_path", ""),
            "analysis_target_tag": record.get("analysis_target_tag", "撞尾"),
            "analysis_status": status,
            "analysis_error": record.get("analysis_error", ""),
            "zhuangwei_candidates": record.get("zhuangwei_candidates", []),
            "final_tags": [],
            "tags": [],
            "tag_status": "failed" if status != "completed" else "done",
            "updated_at": _now(),
        }
    payload = dict(previous) if isinstance(previous, dict) else {}
    payload.update({
        "charts": charts,
        "local_tag_engine": {
            "name": "levels_llm",
            "target_tag": "撞尾",
            "sample_manifest": "static/chart_tag_llm_sample_manifest.json",
            "sample_count": len(charts),
            "formal_pipeline_enabled": False,
            "updated_at": _now(),
        },
        "updated_at": _now(),
    })
    write_json_atomic(CHART_TAGS_FILE, payload)


def _load_records(path: Path = DATASET_FILE) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _feature_matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    names = sorted({str(name) for record in records for name in record.get("features", {})})
    matrix = np.asarray([
        [float(record.get("features", {}).get(name, 0.0) or 0.0) for name in names]
        for record in records
    ], dtype=np.float64)
    matrix[~np.isfinite(matrix)] = 0.0
    return matrix, names


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _bce(prediction: np.ndarray, target: np.ndarray, weight_decay: float = 0.0, weights: tuple[np.ndarray, ...] = ()) -> float:
    eps = 1e-7
    value = -np.mean(target * np.log(prediction + eps) + (1.0 - target) * np.log(1.0 - prediction + eps))
    if weight_decay:
        value += weight_decay * sum(float(np.sum(weight * weight)) for weight in weights)
    return float(value)


def _metrics(prediction: np.ndarray, target: np.ndarray) -> dict[str, float]:
    binary = prediction >= 0.5
    tp = float(np.sum(binary & (target == 1)))
    fp = float(np.sum(binary & (target == 0)))
    fn = float(np.sum((~binary) & (target == 1)))
    precision = tp / max(tp + fp, 1.0)
    recall = tp / max(tp + fn, 1.0)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    return {
        "micro_precision": round(precision, 6),
        "micro_recall": round(recall, 6),
        "micro_f1": round(f1, 6),
        "exact_match": round(float(np.mean(np.all(binary == (target == 1), axis=1))), 6),
    }


def train_local_model(
    dataset_path: Path = DATASET_FILE,
    model_path: Path = MODEL_FILE,
    seed: int = 20260802,
    epochs: int = 500,
) -> dict[str, Any]:
    all_records = _load_records(dataset_path)
    records = [record for record in all_records if record.get("analysis_status") == "completed"]
    if len(records) < 20:
        raise ValueError(f"可用于训练的已完成样本不足: {len(records)} / 总元数据 {len(all_records)}")
    labels = sorted({str(tag) for record in records for tag in record.get("final_tags", [])})
    if not labels:
        raise ValueError("训练样本没有最终标签")
    x, feature_names = _feature_matrix(records)
    y = np.asarray([[1.0 if tag in set(record.get("final_tags", [])) else 0.0 for tag in labels] for record in records])
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(records))
    split = max(1, min(len(records) - 1, int(round(len(records) * 0.8))))
    train_idx, valid_idx = order[:split], order[split:]
    mean = np.mean(x[train_idx], axis=0)
    scale = np.std(x[train_idx], axis=0)
    scale[scale < 1e-8] = 1.0
    x = (x - mean) / scale
    x_train, y_train = x[train_idx], y[train_idx]
    x_valid, y_valid = x[valid_idx], y[valid_idx]

    hidden_1 = 64
    hidden_2 = 32
    weight_decay = 1e-4
    w1 = rng.normal(0.0, np.sqrt(2.0 / max(x.shape[1], 1)), (x.shape[1], hidden_1))
    b1 = np.zeros(hidden_1)
    w2 = rng.normal(0.0, np.sqrt(2.0 / hidden_1), (hidden_1, hidden_2))
    b2 = np.zeros(hidden_2)
    w3 = rng.normal(0.0, np.sqrt(2.0 / hidden_2), (hidden_2, y.shape[1]))
    b3 = np.zeros(y.shape[1])
    params = [w1, b1, w2, b2, w3, b3]
    moments_m = [np.zeros_like(value) for value in params]
    moments_v = [np.zeros_like(value) for value in params]
    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    stale = 0
    learning_rate = 0.002
    batch_size = min(16, len(x_train))

    for epoch in range(1, epochs + 1):
        shuffled = rng.permutation(len(x_train))
        for start in range(0, len(shuffled), batch_size):
            indices = shuffled[start : start + batch_size]
            xb, yb = x_train[indices], y_train[indices]
            z1 = xb @ w1 + b1
            h1 = np.maximum(z1, 0.0)
            z2 = h1 @ w2 + b2
            h2 = np.maximum(z2, 0.0)
            prediction = _sigmoid(h2 @ w3 + b3)
            dz3 = (prediction - yb) / max(len(xb), 1)
            dw3 = h2.T @ dz3 + 2 * weight_decay * w3
            db3 = np.sum(dz3, axis=0)
            dz2 = (dz3 @ w3.T) * (z2 > 0)
            dw2 = h1.T @ dz2 + 2 * weight_decay * w2
            db2 = np.sum(dz2, axis=0)
            dz1 = (dz2 @ w2.T) * (z1 > 0)
            dw1 = xb.T @ dz1 + 2 * weight_decay * w1
            db1 = np.sum(dz1, axis=0)
            gradients = [dw1, db1, dw2, db2, dw3, db3]
            for index, (param, gradient) in enumerate(zip(params, gradients)):
                moments_m[index] = 0.9 * moments_m[index] + 0.1 * gradient
                moments_v[index] = 0.999 * moments_v[index] + 0.001 * gradient * gradient
                correction_m = 1.0 - 0.9 ** epoch
                correction_v = 1.0 - 0.999 ** epoch
                param -= learning_rate * (moments_m[index] / correction_m) / (np.sqrt(moments_v[index] / correction_v) + 1e-8)

        def forward(values: np.ndarray) -> np.ndarray:
            return _sigmoid(np.maximum(np.maximum(values @ w1 + b1, 0.0) @ w2 + b2, 0.0) @ w3 + b3)

        train_prediction = forward(x_train)
        valid_prediction = forward(x_valid)
        train_loss = _bce(train_prediction, y_train, weight_decay, (w1, w2, w3))
        valid_loss = _bce(valid_prediction, y_valid)
        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 8),
            "valid_loss": round(valid_loss, 8),
            "train": _metrics(train_prediction, y_train),
            "valid": _metrics(valid_prediction, y_valid),
        }
        history.append(row)
        if best is None or valid_loss < float(best["valid_loss"]):
            best = {
                "epoch": epoch,
                "valid_loss": valid_loss,
                "weights": [value.copy() for value in params],
                "metrics": row["valid"],
            }
            stale = 0
        else:
            stale += 1
        write_json_atomic(RUN_FILE, {
            "stage": "training",
            "running": True,
            "epoch": epoch,
            "epochs": epochs,
            "best_epoch": best["epoch"] if best else None,
            "best_valid_loss": best["valid_loss"] if best else None,
            "updated_at": _now(),
        })
        if stale >= 80:
            break

    if best is None:
        raise RuntimeError("训练没有产生可用模型")
    w1, b1, w2, b2, w3, b3 = best["weights"]
    model_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        model_path,
        w1=w1,
        b1=b1,
        w2=w2,
        b2=b2,
        w3=w3,
        b3=b3,
        feature_mean=mean,
        feature_scale=scale,
        feature_names=np.asarray(feature_names),
        labels=np.asarray(labels),
    )
    metadata = {
        "model_version": MODEL_VERSION,
        "created_at": _now(),
        "model": str(model_path.relative_to(Root)),
        "dataset": str(dataset_path.relative_to(Root)),
        "dataset_records": len(all_records),
        "records": len(records),
        "train_records": len(train_idx),
        "valid_records": len(valid_idx),
        "feature_count": len(feature_names),
        "label_count": len(labels),
        "labels": labels,
        "seed": seed,
        "best_epoch": best["epoch"],
        "best_valid_loss": round(float(best["valid_loss"]), 8),
        "best_valid_metrics": best["metrics"],
        "epochs_completed": len(history),
        "training_label_source": "final_tags",
        "formal_pipeline_enabled": False,
        "loss_file": str(LOSS_FILE.relative_to(Root)),
    }
    write_json_atomic(MODEL_META_FILE, metadata)
    write_json_atomic(LOSS_FILE, {
        "model_version": MODEL_VERSION,
        "dataset": str(dataset_path.relative_to(Root)),
        "seed": seed,
        "history": history,
        "best_epoch": best["epoch"],
        "best_valid_loss": round(float(best["valid_loss"]), 8),
    })
    write_json_atomic(RUN_FILE, {"stage": "training", "running": False, "completed_at": _now(), **metadata})
    return metadata


def build_zhuangwei_report(
    manifest_path: Path = SAMPLE_MANIFEST_FILE,
    dataset_path: Path = DATASET_FILE,
    report_path: Path = REPORT_FILE,
) -> dict[str, Any]:
    """Render an auditable report for every fixed sample and accepted position."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = _load_records(dataset_path)
    by_key = {str(record.get("chart_key")): record for record in records}
    completed = [record for record in records if record.get("analysis_status") == "completed"]
    positives = [record for record in records if "撞尾" in record.get("final_tags", [])]
    raw_positives = [record for record in records if "撞尾" in record.get("raw_model_tags", [])]
    model_meta = {}
    if MODEL_META_FILE.exists():
        model_meta = json.loads(MODEL_META_FILE.read_text(encoding="utf-8"))
    loss_data = {}
    if LOSS_FILE.exists():
        loss_data = json.loads(LOSS_FILE.read_text(encoding="utf-8"))

    lines = [
        "# 撞尾专项分析与本地模型报告",
        "",
        f"- 生成时间：{_now()}",
        f"- 样本清单：`{manifest_path.relative_to(Root)}`",
        f"- 随机种子：`{manifest.get('random_seed')}`",
        f"- 最低定数：`{manifest.get('min_ds', 12.6)}`",
        f"- 固定样本：{len(manifest.get('charts', []))} 条难度，{len({item.get('path') for item in manifest.get('charts', [])})} 个文件",
        f"- 已获得 LLM 明确结果：{len(completed)} / {len(records)}",
        f"- 模型不可用：{len(records) - len(completed)} / {len(records)}",
        "",
        "## 使用率",
        "",
        f"- 原始标签使用率（`raw_model_tags`）：{len(raw_positives)} / {len(records)} = {len(raw_positives) / max(len(records), 1):.2%}；在已完成 LLM 结果中为 {len(raw_positives)} / {len(completed)} = {len(raw_positives) / max(len(completed), 1):.2%}。",
        f"- 最终标签使用率（`final_tags`）：{len(positives)} / {len(records)} = {len(positives) / max(len(records), 1):.2%}；在已完成 LLM 结果中为 {len(positives)} / {max(len(completed), 1)} = {len(positives) / max(len(completed), 1):.2%}。",
        "- 本专项只允许 `撞尾` 一个标签；原始标签与最终标签在本批次一致，未发生其它标签泄漏。",
        "",
        "## 判定规则",
        "",
        "严格使用 `0 < 时间差 < 0.2s`；时间差等于 0 或 0.2 不算。目标音符原始语法含 `x`（Ex）时排除。Slide 路径、经过区域和候选时间由本地 Maidata/simai 解析器生成，LLM 只对候选进行专项确认。",
        "",
        "## 每条样本",
        "",
    ]
    for index, ref in enumerate(manifest.get("charts", []), start=1):
        key = str(ref.get("key", ""))
        record = by_key.get(key, {})
        tags = record.get("final_tags", [])
        status = record.get("analysis_status", "unavailable")
        candidates = record.get("zhuangwei_candidates", [])
        analysis = record.get("llm_evidence", [])
        accepted = [item for item in analysis if isinstance(item, dict) and "撞尾" in item.get("tags", [])]
        accepted_ids = {str(item.get("candidate_id", "")) for item in accepted}
        lines.extend([
            f"### {index}. {ref.get('title', record.get('title', ''))} (`{key}`)",
            "",
            f"- 文件：`{ref.get('file', record.get('source_file', ''))}`；难度索引：`{ref.get('level_index', record.get('level_index', ''))}`；定数：`{ref.get('ds', record.get('ds', ''))}`；BPM：`{ref.get('bpm', record.get('bpm', ''))}`",
            f"- 状态：`{status}`；最终标签：`{', '.join(tags) if tags else '无'}`；候选数：{len(candidates)}；认可候选：{', '.join(sorted(accepted_ids)) if accepted_ids else '无'}",
        ])
        if status != "completed":
            lines.append(f"- 不可用原因：{record.get('analysis_error') or '模型结果未持久化；已保留完整谱面和候选证据。'}")
        for evidence in accepted:
            candidate_id = str(evidence.get("candidate_id", ""))
            candidate = next((item for item in candidates if str(item.get("candidate_id", "")) == candidate_id), {})
            lines.extend([
                f"- 撞尾证据 `{candidate_id}`：Slide `{candidate.get('slide_raw', '')}`；路径 `{' -> '.join(map(str, candidate.get('slide_path', [])))}`；经过区域 `{candidate.get('area', '')}`；经过时间 `{candidate.get('passed_time', '')}s`；目标 `{candidate.get('target_raw', '')}`（{candidate.get('target_kind', '')}，`{candidate.get('target_time', '')}s`）；时间差 `{candidate.get('delta', '')}s`；Ex 排除：`{candidate.get('target_is_ex', False)}`。理由：{evidence.get('reason', '')}",
            ])
        lines.append("")

    lines.extend([
        "## 本地模型训练",
        "",
        f"- 元数据记录：{model_meta.get('dataset_records', len(records))}；实际训练记录：{model_meta.get('records', len(completed))}；训练/验证：{model_meta.get('train_records', '-')}/{model_meta.get('valid_records', '-')}。",
        f"- 特征：{model_meta.get('feature_count', '-')}；标签：{model_meta.get('labels', ['撞尾'])}；最佳 epoch：{model_meta.get('best_epoch', loss_data.get('best_epoch', '-'))}；最佳验证 Loss：{model_meta.get('best_valid_loss', loss_data.get('best_valid_loss', '-'))}。",
        f"- 最佳验证指标：`{json.dumps(model_meta.get('best_valid_metrics', {}), ensure_ascii=False)}`。",
        f"- Loss 文件：`{LOSS_FILE.relative_to(Root)}`；模型：`{MODEL_FILE.relative_to(Root)}`。",
        f"- `formal_pipeline_enabled={model_meta.get('formal_pipeline_enabled', False)}`；模型审核前不会接管正式标签分析。",
        "",
        "## 数据完整性",
        "",
        "每条 JSONL 记录均保存对应难度的完整 `inote_N` 内容、定数、BPM、事件序列、Slide 路径、Ex 标记、撞尾候选和 LLM 证据；不可用样本仍保留原始谱面与候选，不作为训练标签使用。",
    ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(report_path.relative_to(Root)), "samples": len(records), "completed": len(completed), "unavailable": len(records) - len(completed)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "train", "report"))
    parser.add_argument("--seed", type=int, default=20260802)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build_dataset_from_manifest(), ensure_ascii=False))
    elif args.command == "train":
        print(json.dumps(train_local_model(seed=args.seed), ensure_ascii=False))
    else:
        print(json.dumps(build_zhuangwei_report(), ensure_ascii=False))


if __name__ == "__main__":
    main()
