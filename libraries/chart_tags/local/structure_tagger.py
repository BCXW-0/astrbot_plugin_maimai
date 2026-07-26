from __future__ import annotations

"""从解析后的谱面事件提取结构特征，并映射为加权标签。

判定逻辑（结合圈内配置语言 + BPM/密度/键位几何，而非网页文案）：
- 管子：滑键占比高
- 双押：同时按比例高
- 手速：单位时间 note 密度高（与 BPM、{32}/{16} 网格相关）
- 底力：长时域持续高密度 + 总量大
- 爆发：局部密度尖峰远高于中位
- 交互：相邻键快速交替（trill）
- 纵连：同键位连续重复
- 扫键：顺时针/逆时针环绕序列
- 飞手：大跨度跳键
- 定位：DX 触摸键占比高
- 一笔划/秒划/如龙：滑键时长与形状
- 留尾：hold 与其它键重叠
- 散打：高 tap、低 slide、键位分散
- 节奏/跳拍/定拍：网格与间隔规律性
"""


import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..constants import TAG_WEIGHTS
from ..rule_tags import select_final_tags, tag_weight
from .maidata_parser import MaidataChart, NoteEvent, parse_maidata


def _button_dist(a: str, b: str) -> int:
    try:
        x, y = int(a), int(b)
    except Exception:
        return 0
    d = abs(x - y) % 8
    return min(d, 8 - d)


