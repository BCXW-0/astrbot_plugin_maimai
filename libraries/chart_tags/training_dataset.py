from __future__ import annotations

"""Build reviewed chart-tag metadata and train the offline local model."""

import gzip
import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from ... import Root
from .chart_metadata import (
    AUDIT_FILE,
    DATASET_FILE,
    MANIFEST_FILE,
    PROGRESS_FILE,
    REPORT_FILE,
    _apply_difficulty_caps,
    _make_record,
    _relative_path,
    collect_eligible_chart_refs,
    now,
    resolve_levels_directory,
)
from .constants import (
    ALLOWED_TAGS,
    MAX_TAG_DS,
    RULE_ENGINE,
    RULE_SPEC_SOURCE,
    TAG_RULE_VERSION,
    TRAIN_MIN_DS,
)
from .external_evidence import (
    EFFECTIVE_OVERLAP,
    FALLBACK_SAMPLE_MIN,
    FALLBACK_SAMPLE_TARGET,
    REFERENCE_SOURCES,
    select_effective_samples,
    traceable_sources,
)
from .local.maidata_parser import parse_maidata
from .model_consensus import is_accepted_model_review
from .rule_tags import filter_allowed_tags, select_final_tags, tag_weight
from .storage import write_json_atomic, write_json_gzip_atomic

LOSS_FILE = Root / "static" / "chart_tag_loss.json"
RUN_FILE = Root / "static" / "chart_tag_training_run.json"
MODEL_FILE = Root / "static" / "maimai_chart_tag_model.npz"
MODEL_META_FILE = Root / "static" / "maimai_chart_tag_model.json"

TRAINING_SEED = 20260805
EFFECTIVE_SAMPLE_TARGET = 200
FALLBACK_TARGET = FALLBACK_SAMPLE_TARGET
FALLBACK_MIN = FALLBACK_SAMPLE_MIN
TRAINING_TARGET_FIELD = "training_tags"
ENSEMBLE_MEMBERS = 5
EPOCHS = 480
EARLY_STOPPING_PATIENCE = 64
LEARNING_RATE = 0.018
L2 = 0.002
VALIDATION_FRACTION = 0.20
HOLDOUT_FRACTION = 0.20
PROGRESS_INTERVAL_SECONDS = 300
MIN_VALIDATION_PRECISION = 0.80
MIN_HOLDOUT_PRECISION = 0.80
MIN_HOLDOUT_F1 = 0.40
MIN_HOLDOUT_PREDICTIONS = 1
DEPRECATED_TAGS = {"背谱", "手序", "一笔划", "拆谱", "拆譜", "秒划", "秒画", "秒畫"}


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _weighted_bce(
    prediction: np.ndarray,
    target: np.ndarray,
    positive_weight: np.ndarray,
    supervision_weight: np.ndarray | None = None,
) -> float:
    clipped = np.clip(prediction, 1e-7, 1.0 - 1e-7)
    weights = np.where(target >= 0.5, positive_weight[None, :], 1.0)
    if supervision_weight is not None:
        weights = weights * supervision_weight
    loss = -(target * np.log(clipped) + (1.0 - target) * np.log(1.0 - clipped))
    return float(np.sum(loss * weights) / max(float(weights.sum()), 1.0))


