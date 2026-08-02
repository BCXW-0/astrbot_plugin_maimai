from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ... import Root, log
from .chart_metadata import TAG_WINDOW_HINTS, _review_collision, _slide_collision_candidates, _window_payload
from .constants import ALLOWED_TAGS, DIFFICULTY_NAMES, TAG_CATEGORIES, TAG_RULE_VERSION, TAG_WEIGHTS, TARGET_LEVEL_INDEXES
from .local.maidata_parser import MaidataChart, MaidataSong, parse_maidata
from .local.structure_tagger import extract_features
from .official_downloader import DEFAULT_LEVELS_PATH, OfficialChartDownloader, validate_mode, validate_range
from .rule_tags import filter_allowed_tags, select_final_tags
from .storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic

CN_TZ = timezone(timedelta(hours=8))
AUTO_PROGRESS_FILE = Root / "static" / "auto_tag_progress.json"
MODEL_FILE = Root / "static" / "maimai_chart_tag_model.npz"
MODEL_META_FILE = Root / "static" / "maimai_chart_tag_model.json"
MODEL_NAME = "local_chart_tag_model"
MODEL_THRESHOLD = 0.50
MODEL_FALLBACK_THRESHOLD = 0.25
CATALOG_MIN_DS = 10.0
CATALOG_MAX_DS = 15.0
MAPPING_VERSION = 1


def now_text() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _round(value: Any, digits: int = 6) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _note_counts(chart: MaidataChart) -> dict[str, int]:
    counts = {"tap": 0, "hold": 0, "slide": 0, "touch": 0, "break": 0}
    for event in chart.events:
        if event.kind in counts:
            counts[event.kind] += 1
    counts["total"] = sum(counts.values())
    return counts


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