def extract_features(chart: MaidataChart) -> dict[str, float]:
    events = [e for e in chart.events if e.kind != ""]
    if not events:
        return {"empty": 1.0}

    taps = [e for e in events if e.kind in {"tap", "break"}]
    holds = [e for e in events if e.kind == "hold"]
    slides = [e for e in events if e.kind == "slide"]
    touches = [e for e in events if e.kind == "touch"]
    total = max(len(events), 1)
    duration = max(events[-1].time, 0.01)
    # group simultaneous by time bucket
    buckets: dict[int, list[NoteEvent]] = defaultdict(list)
    for e in events:
        buckets[int(round(e.time * 1000))].append(e)
    sim_groups = list(buckets.values())
    multi = 0
    for g in sim_groups:
        button_set = []
        for e in g:
            button_set.extend(list(e.buttons))
        # 同时按：同一时间桶 >=2 事件，或单事件多键
        if len(g) >= 2 or len(set(button_set)) >= 2:
            multi += 1

    # density windows 1s
    dens = []
    t0 = events[0].time
    t1 = events[-1].time
    step = 0.5
    cur = t0
    while cur <= t1:
        c = sum(1 for e in events if cur <= e.time < cur + 1.0)
        dens.append(c)
        cur += step
    dens = dens or [0]
    peak = max(dens)
    median = statistics.median(dens)
    mean_d = statistics.mean(dens)

    # sequences of single taps for patterns
    single_tap_buttons: list[tuple[float, str]] = []
    for e in events:
        if e.kind in {"tap", "break"} and len(e.buttons) == 1 and e.buttons[0].isdigit():
            single_tap_buttons.append((e.time, e.buttons[0]))

    trill = 0
    stack = 0  # 纵连
    sweep = 0
    jump = 0
    for i in range(1, len(single_tap_buttons)):
        t0_, b0 = single_tap_buttons[i - 1]
        t1_, b1 = single_tap_buttons[i]
        dt = t1_ - t0_
        if dt <= 0 or dt > 0.35:
            continue
        dist = _button_dist(b0, b1)
        if dist == 0:
            stack += 1
        elif dist == 1:
            # possible trill/sweep
            if i >= 2:
                b_2 = single_tap_buttons[i - 2][1]
                if b_2 == b1 and b0 != b1:
                    trill += 1
            sweep += 1
        elif dist >= 3:
            jump += 1

    # longer sweep detection: 4+ consecutive circular steps
    run = 1
    max_run = 1
    direction = 0
    for i in range(1, len(single_tap_buttons)):
        b0 = int(single_tap_buttons[i - 1][1])
        b1 = int(single_tap_buttons[i][1])
        dt = single_tap_buttons[i][0] - single_tap_buttons[i - 1][0]
        if dt > 0.25:
            run = 1
            direction = 0
            continue
        diff = (b1 - b0) % 8
        if diff == 1 or diff == 7:
            dir_now = 1 if diff == 1 else -1
            if direction in (0, dir_now):
                direction = dir_now
                run += 1
                max_run = max(max_run, run)
            else:
                direction = dir_now
                run = 2
        else:
            run = 1
            direction = 0

    slide_shapes = Counter(e.shape or "-" for e in slides)
    short_slides = sum(1 for e in slides if 0 < e.duration <= 0.12)
    long_slides = sum(1 for e in slides if e.duration >= 0.75)
    wifi = sum(1 for e in slides if "w" in (e.shape or ""))
    curve = sum(1 for e in slides if any(x in (e.shape or "") for x in ("pp", "qq", "p", "q", "z")))
    # hold overlap rough: hold interval overlaps other events on other buttons
    overlap = 0
    for h in holds:
        end = h.time + max(h.duration, 0)
        hb = set(h.buttons)
        for e in events:
            if e is h:
                continue
            if h.time < e.time < end and set(e.buttons) - hb:
                overlap += 1
                break

    # rhythm regularity on inter-onset intervals
    times = sorted({round(e.time, 4) for e in events})
    ioi = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
    if len(ioi) >= 8:
        med = statistics.median(ioi)
        irregular = sum(1 for x in ioi if abs(x - med) > med * 0.55) / len(ioi)
        cv = (statistics.pstdev(ioi) / med) if med > 1e-6 else 0
    else:
        irregular = 0.0
        cv = 0.0

    nps = total / duration
    tap_ratio = len(taps) / total
    slide_ratio = len(slides) / total
    hold_ratio = len(holds) / total
    touch_ratio = len(touches) / total
    break_ratio = sum(1 for e in events if e.is_break or e.kind == "break") / total

    # key entropy for 散打
    key_counts = Counter()
    for e in taps:
        for b in e.buttons:
            if b.isdigit():
                key_counts[b] += 1
    if key_counts:
        s = sum(key_counts.values())
        probs = [c / s for c in key_counts.values()]
        entropy = -sum(p * math.log(p + 1e-12) for p in probs) / math.log(8)
    else:
        entropy = 0.0

    return {
        "ds": float(chart.ds or 0),
        "bpm": float(chart.bpm or 0),
        "duration": duration,
        "total": float(total),
        "nps": nps,
        "tap_ratio": tap_ratio,
        "slide_ratio": slide_ratio,
        "hold_ratio": hold_ratio,
        "touch_ratio": touch_ratio,
        "break_ratio": break_ratio,
        "multi_ratio": multi / max(len(sim_groups), 1),
        "peak_density": float(peak),
        "mean_density": float(mean_d),
        "median_density": float(median),
        "burst_ratio": float(peak / max(median, 1.0)),
        "trill": float(trill),
        "stack": float(stack),
        "jump": float(jump),
        "sweep_run": float(max_run),
        "short_slides": float(short_slides),
        "long_slides": float(long_slides),
        "wifi_slides": float(wifi),
        "curve_slides": float(curve),
        "hold_overlap": float(overlap),
        "ioi_irregular": float(irregular),
        "ioi_cv": float(cv),
        "key_entropy": float(entropy),
        "slide_count": float(len(slides)),
        "touch_count": float(len(touches)),
        "tap_count": float(len(taps)),
    }


