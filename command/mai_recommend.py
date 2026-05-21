from __future__ import annotations

import asyncio
import math
from typing import Any

from astrbot.api.event import AstrMessageEvent

from .. import log
from ..command.mai_base import convert_message_segment_to_chain, extract_at_qqid
from ..libraries.chart_tags.lookup import get_chart_tags
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import UserDisabledQueryError, UserNotExistsError, UserNotFoundError
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_info

QUERY_B50_TIMEOUT_SECONDS = 20
SSSP_RATING_FACTOR = 22.512
RECOMMEND_RATING_LOW_DIVISOR = 1130
RECOMMEND_RATING_HIGH_DIVISOR = 1075
RECOMMEND_MAX_DS = 15.0
RECOMMEND_LEVEL_INDEXES = (2, 3, 4)
RECOMMEND_CONCURRENCY_LIMIT = 2
_RECOMMEND_SEMAPHORE = asyncio.Semaphore(RECOMMEND_CONCURRENCY_LIMIT)


def _sssp_rating(ds: float) -> int:
    return math.floor(float(ds) * SSSP_RATING_FACTOR)


def _fit_diff(music: Any, level_index: int) -> float | None:
    if not music or not music.stats or level_index >= len(music.stats):
        return None
    stats = music.stats[level_index]
    value = getattr(stats, "fit_diff", None) if stats else None
    return float(value) if value is not None else None


def _buckets(user: Any) -> tuple[list[Any], list[Any]]:
    charts = getattr(user, "charts", None)
    b35 = list((getattr(charts, "sd", None) or [])[:35]) if charts else []
    b15 = list((getattr(charts, "dx", None) or [])[:15]) if charts else []
    return b35, b15


def _chart_key(chart: Any) -> tuple[str, int]:
    return str(chart.song_id), int(chart.level_index)


def _current_rating(user: Any, b35: list[Any], b15: list[Any]) -> int:
    rating = int(getattr(user, "rating", 0) or 0)
    if rating > 0:
        return rating
    return sum(int(getattr(chart, "ra", 0) or 0) for chart in [*b35, *b15])


def _rating_range(rating: int) -> tuple[float, float]:
    low = rating / RECOMMEND_RATING_LOW_DIVISOR
    high = min(rating / RECOMMEND_RATING_HIGH_DIVISOR, RECOMMEND_MAX_DS)
    return min(low, high), high


def _floor_ds_min(floor_ra: int) -> float:
    if floor_ra <= 0:
        return 0.0
    return (floor_ra + 1) / SSSP_RATING_FACTOR


def _bucket_floor(charts: list[Any], full_size: int) -> tuple[int, bool]:
    ratings = [int(getattr(chart, "ra", 0) or 0) for chart in charts]
    floor = min((rating for rating in ratings if rating > 0), default=0)
    return floor, len(charts) >= full_size


def _is_sssp_in_b50(chart: Any) -> bool:
    rate = str(getattr(chart, "rate", "") or "").lower()
    achievements = float(getattr(chart, "achievements", 0) or 0)
    return rate in {"sssp", "sss+"} or achievements >= 100.5


def _sort_key(candidate: dict[str, Any]) -> tuple[bool, float, int, str]:
    fit_delta = candidate.get("fit_delta")
    return (
        float(candidate["ds"]),
        fit_delta is None,
        float(fit_delta) if fit_delta is not None else 999,
        -int(candidate["sssp_ra"]),
        str(candidate["title"]),
    )


