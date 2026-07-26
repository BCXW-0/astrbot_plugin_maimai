from __future__ import annotations

"""从既有网页证据中抽取“高可信、低歧义”标签，作为本地模型/规则校准的训练元数据。

可信度原则（偏严）：
1. manual_tags：人工标注，直接采信
2. 多来源互相印证：同一标签被 >=2 个不同 source 的证据文本命中
3. 单来源但为“攻略正文”且命中高辨识标签（非仅物量摘要）
4. 明确排除：仅由 note 统计摘要（総数/密度）启发式灌进的泛化标签单独作为金标

输出 JSONL，每行一个谱面难度，便于后续特征对齐与模型训练。
"""


import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..constants import ALLOWED_TAGS, TAG_ALIASES, TAG_WEIGHTS
from ..rule_tags import filter_allowed_tags, normalize_tag
from ..storage import CHART_TAGS_FILE, read_chart_tags, write_json_atomic

CN_TZ = timezone(timedelta(hours=8))

# 高辨识、歧义相对少的标签（更适合当金标）
HIGH_PRECISION_TAGS = frozenset({
    "死镰", "如龙", "秒划", "一笔划", "延迟星星", "错位", "飞手", "手序",
    "跳拍", "背谱", "防蹭", "纵连", "叠键", "交互", "扫键", "拆谱", "留尾",
    "双押", "管子", "定位",
})

# 单独出现时歧义大，需要多源或正文强证据
AMBIGUOUS_ALONE = frozenset({"底力", "手速", "爆发", "散打", "节奏", "定拍"})

# 物量摘要噪声
NOTES_SUMMARY_MARKERS = ("譜面データ", "総数", "ノーツ密度", "Tap ", "Slide ", "Break ")

# 标签关键词（用于从 evidence 文本回证，避免“只有 final_tags 没有正文依据”）
TAG_TEXT_PATTERNS: dict[str, list[str]] = {
    "管子": [r"管子", r"ホールド.*密集", r"短.*ホールド", r"hold\s*chain", r"ホールド連"],
    "双押": [r"双押", r"雙押", r"同時押し", r"同押"],
    "定位": [r"定位", r"卡手", r"手位", r"配置.*キツ", r"擦りやすい"],
    "手速": [r"手速", r"高速配置", r"高\s*BPM", r"速度が"],
    "底力": [r"底力", r"物量", r"耐力", r"総合力"],
    "爆发": [r"爆发", r"爆發", r"高密度地帯", r"尾杀", r"瞬間密度"],
    "交互": [r"交互", r"トリル", r"trill"],
    "纵连": [r"纵连", r"縦連", r"長縦", r"长纵"],
    "叠键": [r"叠键", r"短纵", r"短縦"],
    "扫键": [r"扫键", r"掃鍵", r"回転", r"转圈"],
    "飞手": [r"飞手", r"大位移", r"遠距離", r"出張"],
    "手序": [r"手序", r"运指", r"運指", r"骗手"],
    "一笔划": [r"一笔划", r"一笔画", r"一筆書き"],
    "秒划": [r"秒划", r"秒画", r"即划"],
    "如龙": [r"如龙", r"如龍", r"如龙扫"],
    "死镰": [r"死镰", r"死鎌", r"镰刀"],
    "错位": [r"错位", r"拍划", r"ズレ", r"不匀"],
    "跳拍": [r"跳拍", r"跳节奏"],
    "背谱": [r"背谱", r"初见杀", r"覚えゲー"],
    "防蹭": [r"防蹭", r"防擦", r"蹭星"],
    "留尾": [r"留尾", r"尾巴", r"尾判"],
    "拆谱": [r"拆谱", r"拆配", r"左右分解"],
    "延迟星星": [r"延迟星星", r"延迟星", r"遅延"],
    "散打": [r"散打", r"散点", r"乱れ打ち"],
    "爆发": [r"爆发", r"爆發", r"发狂"],
}


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _is_notes_only_summary(text: str) -> bool:
    value = str(text or "")
    if not value:
        return True
    hits = sum(1 for m in NOTES_SUMMARY_MARKERS if m in value)
    # 几乎只有物量数字行
    if hits >= 2 and len(re.findall(r"[\u4e00-\u9fff]{2,}", value)) <= 3:
        return True
    if "譜面データ" in value and "ノーツ密度" in value and len(value) < 180:
        return True
    return False


def _text_mentions_tag(text: str, tag: str) -> bool:
    patterns = TAG_TEXT_PATTERNS.get(tag) or [re.escape(tag)]
    return any(re.search(p, text, flags=re.I) for p in patterns)


def _evidence_quality(item: dict[str, str]) -> str:
    source = str(item.get("source") or "")
    summary = str(item.get("summary") or "")
    title = str(item.get("title") or "")
    blob = f"{title}\n{summary}"
    if source == "maidata":
        return "local"
    if _is_notes_only_summary(summary):
        return "notes_summary"
    if source == "gamerch" and ("譜面" in blob or "配置" in blob or "攻略" in title or len(summary) >= 80):
        return "prose"
    if source in {"bilibili", "youtube"} and re.search(r"谱面|譜面|手元|攻略|配置", blob):
        return "prose"
    if source in {"bilibili", "youtube", "gamerch"}:
        return "weak"
    return "other"


