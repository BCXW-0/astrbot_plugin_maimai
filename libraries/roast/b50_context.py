from __future__ import annotations

from typing import Any

from .common import f, i
from ..chart_tags.lookup import format_chart_tags
from .rating_band import rating_band_hint, rating_band_label
from .value_level import (
    average_value_level_text,
    chart_author_info,
    chart_ds,
    chart_value_score,
    evaluate_chart_value,
)


def counter_lines(charts: list[Any], label: str, getter) -> list[str]:
    counts: dict[str, int] = {}
    for chart in charts:
        value = getter(chart)
        if not value or str(value).startswith("未知"):
            continue
        counts[value] = counts.get(value, 0) + 1
    items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
    if not items:
        return [f"{label}：暂无"]
    return [f"{label}：" + "、".join(f"{name}×{count}" for name, count in items)]


def chart_bucket(chart_type: str) -> str:
    return "B15" if str(chart_type).lower() == "dx" else "B35"


def _pair_list(sd: list[Any], dx: list[Any]) -> list[tuple[Any, str]]:
    return [(c, "B35") for c in sd] + [(c, "B15") for c in dx]


def chart_line(
    chart: Any,
    bucket: str | None = None,
    *,
    is_floor: bool = False,
    floor_ra: int = 0,
    compact: bool = True,
) -> str:
    bucket = bucket or chart_bucket(chart.type)
    info = evaluate_chart_value(chart, bucket, is_floor=is_floor, floor_ra=floor_ra)
    fc = f" {chart.fc.upper()}" if chart.fc else ""
    fs = f" {chart.fs.upper()}" if chart.fs else ""
    tags = format_chart_tags(chart.song_id, chart.level_index)
    roles = "/".join(info["roles"]) if info["roles"] else ""
    role_text = f" [{roles}]" if roles else ""
    if compact:
        # 精简：不写曲师/谱师全文（汇总区另有 TOP），标签仅辅助
        tag_text = tags if tags else ""
        return (
            f"[{bucket} {info['ds']:.1f}] {chart.title} "
            f"{chart.achievements:.4f}% RA{chart.ra} {chart.rate.upper()}{fc}{fs} "
            f"{info['label']}{role_text}{tag_text}"
        )
    artist, charter = info["artist"], info["charter"]
    return (
        f"[{bucket} {chart.type} {info['ds']}] {chart.title} "
        f"{chart.achievements:.4f}% RA {chart.ra} {chart.rate.upper()}{fc}{fs} "
        f"{info['label']}{role_text}{tags} 曲师:{artist} 谱师:{charter}"
    )


def _rate_counts(charts: list[Any]) -> tuple[int, int, int, int]:
    bird = bird_plus = sssp = sss = 0
    for c in charts:
        ach = f(c.achievements)
        if ach >= 100.5:
            bird_plus += 1
            bird += 1
        elif ach >= 100.0:
            bird += 1
        if str(getattr(c, "rate", "") or "").lower() in {"sssp"}:
            sssp += 1
        if ach >= 99.5:
            sss += 1
    return bird, bird_plus, sssp, sss


def _potential_cards(pairs: list[tuple[Any, str]], limit: int = 5) -> list[tuple[Any, str]]:
    """未满鸟但仍吃进 B50：潜力/债候选。"""
    items = [(c, b) for c, b in pairs if f(c.achievements) < 100.0]
    items.sort(key=lambda item: (chart_ds(item[0]), i(item[0].ra)), reverse=True)
    return items[:limit]


