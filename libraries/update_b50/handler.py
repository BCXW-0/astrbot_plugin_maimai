from __future__ import annotations

import re
from typing import Any, AsyncGenerator

from ... import log
from ..arcade_credential_manager import get_arcade_credential_manager
from ..user_token_manager import get_token_manager
from .formatter import format_success
from .recall import recall_current_message
from .sgid import extract_sgid, is_probable_sgid, validate_sgid_for_one_time_use
from .sync_service import MaimaiUpdateService

_service: MaimaiUpdateService | None = None
_service_key: tuple[int, str] | None = None


def int_config(config: dict | None, key: str, default: int) -> int:
    try:
        return int((config or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def get_service(config: dict | None) -> MaimaiUpdateService:
    global _service, _service_key
    timeout = max(5, int_config(config, "request_timeout_seconds", 30))
    proxy = str((config or {}).get("maimai_http_proxy", "") or "")
    key = (timeout, proxy)
    if _service is None or _service_key != key:
        _service = MaimaiUpdateService(timeout=timeout, http_proxy=proxy)
        _service_key = key
    return _service


def join_messages(*messages: str) -> str:
    return "\n".join(message for message in messages if message)


async def sgwcmaid_update_handler(event: Any, context: Any | None = None, config: dict | None = None) -> AsyncGenerator[str, None]:
    message_str = (getattr(event, "message_str", "") or "").strip()
    raw_arg = re.sub(r"^(更新b50|导)(?:[\s:：]+)?", "", message_str, flags=re.IGNORECASE).strip()
    sgid = extract_sgid(raw_arg)
    qqid = str(event.get_sender_id())
    mgr = get_token_manager()
    import_token = mgr.get_token(qqid) if mgr else None
    if not import_token:
        yield "❌ 尚未绑定水鱼 Import-Token。\n请先执行：绑定水鱼 <水鱼 Import-Token>。"
        return
    credential_mgr = get_arcade_credential_manager()
    saved_credentials = credential_mgr.get_credential(qqid) if credential_mgr else None
    service = get_service(config)
    if sgid:
        recall_notice = await recall_current_message(event, context, config)
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()
        if not is_probable_sgid(sgid):
            yield join_messages(recall_notice, "❌ SGID 格式不正确，请发送以 SGWCMAID 开头的完整文本。")
            return
        max_age_seconds = max(30, int_config(config, "sgid_max_age_seconds", 180))
        if validation_error := validate_sgid_for_one_time_use(sgid, max_age_seconds):
            yield join_messages(recall_notice, f"❌ {validation_error}")
            return
        try:
            arcade_identifier = await service.arcade_identifier_from_sgid(sgid)
            credentials = getattr(arcade_identifier, "credentials", None)
            if credential_mgr and isinstance(credentials, str) and credentials.strip():
                credential_mgr.set_credential(qqid, credentials)
            result = await service.sync_to_divingfish(arcade_identifier, import_token)
        except Exception as exc:
            log.exception("更新b50失败")
            yield join_messages(recall_notice, f"❌ 更新失败：{service.describe_error(exc)}")
            return
        yield join_messages(recall_notice, format_success(result))
        return
    if saved_credentials:
        try:
            result = await service.sync_from_credentials_to_divingfish(saved_credentials, import_token)
        except Exception as exc:
            log.exception("使用已绑定机台用户信息更新b50失败")
            yield f"❌ 更新失败：{service.describe_error(exc)}\n如果旧绑定已失效，请重新执行：更新b50 <SGWCMAID识别码>"
            return
        yield format_success(result)
        return
    yield "用法：更新b50 <SGWCMAID识别码>\n别名：导 <SGWCMAID识别码>\n首次使用必须提供官方公众号 SGWCMAID 识别码；成功绑定后，下次可直接发送“更新b50”尝试复用已绑定的机台用户信息。"
