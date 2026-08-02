from __future__ import annotations

"""Validate chart records and train the local classifier used by WebUI auto analysis."""

import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ... import Root
from .chart_metadata import (
    REVIEW_FILE,
    DATASET_FILE,
    MIN_DS,
    PROGRESS_FILE,
    REPORT_FILE,
    SAMPLE_MANIFEST_FILE,
    load_codex_records,
    now,
    run_codex_annotation,
)
from .constants import ALLOWED_TAGS
from .storage import write_json_atomic

LOSS_FILE = Root / "static" / "chart_tag_loss.json"
RUN_FILE = Root / "static" / "chart_tag_training_run.json"
MODEL_FILE = Root / "static" / "maimai_chart_tag_model.npz"
MODEL_META_FILE = Root / "static" / "maimai_chart_tag_model.json"
TRAINING_SEED = 2026080203
EPOCHS = 240
LEARNING_RATE = 0.035
L2 = 0.0008
VALIDATION_SIZE = 20
PROGRESS_INTERVAL_SECONDS = 300


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _bce(prediction: np.ndarray, target: np.ndarray) -> float:
    clipped = np.clip(prediction, 1e-7, 1.0 - 1e-7)
    return float(-np.mean(target * np.log(clipped) + (1.0 - target) * np.log(1.0 - clipped)))


def _metrics(prediction: np.ndarray, target: np.ndarray) -> dict[str, float]:
    predicted = prediction >= 0.5
    truth = target >= 0.5
    tp = float(np.logical_and(predicted, truth).sum())
    fp = float(np.logical_and(predicted, ~truth).sum())
    fn = float(np.logical_and(~predicted, truth).sum())
    tn = float(np.logical_and(~predicted, ~truth).sum())
    precision = tp / max(tp + fp, 1.0)
    recall = tp / max(tp + fn, 1.0)
    return {
        "micro_accuracy": float((predicted == truth).mean()),
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": 2.0 * precision * recall / max(precision + recall, 1e-9),
        "positive_cells": float(truth.sum()),
        "predicted_positive_cells": float(predicted.sum()),
        "true_negative_cells": tn,
    }


def validate_records(records: list[dict[str, Any]], expected: int = 100) -> None:
    if len(records) != expected:
        raise ValueError(f"训练要求 {expected} 条完整 Codex 记录，当前为 {len(records)}")
    keys: set[str] = set()
    for record in records:
        key = str(record.get("record_key", ""))
        if not key or key in keys:
            raise ValueError(f"记录 key 缺失或重复: {key}")
        keys.add(key)
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        chart = record.get("chart") if isinstance(record.get("chart"), dict) else {}
        if record.get("analysis_status") != "completed" or record.get("model_call_status") != "success":
            raise ValueError(f"记录未完成: {key}")
        if float(source.get("ds", 0.0) or 0.0) < MIN_DS:
            raise ValueError(f"记录定数不符合要求: {key}")
        if not str(chart.get("inote", "")) or not isinstance(chart.get("events"), list):
            raise ValueError(f"记录缺少完整谱面内容: {key}")
        if not isinstance(record.get("raw_tags"), list) or not isinstance(record.get("final_tags"), list):
            raise ValueError(f"记录缺少原始/最终标签: {key}")
        if not isinstance(record.get("tag_positions"), dict) or not str(record.get("summary", "")).strip():
            raise ValueError(f"记录缺少标签位置或摘要: {key}")
        for tag in [*record["raw_tags"], *record["final_tags"]]:
            if tag not in ALLOWED_TAGS:
                raise ValueError(f"记录包含非法标签 {tag}: {key}")