def features_to_tag_scores(feat: dict[str, float]) -> dict[str, float]:
    if feat.get("empty"):
        return {}

    scores: dict[str, float] = {}

    def add(tag: str, strength: float) -> None:
        if strength <= 0:
            return
        scores[tag] = max(scores.get(tag, 0.0), tag_weight(tag) * min(1.45, strength))

    total = max(feat["total"], 1.0)
    nps = feat["nps"]
    bpm = feat["bpm"]
    ds = feat["ds"]
    duration = max(feat["duration"], 1.0)

    slide_ratio = feat["slide_ratio"]
    tap_ratio = feat["tap_ratio"]
    multi_ratio = feat["multi_ratio"]
    touch_ratio = feat["touch_ratio"]

    # 率类特征：按 note 数归一，避免长谱绝对计数虚高
    jump_rate = feat["jump"] / total
    trill_rate = feat["trill"] / total
    stack_rate = feat["stack"] / total
    short_slide_rate = feat["short_slides"] / max(feat["slide_count"], 1.0)
    long_slide_rate = feat["long_slides"] / max(feat["slide_count"], 1.0)

    # 管子：滑键是主要配置之一
    if slide_ratio >= 0.09 or feat["slide_count"] >= 45:
        add("管子", 0.5 + min(0.85, slide_ratio * 3.2 + feat["slide_count"] / 160.0))

    # 双押
    if multi_ratio >= 0.14:
        add("双押", 0.45 + min(0.85, (multi_ratio - 0.1) * 3.5))

    # 手速：NPS + BPM + 峰值
    speed = 0.0
    if nps >= 7.5:
        speed += 0.45 + min(0.5, (nps - 7.5) / 8.0)
    if bpm >= 200:
        speed += 0.25 + min(0.35, (bpm - 200) / 120.0)
    if feat["peak_density"] >= 14:
        speed += 0.2
    if speed >= 0.55:
        add("手速", speed)

    # 底力：总量 + 持续密度
    if feat["total"] >= 700 and feat["mean_density"] >= 6.5 and duration >= 70:
        stam = feat["total"] / 1100.0 + feat["mean_density"] / 14.0
        add("底力", min(1.3, stam))

    # 爆发：尖峰相对中位
    if feat["burst_ratio"] >= 2.4 and feat["peak_density"] >= 14 and feat["peak_density"] - feat["median_density"] >= 6:
        add("爆发", 0.45 + min(0.9, (feat["burst_ratio"] - 2.2) / 2.5))

    # 交互 trill
    if trill_rate >= 0.035 and feat["trill"] >= 12:
        add("交互", 0.45 + min(0.9, trill_rate * 12 + feat["trill"] / 60.0))

    # 纵连 / 叠键
    if stack_rate >= 0.06 and feat["stack"] >= 18:
        add("纵连", 0.4 + min(0.9, stack_rate * 8))
        if stack_rate >= 0.11:
            add("叠键", 0.4 + min(0.8, stack_rate * 6))

    # 扫键：环绕长串
    if feat["sweep_run"] >= 7:
        add("扫键", 0.45 + min(0.9, (feat["sweep_run"] - 6) / 10.0))

    # 飞手：大跨跳键占比（不是绝对次数）
    if jump_rate >= 0.12 and feat["jump"] >= 30:
        add("飞手", 0.4 + min(0.95, (jump_rate - 0.1) * 6))

    # 定位
    if touch_ratio >= 0.08 or feat["touch_count"] >= 30:
        add("定位", 0.45 + min(0.95, touch_ratio * 5 + feat["touch_count"] / 90.0))

    # 滑键细分
    if feat["slide_count"] >= 20 and long_slide_rate >= 0.18:
        add("一笔划", 0.4 + min(0.9, long_slide_rate * 2.5))
    if feat["slide_count"] >= 25 and short_slide_rate >= 0.45 and feat["short_slides"] >= 18:
        add("秒划", 0.4 + min(0.9, short_slide_rate * 1.5))
    if feat["wifi_slides"] >= 4 or (feat["curve_slides"] >= 12 and slide_ratio >= 0.12):
        add("如龙", 0.45 + min(0.95, feat["wifi_slides"] / 10.0 + feat["curve_slides"] / 40.0))

    # 留尾
    if feat["hold_overlap"] >= 5 and feat["hold_ratio"] >= 0.035:
        add("留尾", 0.4 + min(0.9, feat["hold_overlap"] / 18.0))

    # 散打
    if tap_ratio >= 0.68 and slide_ratio <= 0.09 and feat["key_entropy"] >= 0.8 and nps >= 5:
        add("散打", 0.45 + min(0.85, feat["key_entropy"]))

    # 节奏类
    if feat["ioi_cv"] <= 0.28 and feat["ioi_irregular"] <= 0.22 and total >= 250:
        add("定拍", 0.4 + (0.28 - feat["ioi_cv"])) 
    if feat["ioi_irregular"] >= 0.48 and feat["ioi_cv"] >= 0.55:
        add("跳拍", 0.4 + min(0.9, feat["ioi_irregular"]))
        add("节奏", 0.35 + min(0.85, feat["ioi_cv"] / 2))

    if feat["ioi_irregular"] >= 0.45 and ds >= 13.2 and slide_ratio >= 0.05:
        add("错位", 0.35 + min(0.85, feat["ioi_irregular"]))

    # 手序：多种手法叠加才给
    hand = 0.0
    if jump_rate >= 0.1:
        hand += 0.3
    if trill_rate >= 0.03:
        hand += 0.25
    if multi_ratio >= 0.12:
        hand += 0.2
    if feat["sweep_run"] >= 6 and jump_rate >= 0.08:
        hand += 0.2
    if hand >= 0.65:
        add("手序", hand)

    if jump_rate >= 0.11 and multi_ratio >= 0.1 and nps >= 6.5:
        add("拆谱", 0.4 + min(0.85, jump_rate * 3 + multi_ratio))

    if short_slide_rate >= 0.4 and slide_ratio >= 0.1 and feat["short_slides"] >= 15:
        add("防蹭", 0.35 + min(0.85, short_slide_rate))

    # 死镰：强环绕 + 折返，且不能太容易触发
    if feat["sweep_run"] >= 10 and jump_rate >= 0.1 and stack_rate < 0.15:
        add("死镰", 0.35 + min(0.75, feat["sweep_run"] / 20.0))

    if feat["ioi_cv"] >= 0.85 and feat["ioi_irregular"] >= 0.5 and slide_ratio < 0.2 and ds >= 13.0:
        add("背谱", 0.35 + min(0.75, feat["ioi_cv"] / 2))

    if ds >= 14.0:
        for key in list(scores):
            scores[key] *= 1.04
    return scores


