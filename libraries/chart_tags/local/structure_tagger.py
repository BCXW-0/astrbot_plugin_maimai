from __future__ import annotations

"""Deterministic chart tagging rules defined by the supplied maimai.xls.

The module has two deliberately separate layers:

* ``extract_features`` returns numeric evidence for the local classifier.
* ``analyze_chart_tags`` applies the XLS candidate/difficulty definitions and
  returns auditable spans for the metadata dataset.

All local density decisions use a BPM-aware two-measure window.  No network
source or AstrBot model is consulted here.
"""

import math
import statistics
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..constants import DIFFICULTY_CAPS, GENERIC_TAGS, TAG_WEIGHTS
from ..rule_tags import filter_allowed_tags, tag_weight
from .maidata_parser import MaidataChart, NoteEvent


def _button(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 8 else None


def _ring_distance(left: Any, right: Any) -> int:
    a, b = _button(left), _button(right)
    if a is None or b is None:
        return 0
    distance = abs(a - b) % 8
    return min(distance, 8 - distance)


def _ring_direction(left: Any, right: Any) -> int:
    a, b = _button(left), _button(right)
    if a is None or b is None or a == b:
        return 0
    clockwise = (b - a) % 8
    counter_clockwise = (a - b) % 8
    if clockwise < counter_clockwise:
        return 1
    if counter_clockwise < clockwise:
        return -1
    return 0


def _event_buttons(event: NoteEvent) -> list[int]:
    return [number for number in (_button(value) for value in event.buttons) if number is not None]


def _is_tap(event: NoteEvent) -> bool:
    return event.kind in {"tap", "break"} and bool(_event_buttons(event))


def _measure_seconds(bpm: float) -> float:
    return 240.0 / max(float(bpm or 120.0), 1.0)


def _cv(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    average = math.fsum(values) / len(values)
    if average <= 1e-9:
        return 0.0
    variance = math.fsum((value - average) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / average


def _side(number: int) -> int:
    # XLS convention: 5-8 are the left hand zone, 1-4 the right hand zone.
    return -1 if number in {5, 6, 7, 8} else 1


def _event_zone_crossing(event: NoteEvent) -> int:
    numbers = _event_buttons(event)
    if len(numbers) < 2:
        return 0
    start_side = _side(numbers[0])
    return int(any(_side(number) != start_side for number in numbers[1:]))


def _group_events(chart: MaidataChart) -> list[dict[str, Any]]:
    groups: dict[int, list[tuple[int, NoteEvent]]] = defaultdict(list)
    for index, event in enumerate(sorted(chart.events, key=lambda item: item.time)):
        groups[round(float(event.time) * 1000)].append((index, event))
    result: list[dict[str, Any]] = []
    for stamp, indexed in sorted(groups.items()):
        events = [event for _, event in indexed]
        buttons = sorted({number for event in events for number in _event_buttons(event)})
        result.append({
            "time": stamp / 1000.0,
            "events": events,
            "indexes": [index for index, _ in indexed],
            "buttons": buttons,
            "multi": int(len(buttons) >= 2),
            "hold": int(any(event.kind == "hold" for event in events)),
            "slide": int(any(event.kind == "slide" for event in events)),
            "zone_cross": sum(_event_zone_crossing(event) for event in events),
        })
    return result


def _sequence_text(groups: list[dict[str, Any]], start: float) -> str:
    tokens: list[str] = []
    bpm = float(groups[0]["events"][0].bpm or 120.0) if groups else 120.0
    for group in groups:
        beat = (float(group["time"]) - start) * bpm / 60.0
        raw = "/".join(event.raw for event in group["events"] if event.raw)
        tokens.append(f"{beat:.3f}:{raw}")
    return "; ".join(tokens)[:1600]


def _window_stats(
    chart: MaidataChart,
    *,
    include_evidence: bool = True,
    groups: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    groups = groups if groups is not None else _group_events(chart)
    if not groups:
        return [], {"two_measure_sec": 0.0}
    times = [float(group["time"]) for group in groups]
    prefixes: dict[str, list[int]] = {}
    for key in ("multi", "hold", "slide", "zone_cross"):
        values = [int(group[key]) for group in groups]
        prefix = [0]
        for value in values:
            prefix.append(prefix[-1] + value)
        prefixes[key] = prefix

    windows: list[dict[str, Any]] = []
    best_dual: list[dict[str, Any]] = []
    best_hold: list[dict[str, Any]] = []
    for left, group in enumerate(groups):
        bpm = float(group["events"][0].bpm or chart.bpm or 120.0)
        duration = 2.0 * _measure_seconds(bpm)
        right = bisect_right(times, times[left] + duration - 1e-9)
        count = max(right - left, 1)
        multi = prefixes["multi"][right] - prefixes["multi"][left]
        hold = prefixes["hold"][right] - prefixes["hold"][left]
        slides = prefixes["slide"][right] - prefixes["slide"][left]
        zone_cross = prefixes["zone_cross"][right] - prefixes["zone_cross"][left]
        density = sum(len(item["events"]) for item in groups[left:right]) / max(duration, 1e-6)
        entry = {
            "start": round(times[left], 6),
            "end": round(times[left] + duration, 6),
            "bpm": round(bpm, 3),
            "duration": round(duration, 6),
            "event_indexes": (
                [index for item in groups[left:right] for index in item["indexes"]]
                if include_evidence
                else []
            ),
            "onset_count": count,
            "event_count": sum(len(item["events"]) for item in groups[left:right]),
            "multi_ratio": multi / count,
            "hold_ratio": hold / count,
            "slide_ratio": slides / count,
            "zone_cross": zone_cross,
            "density": density,
            "sequence": _sequence_text(groups[left:right], times[left]) if include_evidence else "",
        }
        if count >= 4:
            windows.append(entry)
            if entry["multi_ratio"] >= 0.75:
                best_dual.append(entry)
            if entry["hold_ratio"] >= 0.50:
                best_hold.append(entry)

    def _span(items: list[dict[str, Any]]) -> float:
        if not items:
            return 0.0
        ordered = sorted(items, key=lambda item: item["start"])
        best = current = ordered[0]["duration"]
        previous_end = ordered[0]["end"]
        for item in ordered[1:]:
            if item["start"] <= previous_end + 1e-6:
                current = max(current, item["end"] - ordered[0]["start"])
                previous_end = max(previous_end, item["end"])
            else:
                current = item["duration"]
                previous_end = item["end"]
            best = max(best, current)
        return best

    summary = {
        "two_measure_sec": 2.0 * _measure_seconds(float(chart.bpm or 120.0)),
        "window_count": float(len(windows)),
        "dual_window_count": float(len(best_dual)),
        "dual_dense_run": float(_span(best_dual)),
        "dual_dense_peak": float(max((item["multi_ratio"] for item in best_dual), default=0.0)),
        "hold_window_count": float(len(best_hold)),
        "hold_dense_run": float(_span(best_hold)),
        "hold_dense_peak": float(max((item["hold_ratio"] for item in best_hold), default=0.0)),
        "window_density_peak": float(max((item["density"] for item in windows), default=0.0)),
        "window_zone_cross_peak": float(max((item["zone_cross"] for item in windows), default=0.0)),
    }
    return windows, summary


def _tap_sequence(
    chart: MaidataChart,
    *,
    groups: list[dict[str, Any]] | None = None,
) -> list[tuple[float, int, int]]:
    sequence: list[tuple[float, int, int]] = []
    for group in (groups if groups is not None else _group_events(chart)):
        taps = [event for event in group["events"] if _is_tap(event)]
        for event in taps:
            for number in _event_buttons(event):
                sequence.append((float(group["time"]), number, len(taps)))
    return sorted(sequence)


def _sweep_features(sequence: list[tuple[float, int, int]], beat: float) -> dict[str, float]:
    if len(sequence) < 2:
        return {"sweep_max": 0.0, "short_sweep_hits": 0.0, "circle_hits": 0.0, "sweep_mixed_hits": 0.0}
    max_run = 1
    current = 1
    direction = 0
    short_hits = 0
    circle_hits = 0
    mixed_hits = 0
    for index in range(1, len(sequence)):
        previous, current_item = sequence[index - 1], sequence[index]
        dt = current_item[0] - previous[0]
        step = _ring_direction(previous[1], current_item[1])
        if 0 < dt <= beat * 0.75 and _ring_distance(previous[1], current_item[1]) == 1 and step:
            if direction in {0, step}:
                direction = step
                current += 1
            else:
                mixed_hits += int(current >= 3)
                direction = step
                current = 2
            max_run = max(max_run, current)
        else:
            mixed_hits += int(current >= 3)
            current = 1
            direction = 0
    mixed_hits += int(current >= 3)
    for start in range(max(0, len(sequence) - 7)):
        sample = sequence[start:start + 8]
        if all(
            0 < sample[index][0] - sample[index - 1][0] <= beat * 0.75
            and _ring_distance(sample[index - 1][1], sample[index][1]) == 1
            for index in range(1, len(sample))
        ):
            directions = [_ring_direction(sample[index - 1][1], sample[index][1]) for index in range(1, len(sample))]
            if directions and all(direction == directions[0] for direction in directions):
                short_hits += 1
    circle_hits = sum(1 for start in range(len(sequence)) if _same_direction_run(sequence, start, beat) >= 7)
    return {
        "sweep_max": float(max_run),
        "short_sweep_hits": float(short_hits),
        "circle_hits": float(circle_hits),
        "sweep_mixed_hits": float(mixed_hits),
    }


def _same_direction_run(sequence: list[tuple[float, int, int]], start: int, beat: float) -> int:
    if start >= len(sequence):
        return 0
    direction = 0
    length = 1
    for index in range(start + 1, len(sequence)):
        previous, current = sequence[index - 1], sequence[index]
        step = _ring_direction(previous[1], current[1])
        if not (0 < current[0] - previous[0] <= beat * 0.75 and _ring_distance(previous[1], current[1]) == 1 and step):
            break
        if direction not in {0, step}:
            break
        direction = step
        length += 1
    return length


def _death_scythe_hits(chart: MaidataChart, sequence: list[tuple[float, int, int]], beat: float) -> int:
    hits = 0
    for slide in (event for event in chart.events if event.kind == "slide"):
        path = [_button(value) for value in (slide.path or slide.buttons)]
        path = [value for value in path if value is not None]
        if len(path) < 2:
            continue
        slide_direction = _ring_direction(path[0], path[1])
        start = float(slide.time)
        end = start + max(float(slide.duration), beat * 0.5) + beat * 0.25
        taps = [(time, number) for time, number, _ in sequence if start - 0.02 <= time <= end]
        run = 1
        best = 1
        best_direction = 0
        for index in range(1, len(taps)):
            dt = taps[index][0] - taps[index - 1][0]
            direction = _ring_direction(taps[index - 1][1], taps[index][1])
            if 0 < dt <= beat * 0.75 and _ring_distance(taps[index - 1][1], taps[index][1]) == 1 and direction:
                if best_direction in {0, direction}:
                    best_direction = direction
                    run += 1
                else:
                    best_direction = direction
                    run = 2
                best = max(best, run)
            else:
                run = 1
                best_direction = 0
        if best >= 3 and slide_direction and best_direction == -slide_direction:
            hits += 1
    return hits


def _rulong_features(chart: MaidataChart, sequence: list[tuple[float, int, int]], beat: float) -> tuple[int, int, int]:
    groups = _group_events(chart)
    tap_multi = [group for group in groups if group["multi"] and all(_is_tap(event) for event in group["events"])]
    double_hits = 0
    for group in tap_multi:
        lead = group["buttons"]
        following = [(time, number, count) for time, number, count in sequence if group["time"] < time <= group["time"] + beat * 2.2]
        for start in range(min(2, len(following))):
            if _same_direction_run(following, start, beat) < 4:
                continue
            if min((_ring_distance(number, following[start][1]) for number in lead), default=9) <= 2:
                double_hits += 1
                break
    half_hits = 0
    for index, (first, second) in enumerate(zip(sequence, sequence[1:])):
        dt = second[0] - first[0]
        if not (abs(dt - beat * 0.5) <= beat * 0.12 or abs(dt - beat) <= beat * 0.12):
            continue
        if first[1] == second[1] or _ring_distance(first[1], second[1]) > 2:
            continue
        if _same_direction_run(sequence, index + 1, beat) >= 4:
            half_hits += 1
    return double_hits + half_hits, double_hits, half_hits


def _hold_features(chart: MaidataChart, two_measure: float) -> dict[str, float]:
    holds = sorted((event for event in chart.events if event.kind == "hold"), key=lambda item: item.time)
    lengths = [float(event.duration) for event in holds if event.duration > 0]
    gaps: list[float] = []
    short_gap = 0
    chain_max = 1
    chain_span = 0.0
    if holds:
        chain = 1
        chain_start = holds[0].time
        chain_end = holds[0].time + max(holds[0].duration, 0.0)
        for previous, current in zip(holds, holds[1:]):
            gap = current.time - (previous.time + max(previous.duration, 0.0))
            gaps.append(gap)
            if gap <= min(0.18, two_measure / 16.0):
                short_gap += 1
                chain += 1
                chain_end = max(chain_end, current.time + max(current.duration, 0.0))
                chain_max = max(chain_max, chain)
                chain_span = max(chain_span, chain_end - chain_start)
            else:
                chain = 1
                chain_start = current.time
                chain_end = current.time + max(current.duration, 0.0)
        chain_span = max(chain_span, chain_end - chain_start)
    return {
        "hold_count": float(len(holds)),
        "short_hold_count": float(sum(0 < event.duration <= 0.4 for event in holds)),
        "hold_gap_min": float(min(gaps, default=9.0)),
        "hold_gap_short": float(short_gap),
        "hold_chain_max": float(chain_max),
        "hold_chain_span_max": float(chain_span),
        "hold_len_cv": float(_cv(lengths)),
        "hold_gap_cv": float(_cv([gap for gap in gaps if 0 <= gap < 2.0])),
        "hold_weird": float(
            len(holds) >= 8
            and chain_span >= two_measure * 0.75
            and (_cv(lengths) >= 0.75 or _cv([gap for gap in gaps if 0 <= gap < 2.0]) >= 0.85)
            and short_gap >= 3
        ),
    }


def _slide_features(chart: MaidataChart, beat: float) -> dict[str, float]:
    slides = [event for event in chart.events if event.kind == "slide"]
    spans: list[int] = []
    durations: list[float] = []
    complex_count = 0
    for slide in slides:
        path = [_button(value) for value in (slide.path or slide.buttons)]
        path = [value for value in path if value is not None]
        span = sum(_ring_distance(path[index - 1], path[index]) for index in range(1, len(path)))
        spans.append(span)
        durations.append(float(slide.duration))
        complex_count += int(len(path) >= 3 or len(str(slide.shape or "")) >= 2)
    large = [span for span in spans if span >= 3]
    fast_large = [duration / max(span, 1) for span, duration in zip(spans, durations) if span >= 3 and duration > 0]
    overlaps = 0
    overlap_peak = 0
    for event in sorted(slides, key=lambda item: item.time):
        active = sum(
            1 for other in slides
            if other.time <= event.time < other.time + max(other.duration, 0.0)
        )
        overlap_peak = max(overlap_peak, active)
        overlaps += int(active >= 2)
    return {
        "slide_count": float(len(slides)),
        "short_slides": float(sum(duration <= beat * 1.5 for duration in durations)),
        "large_span_slides": float(len(large)),
        "fast_large_span_slides": float(sum(value <= beat * 0.35 for value in fast_large)),
        "complex_slide_count": float(complex_count),
        "slide_overlap_count": float(overlaps),
        "slide_overlap_peak": float(overlap_peak),
        "slide_ratio": len(slides) / max(len(chart.events), 1),
        "short_slide_ratio": sum(duration <= beat * 1.5 for duration in durations) / max(len(slides), 1),
    }


def _position_features(chart: MaidataChart, groups: list[dict[str, Any]], two_measure: float) -> dict[str, float]:
    violations = [group for group in groups if group["zone_cross"]]
    times = [float(group["time"]) for group in groups]
    local_peak = 0
    for left, group in enumerate(groups):
        right = bisect_right(times, float(group["time"]) + two_measure - 1e-9)
        local_peak = max(local_peak, sum(item["zone_cross"] for item in groups[left:right]))
    return {
        "zone_violation_count": float(len(violations)),
        "zone_violation_peak": float(local_peak),
        "zone_violation_ratio": len(violations) / max(len(groups), 1),
    }


def _anchor_features(chart: MaidataChart, groups: list[dict[str, Any]], two_measure: float, beat: float) -> dict[str, float]:
    times = [float(group["time"]) for group in groups]
    anchor_hits = 0
    anchor_duration = 0.0
    anchor_peak_nps = 0.0
    for left, group in enumerate(groups):
        right = bisect_right(times, times[left] + two_measure - 1e-9)
        taps = [event for item in groups[left:right] for event in item["events"] if _is_tap(event)]
        by_button: dict[int, list[float]] = defaultdict(list)
        for event in taps:
            for number in _event_buttons(event):
                by_button[number].append(float(event.time))
        for button, button_times in by_button.items():
            if len(button_times) < 6:
                continue
            intervals = [right - left for left, right in zip(button_times, button_times[1:]) if right > left]
            if len(intervals) < 5 or _cv(intervals) > 0.16:
                continue
            other = sum(
                1 for item in groups[left:right]
                for event in item["events"]
                if not (_is_tap(event) and button in _event_buttons(event))
            )
            if other < 3:
                continue
            anchor_hits += 1
            anchor_duration = max(anchor_duration, button_times[-1] - button_times[0])
            anchor_peak_nps = max(anchor_peak_nps, len(taps) / max(two_measure, 1e-6))
    return {
        "anchor_hits": float(anchor_hits),
        "anchor_duration": float(anchor_duration),
        "anchor_peak_nps": float(anchor_peak_nps),
    }


def _misalignment_features(chart: MaidataChart, groups: list[dict[str, Any]], beat: float) -> dict[str, float]:
    if chart.level_index != 3:  # XLS defines this candidate on Master charts.
        return {"misalignment_hits": 0.0, "misalignment_position_changes": 0.0}
    double_times = [group for group in groups if group["multi"] and all(_is_tap(event) for event in group["events"])]
    hits = 0
    positions: set[tuple[int, ...]] = set()
    for slide in (event for event in chart.events if event.kind == "slide"):
        lead = min(double_times, key=lambda group: abs(group["time"] - (slide.time - beat)), default=None)
        if lead is None or abs(lead["time"] - (slide.time - beat)) > beat * 0.12:
            continue
        hits += 1
        positions.add(tuple(lead["buttons"]))
    return {"misalignment_hits": float(hits), "misalignment_position_changes": float(len(positions))}


def _interaction_features(sequence: list[tuple[float, int, int]], beat: float, two_measure: float) -> dict[str, float]:
    axis_hits = 0
    axis_duration = 0.0
    ladder_hits = 0
    ladder_nps = 0.0
    ordinary_hits = 0
    ordinary_duration = 0.0
    ordinary_non_tap = 0
    coord_disp = 0
    stack = 0
    stack_duration = 0.0
    short_stack_points: list[tuple[float, int]] = []

    for start in range(max(0, len(sequence) - 7)):
        sample = sequence[start:start + 8]
        if len(sample) < 8:
            continue
        span = sample[-1][0] - sample[0][0]
        if span <= 0 or span > beat * 2.0:
            continue
        values = [item[1] for item in sample]
        even = values[0::2]
        odd = values[1::2]
        if len(set(even)) == 1 and len(set(odd)) >= 3 and even[0] not in set(odd):
            axis_hits += 1
            axis_duration = max(axis_duration, span)

        templates: list[list[int]] = []
        for mirror in (-1, 1):
            expected = [values[0]]
            for index in range(1, 8):
                step = (index + 1) // 2
                sign = -1 if index % 2 else 1
                expected.append(((values[0] - 1 + mirror * sign * step) % 8) + 1)
            templates.extend((expected, list(reversed(expected))))
        if any(values == template for template in templates):
            ladder_hits += 1
            ladder_nps = max(ladder_nps, 8.0 / max(span, 1e-6))

        distances = [_ring_distance(values[index - 1], values[index]) for index in range(1, 8)]
        intervals = [sample[index][0] - sample[index - 1][0] for index in range(1, 8)]
        if all(0 < interval <= beat * 0.65 for interval in intervals) and sum(distance == 1 for distance in distances) >= 5:
            ordinary_hits += 1
            ordinary_duration = max(ordinary_duration, span)
            ordinary_non_tap += sum(1 for _, _, count in sample if count > 1)

        # A repeated large-displacement alternating shape is an XLS 协调
        # example, rather than a generic jump.
        if values[0] == values[2] and values[1] == values[3] and values[4] == values[6] and values[5] == values[7]:
            if _ring_distance(values[0], values[1]) >= 2 or _ring_distance(values[2], values[4]) >= 3:
                coord_disp += 1

    for left, right in zip(sequence, sequence[1:]):
        dt = right[0] - left[0]
        if left[1] == right[1] and 0 < dt <= beat * 0.6:
            stack += 1
            stack_duration = max(stack_duration, dt)
            short_stack_points.append((right[0], right[1]))

    short_stack_hot = 0
    short_stack_distinct_hot = 0
    for start, _ in short_stack_points:
        selected = [item for item in short_stack_points if start <= item[0] < start + two_measure]
        short_stack_hot = max(short_stack_hot, len(selected))
        short_stack_distinct_hot = max(short_stack_distinct_hot, len({item[1] for item in selected}))
    return {
        "axis_hits": float(axis_hits),
        "axis_duration": float(axis_duration),
        "ladder_hits": float(ladder_hits),
        "ladder_nps": float(ladder_nps),
        "ordinary_interaction_hits": float(ordinary_hits),
        "ordinary_interaction_duration": float(ordinary_duration),
        "ordinary_interaction_non_tap": float(ordinary_non_tap),
        "coord_disp": float(coord_disp),
        "stack": float(stack),
        "stack_ratio": float(stack / max(len(sequence), 1)),
        "stack_duration": float(stack_duration),
        "short_stack_hot": float(short_stack_hot),
        "short_stack_distinct_hot": float(short_stack_distinct_hot),
    }


def _rhythm_features(
    chart: MaidataChart,
    beat: float,
    sequence: list[tuple[float, int, int]] | None = None,
) -> dict[str, float]:
    points = [(time, number) for time, number, _ in (sequence if sequence is not None else _tap_sequence(chart))]
    if len(points) < 10:
        return {"swing_runs": 0.0, "dotted_runs": 0.0, "ioi_cv": 0.0, "ioi_irregular": 0.0}
    intervals = [right[0] - left[0] for left, right in zip(points, points[1:]) if right[0] > left[0]]
    median_interval = statistics.median(intervals)
    swing = dotted = 0
    for start in range(len(intervals) - 7):
        sample = intervals[start:start + 8]
        swing_pairs = dotted_pairs = 0
        for index in range(0, 8, 2):
            left, right = sample[index], sample[index + 1]
            ratio = max(left, right) / max(min(left, right), 1e-9)
            total = left + right
            if 1.6 <= ratio <= 2.6 and 0.72 * beat <= total <= 1.28 * beat:
                swing_pairs += 1
            if 1.7 <= ratio <= 3.4 and 0.42 * beat <= total <= 0.72 * beat:
                dotted_pairs += 1
        swing += int(swing_pairs >= 3)
        dotted += int(dotted_pairs >= 3)
    return {
        "swing_runs": float(swing),
        "dotted_runs": float(dotted),
        "ioi_cv": float(_cv(intervals)),
        "ioi_irregular": float(sum(abs(value - median_interval) > median_interval * 0.28 for value in intervals) / len(intervals)),
    }


def _extract_feature_bundle(
    chart: MaidataChart,
    *,
    include_evidence: bool = False,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    events = sorted([event for event in chart.events if event.kind], key=lambda item: item.time)
    if not events:
        return {"empty": 1.0}, []
    bpm = float(chart.bpm or events[0].bpm or 120.0)
    beat = 60.0 / max(bpm, 1.0)
    two_measure = 2.0 * _measure_seconds(bpm)
    groups = _group_events(chart)
    windows, window_summary = _window_stats(chart, include_evidence=include_evidence, groups=groups)
    sequence = _tap_sequence(chart, groups=groups)
    sweep = _sweep_features(sequence, beat)
    holds = _hold_features(chart, two_measure)
    slides = _slide_features(chart, beat)
    position = _position_features(chart, groups, two_measure)
    anchor = _anchor_features(chart, groups, two_measure, beat)
    misalignment = _misalignment_features(chart, groups, beat)
    rhythm = _rhythm_features(chart, beat, sequence)
    interaction = _interaction_features(sequence, beat, two_measure)
    death = _death_scythe_hits(chart, sequence, beat)
    rulong, rulong_double, rulong_half = _rulong_features(chart, sequence, beat)
    densities = []
    group_times = [float(item["time"]) for item in groups]
    for left, group in enumerate(groups):
        right = bisect_right(group_times, float(group["time"]) + 1.0 - 1e-9)
        densities.append(sum(len(item["events"]) for item in groups[left:right]))
    median_density = statistics.median(densities) if densities else 0.0
    peak_density = max(densities, default=0.0)
    total = len(events)
    taps = sum(_is_tap(event) for event in events)
    hold_count = sum(event.kind == "hold" for event in events)
    duration = max(events[-1].time - events[0].time, 0.01)
    key_counts = [number for event in events for number in _event_buttons(event)]
    entropy = 0.0
    if key_counts:
        counts = [key_counts.count(number) / len(key_counts) for number in range(1, 9)]
        entropy = -sum(value * math.log(value, 2) for value in counts if value > 0) / 3.0
    stack = 0
    for first, second in zip(sequence, sequence[1:]):
        if first[1] == second[1] and 0 < second[0] - first[0] <= beat * 0.6:
            stack += 1
    jump = sum(
        _ring_distance(left[1], right[1]) >= 3 and 0 < right[0] - left[0] <= beat * 0.8
        for left, right in zip(sequence, sequence[1:])
    )
    return {
        "bpm": bpm,
        "ds": float(chart.ds or 0.0),
        "duration": duration,
        "total": float(total),
        "nps": total / duration,
        "tap_ratio": taps / max(total, 1),
        "hold_ratio": hold_count / max(total, 1),
        "slide_ratio": slides["slide_ratio"],
        "touch_ratio": sum(event.kind == "touch" for event in events) / max(total, 1),
        "key_entropy": entropy,
        "mean_density": sum(densities) / max(len(densities), 1),
        "median_density": median_density,
        "peak_density": peak_density,
        "burst_ratio": peak_density / max(median_density, 1.0),
        "multi_ratio": sum(group["multi"] for group in groups) / max(len(groups), 1),
        "stack": float(stack),
        "jump": float(jump),
        "two_measure_sec": two_measure,
        "window_count": window_summary["window_count"],
        "dual_window_count": window_summary["dual_window_count"],
        "dual_dense_run": window_summary["dual_dense_run"],
        "dual_dense_peak": window_summary["dual_dense_peak"],
        "hold_window_count": window_summary["hold_window_count"],
        "hold_dense_run": window_summary["hold_dense_run"],
        "hold_dense_peak": window_summary["hold_dense_peak"],
        "window_density_peak": window_summary["window_density_peak"],
        "window_zone_cross_peak": window_summary["window_zone_cross_peak"],
        **holds,
        **slides,
        **position,
        **anchor,
        **misalignment,
        **rhythm,
        **sweep,
        **interaction,
        "death_scythe_hits": float(death),
        "rulong_hits": float(rulong),
        "rulong_double_hits": float(rulong_double),
        "rulong_half_hits": float(rulong_half),
        "slide_count": slides["slide_count"],
        "tap_count": float(taps),
        "touch_count": float(sum(event.kind == "touch" for event in events)),
        "pure_tap_n": float(len(sequence)),
        "local_window_count": float(len(windows)),
    }, windows


def extract_features(chart: MaidataChart) -> dict[str, float]:
    features, _windows = _extract_feature_bundle(chart, include_evidence=False)
    return features


def extract_features_with_windows(chart: MaidataChart) -> tuple[dict[str, float], list[dict[str, Any]]]:
    return _extract_feature_bundle(chart, include_evidence=True)


def _span_evidence(windows: list[dict[str, Any]], predicate: Any, limit: int = 3) -> list[dict[str, Any]]:
    selected = [window for window in windows if predicate(window)]
    if not selected:
        selected = windows[:1]
    return selected[:limit]


def _add_score(scores: dict[str, float], tag: str, strength: float) -> None:
    if strength > 0:
        scores[tag] = max(scores.get(tag, 0.0), tag_weight(tag) * min(1.45, strength))


def analyze_chart_tags(chart: MaidataChart) -> dict[str, Any]:
    features, windows = extract_features_with_windows(chart)
    ds = float(chart.ds or 0.0)
    bpm = float(chart.bpm or 120.0)
    beat = 60.0 / max(bpm, 1.0)
    raw: list[str] = []
    difficult: list[str] = []
    scores: dict[str, float] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}

    def add(
        tag: str,
        score: float,
        *,
        hard: bool = False,
        candidate_tag: str | None = None,
        spans: list[dict[str, Any]] | None = None,
        reason: str = "",
    ) -> None:
        candidate = candidate_tag or tag
        if candidate not in raw:
            raw.append(candidate)
        _add_score(scores, candidate, score)
        if candidate != tag:
            _add_score(scores, tag, score)
        if hard and tag not in difficult:
            difficult.append(tag)
        if spans:
            evidence[tag] = [
                {
                    "kind": "two_measure_window",
                    "event_indexes": item.get("event_indexes", []),
                    "raw": item.get("sequence", ""),
                    "position": {"start": item.get("start"), "end": item.get("end"), "bpm": item.get("bpm")},
                    "reason": reason,
                }
                for item in spans[:3]
            ]

    dual = _span_evidence(windows, lambda item: item["multi_ratio"] >= 0.75 and item["onset_count"] >= 8)
    hold = _span_evidence(windows, lambda item: item["hold_ratio"] >= 0.50 and item["onset_count"] >= 16)
    strict_hold = _span_evidence(windows, lambda item: item["hold_ratio"] >= 0.65 and item["onset_count"] >= 16)
    if features["dual_window_count"] and dual:
        add("双押", 0.75 + features["dual_dense_peak"] * 0.5, hard=features["dual_dense_peak"] >= 0.82 or features["window_density_peak"] >= 14, spans=dual, reason="连续两小节内同时击组占比达到候选阈值")
    if features["hold_window_count"] and hold:
        hold_strict = bool(strict_hold and (features["hold_gap_min"] <= min(0.12, beat * 0.22) or features["hold_weird"] or features["hold_chain_max"] >= 7))
        add("管子", 0.7 + features["hold_dense_peak"] * 0.5, hard=hold_strict, spans=hold, reason="连续两小节 Hold 密度或 Hold 链结构达到阈值")

    # Timing and generalized physical skills.
    if features["swing_runs"] or features["dotted_runs"]:
        rhythm_windows = _span_evidence(windows, lambda item: True)
        add("跳拍", 0.65 + min(0.6, features["swing_runs"] * 0.08 + features["dotted_runs"] * 0.08), hard=features["swing_runs"] >= 2 or features["dotted_runs"] >= 3, spans=rhythm_windows, reason="局部 Swing、Shuffle 或连续附点节奏成立")
        add("节奏", 0.55 + min(0.6, features["ioi_cv"]), hard=features["ioi_irregular"] >= 0.45, spans=rhythm_windows, reason="局部节奏间隔发生成组变化")
    if features["ioi_cv"] <= 0.18 and features["anchor_hits"]:
        spans = _span_evidence(windows, lambda item: item["onset_count"] >= 6)
        add("定拍", 0.65 + min(0.55, features["anchor_duration"] / max(features["two_measure_sec"], 0.1)), hard=features["anchor_duration"] >= features["two_measure_sec"] * 2 or features["anchor_peak_nps"] >= 8, spans=spans, reason="一只手锚定稳定拍点，另一只手同时处理其他配置")

    # The XLS deletes 背谱 and 一笔划 as model labels.  Their former signals
    # are intentionally not converted to any new label.
    if features["nps"] >= 7.5 or bpm >= 200 or features["peak_density"] >= 14:
        speed_score = 0.55 + min(0.85, max(0.0, features["nps"] - 7.5) / 8.0 + max(0.0, bpm - 200) / 400.0)
        add("手速", speed_score, hard=features["nps"] >= 10.5 or features["peak_density"] >= 18, spans=windows[:2], reason="单位时间处理速度达到候选阈值")
    if features["total"] >= 700 and features["mean_density"] >= 6.5 and features["duration"] >= 70:
        add("底力", 0.7 + min(0.55, features["total"] / 1500.0), hard=features["total"] >= 1000 and features["mean_density"] >= 8, spans=windows[:2], reason="长时间保持较高物量和平均密度")
    if features["burst_ratio"] >= 2.4 and features["peak_density"] >= 14 and features["peak_density"] - features["median_density"] >= 6:
        add("爆发", 0.7 + min(0.6, (features["burst_ratio"] - 2.2) / 2.5), hard=features["burst_ratio"] >= 3.2 and features["peak_density"] >= 18, spans=windows[:2], reason="局部峰值密度显著高于中位密度")
    if features["tap_ratio"] >= 0.68 and features["slide_ratio"] <= 0.09 and features["key_entropy"] >= 0.8 and features["nps"] >= 5:
        add("散打", 0.55 + min(0.75, features["key_entropy"]), hard=features["burst_ratio"] >= 2.4 or features["nps"] >= 8.5, spans=windows[:2], reason="Tap 分散、键位熵高且缺少固定手型")
    if features["jump"] / max(features["total"], 1.0) >= 0.10 and features["jump"] >= 30:
        add("飞手", 0.55 + min(0.75, (features["jump"] / max(features["total"], 1.0) - 0.1) * 6), hard=features["jump"] >= 80 or features["window_zone_cross_peak"] >= 3, spans=windows[:2], reason="局部大跳键比例和数量达到阈值")

    # Hand assignment violations are merged into 协调 per the XLS.
    if features["zone_violation_count"] >= 6 or features["zone_violation_peak"] >= 4:
        hand_score = 0.65 + min(0.75, features["zone_violation_count"] / 18.0 + features["zone_violation_ratio"])
        add("协调", hand_score, hard=features["zone_violation_count"] >= 12 or features["zone_violation_peak"] >= 7, spans=windows[:2], reason="左右手默认分区被大量跨越，合并为协调/手序难点")

    # Interaction family.
    if features["axis_hits"] >= 3:
        add("轴交互", 0.65 + min(0.7, features["axis_hits"] / 10.0), hard=features["axis_hits"] >= 6 or features["axis_duration"] >= features["two_measure_sec"] * 1.5, spans=windows[:2], reason="交替组中固定轴键至少重复三次")
    if features["ladder_hits"] >= 1:
        add("爬梯交互", 0.65 + min(0.7, features["ladder_hits"] / 6.0), hard=features["ladder_hits"] >= 3 or features["ladder_nps"] >= 8, spans=windows[:2], reason="键位按连续方向扩展或收缩形成完整爬梯")
    if features["ordinary_interaction_hits"] >= 3:
        ordinary_score = 0.55 + min(0.75, features["ordinary_interaction_hits"] / 20.0)
        add("交互", ordinary_score, hard=features["ordinary_interaction_duration"] >= features["two_measure_sec"] * 2 or features["ordinary_interaction_non_tap"] >= 2, spans=windows[:2], reason="快速交替成立且没有被轴/爬梯/协调完全解释")
    if features["short_stack_hot"] >= 4 or features["coord_disp"] >= 1:
        add("协调", 0.7 + min(0.65, features["short_stack_hot"] / 16.0 + features["coord_disp"] / 8.0), hard=features["short_stack_hot"] >= 8 or features["coord_disp"] >= 3, spans=windows[:2], reason="短纵、大位移交互或难协调键型重复出现")
    if features["stack_ratio"] >= 0.06 and features["stack"] >= 18:
        add("纵连", 0.55 + min(0.75, features["stack_ratio"] * 6), hard=features["stack_duration"] >= features["two_measure_sec"] * 1.5 or features["bpm"] >= 190, spans=windows[:2], reason="连续纵向重复击打达到局部阈值")

    # Sweep family and named patterns.
    if features["sweep_max"] >= 3 or features["short_sweep_hits"] >= 1:
        add("扫键", 0.55 + min(0.8, features["sweep_max"] / 12.0 + features["short_sweep_hits"] / 8.0), hard=features["circle_hits"] >= 1 or features["sweep_mixed_hits"] >= 3, spans=windows[:2], reason="同侧扫、短扫或转圈序列达到候选阈值")
    if features["death_scythe_hits"] >= 1:
        add("死镰", 0.75 + min(0.65, features["death_scythe_hits"] / 6.0), hard=features["death_scythe_hits"] >= 2, spans=windows[:2], reason="方向相反的 Slide 与连续 Tap 链同时成立")
    if features["rulong_hits"] >= 1:
        add("如龙", 0.75 + min(0.65, features["rulong_hits"] / 6.0), hard=features["rulong_hits"] >= 2, spans=windows[:2], reason="双押/半拍引导后形成同侧连续扫与换手")
    if features["misalignment_hits"] >= 1:
        add("错位", 0.65 + min(0.65, features["misalignment_hits"] / 5.0), hard=features["misalignment_hits"] >= 2 or features["misalignment_position_changes"] >= 2, spans=windows[:2], reason="Master 中双押引导与隔拍 Slide 启动形成错位")

    if features["short_slides"] >= 12 and features["short_slide_ratio"] >= 0.35 and (bpm >= 180 or features["complex_slide_count"] >= 4):
        add("留尾", 0.75 + min(0.65, features["short_slide_ratio"] + features["fast_large_span_slides"] / 12.0), hard=features["fast_large_span_slides"] >= 4 or features["complex_slide_count"] >= 8, spans=windows[:2], reason="高BPM秒划或复杂 Slide 出张达到留尾难点条件")
    if features["short_slide_ratio"] >= 0.35 and features["short_slides"] >= 12 and features["slide_ratio"] >= 0.10:
        add("防蹭", 0.55 + min(0.75, features["short_slide_ratio"]), hard=features["short_slides"] >= 28, spans=windows[:2], reason="大量短星带来跳区与邻近判定区误触风险")
    if features["slide_overlap_peak"] >= 2 and bpm <= 160:
        delayed_hard = features["slide_overlap_peak"] >= 3 or features["slide_overlap_count"] >= 8
        add("延迟星星", 0.55 + min(0.65, features["slide_overlap_peak"] / 4.0), spans=windows[:2], reason="低 BPM 下同时存在多条 Slide，形成视觉时序延后候选")
        add(
            "拆弹",
            0.72 + min(0.6, features["slide_overlap_peak"] / 4.0),
            hard=delayed_hard,
            candidate_tag="延迟星星",
            spans=windows[:2],
            reason="延迟星星候选达到同时多 Slide 的难点条件",
        )

    # The XLS removes the old separate 秒划/一笔划/手序/背谱 labels.
    display_candidates = list(raw)
    # 延迟星星 is the candidate name; only a difficult candidate is exposed as
    # 拆弹, so the two names never appear together in the display layer.
    if "拆弹" in difficult:
        display_candidates = [tag for tag in display_candidates if tag != "延迟星星"]
        display_candidates.append("拆弹")
    return {
        "tags": filter_allowed_tags(display_candidates),
        "raw_tags": filter_allowed_tags(raw),
        "difficulty_tags": filter_allowed_tags(difficult),
        "tag_scores": {tag: round(scores.get(tag, tag_weight(tag)), 6) for tag in filter_allowed_tags(display_candidates)},
        "candidate_scores": {tag: round(value, 6) for tag, value in scores.items()},
        "features": features,
        "windows": windows[:20],
        "tag_evidence": evidence,
        "confidence": round(min(1.0, max(0.0, sum(scores.values()) / max(len(scores), 1))), 4),
        "source": "local_xls_rule_engine",
        "rule_version": 15,
        "difficulty_caps": DIFFICULTY_CAPS,
    }


def analyze_maidata_text(text: str, min_ds: float = 12.6, max_ds: float = 15.0) -> dict[str, Any]:
    """Analyze all supported chart sections in one maidata document."""
    from .maidata_parser import parse_maidata

    song = parse_maidata(text)
    charts_out: dict[str, Any] = {}
    for level_index, chart in song.charts.items():
        if min_ds <= float(chart.ds or 0.0) <= max_ds:
            charts_out[str(level_index)] = analyze_chart_tags(chart)
    return {
        "title": song.title,
        "artist": song.artist,
        "short_id": song.short_id,
        "whole_bpm": song.whole_bpm,
        "charts": charts_out,
    }


def analyze_maidata_file(path: str | Path, min_ds: float = 12.6, max_ds: float = 15.0) -> dict[str, Any]:
    return analyze_maidata_text(Path(path).read_text(encoding="utf-8-sig"), min_ds=min_ds, max_ds=max_ds)


__all__ = [
    "analyze_chart_tags",
    "analyze_maidata_file",
    "analyze_maidata_text",
    "extract_features",
    "extract_features_with_windows",
]
