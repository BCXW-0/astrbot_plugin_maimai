from __future__ import annotations

"""Train the local chart tag model from the complete audited rule dataset."""

import json
import time
from collections import Counter
from typing import Any

import numpy as np

from ... import Root
from .chart_metadata import (
    DATASET_FILE,
    PROGRESS_FILE,
    REPORT_FILE,
    build_report,
    load_local_records,
    now,
    run_full_annotation,
)
from .constants import (
    ALLOWED_TAGS,
    MAX_TAG_DS,
    MIN_TAG_DS,
    RULE_ENGINE,
    RULE_SPEC_SOURCE,
    TAG_RULE_VERSION,
)
from .storage import write_json_atomic

LOSS_FILE = Root / "static" / "chart_tag_loss.json"
RUN_FILE = Root / "static" / "chart_tag_training_run.json"
MODEL_FILE = Root / "static" / "maimai_chart_tag_model.npz"
MODEL_META_FILE = Root / "static" / "maimai_chart_tag_model.json"

TRAINING_SEED = 2026080301
EPOCHS = 220
LEARNING_RATE = 0.04
L2 = 0.0008
VALIDATION_FRACTION = 0.20
PROGRESS_INTERVAL_SECONDS = 300
DEPRECATED_TAGS = {"背谱", "手序", "一笔划", "秒划", "拆谱", "拆譜"}


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _weighted_bce(prediction: np.ndarray, target: np.ndarray, positive_weight: np.ndarray) -> float:
    clipped = np.clip(prediction, 1e-7, 1.0 - 1e-7)
    weights = np.where(target >= 0.5, positive_weight[None, :], 1.0)
    loss = -(target * np.log(clipped) + (1.0 - target) * np.log(1.0 - clipped))
    return float(np.sum(loss * weights) / max(float(weights.sum()), 1.0))


def _metrics(prediction: np.ndarray, target: np.ndarray) -> dict[str, float]:
    predicted = prediction >= 0.5
    truth = target >= 0.5
    tp = float(np.logical_and(predicted, truth).sum())
    fp = float(np.logical_and(predicted, ~truth).sum())
    fn = float(np.logical_and(~predicted, truth).sum())
    precision = tp / max(tp + fp, 1.0)
    recall = tp / max(tp + fn, 1.0)
    return {
        "micro_accuracy": float((predicted == truth).mean()),
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": 2.0 * precision * recall / max(precision + recall, 1e-9),
        "positive_cells": float(truth.sum()),
        "predicted_positive_cells": float(predicted.sum()),
    }


def validate_records(records: list[dict[str, Any]]) -> None:
    if not records:
        raise ValueError("训练数据集为空")
    keys: set[str] = set()
    for record in records:
        key = str(record.get("record_key", ""))
        if not key or key in keys:
            raise ValueError(f"记录 key 缺失或重复: {key}")
        keys.add(key)
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        chart = record.get("chart") if isinstance(record.get("chart"), dict) else {}
        collision = record.get("collision") if isinstance(record.get("collision"), dict) else {}
        if record.get("analysis_status") != "completed":
            raise ValueError(f"记录未完成: {key}")
        ds = float(source.get("ds", 0.0) or 0.0)
        if not MIN_TAG_DS <= ds <= MAX_TAG_DS:
            raise ValueError(f"记录定数不符合要求: {key}")
        if not str(chart.get("inote", "")).strip() or not isinstance(chart.get("events"), list):
            raise ValueError(f"记录缺少完整谱面内容: {key}")
        if not isinstance(chart.get("bpm_segments"), list) or not isinstance(chart.get("note_counts"), dict):
            raise ValueError(f"记录缺少 BPM 或物量元数据: {key}")
        if not isinstance(record.get("two_measure_windows"), list):
            raise ValueError(f"记录缺少两小节窗口: {key}")
        if not isinstance(collision.get("candidates"), list) or not isinstance(collision.get("accepted_candidate_ids"), list):
            raise ValueError(f"记录缺少撞尾审计数据: {key}")
        for field in ("raw_tags", "difficulty_tags", "final_tags"):
            if not isinstance(record.get(field), list):
                raise ValueError(f"记录缺少 {field}: {key}")
        if not isinstance(record.get("tag_positions"), dict) or not isinstance(record.get("tag_evidence"), dict):
            raise ValueError(f"记录缺少标签位置: {key}")
        for tag in [*(record.get("raw_tags") or []), *(record.get("difficulty_tags") or []), *(record.get("final_tags") or [])]:
            if tag in DEPRECATED_TAGS or tag not in ALLOWED_TAGS:
                raise ValueError(f"记录包含非法或废弃标签 {tag}: {key}")


