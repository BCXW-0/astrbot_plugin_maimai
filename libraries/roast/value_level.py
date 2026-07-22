from __future__ import annotations

"""B50 含金量：水鱼 fit_diff 多因子模型。

水鱼 chart_stats.fit_diff 由全站成绩分布拟合得到“实际难度”：
  hardness = fit_diff - 官定定数 ds
  >0 更难（难谱/真难）→ 同 RA 更值钱
  <0 更易（水谱）    → 同 RA 更掺水

单曲得分再叠加达成质量、B15 新谱宽容、低样本降权、地板水谱惩罚。
对外文案只用标签，不把 fit 数字甩给用户。
"""

from typing import Any

from ...libraries.maimaidx_music import mai
from .common import f

# 低样本 fit 不可信：按比例向 0 收缩
MIN_FIT_CNT = 30.0
# B15 新谱略宽容（版本刚开时 fit 与玩家练习时间偏保守）
B15_BUCKET_ADJ = 0.08


def chart_music(chart: Any) -> Any:
    return mai.total_list.by_id(str(chart.song_id)) if hasattr(mai, "total_list") else None


def chart_ds(chart: Any) -> float:
    music = chart_music(chart)
    if music and len(music.ds) > chart.level_index:
        return f(music.ds[chart.level_index])
    return f(chart.ds)


def chart_stats(chart: Any) -> Any | None:
    music = chart_music(chart)
    if not music or not music.stats or len(music.stats) <= chart.level_index:
        return None
    stats = music.stats[chart.level_index]
    return stats or None


def chart_fit_diff_or_none(chart: Any) -> float | None:
    stats = chart_stats(chart)
    if not stats or stats.fit_diff is None:
        return None
    value = f(stats.fit_diff)
    # 0.0 几乎不可能是真实拟合，视为缺失
    if value == 0.0:
        return None
    return value


def chart_fit_diff(chart: Any) -> float:
    """兼容旧接口：未知时返回 0.0。"""
    value = chart_fit_diff_or_none(chart)
    return value if value is not None else 0.0


def chart_fit_cnt(chart: Any) -> float:
    stats = chart_stats(chart)
    if not stats or stats.cnt is None:
        return 0.0
    return max(0.0, f(stats.cnt))


def chart_hardness_delta(chart: Any) -> float | None:
    """纯谱面难度溢价：fit_diff - ds；未知返回 None。"""
    fit = chart_fit_diff_or_none(chart)
    if fit is None:
        return None
    return fit - chart_ds(chart)


def chart_value_delta(chart: Any) -> float:
    """兼容旧接口：未知记 0.0（排序时需配合 has_fit 过滤）。"""
    hardness = chart_hardness_delta(chart)
    return hardness if hardness is not None else 0.0


def chart_author_info(chart: Any) -> tuple[str, str]:
    music = chart_music(chart)
    if not music:
        return "未知曲师", "未知谱师"
    artist = getattr(getattr(music, "basic_info", None), "artist", None) or "未知曲师"
    charter = "未知谱师"
    charts = getattr(music, "charts", None) or []
    if len(charts) > chart.level_index and getattr(charts[chart.level_index], "charter", None):
        charter = charts[chart.level_index].charter
    return str(artist), str(charter)


def chart_bucket(chart_type: str) -> str:
    return "B15" if str(chart_type).lower() == "dx" else "B35"


def _confidence_scale(cnt: float) -> float:
    if cnt <= 0:
        return 0.0
    if cnt >= MIN_FIT_CNT:
        return 1.0
    return cnt / MIN_FIT_CNT


def _achievement_mod(achievements: float, fc: str = "") -> float:
    """达成与 FC/AP 对“这局分含金量”的微调。"""
    ach = f(achievements)
    if ach >= 100.5:
        mod = 0.07  # 鸟加
    elif ach >= 100.0:
        mod = 0.04  # 鸟
    elif ach >= 99.99:
        mod = 0.02  # 贴鸟
    elif ach >= 99.5:
        mod = 0.0  # SSS
    elif ach >= 99.0:
        mod = -0.04  # SS+ 未吃满
    elif ach >= 98.0:
        mod = -0.08
    else:
        mod = -0.12

    fc_key = str(fc or "").lower()
    if fc_key in {"app", "ap"}:
        mod += 0.03
    elif fc_key in {"fcp", "fc"}:
        mod += 0.01
    return mod


