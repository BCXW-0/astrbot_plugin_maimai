from __future__ import annotations

"""从解析后的谱面事件提取结构特征，并映射为加权标签。

圈内定义（已按玩家校准）：
- 管子：指 **hold**，不是滑键。常见为
  1) 短 hold 在某时段密度异常高（局部占比，不是全谱平均），有时节奏怪；
  2) hold 结束到下一 hold 开始间隔极短（链式管子，如 11426 Master）。
- 双押：短时间内同时击比例高（看局部窗口峰值/链式，不是全谱平均，也不看容易饱和的 ratio=1.0）。
- 定位：短时间高密度 + 大位移（卡手）；或难划星星的局部，不单指 DX 触摸键。

其它：
- 手速/底力/爆发：NPS、BPM、窗口密度
- 交互/纵连/扫键/飞手：键位序列几何
- 一笔划/秒划/如龙/防蹭：滑键时长与形状
- 留尾：hold 与其它键重叠
"""


import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..constants import GENERIC_TAGS, TAG_WEIGHTS
from ..rule_tags import select_final_tags, tag_weight
from .maidata_parser import MaidataChart, NoteEvent, parse_maidata


def _button_dist(a: str, b: str) -> int:
    try:
        x, y = int(a), int(b)
    except Exception:
        return 0
    d = abs(x - y) % 8
    return min(d, 8 - d)


def _hit_buttons(group: list[NoteEvent]) -> list[str]:
    hit_buttons: list[str] = []
    for e in group:
        if e.kind not in {"tap", "break", "hold"}:
            continue
        for b in e.buttons:
            if str(b).isdigit():
                hit_buttons.append(str(b))
    return hit_buttons


def _is_multi_group(group: list[NoteEvent]) -> bool:
    return len(set(_hit_buttons(group))) >= 2


def _window_stats(events: list[NoteEvent], window: float = 1.0, step: float = 0.25) -> list[dict[str, float]]:
    if not events:
        return []
    t0 = events[0].time
    t1 = events[-1].time
    out: list[dict[str, float]] = []
    cur = t0
    while cur <= t1 + 1e-9:
        bucket = [e for e in events if cur <= e.time < cur + window]
        if not bucket:
            cur += step
            continue
        groups: dict[int, list[NoteEvent]] = defaultdict(list)
        for e in bucket:
            groups[int(round(e.time * 1000))].append(e)
        multi = sum(1 for g in groups.values() if _is_multi_group(g))
        holds = [e for e in bucket if e.kind == "hold"]
        short_holds = [e for e in holds if 0 < e.duration <= 0.40]
        taps = [
            e for e in bucket
            if e.kind in {"tap", "break"} and len(e.buttons) == 1 and str(e.buttons[0]).isdigit()
        ]
        jump = 0
        max_jump = 0
        big_jump = 0
        for i in range(1, len(taps)):
            d = _button_dist(taps[i - 1].buttons[0], taps[i].buttons[0])
            dt = taps[i].time - taps[i - 1].time
            max_jump = max(max_jump, d)
            if dt <= 0 or dt > 0.22:
                continue
            if d >= 3:
                jump += 1
                big_jump += 1
        slides = [e for e in bucket if e.kind == "slide"]
        hard_slides = [
            e for e in slides
            if e.duration >= 0.55 or any(s in (e.shape or "") for s in ("w", "pp", "qq", "p", "q", "z", "V", "<>"))
        ]
        n = max(len(bucket), 1)
        onset_groups = max(len(groups), 1)
        out.append({
            "t": cur,
            "n": float(n),
            "density": float(n) / window,
            "multi_ratio": multi / onset_groups,
            "multi_count": float(multi),
            "hold_ratio": len(holds) / n,
            "short_hold_ratio": len(short_holds) / n,
            "short_hold_count": float(len(short_holds)),
            "jump_rate": jump / max(len(taps), 1),
            "max_jump": float(max_jump),
            "big_jump": float(big_jump),
            "hard_slide_count": float(len(hard_slides)),
            "hard_slide_ratio": len(hard_slides) / n,
            "slide_ratio": len(slides) / n,
        })
        cur += step
    return out


