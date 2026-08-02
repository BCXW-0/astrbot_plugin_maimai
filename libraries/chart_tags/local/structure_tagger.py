from __future__ import annotations

"""从解析后的谱面事件提取结构特征，并映射为加权标签。

圈内定义（v13 玩家校准）：
- 短时高密：连续 ≥2 小节（按时值=2*4拍/BPM）内，某类配置占比达标
  · 双押：同时击 onset 占比 ≥75%
  · Hold 高密：hold 相关占比 ≥50%（管子密度支路）
- 管子：hold（非滑键）。短 hold 局部过密 / hold 链间隙极短 /
  hold 长度或间隔不稳定（节奏型怪异）
- 双押：一组=同一时刻两键；配置=上述两小节窗口内双押主导
- 定位：短时高密大位移卡手；快速大跨度 slide 卡手也归定位
- 留尾：slide 出张大（跨度大），不再用 hold 重叠定义
- 死镰：Death Scythe 经典——连 Tap（含星头，常 3~4 个相邻键）同时处理
  对向 slide，且 slide 启动方向与 tap 迭代方向相反
- 如龙：双押（或隔半拍）引导换手的同侧扫（如 id270 / Regulus）
- 协调（原拆谱）：难协调键型、短纵(2/3)、大位移交互等
- 交互：普通快速交替；另分出 轴交互 / 爬梯交互
- 跳拍：swing/shuffle 与连续附点，而非泛化“不齐”
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


def _button_dir(a: str, b: str) -> int:
    """+1 = 顺时针(编号增大方向的最短? 用环形步进符号), -1 反向, 0 相同/对侧。"""
    try:
        x, y = int(a), int(b)
    except Exception:
        return 0
    if x == y:
        return 0
    cw = (y - x) % 8
    ccw = (x - y) % 8
    if cw == 0 or ccw == 0:
        return 0
    if cw < ccw:
        return 1
    if ccw < cw:
        return -1
    return 0  # 对侧


def _hit_buttons(group: list[NoteEvent]) -> list[str]:
    hit: list[str] = []
    for e in group:
        if e.kind not in {"tap", "break", "hold"}:
            continue
        for b in e.buttons:
            if str(b).isdigit():
                hit.append(str(b))
    return hit


def _is_multi_group(group: list[NoteEvent]) -> bool:
    return len(set(_hit_buttons(group))) >= 2


def _measure_sec(bpm: float) -> float:
    # 4/4 一小节 = 4 拍
    return 240.0 / max(float(bpm or 120.0), 1.0)


def _slide_span(e: NoteEvent) -> int:
    buttons = [str(b) for b in e.buttons if str(b).isdigit()]
    if len(buttons) >= 2:
        # 路径累计跨度（多点）与起终点跨度取大
        path = 0
        for i in range(1, len(buttons)):
            path += _button_dist(buttons[i - 1], buttons[i])
        ends = _button_dist(buttons[0], buttons[-1])
        return max(path, ends)
    return 0


def _slide_dir(e: NoteEvent) -> int:
    buttons = [str(b) for b in e.buttons if str(b).isdigit()]
    if len(buttons) >= 2:
        return _button_dir(buttons[0], buttons[1] if len(buttons) > 1 else buttons[-1])
    shape = e.shape or ""
    # 粗略：> 常作某一向，< 反向；不足则 0
    if ">" in shape or "p" in shape.lower():
        return 1
    if "<" in shape or "q" in shape.lower():
        return -1
    return 0


def _windowed_ratio_runs(
    times_a: list[float],
    times_all: list[float],
    *,
    window: float,
    step: float,
    min_ratio: float,
    min_all: int = 6,
) -> tuple[float, float, int]:
    """返回 (最长连续达标时长, 达标窗口峰值占比, 达标窗口数)。"""
    if not times_all or window <= 0:
        return 0.0, 0.0, 0
    t0 = times_all[0]
    t1 = times_all[-1]
    cur = t0
    flags: list[tuple[float, bool, float]] = []
    peak = 0.0
    while cur <= t1 + 1e-9:
        all_n = sum(1 for t in times_all if cur <= t < cur + window)
        a_n = sum(1 for t in times_a if cur <= t < cur + window)
        ratio = a_n / max(all_n, 1)
        ok = all_n >= min_all and ratio >= min_ratio
        flags.append((cur, ok, ratio))
        if ok:
            peak = max(peak, ratio)
        cur += step

    best = 0.0
    run = 0.0
    hot = 0
    for i, (t, ok, _) in enumerate(flags):
        if ok:
            hot += 1
            run += step
            # 加上窗口本体超出 step 的部分：用 window 估计连续覆盖
            best = max(best, run + max(0.0, window - step))
        else:
            run = 0.0
    return best, peak, hot


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
    bpm = float(chart.bpm or 0.0) or 120.0
    measure = _measure_sec(bpm)
    two_meas = 2.0 * measure
    beat = 60.0 / bpm

    # onset buckets
    buckets: dict[int, list[NoteEvent]] = defaultdict(list)
    for e in events:
        buckets[int(round(e.time * 1000))].append(e)

    multi_times: list[float] = []
    onset_times: list[float] = []
    hold_onset_times: list[float] = []
    for ts, g in sorted(buckets.items()):
        t = ts / 1000.0
        buttons = _hit_buttons(g)
        if buttons:
            onset_times.append(t)
        if _is_multi_group(g):
            multi_times.append(t)
        if any(e.kind == "hold" for e in g):
            hold_onset_times.append(t)

    multi_ratio_global = len(multi_times) / max(len(onset_times), 1)

    # ===== 两小节密度：双押≥75%，hold≥50% =====
    dual_run, dual_peak, dual_hot = _windowed_ratio_runs(
        multi_times, onset_times, window=two_meas, step=max(beat / 2, 0.05), min_ratio=0.75, min_all=8
    )
    hold_run, hold_peak, hold_hot = _windowed_ratio_runs(
        hold_onset_times, onset_times, window=two_meas, step=max(beat / 2, 0.05), min_ratio=0.50, min_all=6
    )

    # multi absolute nonoverlap 1s (辅助)
    t0, t1 = events[0].time, events[-1].time
    multi_counts = []
    cur = t0
    while cur <= t1 + 1e-9:
        multi_counts.append(sum(1 for t in multi_times if cur <= t < cur + 1.0))
        cur += 1.0
    multi_abs_peak = float(max(multi_counts) if multi_counts else 0)
    multi_hot4 = float(sum(1 for c in multi_counts if c >= 4))
    multi_chain = 1
    multi_chain_max = 1
    for i in range(1, len(multi_times)):
        if multi_times[i] - multi_times[i - 1] <= 0.28:
            multi_chain += 1
            multi_chain_max = max(multi_chain_max, multi_chain)
        else:
            multi_chain = 1

    # ===== holds / 管子 =====
    short_holds = [e for e in holds if 0 < e.duration <= 0.40]
    very_short_holds = [e for e in holds if 0 < e.duration <= 0.22]
    hold_gap_min = 999.0
    hold_gap_short = 0
    hold_chain_max = 1
    hold_gaps: list[float] = []
    hold_lens = [float(e.duration) for e in holds if e.duration > 0]
    if len(holds) >= 2:
        ordered = sorted(holds, key=lambda e: e.time)
        chain = 1
        for i in range(1, len(ordered)):
            prev, cur_h = ordered[i - 1], ordered[i]
            gap = cur_h.time - (prev.time + max(prev.duration, 0.0))
            hold_gaps.append(gap)
            hold_gap_min = min(hold_gap_min, gap)
            if gap <= 0.18:
                hold_gap_short += 1
                chain += 1
                hold_chain_max = max(hold_chain_max, chain)
            else:
                chain = 1
    # 节奏型怪异：长度 CV 或 gap CV 高
    def _cv(xs: list[float]) -> float:
        if len(xs) < 4:
            return 0.0
        m = statistics.mean(xs)
        if m <= 1e-6:
            return 0.0
        return statistics.pstdev(xs) / m

    hold_len_cv = _cv(hold_lens)
    hold_gap_cv = _cv([g for g in hold_gaps if g < 2.0])  # 忽略很长空白
    hold_weird = 1.0 if (
        len(holds) >= 8 and (hold_len_cv >= 0.55 or hold_gap_cv >= 0.65) and hold_gap_short >= 3
    ) else 0.0

    # 细窗口 short-hold 局部
    hold_local_peak = 0.0
    hold_hot_windows = 0
    cur = t0
    while cur <= t1 + 1e-9:
        bucket = [e for e in events if cur <= e.time < cur + 1.0]
        if bucket:
            sh = sum(1 for e in bucket if e.kind == "hold" and 0 < e.duration <= 0.40)
            ratio = sh / max(len(bucket), 1)
            hold_local_peak = max(hold_local_peak, ratio)
            if ratio >= 0.22 and sh >= 3:
                hold_hot_windows += 1
        cur += 0.25

    # ===== single taps sequence =====
    single_taps: list[tuple[float, str]] = []
    for e in events:
        if e.kind in {"tap", "break"} and len(e.buttons) == 1 and str(e.buttons[0]).isdigit():
            single_taps.append((e.time, str(e.buttons[0])))
        elif e.kind == "slide" and e.buttons and str(e.buttons[0]).isdigit():
            # 星头可参与死镰 tap 链
            single_taps.append((e.time, str(e.buttons[0])))

    # 去重同刻同键
    single_taps = sorted(set(single_taps), key=lambda x: (x[0], x[1]))

    trill = stack = jump = 0
    short_stack_runs = 0  # 短纵 2/3
    i = 0
    while i < len(single_taps):
        # stacks of same button
        j = i + 1
        while j < len(single_taps) and single_taps[j][1] == single_taps[i][1] and single_taps[j][0] - single_taps[j - 1][0] <= beat * 0.6:
            j += 1
        run_len = j - i
        if run_len >= 2:
            stack += run_len - 1
            # 短纵：长度 2 或 3，间隔通常快于 16 分（16分间隔=beat/4）
            dts = [single_taps[k][0] - single_taps[k - 1][0] for k in range(i + 1, j)]
            if run_len in {2, 3} and dts and statistics.mean(dts) <= (beat / 4) * 1.15:
                short_stack_runs += 1
        i = max(j, i + 1)

    for k in range(1, len(single_taps)):
        tprev, b0 = single_taps[k - 1]
        t1_, b1 = single_taps[k]
        dt = t1_ - tprev
        if dt <= 0 or dt > 0.35:
            continue
        dist = _button_dist(b0, b1)
        if dist == 1:
            if k >= 2 and single_taps[k - 2][1] == b1 and b0 != b1:
                trill += 1
        elif dist >= 3:
            jump += 1

    # sweep run
    run = 1
    max_run = 1
    direction = 0
    for k in range(1, len(single_taps)):
        b0 = int(single_taps[k - 1][1])
        b1 = int(single_taps[k][1])
        dt = single_taps[k][0] - single_taps[k - 1][0]
        if dt > 0.25:
            run = 1
            direction = 0
            continue
        diff = (b1 - b0) % 8
        if diff in {1, 7}:
            dir_now = 1 if diff == 1 else -1
            if direction in {0, dir_now}:
                direction = dir_now
                run += 1
                max_run = max(max_run, run)
            else:
                direction = dir_now
                run = 2
        else:
            run = 1
            direction = 0

    # ===== 轴交互 / 爬梯交互 / 普通交互 =====
    buttons_only = [b for _, b in single_taps]
    times_only = [t for t, _ in single_taps]

    def _axis_ladder_scan() -> tuple[int, int]:
        ax = 0
        ld = 0
        n = len(buttons_only)
        for start in range(0, max(0, n - 7)):
            seq = buttons_only[start:start + 8]
            ts = times_only[start:start + 8]
            span_t = ts[-1] - ts[0]
            if span_t <= 0 or span_t > beat * 8:
                continue
            even = seq[0::2]
            odd = seq[1::2]
            if len(set(even)) == 1 and len(set(odd)) >= 2 and even[0] not in set(odd):
                ax += 1
            dirs = [_button_dir(seq[i], seq[i + 1]) for i in range(7)]
            dists = [_button_dist(seq[i], seq[i + 1]) for i in range(7)]
            if min(dists) >= 1 and dirs.count(0) <= 2:
                sign_flip = sum(1 for i in range(6) if dirs[i] and dirs[i + 1] and dirs[i] == -dirs[i + 1])
                nondec = sum(1 for i in range(6) if dists[i + 1] >= dists[i])
                if sign_flip >= 4 and nondec >= 3 and len(set(seq)) >= 5:
                    ld += 1
            # 固定模板旋转/镜像：相对中心的配对
            try:
                nums = [int(x) for x in seq]
            except Exception:
                continue
            # 检查是否接近 (c, c-1, c+1, c-2, c+2, ...)
            c = nums[0]
            expect_scores = 0
            for i, v in enumerate(nums[1:], start=1):
                step = (i + 1) // 2
                sign = -1 if i % 2 == 1 else 1
                expect = ((c - 1 + sign * step) % 8) + 1
                if v == expect:
                    expect_scores += 1
            if expect_scores >= 5:
                ld += 1
        return ax, ld

    axis_hits, ladder_hits = _axis_ladder_scan()


    # ===== 死镰：对向 slide + 反向连 tap =====
    def has_opposite_run(window_taps: list[tuple[float, str]], sdir: int, beat_: float) -> bool:
        run_b = 1
        run_dir = 0
        best = 0
        best_dir = 0
        for k in range(1, len(window_taps)):
            d = _button_dir(window_taps[k - 1][1], window_taps[k][1])
            dt = window_taps[k][0] - window_taps[k - 1][0]
            if d != 0 and _button_dist(window_taps[k - 1][1], window_taps[k][1]) == 1 and dt <= beat_ * 0.75:
                if run_dir in {0, d}:
                    run_dir = d
                    run_b += 1
                else:
                    run_dir = d
                    run_b = 2
                if run_b > best:
                    best = run_b
                    best_dir = run_dir
            else:
                run_dir = 0
                run_b = 1
        if best >= 3 and sdir != 0 and best_dir == -sdir:
            return True
        return False

    death_hits = 0
    for sl in slides:
        sdir = _slide_dir(sl)
        window_taps = [
            (t, b) for t, b in single_taps
            if sl.time - 0.02 <= t <= sl.time + max(sl.duration, beat * 0.5) + beat * 0.2
        ]
        if len(window_taps) < 3:
            continue
        span = _slide_span(sl)
        if sdir != 0 and span >= 2 and has_opposite_run(window_taps, sdir, beat):
            death_hits += 1
        elif sdir == 0 and span >= 3 and (
            has_opposite_run(window_taps, 1, beat) or has_opposite_run(window_taps, -1, beat)
        ):
            # 无明确方向时：要求更长连 tap
            run_b = 1
            best = 1
            for k in range(1, len(window_taps)):
                if _button_dist(window_taps[k-1][1], window_taps[k][1]) == 1 and window_taps[k][0]-window_taps[k-1][0] <= beat*0.75:
                    run_b += 1
                    best = max(best, run_b)
                else:
                    run_b = 1
            if best >= 4:
                death_hits += 1

    # ===== 如龙：双押/半拍后同侧扫 =====
    rulong_hits = 0
    multi_set = multi_times
    for mt in multi_set:
        # after multi, look half-beat to 2 beats sweep same direction
        seq = [(t, b) for t, b in single_taps if mt - 0.01 <= t <= mt + beat * 2.2]
        if len(seq) < 4:
            continue
        # find sweep of >=3 adjacent after first 1-2 notes
        for start in range(0, min(3, len(seq))):
            d0 = 0
            length = 1
            ok = False
            for k in range(start + 1, len(seq)):
                d = _button_dir(seq[k - 1][1], seq[k][1])
                dist = _button_dist(seq[k - 1][1], seq[k][1])
                if dist == 1 and d != 0:
                    if d0 in {0, d}:
                        d0 = d
                        length += 1
                        if length >= 4:
                            ok = True
                    else:
                        break
                elif seq[k][0] - seq[k - 1][0] <= beat * 0.55 and dist == 0:
                    continue
                else:
                    if length >= 4:
                        ok = True
                    break
            if ok:
                rulong_hits += 1
                break
    # half-beat gap "pseudo dual": two taps ~0.5 beat apart then sweep
    for k in range(len(single_taps) - 5):
        dt = single_taps[k + 1][0] - single_taps[k][0]
        if abs(dt - beat * 0.5) <= beat * 0.12 or abs(dt - beat) <= beat * 0.12:
            seq = single_taps[k:k + 6]
            d0 = 0
            length = 1
            for i in range(2, len(seq)):
                d = _button_dir(seq[i - 1][1], seq[i][1])
                if _button_dist(seq[i - 1][1], seq[i][1]) == 1 and d != 0 and d0 in {0, d}:
                    d0 = d
                    length += 1
                else:
                    break
            if length >= 4:
                rulong_hits += 1

    # ===== 留尾 = 大跨度 slide；定位兼收快大跨卡手 =====
    large_span_slides = 0
    fast_large_span = 0
    for sl in slides:
        span = _slide_span(sl)
        if span >= 3:
            large_span_slides += 1
            # 快速：单位跨度时值短
            if sl.duration > 0 and (sl.duration / max(span, 1)) <= (beat * 0.35):
                fast_large_span += 1

    # 定位：非重叠 1s 高密大位移 + 快大跨 slide 附近
    dens_list = []
    cur = t0
    while cur <= t1 + 1e-9:
        dens_list.append(sum(1 for e in events if cur <= e.time < cur + 1.0))
        cur += 1.0
    dens_med = statistics.median(dens_list) if dens_list else 0.0
    dingwei_hits = 0
    dingwei_peak = 0.0
    cur = t0
    while cur <= t1 + 1e-9:
        density = float(sum(1 for e in events if cur <= e.time < cur + 1.0))
        wtaps = [(t, b) for t, b in single_taps if cur <= t < cur + 1.0]
        big_jump = 0
        max_jump = 0
        jump_n = 0
        for k in range(1, len(wtaps)):
            d = _button_dist(wtaps[k - 1][1], wtaps[k][1])
            dt = wtaps[k][0] - wtaps[k - 1][0]
            max_jump = max(max_jump, d)
            if 0 < dt <= 0.22 and d >= 3:
                big_jump += 1
                jump_n += 1
        jrate = jump_n / max(len(wtaps), 1)
        local_fast_span = sum(
            1 for sl in slides
            if cur <= sl.time < cur + 1.0 and _slide_span(sl) >= 3 and sl.duration > 0
            and sl.duration / max(_slide_span(sl), 1) <= beat * 0.35
        )
        score = 0.0
        if density >= max(14.0, dens_med * 1.9) and big_jump >= 4 and max_jump >= 3 and jrate >= 0.28:
            score = density / 12.0 + big_jump * 0.2 + jrate
        elif local_fast_span >= 1 and density >= max(10.0, dens_med * 1.3) and (big_jump >= 2 or max_jump >= 3):
            score = 1.6 + local_fast_span * 0.3 + big_jump * 0.1
        if score >= 1.5:
            dingwei_hits += 1
            dingwei_peak = max(dingwei_peak, score)
        cur += 1.0

    # ===== 协调：短纵 + 大位移交互 + 难协调双押夹单点 =====
    # 大位移交互 a,b,a,b,c,d,c,d
    coord_disp = 0
    for start in range(0, max(0, len(buttons_only) - 7)):
        seq = buttons_only[start:start + 8]
        ts = times_only[start:start + 8]
        if ts[-1] - ts[0] > beat * 6:
            continue
        dists = [_button_dist(seq[i], seq[i + 1]) for i in range(7)]
        if sum(1 for d in dists if d >= 2) >= 5 and len(set(seq)) >= 4:
            # 交替感
            if seq[0] != seq[1] and seq[0] == seq[2] or _button_dist(seq[0], seq[1]) >= 2:
                coord_disp += 1
    # 双押夹单键密集：multi 后连续同键/单键
    dual_clamp = 0
    for mt in multi_times:
        mono = [e for e in events if mt < e.time <= mt + beat * 1.5 and e.kind in {"tap", "break"}]
        if len(mono) >= 3:
            dual_clamp += 1

    # slides stats
    short_slides = sum(1 for e in slides if 0 < e.duration <= 0.35)
    long_slides = sum(1 for e in slides if e.duration >= 0.75)
    wifi = sum(1 for e in slides if "w" in (e.shape or ""))
    curve = sum(1 for e in slides if any(s in (e.shape or "") for s in ("pp", "qq", "p", "q", "z", "V", "<>")))

    # IOI for 跳拍：附点/swing，而非泛不齐
    intervals = []
    for k in range(1, len(single_taps)):
        dt = single_taps[k][0] - single_taps[k - 1][0]
        if 0.05 <= dt <= beat * 2.5:
            intervals.append(dt)
    dotted_runs = 0
    swing_runs = 0
    if len(intervals) >= 6:
        # 附点：长短比接近 3:1 或 2:1 的交替
        run = 0
        for i in range(1, len(intervals)):
            a, b = intervals[i - 1], intervals[i]
            ratio = max(a, b) / max(min(a, b), 1e-6)
            long_short = (a > b and 1.7 <= ratio <= 3.4) or (b > a and 1.7 <= ratio <= 3.4)
            if long_short:
                run += 1
                if run >= 3:
                    dotted_runs += 1
                    run = 0
            else:
                run = 0
        # swing/shuffle：连续 even-odd 配对 ratio 稳定在 ~2
        pair_ok = 0
        for i in range(0, len(intervals) - 1, 2):
            a, b = intervals[i], intervals[i + 1]
            ratio = max(a, b) / max(min(a, b), 1e-6)
            if 1.6 <= ratio <= 2.6:
                pair_ok += 1
            else:
                if pair_ok >= 3:
                    swing_runs += 1
                pair_ok = 0
        if pair_ok >= 3:
            swing_runs += 1
        mean_i = statistics.mean(intervals)
        cv = statistics.pstdev(intervals) / max(mean_i, 1e-6)
        irregular = sum(1 for x in intervals if abs(x - mean_i) > mean_i * 0.35) / len(intervals)
    else:
        cv = 0.0
        irregular = 0.0

    # density peaks
    dens = dens_list or [0.0]
    peak = max(dens)
    median = statistics.median(dens)
    mean_d = statistics.mean(dens)

    # entropy
    counter = Counter(b for _, b in single_taps if True)
    # only pure taps entropy roughly
    pure_buttons = []
    for e in events:
        if e.kind in {"tap", "break"} and len(e.buttons) == 1 and str(e.buttons[0]).isdigit():
            pure_buttons.append(str(e.buttons[0]))
    counter = Counter(pure_buttons)
    probs = [c / max(sum(counter.values()), 1) for c in counter.values()] or [1.0]
    entropy = -sum(p * math.log(p + 1e-12, 2) for p in probs) / 3.0

    pure_tap_n = max(len(pure_buttons), 1)

    return {
        "empty": 0.0,
        "ds": float(chart.ds or 0),
        "bpm": float(bpm),
        "duration": float(duration),
        "measure_sec": float(measure),
        "two_measure_sec": float(two_meas),
        "total": float(total),
        "nps": total / duration,
        "tap_ratio": len(taps) / total,
        "slide_ratio": len(slides) / total,
        "hold_ratio": len(holds) / total,
        "touch_ratio": len(touches) / total,
        "break_ratio": sum(1 for e in events if getattr(e, "is_break", False) or e.kind == "break") / total,
        "multi_ratio": float(multi_ratio_global),
        "multi_abs_peak": float(multi_abs_peak),
        "multi_peak": float(multi_abs_peak),
        "multi_hot4": float(multi_hot4),
        "multi_hot_windows": float(multi_hot4),
        "multi_hot4_rate": float(multi_hot4) / max(duration, 1.0),
        "multi_chain_max": float(multi_chain_max),
        "dual_dense_run": float(dual_run),
        "dual_dense_peak": float(dual_peak),
        "dual_dense_hot": float(dual_hot),
        "hold_dense_run": float(hold_run),
        "hold_dense_peak": float(hold_peak),
        "hold_dense_hot": float(hold_hot),
        "peak_density": float(peak),
        "mean_density": float(mean_d),
        "median_density": float(median),
        "burst_ratio": float(peak / max(median, 1.0)),
        "trill": float(trill),
        "stack": float(stack),
        "jump": float(jump),
        "sweep_run": float(max_run),
        "short_stack_runs": float(short_stack_runs),
        "axis_hits": float(axis_hits),
        "ladder_hits": float(ladder_hits),
        "death_scythe_hits": float(death_hits),
        "rulong_hits": float(rulong_hits),
        "coord_disp": float(coord_disp),
        "dual_clamp": float(dual_clamp),
        "short_slides": float(short_slides),
        "long_slides": float(long_slides),
        "wifi_slides": float(wifi),
        "curve_slides": float(curve),
        "large_span_slides": float(large_span_slides),
        "fast_large_span_slides": float(fast_large_span),
        "hold_count": float(len(holds)),
        "short_hold_count": float(len(short_holds)),
        "very_short_hold_count": float(len(very_short_holds)),
        "hold_local_peak": float(hold_local_peak),
        "hold_hot_windows": float(hold_hot_windows),
        "hold_gap_min": float(hold_gap_min if hold_gap_min < 900 else 9.0),
        "hold_gap_short": float(hold_gap_short),
        "hold_chain_max": float(hold_chain_max),
        "hold_len_cv": float(hold_len_cv),
        "hold_gap_cv": float(hold_gap_cv),
        "hold_weird": float(hold_weird),
        "dingwei_hits": float(dingwei_hits),
        "dingwei_peak": float(dingwei_peak),
        "dotted_runs": float(dotted_runs),
        "swing_runs": float(swing_runs),
        "ioi_irregular": float(irregular),
        "ioi_cv": float(cv),
        "key_entropy": float(entropy),
        "slide_count": float(len(slides)),
        "touch_count": float(len(touches)),
        "tap_count": float(len(taps)),
        "pure_tap_n": float(pure_tap_n),
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
    two_meas = max(feat.get("two_measure_sec", 2.0), 0.5)

    jump_rate = feat["jump"] / total
    trill_rate = feat["trill"] / max(feat.get("pure_tap_n", total), 1.0)
    stack_rate = feat["stack"] / total
    short_slide_rate = feat["short_slides"] / max(feat["slide_count"], 1.0)
    long_slide_rate = feat["long_slides"] / max(feat["slide_count"], 1.0)

    # ===== 管子 =====
    guanzi = 0.0
    if feat.get("hold_dense_run", 0) >= two_meas * 0.95 and feat.get("hold_dense_peak", 0) >= 0.5:
        guanzi += 0.6 + min(0.4, feat["hold_dense_peak"])
    if feat["hold_local_peak"] >= 0.28 and feat["hold_hot_windows"] >= 2 and feat["short_hold_count"] >= 10:
        guanzi += 0.45
    if feat["hold_gap_short"] >= 8 and feat["hold_gap_min"] <= 0.12 and feat["short_hold_count"] >= 8:
        guanzi += 0.55 + min(0.4, feat["hold_gap_short"] / 40.0)
    if feat["hold_chain_max"] >= 4 and feat["short_hold_count"] >= 10:
        guanzi += 0.3
    if feat.get("hold_weird", 0) >= 1:
        guanzi += 0.5  # 节奏型怪异
    if feat["very_short_hold_count"] >= 20 and feat["hold_hot_windows"] >= 1:
        guanzi += 0.2
    if guanzi >= 0.55:
        add("管子", guanzi)

    # ===== 双押：两小节窗口 ≥75% =====
    shuangya = 0.0
    if feat.get("dual_dense_run", 0) >= two_meas * 0.95 and feat.get("dual_dense_peak", 0) >= 0.75:
        shuangya += 0.7 + min(0.4, (feat["dual_dense_peak"] - 0.75) * 2)
    if feat.get("dual_dense_hot", 0) >= 2 and feat.get("dual_dense_peak", 0) >= 0.75:
        shuangya += 0.35
    # 弱辅助：极强链式不再单独成标签，除非已有两小节证据
    if shuangya >= 0.7:
        add("双押", shuangya)

    # ===== 定位 =====
    if feat["dingwei_hits"] >= 2 or (feat["dingwei_hits"] >= 1 and feat["dingwei_peak"] >= 2.8):
        add("定位", 0.5 + min(1.0, feat["dingwei_hits"] / 4.0 + feat["dingwei_peak"] / 4.0))
    elif feat.get("fast_large_span_slides", 0) >= 4 and jump_rate >= 0.08:
        add("定位", 0.45 + min(0.7, feat["fast_large_span_slides"] / 12.0))

    # ===== 留尾：大跨度 slide 出张 =====
    if feat.get("large_span_slides", 0) >= 6:
        add("留尾", 0.45 + min(0.9, feat["large_span_slides"] / 20.0))

    # 手速 / 底力 / 爆发
    speed = 0.0
    if nps >= 7.5:
        speed += 0.45 + min(0.5, (nps - 7.5) / 8.0)
    if bpm >= 200:
        speed += 0.25 + min(0.35, (bpm - 200) / 120.0)
    if feat["peak_density"] >= 14:
        speed += 0.2
    if speed >= 0.55:
        add("手速", speed)
    if feat["total"] >= 700 and feat["mean_density"] >= 6.5 and duration >= 70:
        add("底力", min(1.3, feat["total"] / 1100.0 + feat["mean_density"] / 14.0))
    if feat["burst_ratio"] >= 2.4 and feat["peak_density"] >= 14 and feat["peak_density"] - feat["median_density"] >= 6:
        add("爆发", 0.45 + min(0.9, (feat["burst_ratio"] - 2.2) / 2.5))

    # 交互族
    if feat.get("axis_hits", 0) >= 3:
        add("轴交互", 0.5 + min(0.9, feat["axis_hits"] / 12.0))
    if feat.get("ladder_hits", 0) >= 2:
        add("爬梯交互", 0.5 + min(0.9, feat["ladder_hits"] / 10.0))
    if trill_rate >= 0.035 and feat["trill"] >= 12:
        # 若已被更细标签覆盖，仍可保留普通交互但稍弱
        base = 0.45 + min(0.9, trill_rate * 12 + feat["trill"] / 60.0)
        if feat.get("axis_hits", 0) >= 3 or feat.get("ladder_hits", 0) >= 2:
            base *= 0.75
        add("交互", base)

    if stack_rate >= 0.06 and feat["stack"] >= 18:
        add("纵连", 0.4 + min(0.9, stack_rate * 8))
        if stack_rate >= 0.11:
            add("叠键", 0.4 + min(0.8, stack_rate * 6))

    if feat["sweep_run"] >= 7:
        add("扫键", 0.45 + min(0.9, (feat["sweep_run"] - 6) / 10.0))
    if jump_rate >= 0.12 and feat["jump"] >= 30:
        add("飞手", 0.4 + min(0.95, (jump_rate - 0.1) * 6))

    # 死镰 / 如龙（重定义）
    if feat.get("death_scythe_hits", 0) >= 2:
        add("死镰", 0.55 + min(0.9, feat["death_scythe_hits"] / 8.0))
    if feat.get("rulong_hits", 0) >= 3:
        add("如龙", 0.55 + min(0.9, feat["rulong_hits"] / 12.0))
    # 旧 wifi/curve 不再直接打如龙，仅弱辅助
    elif feat["wifi_slides"] >= 6 and feat.get("rulong_hits", 0) >= 1:
        add("如龙", 0.4 + min(0.5, feat["wifi_slides"] / 15.0))

    # 协调（原拆谱）
    coord = 0.0
    if feat.get("short_stack_runs", 0) >= 4:
        coord += 0.45 + min(0.4, feat["short_stack_runs"] / 15.0)
    if feat.get("coord_disp", 0) >= 3:
        coord += 0.45 + min(0.4, feat["coord_disp"] / 12.0)
    if feat.get("dual_clamp", 0) >= 6 and jump_rate >= 0.06:
        coord += 0.3
    if coord >= 0.55:
        add("协调", coord)

    # 滑键其它
    if feat["slide_count"] >= 20 and long_slide_rate >= 0.18:
        add("一笔划", 0.4 + min(0.9, long_slide_rate * 2.5))
    if feat["slide_count"] >= 25 and short_slide_rate >= 0.45 and feat["short_slides"] >= 18:
        add("秒划", 0.4 + min(0.9, short_slide_rate * 1.5))
    if short_slide_rate >= 0.4 and slide_ratio >= 0.1 and feat["short_slides"] >= 15:
        add("防蹭", 0.35 + min(0.85, short_slide_rate))

    if tap_ratio >= 0.68 and slide_ratio <= 0.09 and feat["key_entropy"] >= 0.8 and nps >= 5:
        add("散打", 0.45 + min(0.85, feat["key_entropy"]))

    # 跳拍：swing/dotted
    if feat.get("swing_runs", 0) >= 1 or feat.get("dotted_runs", 0) >= 2:
        add("跳拍", 0.5 + min(0.9, feat.get("swing_runs", 0) * 0.25 + feat.get("dotted_runs", 0) * 0.15))
        add("节奏", 0.35 + min(0.6, feat.get("ioi_cv", 0) / 2))
    if feat["ioi_cv"] <= 0.28 and feat["ioi_irregular"] <= 0.22 and total >= 250:
        add("定拍", 0.4 + (0.28 - feat["ioi_cv"]))
    if feat["ioi_irregular"] >= 0.45 and ds >= 13.2 and slide_ratio >= 0.05 and feat.get("swing_runs", 0) == 0:
        add("错位", 0.35 + min(0.85, feat["ioi_irregular"]))

    hand = 0.0
    if jump_rate >= 0.1:
        hand += 0.3
    if trill_rate >= 0.03:
        hand += 0.25
    if feat.get("dual_dense_run", 0) >= two_meas:
        hand += 0.2
    if feat["sweep_run"] >= 6 and jump_rate >= 0.08:
        hand += 0.2
    if hand >= 0.65:
        add("手序", hand)

    if feat["ioi_cv"] >= 0.85 and feat["ioi_irregular"] >= 0.5 and slide_ratio < 0.2 and ds >= 13.0:
        add("背谱", 0.35 + min(0.75, feat["ioi_cv"] / 2))

    if ds >= 14.0:
        for key in list(scores):
            scores[key] *= 1.04
    return scores


def analyze_chart_tags(chart: MaidataChart) -> dict[str, Any]:
    feat = extract_features(chart)
    scores = features_to_tag_scores(feat)
    two_meas = max(feat.get("two_measure_sec", 2.0), 0.5)

    if "管子" in scores and feat.get("short_hold_count", 0) >= 8 and (
        feat.get("hold_dense_run", 0) >= two_meas * 0.9
        or feat.get("hold_gap_short", 0) >= 8
        or feat.get("hold_weird", 0) >= 1
    ):
        scores["管子"] = max(scores["管子"], tag_weight("管子") * 1.08)
    if "双押" in scores and feat.get("dual_dense_run", 0) >= two_meas * 0.95:
        scores["双押"] = max(scores["双押"], tag_weight("双押") * 1.08)
    if "死镰" in scores and feat.get("death_scythe_hits", 0) >= 3:
        scores["死镰"] = max(scores["死镰"], tag_weight("死镰") * 1.1)
    if "如龙" in scores and feat.get("rulong_hits", 0) >= 4:
        scores["如龙"] = max(scores["如龙"], tag_weight("如龙") * 1.08)
    if "协调" in scores and (
        feat.get("short_stack_runs", 0) >= 5 or feat.get("coord_disp", 0) >= 4
    ):
        scores["协调"] = max(scores["协调"], tag_weight("协调") * 1.05)

    tags, selected = select_final_tags(scores)

    forced: list[str] = []
    if "管子" in scores and (
        feat.get("hold_weird", 0) >= 1
        or (feat.get("hold_gap_short", 0) >= 8 and feat.get("short_hold_count", 0) >= 8)
        or feat.get("hold_dense_run", 0) >= two_meas
    ):
        forced.append("管子")
    if "双押" in scores and feat.get("dual_dense_run", 0) >= two_meas and feat.get("dual_dense_peak", 0) >= 0.75:
        forced.append("双押")
    if "死镰" in scores and feat.get("death_scythe_hits", 0) >= 3:
        forced.append("死镰")
    if "如龙" in scores and feat.get("rulong_hits", 0) >= 5:
        forced.append("如龙")
    if "轴交互" in scores and feat.get("axis_hits", 0) >= 4:
        forced.append("轴交互")
    if "爬梯交互" in scores and feat.get("ladder_hits", 0) >= 3:
        forced.append("爬梯交互")
    if "协调" in scores and feat.get("short_stack_runs", 0) >= 6:
        forced.append("协调")

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