def analyze_chart_tags(chart: MaidataChart) -> dict[str, Any]:
    feat = extract_features(chart)
    scores = features_to_tag_scores(feat)
    # 结构主配置保底：滑键/双押足够强时，避免被其它高分标签完全挤掉
    if feat.get("slide_ratio", 0) >= 0.11 and "管子" in scores:
        scores["管子"] = max(scores["管子"], tag_weight("管子") * 1.05)
    if feat.get("multi_ratio", 0) >= 0.18 and "双押" in scores:
        scores["双押"] = max(scores["双押"], tag_weight("双押") * 1.0)
    tags, selected = select_final_tags(scores)
    # 主配置保底插入（不占用“炫技标签”挤出真实主构成）
    forced = []
    if feat.get("slide_ratio", 0) >= 0.11:
        forced.append("管子")
    if feat.get("multi_ratio", 0) >= 0.2:
        forced.append("双押")
    if feat.get("touch_ratio", 0) >= 0.1:
        forced.append("定位")
    if forced:
        merged_scores = dict(selected)
        for tag in forced:
            merged_scores[tag] = max(float(merged_scores.get(tag, 0.0) or 0.0), float(scores.get(tag, tag_weight(tag))))
        for tag in tags:
            merged_scores[tag] = max(float(merged_scores.get(tag, 0.0) or 0.0), float(selected.get(tag, tag_weight(tag))))
        tags, selected = select_final_tags(merged_scores)
    conf = 0.0
    if selected:
        # 以选中标签的相对强度与“非空泛化”比例估计置信度
        strengths = []
        for tag, score in selected.items():
            base = max(tag_weight(tag), 1e-6)
            strengths.append(min(1.0, float(score) / base))
        conf = sum(strengths) / max(len(strengths), 1)
        distinctive = [t for t in tags if TAG_WEIGHTS.get(t, 0) >= 0.7]
        generic = [t for t in tags if t in {"底力", "手速", "爆发", "管子"}]
        if distinctive:
            conf = min(1.0, conf + 0.06 * min(3, len(distinctive)))
        if tags and len(generic) == len(tags):
            conf *= 0.72
        conf = round(min(1.0, max(0.05, conf)), 4)
    return {
        "tags": tags,
        "tag_scores": selected,
        "features": feat,
        "confidence": round(conf, 4),
        "ds": chart.ds,
        "bpm": chart.bpm,
        "level_index": chart.level_index,
        "source": "maidata_structure",
    }


def analyze_maidata_text(text: str, min_ds: float = 12.6) -> dict[str, Any]:
    song = parse_maidata(text)
    charts_out = {}
    for level_index, chart in song.charts.items():
        if chart.ds < min_ds and level_index < 2:
            continue
        if chart.ds < min_ds:
            continue
        charts_out[str(level_index)] = analyze_chart_tags(chart)
    return {
        "title": song.title,
        "artist": song.artist,
        "short_id": song.short_id,
        "whole_bpm": song.whole_bpm,
        "charts": charts_out,
    }


def analyze_maidata_file(path: str | Path, min_ds: float = 12.6) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return analyze_maidata_text(text, min_ds=min_ds)