class LocalChartModel:
    def __init__(self, model_file: Path = MODEL_FILE, metadata_file: Path = MODEL_META_FILE):
        self.model_file = Path(model_file)
        self.metadata_file = Path(metadata_file)
        self.loaded_at = ""
        self.metadata: dict[str, Any] = {}
        self.feature_names: list[str] = []
        self.label_names: list[str] = []
        self.weights: np.ndarray | None = None
        self.bias: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self._mtime: tuple[int, int] | None = None

    def _load(self) -> None:
        if not self.model_file.is_file() or not self.metadata_file.is_file():
            raise FileNotFoundError("本地谱面标签模型文件不存在")
        stat = self.model_file.stat()
        marker = (int(stat.st_mtime_ns), int(stat.st_size))
        if self._mtime == marker and self.weights is not None:
            return
        metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))
        archive = np.load(self.model_file, allow_pickle=False)
        feature_names = [str(value) for value in archive["feature_names"].tolist()]
        label_names = [str(value) for value in archive["label_names"].tolist()]
        weights = np.asarray(archive["weights"], dtype=np.float64)
        bias = np.asarray(archive["bias"], dtype=np.float64)
        mean = np.asarray(archive["mean"], dtype=np.float64)
        scale = np.asarray(archive["scale"], dtype=np.float64)
        if weights.shape != (len(feature_names), len(label_names)):
            raise ValueError("本地标签模型权重形状与特征元数据不一致")
        if any(array.shape != (len(feature_names),) for array in (mean, scale)):
            raise ValueError("本地标签模型归一化参数与特征元数据不一致")
        if bias.shape != (len(label_names),):
            raise ValueError("本地标签模型偏置与标签元数据不一致")
        self.metadata = metadata if isinstance(metadata, dict) else {}
        self.feature_names = feature_names
        self.label_names = label_names
        self.weights = weights
        self.bias = bias
        self.mean = mean
        self.scale = np.where(scale == 0, 1.0, scale)
        self.loaded_at = now_text()
        self._mtime = marker

    def predict(self, song: MaidataSong, chart: MaidataChart) -> dict[str, Any]:
        self._load()
        assert self.weights is not None
        assert self.bias is not None
        assert self.mean is not None
        assert self.scale is not None
        features = extract_features(chart)
        candidates, _excluded = _slide_collision_candidates(chart)
        accepted = _review_collision(candidates)
        windows = _window_payload(chart)
        values: dict[str, float] = {
            f"feature.{key}": float(value)
            for key, value in features.items()
            if isinstance(value, (int, float)) and np.isfinite(float(value))
        }
        values.update({
            "context.ds": float(chart.ds or 0.0),
            "context.bpm": float(chart.bpm or song.whole_bpm or 0.0),
            "context.whole_bpm": float(song.whole_bpm or chart.bpm or 0.0),
            "context.level_index": float(chart.level_index),
            "context.diff_id": float(chart.diff_id),
            "context.event_count": float(len(chart.events)),
            "context.window_count": float(len(windows)),
            "context.collision_count": float(len(candidates)),
            "context.accepted_collision_count": float(len(accepted)),
        })
        vector = np.asarray([values.get(name, 0.0) for name in self.feature_names], dtype=np.float64)
        normalized = (vector - self.mean) / self.scale
        logits = normalized @ self.weights + self.bias
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -40.0, 40.0)))
        probability_map = {
            tag: round(float(probability), 6)
            for tag, probability in zip(self.label_names, probabilities)
            if tag in ALLOWED_TAGS
        }
        candidates_scores = {tag: value for tag, value in probability_map.items() if value >= MODEL_THRESHOLD}
        if not candidates_scores and probability_map:
            top_tag, top_score = max(probability_map.items(), key=lambda item: item[1])
            if top_score >= MODEL_FALLBACK_THRESHOLD:
                candidates_scores = {top_tag: top_score}
        model_tags, model_scores = select_final_tags(candidates_scores)
        positions = _tag_positions(model_tags, windows, candidates, accepted)
        return {
            "model_tags": filter_allowed_tags(model_tags),
            "model_scores": {tag: _round(score, 6) for tag, score in model_scores.items()},
            "model_probabilities": probability_map,
            "features": {key: _round(value, 6) for key, value in features.items()},
            "windows": windows,
            "collision_candidates": candidates,
            "accepted_collision_ids": [item["candidate_id"] for item in accepted],
            "tag_positions": positions,
            "note_counts": _note_counts(chart),
            "model_loaded_at": self.loaded_at,
            "model_metadata": {
                "model_type": self.metadata.get("model_type", ""),
                "best_epoch": self.metadata.get("best_epoch"),
                "best_valid_loss": self.metadata.get("best_valid_loss"),
                "feature_count": len(self.feature_names),
                "model_file": _relative_path(self.model_file),
            },
        }