def _nonoverlap_multi_stats(
    multi_times: list[float],
    events: list[NoteEvent],
    duration: float,
) -> dict[str, float]:
    """双押主特征：非重叠 1s 绝对次数 + 链式，避免 sliding ratio 饱和误判。"""
    if not events:
        return {
            "multi_abs_peak": 0.0,
            "multi_p90": 0.0,
            "multi_hot4": 0.0,
            "multi_hot5": 0.0,
            "multi_hot4_rate": 0.0,
            "multi_share_peak": 0.0,
            "multi_chain_max": 1.0,
        }

    t0 = events[0].time
    t1 = events[-1].time
    counts: list[int] = []
    shares: list[float] = []
    cur = t0
    while cur <= t1 + 1e-9:
        c = sum(1 for t in multi_times if cur <= t < cur + 1.0)
        counts.append(c)
        bucket = [e for e in events if cur <= e.time < cur + 1.0]
        if len(bucket) >= 6:
            groups: dict[int, list[NoteEvent]] = defaultdict(list)
            for e in bucket:
                groups[int(round(e.time * 1000))].append(e)
            onset = 0
            multi = 0
            for g in groups.values():
                buttons = _hit_buttons(g)
                if buttons:
                    onset += 1
                if len(set(buttons)) >= 2:
                    multi += 1
            if onset >= 6:
                shares.append(multi / onset)
        cur += 1.0

    hot4 = sum(1 for c in counts if c >= 4)
    hot5 = sum(1 for c in counts if c >= 5)
    peak = float(max(counts) if counts else 0)
    if counts:
        ordered = sorted(counts)
        p90 = float(ordered[max(0, int(len(ordered) * 0.9) - 1)])
    else:
        p90 = 0.0

    chain = 1
    chain_max = 1
    for i in range(1, len(multi_times)):
        gap = multi_times[i] - multi_times[i - 1]
        if gap <= 0.28:
            chain += 1
            chain_max = max(chain_max, chain)
        else:
            chain = 1

    return {
        "multi_abs_peak": peak,
        "multi_p90": p90,
        "multi_hot4": float(hot4),
        "multi_hot5": float(hot5),
        "multi_hot4_rate": float(hot4) / max(duration, 1.0),
        "multi_share_peak": float(max(shares) if shares else 0.0),
        "multi_chain_max": float(chain_max),
    }