def extract_high_confidence_labels(chart: dict[str, Any]) -> dict[str, Any]:
    manual = filter_allowed_tags(chart.get("manual_tags") or [])
    evidence = chart.get("evidence") if isinstance(chart.get("evidence"), list) else []

    # tag -> set(sources that prose-mention it)
    mention_sources: dict[str, set[str]] = {}
    prose_mentions: dict[str, list[dict[str, str]]] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        quality = _evidence_quality(item)
        if quality in {"notes_summary", "local", "other"}:
            # notes_summary 不作为金标依据；local 是预测不是网页金标
            continue
        if quality == "weak":
            # weak 只对高辨识标签计半分源
            pass
        source = str(item.get("source") or "unknown")
        blob = f"{item.get('title', '')}\n{item.get('summary', '')}"
        for tag in ALLOWED_TAGS:
            if not _text_mentions_tag(blob, tag):
                continue
            if quality == "weak" and tag in AMBIGUOUS_ALONE:
                continue
            mention_sources.setdefault(tag, set()).add(source)
            prose_mentions.setdefault(tag, []).append({
                "source": source,
                "quality": quality,
                "title": str(item.get("title") or "")[:120],
                "url": str(item.get("url") or ""),
            })

    labels: list[str] = []
    reasons: dict[str, str] = {}
    conf: dict[str, float] = {}

    for tag in manual:
        labels.append(tag)
        reasons[tag] = "manual"
        conf[tag] = 1.0

    for tag, sources in mention_sources.items():
        if tag in manual:
            continue
        # 多源互证
        if len(sources) >= 2 and tag in HIGH_PRECISION_TAGS:
            labels.append(tag)
            reasons[tag] = f"multi_source:{','.join(sorted(sources))}"
            conf[tag] = 0.92
            continue
        # 单源正文 + 高辨识
        prose_items = [x for x in prose_mentions.get(tag, []) if x.get("quality") == "prose"]
        if prose_items and tag in HIGH_PRECISION_TAGS and tag not in AMBIGUOUS_ALONE:
            labels.append(tag)
            reasons[tag] = f"prose:{prose_items[0].get('source')}"
            conf[tag] = 0.8 if tag not in {"管子", "双押", "定位"} else 0.78
            continue
        # 歧义标签需要更强：多源或 manual 已处理
        if tag in AMBIGUOUS_ALONE and len(sources) >= 2 and any(x.get("quality") == "prose" for x in prose_mentions.get(tag, [])):
            labels.append(tag)
            reasons[tag] = f"ambiguous_multi:{','.join(sorted(sources))}"
            conf[tag] = 0.7

    # 保持白名单与权重序
    labels = filter_allowed_tags(labels)
    labels = sorted(labels, key=lambda t: (-float(conf.get(t, 0)), -float(TAG_WEIGHTS.get(t, 0.5)), t))

    return {
        "labels": labels,
        "label_confidence": {k: conf[k] for k in labels if k in conf},
        "label_reasons": {k: reasons[k] for k in labels if k in reasons},
        "mention_sources": {k: sorted(v) for k, v in mention_sources.items() if k in labels},
        "evidence_snippets": {k: prose_mentions.get(k, [])[:3] for k in labels},
    }


def build_training_dataset(
    *,
    output_path: str | Path | None = None,
    min_ds: float = 12.6,
    require_labels: bool = True,
) -> dict[str, Any]:
    data = read_chart_tags()
    charts = data.get("charts") if isinstance(data, dict) else {}
    if not isinstance(charts, dict):
        charts = {}

    rows: list[dict[str, Any]] = []
    for key, chart in charts.items():
        if not isinstance(chart, dict):
            continue
        try:
            ds = float(chart.get("ds") or 0)
        except Exception:
            ds = 0.0
        if ds < min_ds:
            continue
        extracted = extract_high_confidence_labels(chart)
        if require_labels and not extracted["labels"]:
            continue
        row = {
            "key": key,
            "song_id": str(chart.get("song_id") or key.split(":")[0]),
            "level_index": chart.get("level_index"),
            "title": chart.get("title"),
            "ds": ds,
            "bpm": chart.get("bpm"),
            "type": chart.get("type"),
            "labels": extracted["labels"],
            "label_confidence": extracted["label_confidence"],
            "label_reasons": extracted["label_reasons"],
            "mention_sources": extracted["mention_sources"],
            "evidence_snippets": extracted["evidence_snippets"],
            # 便于对照：当前最终展示标签 / 本地结构标签
            "final_tags": filter_allowed_tags(chart.get("final_tags") or []),
            "local_tags": filter_allowed_tags(chart.get("local_tags") or []),
            "manual_tags": filter_allowed_tags(chart.get("manual_tags") or []),
        }
        rows.append(row)

    out = Path(output_path) if output_path else (Path(CHART_TAGS_FILE).parent / "chart_tag_training_labels.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 同步一份 summary 到标签库 meta，方便 WebUI/排查
    if isinstance(data, dict):
        data["training_labels"] = {
            "path": str(out),
            "count": len(rows),
            "updated_at": _now(),
            "rules": {
                "high_precision_tags": sorted(HIGH_PRECISION_TAGS),
                "ambiguous_alone": sorted(AMBIGUOUS_ALONE),
                "min_ds": min_ds,
            },
        }
        data["updated_at"] = _now()
        write_json_atomic(CHART_TAGS_FILE, data)

    # 简单统计
    from collections import Counter
    c = Counter()
    for row in rows:
        c.update(row["labels"])
    return {
        "ok": True,
        "path": str(out),
        "count": len(rows),
        "label_freq": c.most_common(),
        "updated_at": _now(),
    }