def build_analysis_context(userinfo: Any, qqid: str) -> str:
    """压缩证据包：规则写在 SYSTEM，这里只给结构化事实。"""
    sd = list((userinfo.charts.sd or [])[:35])
    dx = list((userinfo.charts.dx or [])[:15])
    charts = sd + dx
    pairs = _pair_list(sd, dx)

    rating = i(userinfo.rating)
    b35_ra = sum(i(c.ra) for c in sd)
    b15_ra = sum(i(c.ra) for c in dx)
    b35_floor = min((i(c.ra) for c in sd if i(c.ra) > 0), default=0)
    b15_floor = min((i(c.ra) for c in dx if i(c.ra) > 0), default=0)
    avg_ach = sum(f(c.achievements) for c in charts) / len(charts) if charts else 0.0
    avg_ds = sum(chart_ds(c) for c in charts) / len(charts) if charts else 0.0

    scored: list[tuple[Any, str, float]] = []
    for c, bucket in pairs:
        is_floor = (bucket == "B35" and i(c.ra) == b35_floor) or (bucket == "B15" and i(c.ra) == b15_floor)
        score = chart_value_score(c, bucket, is_floor=is_floor)
        if score is not None:
            scored.append((c, bucket, score))

    avg_score = sum(s for *_, s in scored) / len(scored) if scored else 0.0
    bird, bird_plus, sssp, sss = _rate_counts(charts)

    top_by_ra = sorted(pairs, key=lambda item: i(item[0].ra), reverse=True)[:5]
    floor_cards = sorted(pairs, key=lambda item: i(item[0].ra))[:5]
    high_value = sorted(scored, key=lambda item: item[2], reverse=True)[:5]
    low_value = sorted(scored, key=lambda item: item[2])[:5]
    potential = _potential_cards(pairs, 5)

    def _floor_flag(c: Any, bucket: str) -> bool:
        if bucket == "B35":
            return b35_floor > 0 and i(c.ra) == b35_floor
        return b15_floor > 0 and i(c.ra) == b15_floor

    def _lines(items: list[tuple[Any, str]], floor_ra_for: str | None = None) -> list[str]:
        out: list[str] = []
        for c, bucket in items:
            fr = b35_floor if bucket == "B35" else b15_floor
            out.append(
                chart_line(
                    c,
                    bucket,
                    is_floor=_floor_flag(c, bucket),
                    floor_ra=fr,
                    compact=True,
                )
            )
        return out or ["（无）"]

    high_lines = [
        chart_line(c, b, is_floor=_floor_flag(c, b), floor_ra=(b35_floor if b == "B35" else b15_floor))
        for c, b, _ in high_value
    ] or ["（无可靠拟合数据）"]
    low_lines = [
        chart_line(c, b, is_floor=_floor_flag(c, b), floor_ra=(b35_floor if b == "B35" else b15_floor))
        for c, b, _ in low_value
    ] or ["（无可靠拟合数据）"]

    nickname = userinfo.nickname or userinfo.username or qqid
    lines = [
        "【B50快照】",
        f"玩家={nickname} Rating={rating} 段位={rating_band_label(rating)}",
        f"B35_RA={b35_ra} B15_RA={b15_ra} B35地板={b35_floor} B15地板={b15_floor}",
        (
            f"均达成={avg_ach:.2f}% 均定数={avg_ds:.2f} "
            f"鸟={bird}/50 鸟加={bird_plus} SSS+={sssp} SSS及以上={sss} "
            f"整体含金量={average_value_level_text(avg_score)}"
        ),
        f"段位提示：{rating_band_hint(rating)}",
        "",
        "【高含金量TOP】",
        *high_lines,
        "",
        "【水分/低含金量TOP】",
        *low_lines,
        "",
        "【地板】",
        *_lines(floor_cards),
        "",
        "【RA顶】",
        *_lines(top_by_ra),
        "",
        "【潜力(未满鸟吃分)】",
        *_lines(potential),
        "",
        *counter_lines(charts, "曲师TOP", lambda c: chart_author_info(c)[0]),
        *counter_lines(charts, "谱师TOP", lambda c: chart_author_info(c)[1]),
        "",
        "【字段说明】标签=含金量标签+角色(水谱/难谱/鸟/地板/未满鸟吃分等)；谱面特征标签仅辅助偏科判断。",
    ]
    return "\n".join(lines)