def _record_features(record: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    raw = record.get("features") if isinstance(record.get("features"), dict) else {}
    for key, value in raw.items():
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            features[f"feature.{key}"] = float(value)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    chart = record.get("chart") if isinstance(record.get("chart"), dict) else {}
    collision = record.get("collision") if isinstance(record.get("collision"), dict) else {}
    features.update({
        "context.ds": float(source.get("ds", 0.0) or 0.0),
        "context.bpm": float(source.get("bpm", 0.0) or 0.0),
        "context.whole_bpm": float(source.get("whole_bpm", 0.0) or 0.0),
        "context.level_index": float(source.get("level_index", 0.0) or 0.0),
        "context.diff_id": float(source.get("difficulty_id", 0.0) or 0.0),
        "context.event_count": float(len(chart.get("events") or [])),
        "context.window_count": float(len(record.get("two_measure_windows") or [])),
        "context.collision_count": float(len(collision.get("candidates") or [])),
        "context.accepted_collision_count": float(len(collision.get("accepted_candidate_ids") or [])),
    })
    return features


def _feature_matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    feature_maps = [_record_features(record) for record in records]
    names = sorted({name for item in feature_maps for name in item})
    matrix = np.zeros((len(records), len(names)), dtype=np.float64)
    positions = {name: index for index, name in enumerate(names)}
    for row, item in enumerate(feature_maps):
        for name, value in item.items():
            matrix[row, positions[name]] = value
    return matrix, names


def _target_matrix(records: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray(
        [[1.0 if tag in set(record.get("final_tags") or []) else 0.0 for tag in ALLOWED_TAGS] for record in records],
        dtype=np.float64,
    )


def _write_training_progress(epoch: int, total: int, best_epoch: int, best_loss: float, started: float) -> None:
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": "codex_conversation_model",
        "status": "training",
        "epoch": epoch,
        "epochs": total,
        "best_epoch": best_epoch,
        "best_valid_loss": best_loss,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "updated_at": now(),
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
    })


def train_local_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    validate_records(records)
    matrix, feature_names = _feature_matrix(records)
    targets = _target_matrix(records)
    if matrix.shape[1] == 0:
        raise ValueError("没有可训练的数值特征")

    rng = np.random.default_rng(TRAINING_SEED)
    order = rng.permutation(len(records))
    valid_size = min(VALIDATION_SIZE, max(1, len(records) // 4))
    valid_indices = order[:valid_size]
    train_indices = order[valid_size:]
    train_x = matrix[train_indices]
    valid_x = matrix[valid_indices]
    train_y = targets[train_indices]
    valid_y = targets[valid_indices]
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    train_x = (train_x - mean) / scale
    valid_x = (valid_x - mean) / scale

    weights = np.zeros((matrix.shape[1], len(ALLOWED_TAGS)), dtype=np.float64)
    bias = np.zeros(len(ALLOWED_TAGS), dtype=np.float64)
    loss_curve: list[dict[str, Any]] = []
    best_valid_loss = float("inf")
    best_epoch = 0
    best_weights = weights.copy()
    best_bias = bias.copy()
    started = time.monotonic()
    for epoch in range(1, EPOCHS + 1):
        train_prediction = _sigmoid(train_x @ weights + bias)
        error = train_prediction - train_y
        grad_w = (train_x.T @ error) / max(len(train_x), 1) + L2 * weights
        grad_b = error.mean(axis=0)
        weights -= LEARNING_RATE * grad_w
        bias -= LEARNING_RATE * grad_b

        train_prediction = _sigmoid(train_x @ weights + bias)
        valid_prediction = _sigmoid(valid_x @ weights + bias)
        train_loss = _bce(train_prediction, train_y) + L2 * float(np.mean(weights * weights)) / 2.0
        valid_loss = _bce(valid_prediction, valid_y)
        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 8),
            "valid_loss": round(valid_loss, 8),
            "train": _metrics(train_prediction, train_y),
            "valid": _metrics(valid_prediction, valid_y),
        }
        loss_curve.append(row)
        if valid_loss < best_valid_loss:
            best_valid_loss = float(valid_loss)
            best_epoch = epoch
            best_weights = weights.copy()
            best_bias = bias.copy()
        if epoch == 1 or epoch == EPOCHS or time.monotonic() - started >= PROGRESS_INTERVAL_SECONDS:
            _write_training_progress(epoch, EPOCHS, best_epoch, best_valid_loss, started)

    np.savez_compressed(
        MODEL_FILE,
        weights=best_weights,
        bias=best_bias,
        mean=mean,
        scale=scale,
        feature_names=np.asarray(feature_names),
        label_names=np.asarray(ALLOWED_TAGS),
    )
    loss_data = {
        "version": 1,
        "analysis_engine": "codex_conversation_model",
        "seed": TRAINING_SEED,
        "epochs": EPOCHS,
        "learning_rate": LEARNING_RATE,
        "l2": L2,
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "feature_count": len(feature_names),
        "labels": ALLOWED_TAGS,
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "curve": loss_curve,
        "created_at": now(),
    }
    write_json_atomic(LOSS_FILE, loss_data)
    metadata = {
        "version": 1,
        "model_type": "multilabel_logistic_regression",
        "analysis_engine": "codex_conversation_model",
        "call_mode": "in_conversation",
        "dataset": str(DATASET_FILE.relative_to(Root)),
        "sample_manifest": str(SAMPLE_MANIFEST_FILE.relative_to(Root)),
        "records": len(records),
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "feature_count": len(feature_names),
        "feature_names": feature_names,
        "labels": ALLOWED_TAGS,
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "loss_file": str(LOSS_FILE.relative_to(Root)),
        "model_file": str(MODEL_FILE.relative_to(Root)),
        "formal_pipeline_enabled": True,
        "review_status": "available_for_webui_auto_analysis",
        "created_at": now(),
    }
    write_json_atomic(MODEL_META_FILE, metadata)
    write_json_atomic(RUN_FILE, {
        "status": "completed",
        "analysis_engine": "codex_conversation_model",
        "dataset_records": len(records),
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "updated_at": now(),
    })
    return {
        "ok": True,
        "records": len(records),
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "feature_count": len(feature_names),
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "loss_file": str(LOSS_FILE.relative_to(Root)),
        "model_file": str(MODEL_FILE.relative_to(Root)),
        "model_meta": str(MODEL_META_FILE.relative_to(Root)),
    }


