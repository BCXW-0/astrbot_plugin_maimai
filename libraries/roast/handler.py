from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import astrbot.api.message_components as Comp

from ... import log
from ..maimaidx_api_data import maiApi
from ..roast_persona_manager import get_roast_persona_manager
from .b50_context import build_analysis_context
from .llm_client import call_llm
from .renderer import render_analysis_image


async def b50_analysis_handler(event, context, config: dict | None = None):
    qqid = event.get_sender_id()
    message = event.message_str.strip()
    style = message.replace('/锐评b50', '').replace('锐评b50', '').strip()
    image_path = ""
    try:
        yield event.plain_result("正在从水鱼查分器拉取 B50 并调用 LLM 生成锐评，请稍候...")
        userinfo = await maiApi.query_user_b50(qqid=int(qqid))
        prompt = await asyncio.to_thread(build_analysis_context, userinfo, str(qqid))
        persona_prompt = ""
        matched_persona_name = None
        taste_roast_setting = ""
        special_note_setting = ""
        if style:
            manager = get_roast_persona_manager()
            if manager:
                persona_prompt, matched_persona_name, taste_roast_setting, special_note_setting = manager.build_prompt_by_style(style)
        analysis = await call_llm(context, prompt, style, persona_prompt, matched_persona_name, taste_roast_setting, special_note_setting, config)
        image_path = render_analysis_image(userinfo, analysis)
        yield event.chain_result([Comp.Image.fromFileSystem(image_path)])
    except Exception as e:
        log.error(f"锐评b50 失败: {type(e).__name__} - {e}")
        yield event.plain_result("锐评b50 失败：生成过程中出现错误，请稍后再试；管理员可查看插件日志。")
    finally:
        if image_path:
            with suppress(FileNotFoundError):
                Path(image_path).unlink()