def chart_value_score(
    chart: Any,
    bucket: str | None = None,
    *,
    is_floor: bool = False,
) -> float | None:
    """
    单曲含金量综合分（越高越硬核）。
    None = 无可靠 fit，不宜下结论。
    """
    hardness = chart_hardness_delta(chart)
    if hardness is None:
        return None

    scale = _confidence_scale(chart_fit_cnt(chart))
    if scale <= 0:
        return None

    hardness *= scale
    bucket = bucket or chart_bucket(getattr(chart, "type", ""))
    bucket_adj = B15_BUCKET_ADJ if bucket == "B15" else 0.0
    ach_mod = _achievement_mod(getattr(chart, "achievements", 0), getattr(chart, "fc", "") or "")

    floor_mod = 0.0
    # 地板位还靠水谱撑着：多扣一点“含水量”
    if is_floor and hardness < -0.15:
        floor_mod = -0.08

    return hardness + ach_mod + bucket_adj + floor_mod


def value_level_text_from_score(score: float | None) -> str:
    if score is None:
        return "含金量未知"
    # 阈值对照全站 hardness 分布（约 p90≈0.36 / p10≈-0.32）并含达成微调
    if score >= 0.28:
        return "含金量特别高"
    if score >= 0.08:
        return "含金量偏高"
    if score >= -0.12:
        return "含金量正常"
    if score >= -0.35:
        return "含水量较高"
    return "含水量很高"


def value_level_text(value_delta: float, bucket: str, chart: Any | None = None) -> str:
    """
    兼容旧签名 value_level_text(delta, bucket)。
    若传入 chart，优先用多因子 score。
    """
    if chart is not None:
        return value_level_text_from_score(chart_value_score(chart, bucket))
    adj = value_delta + B15_BUCKET_ADJ if bucket == "B15" else value_delta
    return value_level_text_from_score(adj)


def average_value_level_text(value_delta: float) -> str:
    if value_delta >= 0.18:
        return "整体含金量偏高"
    if value_delta >= -0.10:
        return "整体含金量正常"
    if value_delta >= -0.28:
        return "整体略有水分"
    return "整体含水量偏高"


def chart_role_tags(chart: Any, *, is_floor: bool = False, floor_ra: int = 0) -> list[str]:
    """给锐评证据用的角色标签（短词）。"""
    tags: list[str] = []
    ach = f(getattr(chart, "achievements", 0))
    ra = int(getattr(chart, "ra", 0) or 0)
    hardness = chart_hardness_delta(chart)

    if is_floor:
        tags.append("地板")
    if hardness is not None:
        if hardness >= 0.25:
            tags.append("难谱")
        elif hardness <= -0.25:
            tags.append("水谱")
    if ach >= 100.5:
        tags.append("鸟加")
    elif ach >= 100.0:
        tags.append("鸟")
    elif ach >= 99.5:
        tags.append("SSS未鸟")
    elif ach < 99.0:
        tags.append("低达成")

    # 未满鸟但还在 B50 吃分 → 潜力/债，由段位决定怎么骂
    if ach < 100.0 and ra > 0:
        tags.append("未满鸟吃分")
    if floor_ra > 0 and ra <= floor_ra:
        if "地板" not in tags:
            tags.append("地板")
    return tags


def evaluate_chart_value(
    chart: Any,
    bucket: str | None = None,
    *,
    is_floor: bool = False,
    floor_ra: int = 0,
) -> dict[str, Any]:
    bucket = bucket or chart_bucket(getattr(chart, "type", ""))
    hardness = chart_hardness_delta(chart)
    score = chart_value_score(chart, bucket, is_floor=is_floor)
    return {
        "bucket": bucket,
        "ds": chart_ds(chart),
        "fit_diff": chart_fit_diff_or_none(chart),
        "hardness": hardness,
        "score": score,
        "label": value_level_text_from_score(score),
        "roles": chart_role_tags(chart, is_floor=is_floor, floor_ra=floor_ra),
        "artist": chart_author_info(chart)[0],
        "charter": chart_author_info(chart)[1],
    }