def _usage(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counter = Counter(tag for record in records for tag in (record.get(field) or []))
    total = max(len(records), 1)
    return [
        {"tag": tag, "count": int(counter.get(tag, 0)), "rate": round(counter.get(tag, 0) / total, 4)}
        for tag in ALLOWED_TAGS
    ]


def build_report(records: list[dict[str, Any]], model: dict[str, Any]) -> str:
    validate_records(records)
    manifest = json.loads(SAMPLE_MANIFEST_FILE.read_text(encoding="utf-8"))
    raw_usage = _usage(records, "raw_tags")
    final_usage = _usage(records, "final_tags")
    lines = [
        "# Codex 谱面元数据与本地模型报告",
        "",
        f"- 生成时间：{now()}",
        "- 分析引擎：`codex_conversation_model`（对话内完成；未调用 AstrBot 谱面分析接口）",
        f"- 随机种子：`{manifest.get('random_seed')}`；候选有效难度：{manifest.get('eligible_pool_count')}；强制重算：是",
        f"- 样本：{len(records)} / {manifest.get('sample_size_requested')}；完整标注成功：{sum(item.get('model_call_status') == 'success' for item in records)}",
        f"- 数据集：`{DATASET_FILE.relative_to(Root)}`；每条记录含完整 `inote`、事件、BPM 段、窗口、候选和标签位置",
        "- 本地模型可由 WebUI 自动打标任务使用；任务执行后将模型结果写入正式标签文件的 `model_tags` 和 `final_tags`",
        "",
        "## 撞尾依据",
        "",
        "- 参考资料：" + "、".join(f"[{index}]({url})" for index, url in enumerate(manifest.get("reference_sources", []), start=1)),
        f"- 候选窗口：Slide 进入路径区域前 {abs(-0.05):.2f}s 至进入后 {0.20:.2f}s；`delta=0` 为绝对撞尾，正向至 {0.15:.2f}s 为硬撞尾，两侧边缘为软撞尾；最后 A 区延伸到 Slide 结束并保留后 {0.20:.2f}s。",
        "- 目标原始语法含 `x` 的 Ex 音符被单独记录并排除；孤立软边界不直接成为标签，重复或硬冲突才进入 Codex 复核证据。",
        "",
        "## 标签使用率",
        "",
        "| 标签 | 原始次数 | 原始使用率 | 最终次数 | 最终使用率 |",
        "|:--|--:|--:|--:|--:|",
    ]
    final_by_tag = {row["tag"]: row for row in final_usage}
    for row in raw_usage:
        final = final_by_tag[row["tag"]]
        lines.append(f"| {row['tag']} | {row['count']} | {row['rate']:.1%} | {final['count']} | {final['rate']:.1%} |")
    lines.extend([
        "",
        "## 逐谱面标注",
        "",
        "| # | Key | 定数 | BPM | 原始标签 | 最终标签 | 撞尾证据 | 状态 |",
        "|--:|:--|--:|--:|:--|:--|--:|:--|",
    ])
    for index, record in enumerate(records, start=1):
        source = record["source"]
        collision_count = len((record.get("collision") or {}).get("accepted_candidate_ids") or [])
        lines.append(
            f"| {index} | `{record['record_key']}` {source.get('title', '')} {source.get('difficulty', '')} | "
            f"{float(source.get('ds', 0.0)):.1f} | {float(source.get('bpm', 0.0)):.1f} | "
            f"{'、'.join(record.get('raw_tags') or []) or '无'} | {'、'.join(record.get('final_tags') or []) or '无'} | "
            f"{collision_count} | {record.get('analysis_status')} |")
    lines.extend([
        "",
        "## 训练结果",
        "",
        f"- 模型：`{model['model_file']}`；元数据：`{model['model_meta']}`；Loss 曲线：`{model['loss_file']}`。",
        f"- 训练/验证：{model.get('train_records', '-')} / {model.get('valid_records', '-')}；特征数：{model.get('feature_count', '-')}；最佳 epoch：{model.get('best_epoch', '-')}；最佳验证 Loss：{model.get('best_valid_loss', '-')}",
        "- 训练目标是多标签分类；训练集只接收 100 条完整、成功、定数不低于 12.6 的记录，不把缺失结果当作否定标签。",
        "- Loss 文件按 epoch 保存训练/验证 Loss 和微平均指标，可直接绘制曲线；正式标签文件只在管理员从 WebUI 启动分析后更新。",
        "",
        "## 文件清单",
        "",
        f"- 样本清单：`{SAMPLE_MANIFEST_FILE.relative_to(Root)}`",
        f"- 进度：`{PROGRESS_FILE.relative_to(Root)}`",
        f"- Codex 审阅清单：`{REVIEW_FILE.relative_to(Root)}`（正式标签库不写入审阅结果）",
        "- 正式标签库：`static/maimaidx_chart_tags.json`（由 WebUI 自动分析任务按映射条目更新）",
    ])
    return "\n".join(lines) + "\n"


def run_full_pipeline() -> dict[str, Any]:
    annotation = run_codex_annotation()
    records = load_codex_records()
    model = train_local_model(records)
    report = build_report(records, model)
    _write_text_atomic(REPORT_FILE, report)
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": "codex_conversation_model",
        "status": "completed",
        "processed": len(records),
        "total": len(records),
        "training_best_epoch": model["best_epoch"],
        "training_best_valid_loss": model["best_valid_loss"],
        "updated_at": now(),
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
    })
    return {"annotation": annotation, "training": model, "report": str(REPORT_FILE.relative_to(Root))}


def main() -> None:
    print(json.dumps(run_full_pipeline(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
