"""管理员运维指令：体检 / 初始化。"""
from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..libraries.ops_service import collect_health_report, full_initialize


def _is_superuser(event: AstrMessageEvent, superusers: list | None) -> bool:
    if not superusers:
        return False
    return str(event.get_sender_id()) in {str(x) for x in superusers}


async def health_check_handler(event: AstrMessageEvent, superusers: list | None = None, config: dict | None = None):
    """舞萌体检 / 舞萌状态"""
    if not _is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    text = await collect_health_report(config or {}, superusers or [])
    yield event.plain_result(text)


async def full_init_handler(event: AstrMessageEvent, superusers: list | None = None, config: dict | None = None):
    """舞萌初始化：固定一次性执行曲库、别名、定数表、完成表并热加载。"""
    if not _is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return

    yield event.plain_result(
        '开始舞萌初始化：将依次更新曲库、别名、定数表、完成表，并热加载到运行态。请稍候…'
    )
    report = await full_initialize()
    yield event.plain_result(report.as_text())