def _metrics(
    prediction: np.ndarray,
    target: np.ndarray,
    thresholds: float | np.ndarray = 0.5,
) -> dict[str, float]:
    predicted = prediction >= thresholds
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
    if not FALLBACK_MIN <= len(records) <= EFFECTIVE_SAMPLE_TARGET:
        raise ValueError(
            f"有效训练样本必须在 {FALLBACK_MIN}-{EFFECTIVE_SAMPLE_TARGET} 条之间，"
            f"实际为 {len(records)} 条"
        )
    keys: set[str] = set()
    for record in records:
        key = str(record.get("record_key", ""))
        if not key or key in keys:
            raise ValueError(f"记录 key 缺失或重复: {key}")
        keys.add(key)
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        chart = record.get("chart") if isinstance(record.get("chart"), dict) else {}
        collision = record.get("collision") if isinstance(record.get("collision"), dict) else {}
        validation = record.get("validation") if isinstance(record.get("validation"), dict) else {}
        external = record.get("external_evidence") if isinstance(record.get("external_evidence"), dict) else {}
        if record.get("analysis_status") != "completed":
            raise ValueError(f"记录未完成: {key}")
        ds = float(source.get("ds", 0.0) or 0.0)
        if not TRAIN_MIN_DS <= ds <= MAX_TAG_DS:
            raise ValueError(f"记录定数不符合训练范围: {key}")
        if not str(chart.get("inote", "")).strip() or not isinstance(chart.get("events"), list):
            raise ValueError(f"记录缺少完整谱面内容: {key}")
        if not isinstance(chart.get("bpm_segments"), list) or not isinstance(chart.get("note_counts"), dict):
            raise ValueError(f"记录缺少 BPM 或物量元数据: {key}")
        if not isinstance(record.get("two_measure_windows"), list):
            raise ValueError(f"记录缺少两小节窗口: {key}")
        if not isinstance(collision.get("candidates"), list) or not isinstance(collision.get("accepted_candidate_ids"), list):
            raise ValueError(f"记录缺少撞尾审计数据: {key}")
        if not isinstance(record.get("raw_tags"), list) or not isinstance(record.get("final_tags"), list):
            raise ValueError(f"记录缺少标签: {key}")
        if not isinstance(record.get("training_tags"), list) or not isinstance(record.get("validated_tags"), list):
            raise ValueError(f"记录缺少多来源标签: {key}")
        if not isinstance(record.get("tag_positions"), dict) or not isinstance(record.get("tag_evidence"), dict):
            raise ValueError(f"记录缺少标签位置: {key}")
        if not isinstance(record.get("training_tag_positions"), dict):
            raise ValueError(f"记录缺少训练标签位置: {key}")
        consensus = record.get("model_consensus") if isinstance(record.get("model_consensus"), dict) else {}
        comparison = consensus.get("comparison") if isinstance(consensus.get("comparison"), dict) else {}
        required_models = int(comparison.get("required_models", 0) or 0)
        required_label = "三模型" if required_models == 3 else "双模型" if required_models == 2 else "未知模型数量"
        if not is_accepted_model_review(consensus, allow_legacy_three=True):
            raise ValueError(f"记录缺少有效{required_label}一致性审核: {key}")
        sources = traceable_sources(external)
        if external.get("status") != "completed" or not sources:
            raise ValueError(f"记录缺少可追溯外部媒体来源: {key}")
        if int(validation.get("evidence_source_count", 0) or 0) < 1:
            raise ValueError(f"记录缺少外部来源计数: {key}")
        selection_mode = str(validation.get("selection_mode") or "")
        overlap = float(validation.get("overlap", 0.0) or 0.0)
        if not 0.0 <= overlap <= 1.0:
            raise ValueError(f"记录外部证据重合度非法: {key}")
        if selection_mode == "external_threshold":
            if not bool(validation.get("effective")) or overlap < EFFECTIVE_OVERLAP:
                raise ValueError(f"记录未达到外部证据重合阈值: {key}")
        elif selection_mode == "confidence_fallback":
            if not bool(validation.get("training_eligible")):
                raise ValueError(f"降级样本没有训练资格: {key}")
        else:
            raise ValueError(f"记录缺少有效样本选择模式: {key}")
        if len(record.get("final_tags") or []) > 5:
            raise ValueError(f"记录超过单谱面 5 个标签上限: {key}")
        if len(record.get(TRAINING_TARGET_FIELD) or []) > 5:
            raise ValueError(f"训练目标超过单谱面 5 个标签上限: {key}")
        for field in ("raw_tags", "difficulty_tags", "training_tags", "validated_tags", "final_tags"):
            for tag in record.get(field) or []:
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
    """Train on consensus labels before display-only prevalence caps.

    ``final_tags`` is capped per constant and limited to five labels for
    presentation. Training on that field would turn the cap into a negative
    label and teach the model the sample selection order.
    """
    return np.asarray(
        [[1.0 if tag in set(record.get(TRAINING_TARGET_FIELD) or []) else 0.0 for tag in ALLOWED_TAGS] for record in records],
        dtype=np.float64,
    )


def _supervision_matrix(records: list[dict[str, Any]]) -> np.ndarray:
    matrix = np.ones((len(records), len(ALLOWED_TAGS)), dtype=np.float64)
    positions = {tag: index for index, tag in enumerate(ALLOWED_TAGS)}
    for row, record in enumerate(records):
        validation = record.get("validation") if isinstance(record.get("validation"), dict) else {}
        confidence = float(validation.get("confidence", validation.get("overlap", 0.0)) or 0.0)
        # Every selected record has a source, but a low-overlap source is only
        # weak supervision. Keep it as an example without allowing it to
        # outweigh high-confidence records.
        matrix[row, :] *= max(0.25, min(1.0, 0.35 + 0.65 * confidence))
        target_tags = set(record.get(TRAINING_TARGET_FIELD) or [])
        for tag in record.get("external_tags") or []:
            if tag in positions:
                # A conflicting external tag is disagreement evidence, not a
                # stronger negative example.
                weight = 1.50 if tag in target_tags else 0.75
                matrix[row, positions[tag]] = max(matrix[row, positions[tag]], weight)
        for tag in record.get("validated_tags") or []:
            if tag in positions:
                matrix[row, positions[tag]] = max(matrix[row, positions[tag]], 2.50)
    return matrix


def _write_progress(updates: dict[str, Any]) -> None:
    write_json_atomic(PROGRESS_FILE, {
        "analysis_engine": RULE_ENGINE,
        "task": "training",
        "report_interval_seconds": PROGRESS_INTERVAL_SECONDS,
        "updated_at": now(),
        **updates,
    })


