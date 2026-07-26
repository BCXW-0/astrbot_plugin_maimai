from __future__ import annotations

"""本地 maidata 结构分析 -> 标签库写入。

策略：
1. 从 OneCat 仅拉 maidata.txt（无 BGA）
2. 解析 simai 事件并做 BPM/密度/键位几何特征
3. 映射为加权标签，写入 chart_tags 的 local_tags / tag_scores
4. 置信度足够时可替代联网文案标签；否则保留原证据标签作补充
"""


import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..constants import ALLOWED_TAGS, TAG_CATEGORIES, TAG_RULE_VERSION, TAG_WEIGHTS
from ..rule_tags import filter_allowed_tags, select_final_tags, sort_tags_by_weight
from ..storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic
from .onecat_client import OneCatClient
from .structure_tagger import analyze_maidata_text

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "static" / "maidata_cache"
MIN_LOCAL_CONFIDENCE = 0.42


def now_text() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def analyze_song_id(music_id: str | int, client: OneCatClient | None = None, min_ds: float = 12.6) -> dict[str, Any]:
    client = client or OneCatClient()
    text = client.download_maidata(music_id)
    result = analyze_maidata_text(text, min_ds=min_ds)
    result["music_id"] = str(music_id)
    return result


def _merge_local_into_item(item: dict[str, Any], local: dict[str, Any], *, prefer_local: bool = True) -> dict[str, Any]:
    local_tags = filter_allowed_tags(local.get("tags") or [])
    local_scores = local.get("tag_scores") if isinstance(local.get("tag_scores"), dict) else {}
    conf = float(local.get("confidence") or 0.0)
    item["local_tags"] = local_tags
    item["local_tag_scores"] = local_scores
    item["local_confidence"] = conf
    item["local_features"] = local.get("features") or {}
    item["local_source"] = "maidata_structure"
    item["local_updated_at"] = now_text()

    manual = filter_allowed_tags(item.get("manual_tags") or [])
    old_scores = item.get("tag_scores") if isinstance(item.get("tag_scores"), dict) else {}
    web_tags = filter_allowed_tags(item.get("llm_tags") or item.get("final_tags") or [])

    if prefer_local and conf >= MIN_LOCAL_CONFIDENCE and local_tags:
        # 本地结构为主，手动标签始终保留
        merged_scores = {**local_scores}
        for tag in manual:
            merged_scores[tag] = max(float(merged_scores.get(tag, 0.0) or 0.0), float(TAG_WEIGHTS.get(tag, 0.5)) * 1.25)
        # 低置信补充：把原网页标签以降权并入
        if conf < 0.7:
            for tag in web_tags:
                merged_scores[tag] = max(float(merged_scores.get(tag, 0.0) or 0.0), float(old_scores.get(tag, TAG_WEIGHTS.get(tag, 0.4))) * 0.55)
        final_tags, tag_scores = select_final_tags(merged_scores, manual)
        item["final_tags"] = final_tags
        item["tags"] = final_tags
        item["tag_scores"] = tag_scores
        item["tag_status"] = "done"
        item["tag_error"] = ""
        item["tag_rule_version"] = max(int(item.get("tag_rule_version") or 0), TAG_RULE_VERSION)
    elif local_tags:
        # 置信不足：与旧分融合
        merged = dict(old_scores)
        for tag, score in local_scores.items():
            merged[tag] = max(float(merged.get(tag, 0.0) or 0.0), float(score) * 0.85)
        final_tags, tag_scores = select_final_tags(merged, manual)
        if final_tags:
            item["final_tags"] = final_tags
            item["tags"] = final_tags
            item["tag_scores"] = tag_scores
            item["tag_status"] = "done"
    item["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in item.get("final_tags") or [] if tag in TAG_CATEGORIES}
    item["updated_at"] = now_text()
    return item


def rebuild_tags_from_maidata(
    *,
    min_ds: float = 12.6,
    limit: int | None = None,
    cache_dir: str | Path | None = None,
    prefer_local: bool = True,
    sleep_s: float = 0.02,
    music_ids: list[str] | None = None,
    client: OneCatClient | None = None,
) -> dict[str, Any]:
    client = client or OneCatClient()
    cache_path = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)

    data = read_chart_tags()
    if not isinstance(data, dict):
        data = {}
    charts = data.get("charts") if isinstance(data.get("charts"), dict) else {}
    if not isinstance(charts, dict):
        charts = {}

    if music_ids:
        targets = [{"id": mid} for mid in music_ids]
    else:
        targets = client.iter_high_level_songs(min_ds=min_ds)
    if limit is not None:
        targets = targets[: max(0, int(limit))]

    ok = fail = updated = 0
    errors: list[str] = []
    for song in targets:
        mid = str(song.get("id") or "")
        if not mid:
            continue
        try:
            text = client.download_maidata(mid)
            (cache_path / f"{mid}.txt").write_text(text, encoding="utf-8")
            analyzed = analyze_maidata_text(text, min_ds=min_ds)
            ok += 1
            for level_s, local in (analyzed.get("charts") or {}).items():
                key = f"{mid}:{level_s}"
                item = charts.get(key) if isinstance(charts.get(key), dict) else {
                    "song_id": mid,
                    "title": analyzed.get("title") or song.get("title") or "",
                    "level_index": int(level_s),
                    "ds": local.get("ds"),
                    "bpm": local.get("bpm") or analyzed.get("whole_bpm"),
                    "manual_tags": [],
                    "llm_tags": [],
                    "final_tags": [],
                    "tags": [],
                }
                # fill basics
                item.setdefault("song_id", mid)
                item.setdefault("title", analyzed.get("title") or song.get("title") or "")
                item["level_index"] = int(level_s)
                if local.get("ds"):
                    item["ds"] = local.get("ds")
                if local.get("bpm"):
                    item["bpm"] = local.get("bpm")
                _merge_local_into_item(item, local, prefer_local=prefer_local)
                charts[key] = item
                updated += 1
        except Exception as exc:
            fail += 1
            if len(errors) < 20:
                errors.append(f"{mid}: {type(exc).__name__}: {exc}")
        if sleep_s > 0:
            time.sleep(sleep_s)

    data["charts"] = charts
    data["updated_at"] = now_text()
    data["tag_rule_version"] = TAG_RULE_VERSION
    data["allowed_tags"] = ALLOWED_TAGS
    data["tag_weights"] = TAG_WEIGHTS
    data["local_tag_engine"] = {
        "name": "maidata_structure",
        "min_ds": min_ds,
        "min_confidence": MIN_LOCAL_CONFIDENCE,
        "prefer_local": prefer_local,
        "updated_at": now_text(),
    }
    write_json_atomic(CHART_TAGS_FILE, data)
    return {
        "ok": True,
        "songs_ok": ok,
        "songs_fail": fail,
        "charts_updated": updated,
        "total_targets": len(targets),
        "errors": errors,
        "cache_dir": str(cache_path),
        "path": str(CHART_TAGS_FILE),
    }