def _record_features(record: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    raw_features = record.get("features") if isinstance(record.get("features"), dict) else {}
    for key, value in raw_features.items():
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            result[f"feature.{key}"] = float(value)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    chart = record.get("chart") if isinstance(record.get("chart"), dict) else {}
    collision = record.get("collision") if isinstance(record.get("collision"), dict) else {}
    result.update({
        "context.ds": float(source.get("ds", 0.0) or 0.0),
        "context.bpm": float(source.get("bpm", 0.0) or 0.0),
        "context.whole_bpm": float(source.get("whole_bpm", 0.0) or 0.0),
        "context.level_index": float(source.get("level_index", 0.0) or 0.0),
        "context.diff_id": float(source.get("diff_id", 0.0) or 0.0),
        "context.event_count": float(len(chart.get("events") or [])),
        "context.window_count": float(len(record.get("two_measure_windows") or [])),
        "context.collision_count": float(len(collision.get("candidates") or [])),
        "context.accepted_collision_count": float(len(collision.get("accepted_candidate_ids") or [])),
    })
    return result


def _feature_matrix(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    maps = [_record_features(record) for record in records]
    names = sorted({name for values in maps for name in values})
    matrix = np.zeros((len(records), len(names)), dtype=np.float64)
    positions = {name: index for index, name in enumerate(names)}
    for row, values in enumerate(maps):
        for name, value in values.items():
            matrix[row, positions[name]] = value
    return matrix, names


def _target_matrix(records: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray(
        [[1.0 if tag in set(record.get("final_tags") or []) else 0.0 for tag in ALLOWED_TAGS] for record in records],
        dtype=np.float64,
    )


def _write_progress(updates: dict[str, Any]) -> None:
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": RULE_ENGINE,
        "task": "training",
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
        "updated_at": now(),
        **updates,
    })


def train_local_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    validate_records(records)
    matrix, feature_names = _feature_matrix(records)
    targets = _target_matrix(records)
    if matrix.shape[1] == 0:
        raise ValueError("没有可训练的数值特征")

    rng = np.random.default_rng(TRAINING_SEED)
    order = rng.permutation(len(records))
    valid_size = max(1, int(round(len(records) * VALIDATION_FRACTION)))
    if len(records) > 5:
        valid_size = min(valid_size, len(records) - 1)
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

    positives = train_y.sum(axis=0)
    negatives = train_y.shape[0] - positives
    positive_weight = np.clip((negatives + 1.0) / (positives + 1.0), 1.0, 8.0)
    weights = np.zeros((matrix.shape[1], len(ALLOWED_TAGS)), dtype=np.float64)
    bias = np.log((positives + 0.5) / (negatives + 0.5))
    loss_curve: list[dict[str, Any]] = []
    best_valid_loss = float("inf")
    best_epoch = 0
    best_weights = weights.copy()
    best_bias = bias.copy()
    started = time.monotonic()
    last_progress = started - PROGRESS_INTERVAL_SECONDS

    for epoch in range(1, EPOCHS + 1):
        prediction = _sigmoid(train_x @ weights + bias)
        error = prediction - train_y
        gradient_weight = np.where(train_y >= 0.5, positive_weight[None, :], 1.0)
        gradient = error * gradient_weight
        grad_w = (train_x.T @ gradient) / max(float(gradient_weight.sum()), 1.0) + L2 * weights
        grad_b = gradient.sum(axis=0) / max(float(gradient_weight.sum()), 1.0)
        weights -= LEARNING_RATE * grad_w
        bias -= LEARNING_RATE * grad_b

        train_prediction = _sigmoid(train_x @ weights + bias)
        valid_prediction = _sigmoid(valid_x @ weights + bias)
        train_loss = _weighted_bce(train_prediction, train_y, positive_weight) + L2 * float(np.mean(weights * weights)) / 2.0
        valid_loss = _weighted_bce(valid_prediction, valid_y, positive_weight)
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
        current = time.monotonic()
        if epoch == 1 or epoch == EPOCHS or current - last_progress >= PROGRESS_INTERVAL_SECONDS:
            _write_progress({
                "status": "training",
                "epoch": epoch,
                "epochs": EPOCHS,
                "records": len(records),
                "train_records": len(train_indices),
                "valid_records": len(valid_indices),
                "best_epoch": best_epoch,
                "best_valid_loss": round(best_valid_loss, 8),
                "elapsed_seconds": round(current - started, 3),
            })
            last_progress = current

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
        "version": 2,
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "seed": TRAINING_SEED,
        "epochs": EPOCHS,
        "learning_rate": LEARNING_RATE,
        "l2": L2,
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "feature_count": len(feature_names),
        "labels": ALLOWED_TAGS,
        "positive_counts": {tag: int(value) for tag, value in zip(ALLOWED_TAGS, targets.sum(axis=0))},
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "curve": loss_curve,
        "created_at": now(),
    }
    write_json_atomic(LOSS_FILE, loss_data)
    metadata = {
        "version": 2,
        "model_type": "multilabel_logistic_regression",
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "dataset": str(DATASET_FILE.relative_to(Root)),
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
        "created_at": now(),
    }
    write_json_atomic(MODEL_META_FILE, metadata)
    result = {
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
    write_json_atomic(RUN_FILE, {
        "version": 2,
        "status": "completed",
        "analysis_engine": RULE_ENGINE,
        "rule_version": TAG_RULE_VERSION,
        "dataset": str(DATASET_FILE.relative_to(Root)),
        "dataset_records": len(records),
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "updated_at": now(),
    })
    _write_progress({
        "status": "training_completed",
        "epoch": EPOCHS,
        "epochs": EPOCHS,
        "records": len(records),
        "best_epoch": best_epoch,
        "best_valid_loss": round(best_valid_loss, 8),
        "finished_at": now(),
    })
    return result


def _usage(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counter = Counter(tag for record in records for tag in (record.get(field) or []))
    total = max(len(records), 1)
    return [
        {"tag": tag, "count": int(counter.get(tag, 0)), "rate": round(counter.get(tag, 0) / total, 6)}
        for tag in ALLOWED_TAGS
    ]


def run_full_pipeline(directory: str = "static/Levels") -> dict[str, Any]:
    annotation = run_full_annotation(directory)
    records = load_local_records()
    training = train_local_model(records)
    report = build_report(records, training)
    REPORT_FILE.write_text(report, encoding="utf-8")
    run = {
        "annotation": annotation,
        "training": training,
        "records": len(records),
        "raw_usage": _usage(records, "raw_tags"),
        "final_usage": _usage(records, "final_tags"),
        "report": str(REPORT_FILE.relative_to(Root)),
    }
    write_json_atomic(RUN_FILE, {**json.loads(RUN_FILE.read_text(encoding="utf-8")), "report": run["report"], "updated_at": now()})
    _write_progress({
        "status": "completed",
        "task": "pipeline",
        "processed": len(records),
        "total": len(records),
        "training_best_epoch": training["best_epoch"],
        "training_best_valid_loss": training["best_valid_loss"],
        "finished_at": now(),
    })
    return run


def main() -> None:
    print(json.dumps(run_full_pipeline(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
