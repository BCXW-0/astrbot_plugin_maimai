"""运维体检、初始化与数据热加载（免费开源能力）。"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

from .. import (
    alias_file,
    chart_file,
    log,
    music_file,
    platedir,
    ratingdir,
)
from .maimaidx_api_data import maiApi
from .maimaidx_music import mai
from .maimaidx_update_table import update_plate_table, update_rating_table

try:
    from .tool import playwright_chromium_status
except Exception:  # pragma: no cover
    playwright_chromium_status = None


def _fmt_mtime(path: Path) -> str:
    if not path.exists():
        return '缺失'
    return datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')


def _file_size(path: Path) -> str:
    if not path.exists():
        return '-'
    size = path.stat().st_size
    if size >= 1024 * 1024:
        return f'{size / 1024 / 1024:.1f}MB'
    if size >= 1024:
        return f'{size / 1024:.0f}KB'
    return f'{size}B'


def _safe_len(obj: Any) -> int:
    try:
        return len(obj) if obj is not None else 0
    except Exception:
        return 0


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ''
    seconds: float = 0.0


@dataclass
class InitReport:
    steps: List[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    def as_text(self) -> str:
        lines = ['【舞萌初始化结果】']
        for step in self.steps:
            mark = 'OK' if step.ok else 'FAIL'
            extra = f' ({step.seconds:.1f}s)' if step.seconds else ''
            detail = f' - {step.detail}' if step.detail else ''
            lines.append(f'[{mark}] {step.name}{extra}{detail}')
        lines.append('全部完成' if self.ok else '存在失败项，请根据上方信息排查或重试失败步骤')
        return '\n'.join(lines)


async def _run_step(name: str, coro_factory: Callable[[], Awaitable[Any]]) -> StepResult:
    t0 = time.time()
    try:
        result = await coro_factory()
        detail = str(result) if result is not None else ''
        return StepResult(name=name, ok=True, detail=detail, seconds=time.time() - t0)
    except Exception as exc:
        log.error(f'{name} 失败: {type(exc).__name__}: {exc}')
        log.error(traceback.format_exc())
        return StepResult(name=name, ok=False, detail=f'{type(exc).__name__}: {exc}', seconds=time.time() - t0)


async def refresh_runtime_music(include_plate: bool = True, include_guess: bool = True) -> None:
    """刷新运行态曲库，并尽量同步牌子与猜歌数据。"""
    await mai.get_music()
    if include_plate:
        try:
            await mai.get_plate_json()
        except Exception as exc:
            fallback = {}
            try:
                fallback = mai.build_plate_id_list_from_music()
                mai.total_plate_id_list = fallback
            except Exception:
                pass
            log.warning(f'牌子数据刷新失败，已尝试兜底: {type(exc).__name__}')
    if include_guess:
        try:
            if hasattr(mai, 'hot_music_ids'):
                mai.hot_music_ids = []
            mai.guess()
        except Exception as exc:
            log.warning(f'猜歌数据刷新失败: {type(exc).__name__}: {exc}')


async def refresh_runtime_alias() -> None:
    await mai.get_music_alias()


async def full_initialize(include_tables: bool = True) -> InitReport:
    """一键执行曲库/别名/牌子/表图更新，并热加载到运行态。"""
    report = InitReport()
    report.steps.append(await _run_step('更新maimai数据(曲库+拟合定数)', lambda: refresh_runtime_music(True, True)))
    report.steps.append(await _run_step('更新别名库', refresh_runtime_alias))
    if include_tables:
        # 表图生成依赖 total_level_data / total_plate_id_list
        if not getattr(mai, 'total_list', None):
            report.steps.append(StepResult('更新定数表', False, '曲库未加载，已跳过'))
            report.steps.append(StepResult('更新完成表', False, '曲库未加载，已跳过'))
        else:
            report.steps.append(await _run_step('更新定数表', update_rating_table))
            if not getattr(mai, 'total_plate_id_list', None):
                try:
                    mai.total_plate_id_list = mai.build_plate_id_list_from_music()
                except Exception:
                    pass
            report.steps.append(await _run_step('更新完成表', update_plate_table))
    return report


async def collect_health_report(config: Optional[dict] = None, superusers: Optional[list] = None) -> str:
    """生成面向管理员的体检报告文本。"""
    config = config or {}
    music_count = _safe_len(getattr(mai, 'total_list', None))
    alias_count = _safe_len(getattr(mai, 'total_alias_list', None))
    plate_groups = _safe_len(getattr(mai, 'total_plate_id_list', None))
    level_keys = _safe_len(getattr(mai, 'total_level_data', None))

    rating_pngs = len(list(ratingdir.glob('*.png'))) if ratingdir.exists() else 0
    plate_pngs = len(list(platedir.glob('*.png'))) if platedir.exists() else 0

    token_ok = bool(str(config.get('maimaidxtoken') or getattr(maiApi.config, 'maimaidxtoken', '') or '').strip())
    webui_on = bool(config.get('roast_persona_webui_enabled', False))
    webui_host = str(config.get('roast_persona_webui_host', '127.0.0.1') or '127.0.0.1')
    webui_port = config.get('roast_persona_webui_port', 8796)
    auto_alias = bool(config.get('daily_update_alias', True))
    auto_tables = bool(config.get('daily_update_tables_if_empty', True))

    pw_text = '未检测'
    if playwright_chromium_status:
        try:
            ok, detail = await playwright_chromium_status()
            pw_text = f'可用 ({detail})' if ok else f'不可用 ({detail})'
        except Exception as exc:
            pw_text = f'检测失败: {type(exc).__name__}'

    issues: List[str] = []
    if music_count <= 0:
        issues.append('运行态曲库为空：请执行「更新maimai数据」或「舞萌初始化」')
    if alias_count <= 0:
        issues.append('运行态别名为空：请执行「更新别名库」')
    if rating_pngs <= 0:
        issues.append('定数表图片缺失：请执行「更新定数表」')
    if plate_pngs <= 0:
        issues.append('完成表图片缺失：请执行「更新完成表」')
    if not token_ok:
        issues.append('未配置 Developer-Token：部分开发者成绩接口可能不可用（B50 基础查询通常仍可用）')
    if '不可用' in pw_text:
        issues.append('Playwright Chromium 不可用：ginfo 统计图可能失败')

    lines = [
        '【舞萌插件体检】',
        f'时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        '',
        '一、运行态数据',
        f'- 曲库：{music_count} 首 / 等级分组 {level_keys}',
        f'- 别名：{alias_count} 条',
        f'- 牌子组：{plate_groups}',
        '',
        '二、本地缓存',
        f'- music_data.json：{_fmt_mtime(music_file)} ({_file_size(music_file)})',
        f'- music_chart.json：{_fmt_mtime(chart_file)} ({_file_size(chart_file)})',
        f'- music_alias.json：{_fmt_mtime(alias_file)} ({_file_size(alias_file)})',
        f'- 定数表 PNG：{rating_pngs} 张',
        f'- 完成表 PNG：{plate_pngs} 张',
        '',
        '三、配置与依赖',
        f'- 管理员数量：{_safe_len(superusers)}',
        f'- Developer-Token：{"已配置" if token_ok else "未配置"}',
        f'- 每日自动更新别名：{"开" if auto_alias else "关"}',
        f'- 表图空时自动补全：{"开" if auto_tables else "关"}',
        f'- Playwright：{pw_text}',
        f'- WebUI：{"开启" if webui_on else "关闭"}'
        + (f' ({webui_host}:{webui_port})' if webui_on else ''),
        '',
    ]
    if issues:
        lines.append('四、待处理')
        for idx, item in enumerate(issues, 1):
            lines.append(f'{idx}. {item}')
        lines.append('')
        lines.append('建议：管理员私聊执行「舞萌初始化」一键修复。')
    else:
        lines.append('四、结论：状态健康，可正常使用。')
    lines.append('')
    lines.append('常用管理指令：舞萌体检 / 舞萌初始化 / 更新maimai数据 / 更新别名库 / 更新定数表 / 更新完成表')
    return '\n'.join(lines)


async def daily_maintenance(config: Optional[dict] = None) -> str:
    """定时维护：曲库必更，别名/空表按配置增量更新。"""
    config = config or {}
    parts: List[str] = []
    try:
        await refresh_runtime_music(include_plate=True, include_guess=True)
        parts.append('曲库/牌子/猜歌: OK')
    except Exception as exc:
        parts.append(f'曲库: FAIL {type(exc).__name__}')
        log.error(traceback.format_exc())

    if bool(config.get('daily_update_alias', True)):
        try:
            await refresh_runtime_alias()
            parts.append('别名: OK')
        except Exception as exc:
            parts.append(f'别名: FAIL {type(exc).__name__}')
            log.error(traceback.format_exc())

    if bool(config.get('daily_update_tables_if_empty', True)):
        need_rating = not ratingdir.exists() or not any(ratingdir.glob('*.png'))
        need_plate = not platedir.exists() or not any(platedir.glob('*.png'))
        if need_rating and getattr(mai, 'total_list', None):
            try:
                msg = await update_rating_table()
                parts.append(f'定数表补全: {msg}')
            except Exception as exc:
                parts.append(f'定数表补全: FAIL {type(exc).__name__}')
        if need_plate and getattr(mai, 'total_list', None):
            try:
                if not getattr(mai, 'total_plate_id_list', None):
                    mai.total_plate_id_list = mai.build_plate_id_list_from_music()
                msg = await update_plate_table()
                parts.append(f'完成表补全: {msg}')
            except Exception as exc:
                parts.append(f'完成表补全: FAIL {type(exc).__name__}')

    summary = '；'.join(parts) if parts else '无操作'
    log.info(f'每日维护完成: {summary}')
    return summary