def _tag_positions(
    tags: list[str],
    windows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        if tag == "撞尾":
            result[tag] = [
                {
                    "kind": "slide_collision",
                    "event_indexes": [item["slide_event_index"], item["target_event_index"]],
                    "raw": f"{item['slide_raw']} -> {item['target_raw']}",
                    "position": {
                        "slide_time": item["slide_start"],
                        "passed_time": item["passed_time"],
                        "target_time": item["target_time"],
                        "area": item["area"],
                        "delta": item["delta"],
                    },
                }
                for item in accepted[:3]
            ]
            continue
        hints = TAG_WINDOW_HINTS.get(tag, ())
        matching = [window for window in windows if hints and any(hint in str(window.get("sequence", "")) for hint in hints)]
        if not matching:
            matching = windows[:2]
        result[tag] = [
            {
                "kind": "two_measure_window",
                "window_id": window.get("id"),
                "event_indexes": window.get("event_indexes", []),
                "raw": window.get("sequence", ""),
                "position": {
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "bpm": window.get("bpm"),
                },
            }
            for window in matching[:3]
        ]
    return result


class LocalChartCatalog:
    def __init__(self, directory: str | Path = DEFAULT_LEVELS_PATH):
        self.directory = Path(directory).resolve()
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._refs: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def _file_signature(self) -> tuple[tuple[str, int, int], ...]:
        entries = []
        for path in sorted(self.directory.glob("*.txt")):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(entries)

    def refs(self) -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            signature = self._file_signature()
            if signature == self._signature:
                return [dict(item) for item in self._refs]
            refs: dict[str, dict[str, Any]] = {}
            for path in sorted(self.directory.glob("*.txt")):
                try:
                    raw = path.read_bytes()
                    song = parse_maidata(raw.decode("utf-8-sig"))
                except Exception:
                    continue
                if not song.short_id or not song.title:
                    continue
                if str(song.title).lstrip().startswith("[") or "宴" in str(song.meta.get("genre", "")):
                    continue
                source_hash = _sha256(raw)
                for level_index in TARGET_LEVEL_INDEXES:
                    chart = song.charts.get(level_index)
                    if chart is None or not CATALOG_MIN_DS <= float(chart.ds or 0.0) <= CATALOG_MAX_DS:
                        continue
                    key = f"{song.short_id}:{level_index}"
                    refs[key] = {
                        "key": key,
                        "path": str(path),
                        "source_path": _relative_path(path),
                        "file": path.name,
                        "source_sha256": source_hash,
                        "song_id": str(song.short_id),
                        "title": song.title,
                        "artist": song.artist,
                        "genre": song.meta.get("genre", ""),
                        "version": song.version,
                        "level_index": level_index,
                        "diff_id": chart.diff_id,
                        "difficulty": DIFFICULTY_NAMES.get(level_index, str(level_index)),
                        "level": song.meta.get(f"lv_{chart.diff_id}", str(chart.ds)),
                        "ds": float(chart.ds),
                        "bpm": float(chart.bpm or song.whole_bpm or 0.0),
                        "whole_bpm": float(song.whole_bpm or chart.bpm or 0.0),
                        "designer": chart.designer,
                    }
            self._signature = signature
            self._refs = sorted(refs.values(), key=lambda item: (str(item["song_id"]), int(item["level_index"])))
            return [dict(item) for item in self._refs]

    def find(self, key: str) -> dict[str, Any] | None:
        return next((item for item in self.refs() if item.get("key") == key), None)


def _base_item(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "song_id": ref.get("song_id", ""),
        "chart_id": None,
        "title": ref.get("title", ""),
        "type": "local",
        "difficulty": ref.get("difficulty", ""),
        "level_index": ref.get("level_index"),
        "level": ref.get("level", ""),
        "ds": ref.get("ds"),
        "fit_diff": None,
        "bpm": ref.get("bpm"),
        "artist": ref.get("artist", ""),
        "genre": ref.get("genre", ""),
        "version": ref.get("version", ""),
        "is_new": False,
        "charter": ref.get("designer", ""),
        "notes": {},
        "tags": [],
        "manual_tags": [],
        "llm_tags": [],
        "model_tags": [],
        "final_tags": [],
        "tag_scores": {},
        "model_scores": {},
        "model_probabilities": {},
        "tag_categories": {},
        "evidence": [],
        "tag_positions": {},
        "analysis_status": "pending",
        "analysis_engine": "",
        "tag_status": "",
        "tag_error": "",
        "source_file": ref.get("file", ""),
        "source_path": ref.get("source_path", ""),
        "source_sha256": ref.get("source_sha256", ""),
        "mapping": _mapping_from_ref(ref),
        "updated_at": "",
    }


def _mapping_from_ref(ref: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    old = old if isinstance(old, dict) else {}
    chart_id = old.get("chart_id")
    diff_id = int(ref.get("diff_id", 0) or 0)
    song_id = str(ref.get("song_id", ""))
    return {
        "mapping_version": MAPPING_VERSION,
        "tag_file": _relative_path(CHART_TAGS_FILE),
        "mapping_id": f"{song_id}:{diff_id}",
        "tag_file_key": str(ref.get("key", "")),
        "music_id": song_id,
        "shortid": song_id,
        "chart_id": chart_id,
        "diff_id": diff_id,
        "level_index": int(ref.get("level_index", 0) or 0),
        "difficulty": ref.get("difficulty", ""),
        "chart_section": f"inote_{diff_id}",
        "chart_file": ref.get("source_path", ""),
        "chart_file_name": ref.get("file", ""),
        "chart_file_sha256": ref.get("source_sha256", ""),
        "chart_info": {
            "title": ref.get("title", ""),
            "artist": ref.get("artist", ""),
            "genre": ref.get("genre", ""),
            "version": ref.get("version", ""),
            "bpm": ref.get("bpm"),
            "whole_bpm": ref.get("whole_bpm"),
            "level": ref.get("level", ""),
            "ds": ref.get("ds"),
            "designer": ref.get("designer", ""),
        },
    }


def _chart_file_mapping(mappings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the reverse index from one local chart file to all its tag keys."""
    files: dict[str, dict[str, Any]] = {}
    for key, mapping in mappings.items():
        chart_file = str(mapping.get("chart_file", ""))
        if not chart_file:
            continue
        entry = files.setdefault(chart_file, {
            "chart_file": chart_file,
            "chart_file_name": mapping.get("chart_file_name", ""),
            "chart_file_sha256": mapping.get("chart_file_sha256", ""),
            "tag_file": mapping.get("tag_file", _relative_path(CHART_TAGS_FILE)),
            "shortid": mapping.get("shortid", ""),
            "chart_info": mapping.get("chart_info", {}),
            "tag_file_keys": [],
            "chart_sections": [],
        })
        entry["tag_file_keys"].append(key)
        entry["chart_sections"].append({
            "tag_file_key": key,
            "mapping_id": mapping.get("mapping_id", ""),
            "diff_id": mapping.get("diff_id"),
            "level_index": mapping.get("level_index"),
            "chart_section": mapping.get("chart_section", ""),
        })
    for entry in files.values():
        entry["tag_file_keys"] = sorted(set(entry["tag_file_keys"]))
        entry["chart_sections"] = sorted(
            entry["chart_sections"],
            key=lambda item: (int(item.get("level_index", 0) or 0), str(item.get("tag_file_key", ""))),
        )
    return dict(sorted(files.items()))


def _mapping_is_current(data: dict[str, Any], refs: list[dict[str, Any]]) -> bool:
    mappings = data.get("chart_mapping") if isinstance(data.get("chart_mapping"), dict) else {}
    if set(mappings) != {str(ref.get("key", "")) for ref in refs}:
        return False
    for ref in refs:
        mapping = mappings.get(str(ref["key"]))
        if not isinstance(mapping, dict):
            return False
        if (
            mapping.get("chart_file") != ref.get("source_path")
            or mapping.get("chart_file_sha256") != ref.get("source_sha256")
            or mapping.get("tag_file") != _relative_path(CHART_TAGS_FILE)
            or mapping.get("diff_id") != ref.get("diff_id")
            or mapping.get("level_index") != ref.get("level_index")
            or mapping.get("tag_file_key") != ref.get("key")
        ):
            return False
    expected_files = _chart_file_mapping(mappings)
    actual_files = data.get("chart_file_mapping") if isinstance(data.get("chart_file_mapping"), dict) else {}
    return actual_files == expected_files


class AutoTagJob:
    def __init__(self, directory: str | Path = DEFAULT_LEVELS_PATH):
        self.downloader = OfficialChartDownloader(directory)
        self.catalog = LocalChartCatalog(directory)
        self.model = LocalChartModel()
        self.worker_thread: threading.Thread | None = None
        self.stop_requested = False
        self.lock = threading.RLock()

    def _state(self) -> dict[str, Any]:
        if not AUTO_PROGRESS_FILE.exists():
            return {}
        try:
            data = json.loads(AUTO_PROGRESS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_state(self, updates: dict[str, Any]) -> dict[str, Any]:
        state = self._state()
        state.update(updates)
        state["updated_at"] = now_text()
        write_json_atomic(AUTO_PROGRESS_FILE, state)
        return state

    def status(self) -> dict[str, Any]:
        state = self._state()
        refs = self.catalog.refs()
        data = read_chart_tags()
        if not _mapping_is_current(data, refs):
            data = self._sync_mapping(refs)
        model_error = ""
        try:
            self.model._load()
        except Exception as exc:
            model_error = f"{type(exc).__name__}: {exc}"
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        local_items = [charts.get(ref["key"]) for ref in refs]
        analyzed = sum(1 for item in local_items if isinstance(item, dict) and item.get("analysis_status") == "completed")
        tagged = sum(1 for item in local_items if isinstance(item, dict) and filter_allowed_tags(item.get("model_tags") or item.get("llm_tags") or []))
        running = bool(self.worker_thread and self.worker_thread.is_alive())
        state.update({
            "ok": True,
            "running": running,
            "stop_requested": self.stop_requested,
            "catalog_total": len(refs),
            "catalog_analyzed": analyzed,
            "catalog_tagged": tagged,
            "catalog_untagged": max(0, len(refs) - tagged),
            "model_file": _relative_path(MODEL_FILE),
            "model_metadata": self.model.metadata if self.model.metadata else {},
            "model_error": model_error,
        })
        return state

    def start_download(self, *, min_ds: Any, max_ds: Any, mode: Any, query: Any = "") -> dict[str, Any]:
        low, high = validate_range(min_ds, max_ds)
        mode_value, query_value = validate_mode(mode, query)
        with self.lock:
            if self.worker_thread and self.worker_thread.is_alive():
                return {"ok": True, "message": "已有自动打标任务在运行", **self.status()}
            self.stop_requested = False
            self.worker_thread = threading.Thread(
                target=self._run_download,
                args=(low, high, mode_value, query_value),
                name="maimai-chart-download",
                daemon=True,
            )
            self._write_state({
                "task": "download",
                "status": "running",
                "min_ds": low,
                "max_ds": high,
                "mode": mode_value,
                "query": query_value,
                "selected": 0,
                "total": 0,
                "processed": 0,
                "started_at": now_text(),
                "error": "",
            })
            self.worker_thread.start()
        return {"ok": True, "message": "谱面下载任务已启动", **self.status()}

    def start_analysis(self, *, min_ds: Any, max_ds: Any, force: Any = False) -> dict[str, Any]:
        low, high = validate_range(min_ds, max_ds)
        with self.lock:
            if self.worker_thread and self.worker_thread.is_alive():
                return {"ok": True, "message": "已有自动打标任务在运行", **self.status()}
            self.stop_requested = False
            force_value = bool(force)
            self.worker_thread = threading.Thread(
                target=self._run_analysis,
                args=(low, high, force_value),
                name="maimai-local-chart-analysis",
                daemon=True,
            )
            self._write_state({
                "task": "analysis",
                "status": "running",
                "min_ds": low,
                "max_ds": high,
                "force": force_value,
                "started_at": now_text(),
                "error": "",
            })
            self.worker_thread.start()
        return {"ok": True, "message": "本地模型谱面分析任务已启动", **self.status()}

    def stop(self) -> dict[str, Any]:
        self.stop_requested = True
        state = self._write_state({"stop_requested": True, "message": "已请求停止，当前谱面处理完成后停止"})
        return {"ok": True, **state, **self.status()}

    def shutdown(self) -> None:
        self.stop_requested = True
        thread = self.worker_thread
        if thread and thread.is_alive():
            thread.join(20)
        if not thread or not thread.is_alive():
            self.worker_thread = None
        self._write_state({"status": "stopped", "running": False, "message": "自动打标任务已停止"})

    def _progress(self, updates: dict[str, Any]) -> None:
        self._write_state(updates)

    def _sync_mapping(self, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self._sync_mapping_for_refs(refs if refs is not None else self.catalog.refs())

    def _sync_mapping_for_refs(self, refs: list[dict[str, Any]]) -> dict[str, Any]:
        data = read_chart_tags()
        if not isinstance(data, dict):
            data = {}
        charts = data.get("charts") if isinstance(data.get("charts"), dict) else {}
        mappings: dict[str, Any] = {}
        for ref in refs:
            key = str(ref["key"])
            old = charts.get(key) if isinstance(charts.get(key), dict) else {}
            item = _base_item(ref)
            if isinstance(old, dict):
                item.update(old)
            mapping = _mapping_from_ref(ref, item)
            item["mapping"] = mapping
            item["source_file"] = ref.get("file", "")
            item["source_path"] = ref.get("source_path", "")
            item["source_sha256"] = ref.get("source_sha256", "")
            charts[key] = item
            mappings[key] = mapping
        data.update({
            "version": max(3, int(data.get("version", 1) or 1)),
            "mapping_version": MAPPING_VERSION,
            "allowed_tags": ALLOWED_TAGS,
            "tag_weights": TAG_WEIGHTS,
            "tag_rule_version": TAG_RULE_VERSION,
            "analysis_engine": MODEL_NAME,
            "chart_mapping": mappings,
            "chart_file_mapping": _chart_file_mapping(mappings),
            "charts": charts,
            "generated_at": now_text(),
        })
        with self.lock:
            write_json_atomic(CHART_TAGS_FILE, data)
        return data

    def _run_download(self, low: float, high: float, mode: str, query: str) -> None:
        try:
            result = self.downloader.download(
                min_ds=low,
                max_ds=high,
                mode=mode,
                query=query,
                should_stop=lambda: self.stop_requested,
                progress=lambda state: self._progress({"task": "download", "status": "running", **state}),
            )
            self._progress({"task": "download", "status": "completed" if result.get("completed") else "stopped", **result, "finished_at": now_text()})
            self.catalog._signature = None
            self._sync_mapping()
        except Exception as exc:
            log.error(f"谱面下载任务失败: {type(exc).__name__} - {exc}")
            self._progress({"task": "download", "status": "failed", "error": f"{type(exc).__name__}: {exc}", "finished_at": now_text()})
        finally:
            self.worker_thread = None

    def _run_analysis(self, low: float, high: float, force: bool) -> None:
        try:
            data = self._sync_mapping()
            refs = [ref for ref in self.catalog.refs() if low <= float(ref["ds"]) <= high]
            charts = data.get("charts") if isinstance(data.get("charts"), dict) else {}
            data.update({
                "version": max(3, int(data.get("version", 1) or 1)),
                "mapping_version": MAPPING_VERSION,
                "allowed_tags": ALLOWED_TAGS,
                "tag_weights": TAG_WEIGHTS,
                "tag_rule_version": TAG_RULE_VERSION,
                "analysis_engine": MODEL_NAME,
            })
            self._progress({
                "task": "analysis",
                "status": "running",
                "min_ds": low,
                "max_ds": high,
                "force": force,
                "total": len(refs),
                "processed": 0,
                "analyzed": 0,
                "skipped": 0,
                "failed": 0,
                "current": "",
            })
            analyzed = skipped = failed = 0
            for index, ref in enumerate(refs, start=1):
                if self.stop_requested:
                    self._progress({"status": "stopped", "processed": index - 1, "current": ""})
                    break
                old = charts.get(ref["key"]) if isinstance(charts.get(ref["key"]), dict) else {}
                if not force and self._has_current_model(old, ref):
                    skipped += 1
                    self._progress({"processed": index, "skipped": skipped, "current": ref["key"]})
                    continue
                try:
                    raw = Path(ref["path"]).read_text(encoding="utf-8-sig")
                    song = parse_maidata(raw)
                    chart = song.charts.get(int(ref["level_index"]))
                    if chart is None:
                        raise ValueError("谱面难度不存在")
                    prediction = self.model.predict(song, chart)
                    charts[ref["key"]] = self._build_item(ref, old, prediction)
                    analyzed += 1
                except Exception as exc:
                    failed += 1
                    charts[ref["key"]] = self._failed_item(ref, old, exc)
                    log.error(f"本地谱面分析失败 {ref['key']}: {type(exc).__name__} - {exc}")
                data["charts"] = charts
                data["generated_at"] = now_text()
                with self.lock:
                    write_json_atomic(CHART_TAGS_FILE, data)
                self._progress({
                    "status": "running",
                    "processed": index,
                    "analyzed": analyzed,
                    "skipped": skipped,
                    "failed": failed,
                    "current": f"{ref['key']} {ref['title']}",
                })
            data["charts"] = charts
            data["generated_at"] = now_text()
            data["analysis_engine"] = MODEL_NAME
            with self.lock:
                write_json_atomic(CHART_TAGS_FILE, data)
            final_status = "stopped" if self.stop_requested else "completed"
            self._progress({
                "task": "analysis",
                "status": final_status,
                "processed": min(len(refs), int(self._state().get("processed", len(refs)) or 0)),
                "total": len(refs),
                "analyzed": analyzed,
                "skipped": skipped,
                "failed": failed,
                "current": "",
                "finished_at": now_text(),
            })
        except Exception as exc:
            log.error(f"本地模型谱面分析任务失败: {type(exc).__name__} - {exc}")
            self._progress({"task": "analysis", "status": "failed", "error": f"{type(exc).__name__}: {exc}", "finished_at": now_text()})
        finally:
            self.worker_thread = None

    @staticmethod
    def _has_current_model(item: dict[str, Any], ref: dict[str, Any]) -> bool:
        return bool(
            item.get("analysis_status") == "completed"
            and item.get("analysis_engine") == MODEL_NAME
            and item.get("source_sha256") == ref.get("source_sha256")
            and isinstance(item.get("model_tags"), list)
            and bool(item.get("model_tags"))
        )

    def _build_item(self, ref: dict[str, Any], old: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
        item = _base_item(ref)
        if isinstance(old, dict):
            for field in ("manual_tags", "evidence", "tag_error"):
                if field in old:
                    item[field] = old[field]
        manual_tags = filter_allowed_tags(item.get("manual_tags", []))
        model_tags = filter_allowed_tags(prediction.get("model_tags", []))
        model_scores = prediction.get("model_scores") if isinstance(prediction.get("model_scores"), dict) else {}
        score_map = {tag: float(value) for tag, value in model_scores.items() if tag in model_tags}
        final_tags, final_scores = select_final_tags(score_map, manual_tags)
        item.update({
            "manual_tags": manual_tags,
            "model_tags": model_tags,
            "model_scores": model_scores,
            "model_probabilities": prediction.get("model_probabilities", {}),
            "llm_tags": model_tags,
            "final_tags": final_tags,
            "tags": final_tags,
            "tag_scores": final_scores,
            "tag_categories": {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES},
            "tag_positions": prediction.get("tag_positions", {}),
            "model_features": prediction.get("features", {}),
            "model_windows": prediction.get("windows", []),
            "collision_candidates": prediction.get("collision_candidates", []),
            "accepted_collision_ids": prediction.get("accepted_collision_ids", []),
            "model_metadata": prediction.get("model_metadata", {}),
            "mapping": _mapping_from_ref(ref, old),
            "analysis_status": "completed",
            "analysis_engine": MODEL_NAME,
            "analysis_version": 1,
            "tag_status": "done" if final_tags else "no_evidence",
            "tag_error": "",
            "notes": prediction.get("note_counts", {}),
            "updated_at": now_text(),
        })
        return item

    @staticmethod
    def _failed_item(ref: dict[str, Any], old: dict[str, Any], exc: Exception) -> dict[str, Any]:
        item = _base_item(ref)
        if isinstance(old, dict):
            for field in ("manual_tags", "evidence", "model_tags", "model_scores", "model_probabilities", "final_tags", "tags", "tag_scores", "tag_categories", "tag_positions"):
                if field in old:
                    item[field] = old[field]
        item.update({
            "analysis_status": "failed",
            "analysis_engine": MODEL_NAME,
            "tag_status": "failed",
            "tag_error": f"{type(exc).__name__}: {exc}",
            "updated_at": now_text(),
        })
        return item

    def search(self, query: str = "", *, min_ds: Any = CATALOG_MIN_DS, max_ds: Any = CATALOG_MAX_DS, limit: int = 80) -> list[dict[str, Any]]:
        low, high = validate_range(min_ds, max_ds)
        text = str(query or "").strip().lower()
        limit = max(1, min(200, int(limit or 80)))
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        result: list[dict[str, Any]] = []
        for ref in self.catalog.refs():
            if not low <= float(ref["ds"]) <= high:
                continue
            haystack = " ".join(str(ref.get(key, "") or "") for key in ("key", "song_id", "title", "artist", "designer", "difficulty", "level", "ds")).lower()
            if text and text not in haystack:
                continue
            item = _base_item(ref)
            stored = charts.get(ref["key"]) if isinstance(charts, dict) else None
            if isinstance(stored, dict):
                item.update(stored)
            result.append(_summary(item, ref["key"]))
            if len(result) >= limit:
                break
        return result

    def detail(self, key: str) -> dict[str, Any] | None:
        ref = self.catalog.find(key)
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        stored = charts.get(key) if isinstance(charts, dict) else None
        if ref is None and not isinstance(stored, dict):
            return None
        item = _base_item(ref) if ref else {}
        if isinstance(stored, dict):
            item.update(stored)
        mapping = item.get("mapping") if isinstance(item.get("mapping"), dict) else {}
        file_mappings = data.get("chart_file_mapping", {}) if isinstance(data, dict) else {}
        file_mapping = file_mappings.get(mapping.get("chart_file"), {}) if isinstance(file_mappings, dict) else {}
        return _detail(item, key, file_mapping if isinstance(file_mapping, dict) else {})


def _summary(item: dict[str, Any], key: str) -> dict[str, Any]:
    model_tags = filter_allowed_tags(item.get("model_tags") or item.get("llm_tags") or [])
    manual_tags = filter_allowed_tags(item.get("manual_tags", []))
    final_tags = filter_allowed_tags(item.get("final_tags") or item.get("tags") or [*model_tags, *manual_tags])
    mapping = item.get("mapping") if isinstance(item.get("mapping"), dict) else {}
    return {
        "key": key,
        "song_id": item.get("song_id", ""),
        "title": item.get("title", ""),
        "artist": item.get("artist", ""),
        "difficulty": item.get("difficulty", ""),
        "level": item.get("level", ""),
        "ds": item.get("ds"),
        "bpm": item.get("bpm"),
        "charter": item.get("charter", ""),
        "type": item.get("type", ""),
        "manual_tags": manual_tags,
        "model_tags": model_tags,
        "llm_tags": model_tags,
        "final_tags": final_tags,
        "tag_scores": item.get("tag_scores") if isinstance(item.get("tag_scores"), dict) else {},
        "analysis_status": item.get("analysis_status", "pending"),
        "analysis_engine": item.get("analysis_engine", ""),
        "tag_status": item.get("tag_status", ""),
        "tag_error": item.get("tag_error", ""),
        "mapping_id": mapping.get("mapping_id", ""),
        "chart_file": mapping.get("chart_file", item.get("source_path", "")),
        "chart_section": mapping.get("chart_section", ""),
    }


def _detail(item: dict[str, Any], key: str, file_mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _summary(item, key)
    result.update({
        "fit_diff": item.get("fit_diff"),
        "genre": item.get("genre", ""),
        "version": item.get("version", ""),
        "notes": item.get("notes", {}),
        "model_scores": item.get("model_scores", {}),
        "model_probabilities": item.get("model_probabilities", {}),
        "model_features": item.get("model_features", {}),
        "model_windows": item.get("model_windows", []),
        "tag_positions": item.get("tag_positions", {}),
        "collision_candidates": item.get("collision_candidates", []),
        "accepted_collision_ids": item.get("accepted_collision_ids", []),
        "model_metadata": item.get("model_metadata", {}),
        "source_file": item.get("source_file", ""),
        "source_path": item.get("source_path", ""),
        "source_sha256": item.get("source_sha256", ""),
        "mapping": item.get("mapping", {}),
        "file_mapping": file_mapping if isinstance(file_mapping, dict) else {},
        "updated_at": item.get("updated_at", ""),
    })
    return result


__all__ = [
    "AUTO_PROGRESS_FILE",
    "AutoTagJob",
    "LocalChartCatalog",
    "LocalChartModel",
    "MAPPING_VERSION",
    "MODEL_FILE",
    "MODEL_NAME",
]