def extract_features(chart: MaidataChart) -> dict[str, float]:
    events = [e for e in chart.events if e.kind != ""]
    if not events:
        return {"empty": 1.0}

    taps = [e for e in events if e.kind in {"tap", "break"}]
    holds = [e for e in events if e.kind == "hold"]
    slides = [e for e in events if e.kind == "slide"]
    touches = [e for e in events if e.kind == "touch"]
    total = max(len(events), 1)
    duration = max(events[-1].time - events[0].time, 0.01)

    buckets: dict[int, list[NoteEvent]] = defaultdict(list)
    for e in events:
        buckets[int(round(e.time * 1000))].append(e)
    sim_groups = list(buckets.values())
    multi = 0
    multi_times: list[float] = []
    for ts, g in sorted(buckets.items()):
        if _is_multi_group(g):
            multi += 1
            multi_times.append(ts / 1000.0)

    # 细窗口：密度 / hold 局部；双押与定位另用非重叠统计
    windows = _window_stats(events, window=1.0, step=0.25)
    dens = [w["density"] for w in windows] or [0.0]
    peak = max(dens)
    median = statistics.median(dens)
    mean_d = statistics.mean(dens)

    multi_stats = _nonoverlap_multi_stats(multi_times, events, duration)
    # 兼容旧字段名 multi_peak：改为绝对峰值（不再用易饱和的 ratio）
    multi_peak = multi_stats["multi_abs_peak"]
    multi_p90 = multi_stats["multi_p90"]
    multi_hot = multi_stats["multi_hot4"]

    # 管子：短 hold 局部密度 + hold 链间隙
    short_holds = [e for e in holds if 0 < e.duration <= 0.40]
    very_short_holds = [e for e in holds if 0 < e.duration <= 0.22]
    hold_ratio_peaks = sorted((w["short_hold_ratio"] for w in windows), reverse=True)
    hold_local_peak = hold_ratio_peaks[0] if hold_ratio_peaks else 0.0
    hold_local_p90 = hold_ratio_peaks[max(0, int(len(hold_ratio_peaks) * 0.1))] if hold_ratio_peaks else 0.0
    hold_hot_windows = sum(1 for w in windows if w["short_hold_ratio"] >= 0.22 and w["short_hold_count"] >= 3)

    hold_gap_min = 999.0
    hold_gap_short = 0
    hold_chain_max = 1
    if len(holds) >= 2:
        ordered = sorted(holds, key=lambda e: e.time)
        chain = 1
        for i in range(1, len(ordered)):
            prev = ordered[i - 1]
            cur_h = ordered[i]
            gap = cur_h.time - (prev.time + max(prev.duration, 0.0))
            if gap < hold_gap_min:
                hold_gap_min = gap
            if gap <= 0.18:
                hold_gap_short += 1
                chain += 1
                hold_chain_max = max(hold_chain_max, chain)
            else:
                chain = 1
    else:
        hold_gap_min = 999.0

    # 定位：非重叠 1s，要求高密 + 短间隔大位移（卡手），或难星局部
    single_tap_buttons: list[tuple[float, str]] = []
    for e in events:
        if e.kind in {"tap", "break"} and len(e.buttons) == 1 and str(e.buttons[0]).isdigit():
            single_tap_buttons.append((e.time, str(e.buttons[0])))

    # 非重叠密度中位
    t0 = events[0].time
    t1 = events[-1].time
    nonoverlap_dens: list[float] = []
    cur = t0
    while cur <= t1 + 1e-9:
        nonoverlap_dens.append(float(sum(1 for e in events if cur <= e.time < cur + 1.0)))
        cur += 1.0
    dens_med = statistics.median(nonoverlap_dens) if nonoverlap_dens else median

    dingwei_hits = 0
    dingwei_peak = 0.0
    cur = t0
    while cur <= t1 + 1e-9:
        density = float(sum(1 for e in events if cur <= e.time < cur + 1.0))
        wtaps = [(t, b) for t, b in single_tap_buttons if cur <= t < cur + 1.0]
        jump = 0
        max_jump = 0
        big_jump = 0
        for i in range(1, len(wtaps)):
            d = _button_dist(wtaps[i - 1][1], wtaps[i][1])
            dt = wtaps[i][0] - wtaps[i - 1][0]
            max_jump = max(max_jump, d)
            if dt <= 0 or dt > 0.22:
                continue
            if d >= 3:
                jump += 1
                big_jump += 1
        jrate = jump / max(len(wtaps), 1)
        hard_slides = sum(
            1
            for e in slides
            if cur <= e.time < cur + 1.0
            and (
                e.duration >= 0.55
                or any(s in (e.shape or "") for s in ("w", "pp", "qq", "p", "q", "z", "V", "<>"))
            )
        )
        score = 0.0
        if density >= max(14.0, dens_med * 1.9) and big_jump >= 4 and max_jump >= 3 and jrate >= 0.28:
            score = density / 12.0 + big_jump * 0.2 + jrate
        elif density >= max(13.0, dens_med * 1.7) and hard_slides >= 2 and big_jump >= 2 and max_jump >= 3:
            score = density / 15.0 + hard_slides * 0.35 + big_jump * 0.15
        if score >= 1.5:
            dingwei_hits += 1
            dingwei_peak = max(dingwei_peak, score)
        cur += 1.0

    trill = 0
    stack = 0
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
            if i >= 2 and single_tap_buttons[i - 2][1] == b1 and b0 != b1:
                trill += 1
        elif dist >= 3:
            jump += 1

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
        if diff in {1, 7}:
            dir_now = 1 if diff == 1 else -1
            if direction == 0 or direction == dir_now:
                direction = dir_now
                run += 1
                max_run = max(max_run, run)
            else:
                direction = dir_now
                run = 2
        else:
            run = 1
            direction = 0

    # slide shape stats
    short_slides = sum(1 for e in slides if 0 < e.duration <= 0.35)
    long_slides = sum(1 for e in slides if e.duration >= 0.75)
    wifi = sum(1 for e in slides if "w" in (e.shape or ""))
    curve = sum(1 for e in slides if any(s in (e.shape or "") for s in ("pp", "qq", "p", "q", "z", "V", "<>")))

    # hold overlap with other notes
    overlap = 0
    for h in holds:
        if h.duration <= 0:
            continue
        end = h.time + h.duration
        for e in events:
            if e is h:
                continue
            if h.time < e.time < end and e.kind in {"tap", "break", "slide", "touch"}:
                overlap += 1
                break

    # IOI regularity on single taps
    intervals = []
    for i in range(1, len(single_tap_buttons)):
        dt = single_tap_buttons[i][0] - single_tap_buttons[i - 1][0]
        if 0.05 <= dt <= 0.8:
            intervals.append(dt)
    if len(intervals) >= 8:
        mean_i = statistics.mean(intervals)
        stdev_i = statistics.pstdev(intervals)
        cv = stdev_i / max(mean_i, 1e-6)
        irregular = sum(1 for x in intervals if abs(x - mean_i) > mean_i * 0.35) / len(intervals)
    else:
        cv = 0.0
        irregular = 0.0

    # key entropy
    counter = Counter(b for _, b in single_tap_buttons)
    probs = [c / max(sum(counter.values()), 1) for c in counter.values()] or [1.0]
    entropy = -sum(p * math.log(p + 1e-12, 2) for p in probs) / 3.0  # normalize ~ [0,1]

    return {
        "empty": 0.0,
        "ds": float(chart.ds or 0),
        "bpm": float(chart.bpm or 0),
        "duration": float(duration),
        "total": float(total),
        "nps": total / duration,
        "tap_ratio": len(taps) / total,
        "slide_ratio": len(slides) / total,
        "hold_ratio": len(holds) / total,
        "touch_ratio": len(touches) / total,
        "break_ratio": sum(1 for e in events if e.is_break or e.kind == "break") / total,
        "multi_ratio": multi / max(len(sim_groups), 1),
        # multi_peak = 1s 非重叠同时击绝对峰值（旧 ratio 峰值已废弃）
        "multi_peak": float(multi_peak),
        "multi_abs_peak": float(multi_stats["multi_abs_peak"]),
        "multi_p90": float(multi_p90),
        "multi_hot_windows": float(multi_hot),
        "multi_hot4": float(multi_stats["multi_hot4"]),
        "multi_hot5": float(multi_stats["multi_hot5"]),
        "multi_hot4_rate": float(multi_stats["multi_hot4_rate"]),
        "multi_share_peak": float(multi_stats["multi_share_peak"]),
        "multi_chain_max": float(multi_stats["multi_chain_max"]),
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
        "hold_count": float(len(holds)),
        "short_hold_count": float(len(short_holds)),
        "very_short_hold_count": float(len(very_short_holds)),
        "hold_local_peak": float(hold_local_peak),
        "hold_local_p90": float(hold_local_p90),
        "hold_hot_windows": float(hold_hot_windows),
        "hold_gap_min": float(hold_gap_min if hold_gap_min < 900 else 9.0),
        "hold_gap_short": float(hold_gap_short),
        "hold_chain_max": float(hold_chain_max),
        "dingwei_hits": float(dingwei_hits),
        "dingwei_peak": float(dingwei_peak),
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

    jump_rate = feat["jump"] / total
    trill_rate = feat["trill"] / total
    stack_rate = feat["stack"] / total
    short_slide_rate = feat["short_slides"] / max(feat["slide_count"], 1.0)
    long_slide_rate = feat["long_slides"] / max(feat["slide_count"], 1.0)

    multi_abs_peak = feat.get("multi_abs_peak", feat.get("multi_peak", 0.0))
    multi_hot4 = feat.get("multi_hot4", feat.get("multi_hot_windows", 0.0))
    multi_hot5 = feat.get("multi_hot5", 0.0)
    multi_hot4_rate = feat.get("multi_hot4_rate", multi_hot4 / duration)
    multi_chain_max = feat.get("multi_chain_max", 1.0)
    multi_share_peak = feat.get("multi_share_peak", 0.0)

    # ===== 管子 = 短 hold 局部密度 / hold 链，不是滑键 =====
    # 注意：仅“长 hold 首尾相接”不算管子；需要短 hold 密度或短管链式。
    guanzi = 0.0
    if (
        feat["hold_local_peak"] >= 0.28
        and feat["hold_hot_windows"] >= 2
        and feat["short_hold_count"] >= 10
    ):
        guanzi += 0.55 + min(0.5, feat["hold_local_peak"])
    if feat["hold_local_p90"] >= 0.2 and feat["short_hold_count"] >= 12:
        guanzi += 0.35
    if (
        feat["hold_gap_short"] >= 8
        and feat["hold_gap_min"] <= 0.12
        and feat["short_hold_count"] >= 8
    ):
        guanzi += 0.55 + min(0.5, feat["hold_gap_short"] / 40.0)
    if feat["hold_chain_max"] >= 4 and feat["short_hold_count"] >= 10:
        guanzi += 0.35
    if feat["very_short_hold_count"] >= 20 and feat["hold_hot_windows"] >= 1:
        guanzi += 0.25
    if guanzi >= 0.55:
        add("管子", guanzi)

    # ===== 双押 = 局部同时击峰值 / 链式（绝对次数 + 时长占比，抑制 ratio 饱和）=====
    shuangya = 0.0
    dense_dual = (
        multi_abs_peak >= 6
        and multi_hot4 >= 5
        and multi_hot4_rate >= 0.08
    )
    chain_dual = (
        multi_chain_max >= 12
        and multi_abs_peak >= 5
        and multi_hot4 >= 3
    )
    if dense_dual:
        shuangya += 0.55 + min(0.4, multi_abs_peak / 15.0 + multi_hot4_rate * 2.0)
    if chain_dual:
        shuangya += 0.45 + min(0.4, multi_chain_max / 40.0)
    if multi_hot5 >= 4 and multi_abs_peak >= 7:
        shuangya += 0.25
    if multi_share_peak >= 0.5 and multi_abs_peak >= 6 and multi_hot4 >= 5:
        shuangya += 0.15
    if shuangya >= 0.7:
        add("双押", shuangya)

    # ===== 定位 = 局部高密大位移 / 难星卡手（非重叠窗口）=====
    if feat["dingwei_hits"] >= 2 or (feat["dingwei_hits"] >= 1 and feat["dingwei_peak"] >= 2.8):
        add(
            "定位",
            0.5 + min(1.0, feat["dingwei_hits"] / 4.0 + feat["dingwei_peak"] / 4.0),
        )

    # 手速
    speed = 0.0
    if nps >= 7.5:
        speed += 0.45 + min(0.5, (nps - 7.5) / 8.0)
    if bpm >= 200:
        speed += 0.25 + min(0.35, (bpm - 200) / 120.0)
    if feat["peak_density"] >= 14:
        speed += 0.2
    if speed >= 0.55:
        add("手速", speed)

    # 底力
    if feat["total"] >= 700 and feat["mean_density"] >= 6.5 and duration >= 70:
        add("底力", min(1.3, feat["total"] / 1100.0 + feat["mean_density"] / 14.0))

    # 爆发
    if feat["burst_ratio"] >= 2.4 and feat["peak_density"] >= 14 and feat["peak_density"] - feat["median_density"] >= 6:
        add("爆发", 0.45 + min(0.9, (feat["burst_ratio"] - 2.2) / 2.5))

    if trill_rate >= 0.035 and feat["trill"] >= 12:
        add("交互", 0.45 + min(0.9, trill_rate * 12 + feat["trill"] / 60.0))

    if stack_rate >= 0.06 and feat["stack"] >= 18:
        add("纵连", 0.4 + min(0.9, stack_rate * 8))
        if stack_rate >= 0.11:
            add("叠键", 0.4 + min(0.8, stack_rate * 6))

    if feat["sweep_run"] >= 7:
        add("扫键", 0.45 + min(0.9, (feat["sweep_run"] - 6) / 10.0))

    if jump_rate >= 0.12 and feat["jump"] >= 30:
        add("飞手", 0.4 + min(0.95, (jump_rate - 0.1) * 6))

    # 滑键类（不再叫管子）
    if feat["slide_count"] >= 20 and long_slide_rate >= 0.18:
        add("一笔划", 0.4 + min(0.9, long_slide_rate * 2.5))
    if feat["slide_count"] >= 25 and short_slide_rate >= 0.45 and feat["short_slides"] >= 18:
        add("秒划", 0.4 + min(0.9, short_slide_rate * 1.5))
    if feat["wifi_slides"] >= 4 or (feat["curve_slides"] >= 12 and slide_ratio >= 0.12):
        add("如龙", 0.45 + min(0.95, feat["wifi_slides"] / 10.0 + feat["curve_slides"] / 40.0))
    if short_slide_rate >= 0.4 and slide_ratio >= 0.1 and feat["short_slides"] >= 15:
        add("防蹭", 0.35 + min(0.85, short_slide_rate))

    if feat["hold_overlap"] >= 5 and feat["hold_ratio"] >= 0.035:
        add("留尾", 0.4 + min(0.9, feat["hold_overlap"] / 18.0))

    if tap_ratio >= 0.68 and slide_ratio <= 0.09 and feat["key_entropy"] >= 0.8 and nps >= 5:
        add("散打", 0.45 + min(0.85, feat["key_entropy"]))

    if feat["ioi_cv"] <= 0.28 and feat["ioi_irregular"] <= 0.22 and total >= 250:
        add("定拍", 0.4 + (0.28 - feat["ioi_cv"]))
    if feat["ioi_irregular"] >= 0.48 and feat["ioi_cv"] >= 0.55:
        add("跳拍", 0.4 + min(0.9, feat["ioi_irregular"]))
        add("节奏", 0.35 + min(0.85, feat["ioi_cv"] / 2))
    if feat["ioi_irregular"] >= 0.45 and ds >= 13.2 and slide_ratio >= 0.05:
        add("错位", 0.35 + min(0.85, feat["ioi_irregular"]))

    hand = 0.0
    if jump_rate >= 0.1:
        hand += 0.3
    if trill_rate >= 0.03:
        hand += 0.25
    if multi_abs_peak >= 5 and multi_hot4_rate >= 0.05:
        hand += 0.2
    if feat["sweep_run"] >= 6 and jump_rate >= 0.08:
        hand += 0.2
    if hand >= 0.65:
        add("手序", hand)

    if jump_rate >= 0.11 and multi_abs_peak >= 5 and nps >= 6.5:
        add("拆谱", 0.4 + min(0.85, jump_rate * 3 + multi_abs_peak / 12.0))

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

    multi_abs_peak = feat.get("multi_abs_peak", feat.get("multi_peak", 0.0))
    multi_hot4 = feat.get("multi_hot4", feat.get("multi_hot_windows", 0.0))
    multi_hot4_rate = feat.get("multi_hot4_rate", 0.0)
    multi_chain_max = feat.get("multi_chain_max", 1.0)

    # 主配置保底：按新定义，且双押/定位阈值更高，避免普通高难谱误标
    if "管子" in scores and feat.get("short_hold_count", 0) >= 8 and (
        feat.get("hold_hot_windows", 0) >= 2
        or feat.get("hold_gap_short", 0) >= 8
        or feat.get("hold_local_peak", 0) >= 0.3
    ):
        scores["管子"] = max(scores["管子"], tag_weight("管子") * 1.08)
    if "双押" in scores and (
        (multi_abs_peak >= 7 and multi_hot4_rate >= 0.09)
        or multi_chain_max >= 16
    ):
        scores["双押"] = max(scores["双押"], tag_weight("双押") * 1.05)
    if "定位" in scores and (
        feat.get("dingwei_hits", 0) >= 2 or feat.get("dingwei_peak", 0) >= 3.0
    ):
        scores["定位"] = max(scores["定位"], tag_weight("定位") * 1.05)

    tags, selected = select_final_tags(scores)

    forced: list[str] = []
    if "管子" in scores and feat.get("short_hold_count", 0) >= 8 and (
        feat.get("hold_gap_short", 0) >= 8
        or (feat.get("hold_local_peak", 0) >= 0.3 and feat.get("hold_hot_windows", 0) >= 2)
    ):
        forced.append("管子")
    if "双押" in scores and (
        (multi_abs_peak >= 7 and multi_hot4 >= 8 and multi_hot4_rate >= 0.09)
        or multi_chain_max >= 16
    ):
        forced.append("双押")
    if "定位" in scores and (
        feat.get("dingwei_hits", 0) >= 2 or feat.get("dingwei_peak", 0) >= 3.5
    ):
        forced.append("定位")

    if forced:
        for tag in forced:
            selected[tag] = max(
                float(selected.get(tag, 0.0) or 0.0),
                float(scores.get(tag, tag_weight(tag)) or tag_weight(tag)),
            )
        ordered: list[str] = []
        for tag in forced + tags:
            if tag not in ordered:
                ordered.append(tag)
        tags = ordered[:5]
        selected = {tag: float(selected.get(tag, scores.get(tag, tag_weight(tag)))) for tag in tags}

    conf = 0.0
    if selected:
        strengths = []
        for tag, score in selected.items():
            base = max(tag_weight(tag), 1e-6)
            strengths.append(min(1.0, float(score) / base))
        conf = sum(strengths) / max(len(strengths), 1)
        distinctive = [t for t in tags if TAG_WEIGHTS.get(t, 0) >= 0.7]
        generic = [t for t in tags if t in GENERIC_TAGS]
        if distinctive:
            conf = min(1.0, conf + 0.06 * min(3, len(distinctive)))
        if tags and len(generic) == len(tags):
            conf *= 0.72
        conf = round(min(1.0, max(0.05, conf)), 4)

    return {
        "tags": tags,
        "tag_scores": selected,
        "features": feat,
        "confidence": conf,
        "ds": chart.ds,
        "bpm": chart.bpm,
        "level_index": chart.level_index,
        "source": "maidata_structure",
    }


def analyze_maidata_text(text: str, min_ds: float = 12.6) -> dict[str, Any]:
    song = parse_maidata(text)
    charts_out = {}
    for level_index, chart in song.charts.items():
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