def _collect_candidates(user: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    b35, b15 = _buckets(user)
    rating = _current_rating(user, b35, b15)
    if rating <= 0:
        return [], {"rating": rating}

    base_ds_min, ds_max = _rating_range(rating)
    b35_floor, b35_full = _bucket_floor(b35, 35)
    b15_floor, b15_full = _bucket_floor(b15, 15)
    b35_ds_min = max(base_ds_min, _floor_ds_min(b35_floor))
    b15_ds_min = max(base_ds_min, _floor_ds_min(b15_floor))
    b35_keys = {_chart_key(chart) for chart in b35}
    b15_keys = {_chart_key(chart) for chart in b15}
    sssp_b50_keys = {_chart_key(chart) for chart in [*b35, *b15] if _is_sssp_in_b50(chart)}
    result = []

    for music in mai.total_list:
        if int(music.id) >= 100000:
            continue
        for level_index in RECOMMEND_LEVEL_INDEXES:
            if level_index >= len(music.ds) or level_index >= len(music.charts):
                continue
            ds = float(music.ds[level_index])
            key = (str(music.id), level_index)

            bucket = "B15" if key in b15_keys or (key not in b35_keys and music.basic_info.is_new) else "B35"
            floor = b15_floor if bucket == "B15" else b35_floor
            bucket_full = b15_full if bucket == "B15" else b35_full
            ds_min = b15_ds_min if bucket == "B15" else b35_ds_min

            # 1. 先用当前 Rating 和对应 B35/B15 地板推导出的定数区间筛掉不可能的谱面。
            if not ds_min <= ds <= ds_max:
                continue

            sssp_ra = _sssp_rating(ds)

            # 2. 只有 SSS+ 理论单曲 Rating 能超过对应 B35/B15 地板时才继续。
            if floor > 0 and sssp_ra <= floor:
                continue

            # 4. 已经以 SSS+ 出现在 B50 中的谱面不再推荐。
            if key in sssp_b50_keys:
                continue

            fit = _fit_diff(music, level_index)
            fit_delta = float(fit) - ds if fit is not None else None
            candidate = {
                "music": music,
                "song_id": str(music.id),
                "title": music.title,
                "level_index": level_index,
                "level": music.level[level_index],
                "ds": ds,
                "fit_diff": fit,
                "fit_delta": fit_delta,
                "is_new": bool(music.basic_info.is_new),
                "bucket": bucket,
                "floor_ra": floor,
                "bucket_full": bucket_full,
                "ds_min": ds_min,
                "ds_max": ds_max,
                "sssp_ra": sssp_ra,
                "entry_margin": sssp_ra - floor,
            }
            result.append(candidate)

    result.sort(key=_sort_key)
    meta = {
        "rating": rating,
        "base_ds_min": base_ds_min,
        "ds_max": ds_max,
        "b35_ds_min": b35_ds_min,
        "b15_ds_min": b15_ds_min,
        "b35_floor": b35_floor,
        "b15_floor": b15_floor,
        "b35_full": b35_full,
        "b15_full": b15_full,
    }
    return result, meta


def _draw_music_info_sync(music: Any, qqid: int, user: Any) -> Any:
    return asyncio.run(draw_music_info(music, qqid, user))


async def score_recommend_handler(event: AstrMessageEvent):
    if _RECOMMEND_SEMAPHORE.locked():
        yield event.plain_result('吃分推荐正在处理其他请求，请稍后再试')
        return

    qq = extract_at_qqid(event) or event.get_sender_id()
    async with _RECOMMEND_SEMAPHORE:
        yield event.plain_result('正在从水鱼查分器拉取 B50 并计算吃分推荐，请稍候...')

        try:
            user = await asyncio.wait_for(
                maiApi.query_user_b50(qqid=int(qq)),
                timeout=QUERY_B50_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            yield event.plain_result('水鱼 B50 查询超时，请稍后再试')
            return
        except (UserNotFoundError, UserNotExistsError):
            yield event.plain_result('没有找到该玩家的水鱼 B50，请确认已绑定 QQ 或允许查询')
            return
        except UserDisabledQueryError:
            yield event.plain_result('该玩家关闭了水鱼第三方成绩查询')
            return
        except Exception as exc:
            yield event.plain_result(f'获取 B50 失败：{type(exc).__name__}')
            return

        b35, b15 = _buckets(user)
        if not b35 and not b15:
            yield event.plain_result('没有获取到可用 B50 数据')
            return

        candidates, meta = await asyncio.to_thread(_collect_candidates, user)
        if not meta.get("rating"):
            yield event.plain_result('没有获取到可用 Rating，暂时无法计算吃分推荐区间')
            return
        if not candidates:
            yield event.plain_result(
                f'暂时没有找到符合条件的吃分候选谱面\n'
                f'当前 Rating：{meta["rating"]}\n'
                f'B35 推荐定数区间：{meta["b35_ds_min"]:.2f} - {meta["ds_max"]:.2f}\n'
                f'B15 推荐定数区间：{meta["b15_ds_min"]:.2f} - {meta["ds_max"]:.2f}\n'
                f'B35 地板：{meta["b35_floor"]}，B15 地板：{meta["b15_floor"]}'
            )
            return

        candidate = candidates[0]
        music = candidate["music"]
        fit_text = f'{candidate["fit_diff"]:.2f}' if candidate.get("fit_diff") is not None else '未知'
        fit_delta_text = f'{candidate["fit_delta"]:+.2f}' if candidate.get("fit_delta") is not None else '未知'
        tags = await asyncio.to_thread(get_chart_tags, candidate["song_id"], candidate["level_index"])
        tags_text = '、'.join(tags[:6]) if tags else '暂无'
        if candidate["floor_ra"] > 0:
            floor_text = f'当前已有最低分：{candidate["floor_ra"]}'
        else:
            floor_text = '暂无已有正分地板'
        if not candidate["bucket_full"]:
            floor_text += '（分区未满）'
        reason = (
            f'推荐吃分：{candidate["title"]} [{candidate["level"]} / {candidate["ds"]}]\n'
            f'当前 Rating：{meta["rating"]}，推荐定数区间：{candidate["ds_min"]:.2f} - {candidate["ds_max"]:.2f}\n'
            f'推荐分区：{candidate["bucket"]}，{floor_text}\n'
            f'SSS+ 理论单曲 Rating：{candidate["sssp_ra"]}（高出地板 {candidate["entry_margin"]}）\n'
            f'拟合定数：{fit_text}，实际定数：{candidate["ds"]}，拟合-实际：{fit_delta_text}\n'
            f'谱面标签：{tags_text}\n'
            f'筛选：定数区间通过（下限不低于对应地板，上限不超过 {RECOMMEND_MAX_DS:.1f}）、SSS+ 理论分高于对应地板，且该谱面未以 SSS+ 出现在 B50 中。'
        )
        yield event.plain_result(reason)
        try:
            pic = await asyncio.to_thread(_draw_music_info_sync, music, int(event.get_sender_id()), user)
            chain = convert_message_segment_to_chain(pic)
            yield event.chain_result(chain)
        except Exception:
            log.exception("吃分推荐谱面详情图生成失败")
            yield event.plain_result('谱面详情图生成失败，但上面的文字推荐已可用')