def _split_by_song(records: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split by song so another difficulty of the same song cannot leak."""
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        group = str(source.get("song_id") or record.get("record_key", "").split(":", 1)[0])
        groups.setdefault(group, []).append(index)
    rng = np.random.default_rng(TRAINING_SEED)
    group_items = list(groups.values())
    rng.shuffle(group_items)
    train_target = max(1, int(round(len(records) * (1.0 - VALIDATION_FRACTION - HOLDOUT_FRACTION))))
    valid_target = max(1, int(round(len(records) * VALIDATION_FRACTION)))
    desired = [train_target, valid_target, max(1, len(records) - train_target - valid_target)]
    buckets: list[list[int]] = [[], [], []]
    for group in sorted(group_items, key=len, reverse=True):
        choices = [
            index for index in range(3)
            if len(buckets[index]) < desired[index] or not buckets[index]
        ]
        if not choices:
            choices = list(range(3))
        bucket = min(
            choices,
            key=lambda index: (
                len(buckets[index]) / max(desired[index], 1),
                len(buckets[index]),
                index,
            ),
        )
        buckets[bucket].extend(group)
    if any(not bucket for bucket in buckets):
        raise ValueError("按歌曲分组切分失败，训练/验证/留出集不能有空集")
    return tuple(np.asarray(bucket, dtype=np.int64) for bucket in buckets)  # type: ignore[return-value]


def _fit_member(
    train_x: np.ndarray,
    train_y: np.ndarray,
    train_supervision: np.ndarray,
    eval_x: np.ndarray,
    eval_y: np.ndarray,
    eval_supervision: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], int, float]:
    positives = train_y.sum(axis=0)
    negatives = train_y.shape[0] - positives
    positive_weight = np.clip((negatives + 1.0) / (positives + 1.0), 1.0, 12.0)
    weights = np.zeros((train_x.shape[1], len(ALLOWED_TAGS)), dtype=np.float64)
    bias = np.log((positives + 0.5) / (negatives + 0.5))
    best_weights = weights.copy()
    best_bias = bias.copy()
    best_valid_loss = float("inf")
    best_epoch = 0
    stale_epochs = 0
    curve: list[dict[str, Any]] = []
    for epoch in range(1, EPOCHS + 1):
        prediction = _sigmoid(train_x @ weights + bias)
        error = prediction - train_y
        gradient_weight = np.where(train_y >= 0.5, positive_weight[None, :], 1.0) * train_supervision
        gradient = error * gradient_weight
        normalizer = max(float(gradient_weight.sum()), 1.0)
        grad_w = (train_x.T @ gradient) / normalizer + L2 * weights
        grad_b = gradient.sum(axis=0) / normalizer
        weights -= LEARNING_RATE * grad_w
        bias -= LEARNING_RATE * grad_b

        train_prediction = _sigmoid(train_x @ weights + bias)
        eval_prediction = _sigmoid(eval_x @ weights + bias)
        train_loss = _weighted_bce(train_prediction, train_y, positive_weight, train_supervision)
        eval_loss = _weighted_bce(eval_prediction, eval_y, positive_weight, eval_supervision)
        curve.append({
            "epoch": epoch,
            "train_loss": round(train_loss + L2 * float(np.mean(weights * weights)) / 2.0, 8),
            "valid_loss": round(eval_loss, 8),
        })
        if eval_loss + 1e-8 < best_valid_loss:
            best_valid_loss = float(eval_loss)
            best_epoch = epoch
            best_weights = weights.copy()
            best_bias = bias.copy()
            stale_epochs = 0
        else:
            stale_epochs += 1
        if stale_epochs >= EARLY_STOPPING_PATIENCE:
            break
    return best_weights, best_bias, curve, best_epoch, best_valid_loss


def _threshold_metrics(prediction: np.ndarray, target: np.ndarray, threshold: float) -> dict[str, float]:
    predicted = prediction >= threshold
    truth = target >= 0.5
    tp = float(np.logical_and(predicted, truth).sum())
    fp = float(np.logical_and(predicted, ~truth).sum())
    fn = float(np.logical_and(~predicted, truth).sum())
    precision = tp / max(tp + fp, 1.0)
    recall = tp / max(tp + fn, 1.0)
    f05 = 1.25 * precision * recall / max(0.25 * precision + recall, 1e-9)
    return {
        "precision": precision,
        "recall": recall,
        "f05": f05,
        "f1": 2.0 * precision * recall / max(precision + recall, 1e-9),
        "positive": float(truth.sum()),
        "predicted": float(predicted.sum()),
    }


def _calibrate_thresholds(prediction: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    grid = np.unique(np.concatenate((np.linspace(0.55, 0.95, 17), np.asarray([0.99]))))
    thresholds = np.full(len(ALLOWED_TAGS), 0.80, dtype=np.float64)
    details: list[dict[str, Any]] = []
    for index, tag in enumerate(ALLOWED_TAGS):
        scores = prediction[:, index]
        truth = target[:, index]
        if int(truth.sum()) < 2:
            details.append({
                "tag": tag,
                "threshold": 0.80,
                "reason": "validation_positive_count_below_2",
                **_threshold_metrics(scores, truth, 0.80),
            })
            continue
        candidates = [
            (float(threshold), _threshold_metrics(scores, truth, float(threshold)))
            for threshold in grid
        ]
        precise = [item for item in candidates if item[1]["predicted"] > 0 and item[1]["precision"] >= 0.80]
        if precise:
            selected_threshold, selected_metrics = max(
                precise,
                key=lambda item: (
                    item[1]["f05"],
                    item[1]["precision"],
                    item[1]["f1"],
                    -item[0],
                ),
            )
        else:
            selected_threshold = 0.80
            selected_metrics = _threshold_metrics(scores, truth, selected_threshold)
        thresholds[index] = selected_threshold
        details.append({
            "tag": tag,
            "threshold": round(selected_threshold, 6),
            "reason": "precision_constrained_f05" if precise else "conservative_default",
            **selected_metrics,
        })
    return thresholds, details


def _assert_model_quality(
    validation_metrics: dict[str, float],
    holdout_metrics: dict[str, float],
) -> None:
    """Reject a model that is accurate only because it predicts nothing."""
    failures: list[str] = []
    if validation_metrics["micro_precision"] < MIN_VALIDATION_PRECISION:
        failures.append(
            f"验证集 precision={validation_metrics['micro_precision']:.3f}"
        )
    if holdout_metrics["micro_precision"] < MIN_HOLDOUT_PRECISION:
        failures.append(
            f"留出集 precision={holdout_metrics['micro_precision']:.3f}"
        )
    if holdout_metrics["micro_f1"] < MIN_HOLDOUT_F1:
        failures.append(f"留出集 F1={holdout_metrics['micro_f1']:.3f}")
    if holdout_metrics["predicted_positive_cells"] < MIN_HOLDOUT_PREDICTIONS:
        failures.append("留出集没有任何正例预测")
    if failures:
        raise ValueError("本地模型质量门禁未通过：" + "；".join(failures))


def _ensemble_prediction(
    values: np.ndarray,
    members: list[tuple[np.ndarray, np.ndarray]],
) -> np.ndarray:
    predictions = [
        _sigmoid(values @ weights + bias)
        for weights, bias in members
    ]
    return np.mean(np.asarray(predictions), axis=0)


def train_local_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    validate_records(records)
    matrix, feature_names = _feature_matrix(records)
    targets = _target_matrix(records)
    supervision = _supervision_matrix(records)
    if matrix.shape[1] == 0:
        raise ValueError("没有可训练的数值特征")

    train_indices, valid_indices, holdout_indices = _split_by_song(records)
    train_x = matrix[train_indices]
    valid_x = matrix[valid_indices]
    holdout_x = matrix[holdout_indices]
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    normalized = (matrix - mean) / scale
    train_x = normalized[train_indices]
    valid_x = normalized[valid_indices]
    holdout_x = normalized[holdout_indices]

    started = time.monotonic()
    last_progress = started - PROGRESS_INTERVAL_SECONDS
    members: list[tuple[np.ndarray, np.ndarray]] = []
    member_curves: list[list[dict[str, Any]]] = []
    member_results: list[dict[str, Any]] = []
    rng = np.random.default_rng(TRAINING_SEED)
    for member_index in range(ENSEMBLE_MEMBERS):
        order = rng.permutation(len(train_indices))
        internal_valid_size = max(1, int(round(len(order) * 0.18)))
        internal_valid = order[:internal_valid_size]
        internal_train = order[internal_valid_size:]
        if not len(internal_train):
            internal_train = order
            internal_valid = order
        weights, bias, curve, best_epoch, best_loss = _fit_member(
            train_x[internal_train],
            targets[train_indices][internal_train],
            supervision[train_indices][internal_train],
            train_x[internal_valid],
            targets[train_indices][internal_valid],
            supervision[train_indices][internal_valid],
        )
        members.append((weights, bias))
        member_curves.append(curve)
        member_results.append({
            "member": member_index + 1,
            "best_epoch": best_epoch,
            "best_valid_loss": round(best_loss, 8),
            "epochs_run": len(curve),
            "fit_records": len(internal_train),
            "internal_valid_records": len(internal_valid),
        })
        current = time.monotonic()
        _write_progress({
            "status": "training",
            "phase": "ensemble_member",
            "member": member_index + 1,
            "members": ENSEMBLE_MEMBERS,
            "records": len(records),
            "train_records": len(train_indices),
            "valid_records": len(valid_indices),
            "holdout_records": len(holdout_indices),
            "best_epoch": max(item["best_epoch"] for item in member_results),
            "best_valid_loss": min(item["best_valid_loss"] for item in member_results),
            "elapsed_seconds": round(current - started, 3),
        })
        last_progress = current

    valid_prediction = _ensemble_prediction(valid_x, members)
    thresholds, threshold_details = _calibrate_thresholds(valid_prediction, targets[valid_indices])
    holdout_prediction = _ensemble_prediction(holdout_x, members)
    train_prediction = _ensemble_prediction(train_x, members)
    valid_metrics = _metrics(valid_prediction, targets[valid_indices], thresholds)
    holdout_metrics = _metrics(holdout_prediction, targets[holdout_indices], thresholds)
    train_metrics = _metrics(train_prediction, targets[train_indices], thresholds)
    _assert_model_quality(valid_metrics, holdout_metrics)
    label_metrics = []
    for index, tag in enumerate(ALLOWED_TAGS):
        metrics = _threshold_metrics(holdout_prediction[:, index], targets[holdout_indices, index], float(thresholds[index]))
        metrics.update({
            "tag": tag,
            "threshold": round(float(thresholds[index]), 6),
        })
        label_metrics.append(metrics)

    curve: list[dict[str, Any]] = []
    for epoch in range(1, max(len(item) for item in member_curves) + 1):
        rows = [item[epoch - 1] for item in member_curves if len(item) >= epoch]
        curve.append({
            "epoch": epoch,
            "members": len(rows),
            "train_loss": round(sum(item["train_loss"] for item in rows) / max(len(rows), 1), 8),
            "valid_loss": round(sum(item["valid_loss"] for item in rows) / max(len(rows), 1), 8),
        })
    best_epoch = min(curve, key=lambda item: item["valid_loss"])["epoch"]
    best_valid_loss = min(item["valid_loss"] for item in curve)
    validated_matrix = np.asarray(
        [[tag in (record.get("validated_tags") or []) for tag in ALLOWED_TAGS] for record in records],
        dtype=np.float64,
    )
    np.savez_compressed(
        MODEL_FILE,
        weights=np.asarray([item[0] for item in members], dtype=np.float64),
        bias=np.asarray([item[1] for item in members], dtype=np.float64),
        thresholds=thresholds,
        mean=mean,
        scale=scale,
        feature_names=np.asarray(feature_names),
        label_names=np.asarray(ALLOWED_TAGS),
    )
    loss_data = {
        "version": 4,
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "seed": TRAINING_SEED,
        "epochs": EPOCHS,
        "early_stopping_patience": EARLY_STOPPING_PATIENCE,
        "learning_rate": LEARNING_RATE,
        "l2": L2,
        "ensemble_members": ENSEMBLE_MEMBERS,
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "holdout_records": len(holdout_indices),
        "feature_count": len(feature_names),
        "training_target_field": TRAINING_TARGET_FIELD,
        "training_target_definition": "双模型共识标签；外部证据重合度仅作为监督权重，final_tags 只用于定数上限与单谱面展示",
        "labels": ALLOWED_TAGS,
        "positive_counts": {tag: int(value) for tag, value in zip(ALLOWED_TAGS, targets.sum(axis=0))},
        "validated_positive_counts": {tag: int(value) for tag, value in zip(ALLOWED_TAGS, validated_matrix.sum(axis=0))},
        "member_results": member_results,
        "thresholds": {tag: round(float(value), 6) for tag, value in zip(ALLOWED_TAGS, thresholds)},
        "threshold_calibration": threshold_details,
        "metrics": {
            "train": train_metrics,
            "validation": valid_metrics,
            "holdout": holdout_metrics,
        },
        "quality_gate": {
            "minimum_validation_precision": MIN_VALIDATION_PRECISION,
            "minimum_holdout_precision": MIN_HOLDOUT_PRECISION,
            "minimum_holdout_f1": MIN_HOLDOUT_F1,
            "minimum_holdout_predictions": MIN_HOLDOUT_PREDICTIONS,
            "passed": True,
        },
        "label_holdout_metrics": label_metrics,
        "best_epoch": int(best_epoch),
        "best_valid_loss": round(float(best_valid_loss), 8),
        "curve": curve,
        "created_at": now(),
    }
    write_json_atomic(LOSS_FILE, loss_data)
    metadata = {
        "version": 4,
        "model_type": "calibrated_ensemble_multilabel_logistic",
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "dataset": str(DATASET_FILE.relative_to(Root)),
        "records": len(records),
        "effective_records": len(records),
        "train_records": len(train_indices),
        "valid_records": len(valid_indices),
        "holdout_records": len(holdout_indices),
        "feature_count": len(feature_names),
        "feature_names": feature_names,
        "training_target_field": TRAINING_TARGET_FIELD,
        "training_target_definition": "双模型共识标签；final_tags 仅为审计展示标签，不作为训练目标",
        "labels": ALLOWED_TAGS,
        "ensemble_members": ENSEMBLE_MEMBERS,
        "thresholds": {tag: round(float(value), 6) for tag, value in zip(ALLOWED_TAGS, thresholds)},
        "training_sources": ["maimai.xls", *REFERENCE_SOURCES],
        "supervision": "XLS rule labels with dual-model consensus targets, traceable external sources as supervision weights, grouped holdout, class weighting, early stopping and validation threshold calibration",
        "quality_gate": {
            "minimum_validation_precision": MIN_VALIDATION_PRECISION,
            "minimum_holdout_precision": MIN_HOLDOUT_PRECISION,
            "minimum_holdout_f1": MIN_HOLDOUT_F1,
            "minimum_holdout_predictions": MIN_HOLDOUT_PREDICTIONS,
            "passed": True,
        },
        "split": "song_grouped_train_validation_holdout",
        "metrics": {
            "validation": valid_metrics,
            "holdout": holdout_metrics,
        },
        "best_epoch": int(best_epoch),
        "best_valid_loss": round(float(best_valid_loss), 8),
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
        "holdout_records": len(holdout_indices),
        "feature_count": len(feature_names),
        "ensemble_members": ENSEMBLE_MEMBERS,
        "best_epoch": int(best_epoch),
        "best_valid_loss": round(float(best_valid_loss), 8),
        "validation_metrics": valid_metrics,
        "holdout_metrics": holdout_metrics,
        "quality_gate": {
            "minimum_validation_precision": MIN_VALIDATION_PRECISION,
            "minimum_holdout_precision": MIN_HOLDOUT_PRECISION,
            "minimum_holdout_f1": MIN_HOLDOUT_F1,
            "minimum_holdout_predictions": MIN_HOLDOUT_PREDICTIONS,
            "passed": True,
        },
        "thresholds": {tag: round(float(value), 6) for tag, value in zip(ALLOWED_TAGS, thresholds)},
        "model_file": str(MODEL_FILE.relative_to(Root)),
        "model_meta": str(MODEL_META_FILE.relative_to(Root)),
    }
    write_json_atomic(RUN_FILE, {
        "version": 4,
        "status": "trained",
        "analysis_engine": RULE_ENGINE,
        "rule_version": TAG_RULE_VERSION,
        "dataset": str(DATASET_FILE.relative_to(Root)),
        "dataset_records": len(records),
        "training": result,
        "updated_at": now(),
    })
    _write_progress({
        "status": "training_completed",
        "phase": "holdout_evaluation",
        "epoch": int(best_epoch),
        "epochs": EPOCHS,
        "records": len(records),
        "best_epoch": int(best_epoch),
        "best_valid_loss": round(float(best_valid_loss), 8),
        "holdout_metrics": holdout_metrics,
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


def _annotate_ref(ref: dict[str, Any]) -> dict[str, Any]:
    raw = Path(ref["path"]).read_text(encoding="utf-8-sig")
    song = parse_maidata(raw)
    chart = song.charts.get(int(ref["level_index"]))
    if chart is None:
        raise ValueError(f"谱面难度不存在: {ref['key']}")
    from .local.structure_tagger import analyze_chart_tags

    return analyze_chart_tags(chart)


def _build_training_records(
    directory: str | Path,
    model_reviews: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    refs = collect_eligible_chart_refs(directory, min_ds=TRAIN_MIN_DS, max_ds=MAX_TAG_DS)
    if len(refs) < EFFECTIVE_SAMPLE_TARGET:
        raise ValueError(f"训练候选谱面不足 {EFFECTIVE_SAMPLE_TARGET} 条，实际为 {len(refs)} 条")
    cache: dict[str, dict[str, Any]] = {}
    reviewed: dict[str, str] = {}

    def annotate(ref: dict[str, Any]) -> dict[str, Any]:
        key = str(ref["key"])
        if key not in cache:
            cache[key] = _annotate_ref(ref)
        return cache[key]

    def consensus(ref: dict[str, Any], _analysis: dict[str, Any]) -> dict[str, Any]:
        return dict(model_reviews.get(str(ref["key"]), {
            "status": "missing",
            "consistent": False,
            "reason": "model_review_missing",
            "first": {"status": "missing", "tags": []},
            "second": {"status": "missing", "tags": []},
            "third": {"status": "missing", "tags": []},
            "comparison": {"required_models": 2, "consistent": False, "reason": "model_review_missing", "first_tags": [], "second_tags": [], "third_tags": []},
        }))

    def progress(processed: int, total: int, item: dict[str, Any]) -> None:
        key = str(item.get("ref", {}).get("key", processed))
        model_consensus = item.get("model_consensus") if isinstance(item.get("model_consensus"), dict) else {}
        comparison = model_consensus.get("comparison") if isinstance(model_consensus.get("comparison"), dict) else {}
        legacy = int(comparison.get("required_models", 0) or 0) == 3
        if item.get("validation", {}).get("effective"):
            reviewed[key] = "effective_legacy_three" if legacy else "effective_dual"
        elif item.get("external_evidence", {}).get("status") == "skipped_model_disagreement":
            reviewed[key] = "model_disagreement"
        else:
            reviewed[key] = "legacy_three_media_mismatch" if legacy else "dual_media_mismatch"
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        model_agreed = sum(value != "model_disagreement" for value in reviewed.values())
        legacy_three = sum("legacy_three" in value for value in reviewed.values())
        _write_progress({
            "status": "external_review",
            "processed": processed,
            "total": total,
            "effective": sum(value.startswith("effective_") for value in reviewed.values()),
            "model_agreed": model_agreed,
            "dual_model_agreed": model_agreed - legacy_three,
            "legacy_three_model_agreed": legacy_three,
            "model_disagreed": sum(value == "model_disagreement" for value in reviewed.values()),
            "media_checked": sum(value not in {"model_disagreement"} for value in reviewed.values()),
            "media_effective": sum(value.startswith("effective_") for value in reviewed.values()),
            "strict_target": EFFECTIVE_SAMPLE_TARGET,
            "fallback_min": FALLBACK_MIN,
            "fallback_target": FALLBACK_TARGET,
            "current": key,
            "current_status": item.get("external_evidence", {}).get("status", ""),
            "current_overlap": validation.get("overlap", 0.0),
        })

    selected = select_effective_samples(
        refs,
        target=EFFECTIVE_SAMPLE_TARGET,
        seed=TRAINING_SEED,
        annotate=annotate,
        consensus=consensus,
        progress=progress,
    )
    if not FALLBACK_MIN <= len(selected) <= EFFECTIVE_SAMPLE_TARGET:
        raise ValueError(
            f"可追溯外部证据样本数量不在 {FALLBACK_MIN}-{EFFECTIVE_SAMPLE_TARGET} 范围：{len(selected)}"
        )

    records: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        ref = item["ref"]
        raw = Path(ref["path"]).read_text(encoding="utf-8-sig")
        song = parse_maidata(raw)
        chart = song.charts.get(int(ref["level_index"]))
        if chart is None:
            raise ValueError(f"谱面难度不存在: {ref['key']}")
        record = _make_record(ref, raw, chart)
        analysis = item["analysis"]
        model_consensus = dict(item["model_consensus"])
        validation = dict(item["validation"])
        external_evidence = item["external_evidence"]
        baseline = filter_allowed_tags(model_consensus.get("first_tags") or analysis.get("difficulty_tags") or analysis.get("tags") or [])
        candidate_scores = analysis.get("candidate_scores") if isinstance(analysis.get("candidate_scores"), dict) else {}
        training_tags, _training_scores = select_final_tags({
            tag: candidate_scores.get(tag, tag_weight(tag)) for tag in baseline
        })
        external_tags = filter_allowed_tags(external_evidence.get("external_tags") or [])
        validated_tags = filter_allowed_tags(validation.get("intersection_tags") or [])
        record.update({
            "record_version": 7,
            "initial_tags": baseline,
            "external_tags": external_tags,
            "validated_tags": validated_tags,
            "training_tags": training_tags,
            "training_label_source": "dual_model_consensus_top5_weighted_by_external_overlap",
            "model_consensus": model_consensus,
            "validation": validation,
            "external_evidence": external_evidence,
        })
        records.append(record)
        _write_progress({
            "status": "dataset_build",
            "processed": index,
            "total": len(selected),
            "effective": len(selected),
            "strict_target": EFFECTIVE_SAMPLE_TARGET,
            "fallback_min": FALLBACK_MIN,
            "fallback_target": FALLBACK_TARGET,
            "current": ref["key"],
        })
    _apply_difficulty_caps(records)
    selected_validations = [item.get("validation") or {} for item in selected]
    selection_modes = sorted({str(item.get("selection_mode") or "") for item in selected_validations})
    confidence_values = [
        float(item.get("confidence", item.get("overlap", 0.0)) or 0.0)
        for item in selected_validations
    ]
    return records, {
        "candidate_pool": len(refs),
        "scanned": len(reviewed),
        "model_agreed": sum(value != "model_disagreement" for value in reviewed.values()),
        "dual_model_agreed": sum("legacy_three" not in value and value != "model_disagreement" for value in reviewed.values()),
        "legacy_three_model_agreed": sum("legacy_three" in value for value in reviewed.values()),
        "model_disagreed": sum(value == "model_disagreement" for value in reviewed.values()),
        "media_checked": sum(value != "model_disagreement" for value in reviewed.values()),
        "media_effective": sum(value.startswith("effective_") for value in reviewed.values()),
        "effective": len(selected),
        "selection_mode": selection_modes[0] if len(selection_modes) == 1 else "mixed",
        "strict_target": EFFECTIVE_SAMPLE_TARGET,
        "fallback_min": FALLBACK_MIN,
        "fallback_target": FALLBACK_TARGET,
        "confidence_min": round(min(confidence_values), 6) if confidence_values else 0.0,
        "confidence_max": round(max(confidence_values), 6) if confidence_values else 0.0,
        "confidence_mean": round(sum(confidence_values) / max(len(confidence_values), 1), 6),
        "traceable_source_records": sum(
            bool(traceable_sources(item.get("external_evidence") if isinstance(item.get("external_evidence"), dict) else {}))
            for item in selected
        ),
        "selection_seed": TRAINING_SEED,
        "overlap_threshold": EFFECTIVE_OVERLAP,
    }


def _write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    with gzip.open(temp, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temp.replace(path)


def _write_training_artifacts(records: list[dict[str, Any]], selection: dict[str, Any], directory: str | Path) -> None:
    resolved = resolve_levels_directory(directory)
    write_json_atomic(MANIFEST_FILE, {
        "manifest_version": 5,
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "created_at": now(),
        "directory": _relative_path(resolved),
        "min_ds": TRAIN_MIN_DS,
        "max_ds": MAX_TAG_DS,
        "strict_sample_target": EFFECTIVE_SAMPLE_TARGET,
        "fallback_sample_range": [FALLBACK_MIN, FALLBACK_TARGET],
        "selection": selection,
        "reference_sources": list(REFERENCE_SOURCES),
        "charts": [record["source"] for record in records],
        "progress_interval_seconds": PROGRESS_INTERVAL_SECONDS,
    })
    _write_jsonl_atomic(DATASET_FILE, records)
    write_json_gzip_atomic(AUDIT_FILE, {
        "version": 2,
        "generated_at": now(),
        "analysis_engine": RULE_ENGINE,
        "rule_spec_source": RULE_SPEC_SOURCE,
        "rule_version": TAG_RULE_VERSION,
        "record_count": len(records),
        "selection": selection,
        "raw_usage": _usage(records, "raw_tags"),
        "initial_usage": _usage(records, "initial_tags"),
        "external_usage": _usage(records, "external_tags"),
        "validated_usage": _usage(records, "validated_tags"),
        "final_usage": _usage(records, "final_tags"),
        "records": records,
    })


def build_report(records: list[dict[str, Any]], training: dict[str, Any], selection: dict[str, Any]) -> str:
    raw_usage = _usage(records, "raw_tags")
    model_usage = _usage(records, "initial_tags")
    external_usage = _usage(records, "external_tags")
    validated_usage = _usage(records, "validated_tags")
    final_usage = _usage(records, "final_tags")
    raw_map = {row["tag"]: row for row in raw_usage}
    model_map = {row["tag"]: row for row in model_usage}
    external_map = {row["tag"]: row for row in external_usage}
    validated_map = {row["tag"]: row for row in validated_usage}
    final_map = {row["tag"]: row for row in final_usage}
    lines = [
        "# 谱面标签多来源重算与本地模型报告",
        "",
        f"- 生成时间：{now()}",
        f"- 规则来源：{RULE_SPEC_SOURCE}；规则版本：{TAG_RULE_VERSION}；分析引擎：{RULE_ENGINE}",
        f"- 训练样本：随机扫描候选 {selection['scanned']} 条，双模型一致 {selection.get('dual_model_agreed', selection['model_agreed'])} 条，历史三模型一致 {selection.get('legacy_three_model_agreed', 0)} 条，进入联网校验 {selection['media_checked']} 条，入选 {selection['effective']} 条；选择模式为 {selection.get('selection_mode', 'unknown')}，严格目标 {EFFECTIVE_SAMPLE_TARGET} 条，降级范围 {FALLBACK_MIN}-{FALLBACK_TARGET} 条",
        f"- 训练范围：{TRAIN_MIN_DS:.1f}-{MAX_TAG_DS:.1f}，Expert / Master / Re:Master；运行时按请求分析，不执行全量打标",
        f"- 有效样本口径：当前对话模型与 AstrBot Gemini 模型标签集合完全一致后，才进入联网校验；此前三模型完全一致的记录继续保留；重合度按模型标签被外部证据覆盖的比例计算，严格模式要求 ≥ {EFFECTIVE_OVERLAP:.0%}；若严格样本不足，则按重合度置信度排序选取最高 {FALLBACK_TARGET} 条，但每条仍必须保留可追溯外部来源；对称 Jaccard 另存于审计字段；不一致的样本不请求媒体校验",
        "- Bilibili 搜索页是一级检索依据；每条外部证据保留搜索页 URL、查询词、候选数量、视频页面、BVID、标题/简介和评论摘要，公开 API 仅作补充；没有可追溯来源的结果不能入选。",
        f"- 参考来源：{', '.join(REFERENCE_SOURCES)}",
        "- 模型训练目标为双模型共识标签；最终展示标签另按定数占比上限与单谱面最多 5 个标签裁剪，裁剪结果不作为负样本。",
        "- 运行时不保存标签库；每次请求从 Levels 读取对应难度并调用本地模型，训练审核结果只保存在元数据中。",
        "",
        "## 标签使用率",
        "",
        "| 标签 | 原始候选 | 模型共识 | 外部来源 | 验证交集 | 模型训练目标 | 最终展示标签 |",
        "|:--|--:|--:|--:|--:|--:|--:|",
    ]
    training_map = {row["tag"]: row for row in _usage(records, TRAINING_TARGET_FIELD)}
    for tag in ALLOWED_TAGS:
        lines.append(f"| {tag} | {raw_map[tag]['count']} ({raw_map[tag]['rate']:.2%}) | {model_map[tag]['count']} ({model_map[tag]['rate']:.2%}) | {external_map[tag]['count']} ({external_map[tag]['rate']:.2%}) | {validated_map[tag]['count']} ({validated_map[tag]['rate']:.2%}) | {training_map[tag]['count']} ({training_map[tag]['rate']:.2%}) | {final_map[tag]['count']} ({final_map[tag]['rate']:.2%}) |")
    lines.extend([
        "",
        "## 逐谱面有效样本标注",
        "",
        "| # | Key | 定数 | BPM | 初步标签 | 双模型标签 | 外部标签 | 验证交集 | 重合度 | 模型训练标签 | 最终展示标签 | 媒体来源（BVID） |",
        "|--:|:--|--:|--:|:--|:--|:--|:--|--:|:--|:--|:--|",
    ])
    for index, record in enumerate(records, start=1):
        source = record["source"]
        validation = record.get("validation") or {}
        external = record.get("external_evidence") or {}
        media_sources = traceable_sources(external)
        media_ids = "、".join(str(item.get("bvid", "")) for item in media_sources) or "未找到"
        lines.append(
            f"| {index} | {record['record_key']} {source.get('title', '')} {source.get('difficulty', '')} | "
            f"{float(source.get('ds', 0.0)):.1f} | {float(source.get('bpm', 0.0)):.1f} | "
            f"{'、'.join(record.get('initial_tags') or []) or '无'} | "
            f"{'、'.join((record.get('model_consensus') or {}).get('first_tags') or []) or '无'} | "
            f"{'、'.join(record.get('external_tags') or []) or '无'} | "
            f"{'、'.join(record.get('validated_tags') or []) or '无'} | "
            f"{float(validation.get('overlap', 0.0) or 0.0):.2%} | "
            f"{'、'.join(record.get(TRAINING_TARGET_FIELD) or []) or '无'} | "
            f"{'、'.join(record.get('final_tags') or []) or '无'} | "
            f"{len(media_sources)} 条：{media_ids} |"
        )
    lines.extend([
        "",
        "## 训练结果",
        "",
        f"- 模型：{training['model_file']}；元数据：{training['model_meta']}；Loss：{LOSS_FILE.relative_to(Root)}。",
        f"- 训练/验证/留出：{training['train_records']} / {training['valid_records']} / {training.get('holdout_records', 0)}；特征数：{training['feature_count']}；集成成员：{training.get('ensemble_members', 1)}；最佳 epoch：{training['best_epoch']}；最佳验证 Loss：{training['best_valid_loss']}",
        f"- 留出集指标：{json.dumps(training.get('holdout_metrics', {}), ensure_ascii=False, separators=(',', ':'))}",
        "- 模型是离线多标签分类器；训练目标来自双模型一致结果，外部媒体重合度作为监督权重，兼容保留此前三模型一致记录；训练使用按歌曲分组留出、五成员集成、早停、类别不平衡权重、逐标签阈值校准和质量门禁；运行时不请求网络或对话模型。",
        "",
        "## 数据文件",
        "",
        f"- 全量清单：{MANIFEST_FILE.relative_to(Root)}",
        f"- 训练元数据：{DATASET_FILE.relative_to(Root)}（含完整 inote、事件、BPM 段、窗口、撞尾候选、标签位置和外部证据）",
        f"- 审计记录：{AUDIT_FILE.relative_to(Root)}",
        "- 运行时标签不写入静态标签库；每次请求从 Levels 解析对应难度后交给本地模型。",
    ])
    return "\n".join(lines) + "\n"


def run_full_pipeline(
    directory: str = "static/Levels",
    model_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _write_progress({"task": "external_review", "status": "starting", "processed": 0, "total": 0, "current": "准备有效样本筛选"})
    if not model_reviews:
        raise ValueError("缺少双模型审核结果，不能进入联网校验或训练")
    records, selection = _build_training_records(directory, model_reviews)
    _write_training_artifacts(records, selection, directory)
    training = train_local_model(records)
    report = build_report(records, training, selection)
    REPORT_FILE.write_text(report, encoding="utf-8")
    result = {
        "ok": True,
        "selection": selection,
        "training": training,
        "records": len(records),
        "report": str(REPORT_FILE.relative_to(Root)),
    }
    write_json_atomic(RUN_FILE, {
        "version": 3,
        "status": "completed",
        "analysis_engine": RULE_ENGINE,
        "rule_version": TAG_RULE_VERSION,
        "selection": selection,
        "training": training,
        "report": result["report"],
        "updated_at": now(),
    })
    _write_progress({
        "task": "pipeline",
        "status": "completed",
        "processed": len(records),
        "total": len(records),
        "training_best_epoch": training["best_epoch"],
        "training_best_valid_loss": training["best_valid_loss"],
        "finished_at": now(),
    })
    return result


def _load_model_reviews(path: str | Path) -> dict[str, dict[str, Any]]:
    review_path = Path(path)
    if not review_path.is_file():
        raise FileNotFoundError(f"审核结果文件不存在: {review_path}")
    payload = json.loads(review_path.read_text(encoding="utf-8-sig"))
    reviews = payload.get("reviews") if isinstance(payload, dict) else payload
    if not isinstance(reviews, dict):
        raise ValueError("审核结果必须是对象，或包含 reviews 对象")
    return {str(key): value for key, value in reviews.items() if isinstance(value, dict)}


def main() -> None:
    parser = argparse.ArgumentParser(description="根据多模型审核结果训练本地谱面标签模型")
    parser.add_argument("--reviews", required=True, help="双模型审核结果 JSON 文件")
    parser.add_argument("--levels", default="static/Levels", help="本地谱面目录")
    args = parser.parse_args()
    reviews = _load_model_reviews(args.reviews)
    print(json.dumps(run_full_pipeline(args.levels, reviews), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
