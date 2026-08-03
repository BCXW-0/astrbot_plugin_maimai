"""用户成长总览与分层帮助。"""
from __future__ import annotations

import asyncio
import re

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from .. import is_reply_enabled, log, static
from ..command.mai_base import extract_at_qqid
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import (
    UserDisabledQueryError,
    UserNotExistsError,
    UserNotFoundError,
)
from ..libraries.maimaidx_music import mai
from ..libraries.user_token_manager import get_token_manager

QUERY_TIMEOUT = 20

HELP_TOPICS = {
    "基础": (
        "【帮助·基础】\n"
        "帮助 / help：总帮助（图片+高频指令）\n"
        "帮助 <主题>：查分 / 推分 / 同步 / 猜歌 / 管理\n"
        "今日舞萌 / jrys：今日运势\n"
        "来个13+：随机指定等级\n"
        "mai什么：随机/推分语义推荐\n"
        "我的舞萌：个人总览"
    ),
    "查分": (
        "【帮助·查分】\n"
        "b50 / B50 / ccb [用户名或@]：Best 50\n"
        "info / minfo <曲名或ID>：自己的单曲详情\n"
        "ginfo <曲名或ID>：全局谱面统计\n"
        "分数线 <难度+ID> <达成率>：容错计算\n"
        "<定数>的<达成率>是多少分：Rating 计算\n"
        "查看排名 / 我的排名：公开榜\n"
        "id <歌曲ID>：曲目详情\n"
        "查歌 / 定数查歌 / bpm查歌 / 曲师查歌 / 谱师查歌"
    ),
    "推分": (
        "【帮助·推分】\n"
        "吃分推荐 [@用户] [目标Rating]：智能吃分\n"
        "吃粪推荐：反向推荐（娱乐）\n"
        "<牌子>进度：如 祭将进度\n"
        "我的舞萌：总览地板/绑定/下一步"
    ),
    "同步": (
        "【帮助·同步成绩】\n"
        "1) 绑定水鱼 <Import-Token>\n"
        "   位置：水鱼查分器 -> 编辑个人资料 -> 成绩上传 token\n"
        "2) 机台二维码同步：\n"
        "   更新b50 <SGWCMAID> 或 导 <SGWCMAID>\n"
        "   首次会保存机台用户信息；之后可直接 更新b50\n"
        "3) 查看水鱼 / 解绑水鱼\n"
        "建议：含 SGID 的消息尽量私聊；插件会尝试撤回敏感消息"
    ),
    "猜歌": (
        "【帮助·猜歌】\n"
        "猜歌 / 猜曲绘：开始\n"
        "重置猜歌：重置当前局\n"
        "开启mai猜歌 / 关闭mai猜歌：群开关\n"
        "游戏中直接发送歌名/别名作答"
    ),
    "管理": (
        "【帮助·管理】（需超级管理员）\n"
        "舞萌体检 / 舞萌状态：健康检查\n"
        "舞萌初始化：一键更新曲库+别名+定数表+完成表并热加载\n"
        "（四项更新已合并进「舞萌初始化」，无需单独执行）\n"
        "开启舞萌功能 / 关闭舞萌功能：当前群开关"
    ),
}

QUICK_HELP = (
    "【舞萌DX · 高频指令】\n"
    "个人：我的舞萌 | b50 | 吃分推荐\n"
    "查歌：查歌 关键词 | id 歌曲ID | 13+定数表\n"
    "同步：绑定水鱼 <token> | 更新b50 <SGID>\n"
    "进度：祭将进度\n"
    "娱乐：今日舞萌 | 猜歌 | 锐评b50\n"
    "帮助 查分/推分/同步/猜歌/管理\n\n"
    "提示：若已配置 help.png，会附带帮助图"
)


def _reply_chain(event: AstrMessageEvent, chain: list) -> list:
    if is_reply_enabled():
        try:
            chain = [Comp.Reply(id=event.message_obj.message_id)] + list(chain)
        except Exception:
            pass
    return chain


def _reply_text(event: AstrMessageEvent, text: str):
    return event.chain_result(_reply_chain(event, [Comp.Plain(text)]))


def _bucket_stats(charts: list) -> dict:
    charts = list(charts or [])
    ras = [int(getattr(c, "ra", 0) or 0) for c in charts]
    positive = [r for r in ras if r > 0]
    sssp = sum(1 for c in charts if float(getattr(c, "achievements", 0) or 0) >= 100.5)
    return {
        "count": len(charts),
        "sum_ra": sum(positive),
        "floor": min(positive) if positive else 0,
        "avg": int(sum(positive) / len(positive)) if positive else 0,
        "sssp": sssp,
    }


async def help_topic_handler(event: AstrMessageEvent):
    """帮助 / help / 帮助 主题"""
    msg = event.message_str.strip()
    m = re.match(r"^(?:帮助|help)\s*(.*)$", msg, re.I)
    topic = (m.group(1) if m else "").strip()

    if topic:
        key = None
        for name in HELP_TOPICS:
            if topic.lower() == name.lower() or topic in name or name in topic:
                key = name
                break
        aliases = {
            "分数": "查分",
            "成绩": "查分",
            "b50": "查分",
            "推荐": "推分",
            "吃分": "推分",
            "上传": "同步",
            "绑定": "同步",
            "更新b50": "同步",
            "导": "同步",
            "游戏": "猜歌",
            "admin": "管理",
            "运维": "管理",
            "初始化": "管理",
            "体检": "管理",
        }
        if key is None:
            low = topic.lower()
            for a, t in aliases.items():
                if a in low or a in topic:
                    key = t
                    break
        if key and key in HELP_TOPICS:
            yield _reply_text(event, HELP_TOPICS[key])
            return
        yield _reply_text(
            event,
            f"未知帮助主题：{topic}\n可用：基础 / 查分 / 推分 / 同步 / 猜歌 / 管理",
        )
        return

    chain: list = [Comp.Plain(QUICK_HELP)]
    help_image = static / "help.png"
    if help_image.exists():
        chain.append(Comp.Image.fromFileSystem(str(help_image)))
    else:
        chain.append(Comp.Plain("\n（未找到 help.png，已仅发送文字帮助；管理员可替换 static/help.png）"))
    yield event.chain_result(_reply_chain(event, chain))


async def my_maimai_handler(event: AstrMessageEvent):
    """我的舞萌：个人成长总览"""
    if not getattr(mai, "total_list", None):
        yield _reply_text(event, "歌曲数据未加载，请稍后再试或联系管理员执行「舞萌初始化」")
        return

    qqid = extract_at_qqid(event) or event.get_sender_id()
    msg = event.message_str.strip()
    username = re.sub(r"^我的舞萌\s*", "", msg).strip()
    if username.startswith("@") or "MSG_ID:" in username:
        username = ""

    try:
        if username:
            user = await asyncio.wait_for(
                maiApi.query_user_b50(username=username),
                timeout=QUERY_TIMEOUT,
            )
        else:
            user = await asyncio.wait_for(
                maiApi.query_user_b50(qqid=int(qqid)),
                timeout=QUERY_TIMEOUT,
            )
    except asyncio.TimeoutError:
        yield _reply_text(event, "水鱼查询超时，请稍后再试")
        return
    except (UserNotFoundError, UserNotExistsError):
        yield _reply_text(
            event,
            "未找到玩家 B50。\n"
            "可能原因：未在水鱼绑定 QQ、用户名不对、或隐私未开启。\n"
            "请先绑定水鱼或确认隐私设置，再使用：b50 水鱼用户名",
        )
        return
    except UserDisabledQueryError:
        yield _reply_text(event, "该玩家关闭了第三方查询，或未同意水鱼用户协议")
        return
    except Exception as exc:
        log.error(f"我的舞萌失败: {type(exc).__name__}: {exc}")
        yield _reply_text(event, f"查询失败：{type(exc).__name__}")
        return

    charts = getattr(user, "charts", None)
    b35 = list((getattr(charts, "sd", None) or [])[:35]) if charts else []
    b15 = list((getattr(charts, "dx", None) or [])[:15]) if charts else []
    s35 = _bucket_stats(b35)
    s15 = _bucket_stats(b15)
    rating = int(getattr(user, "rating", 0) or 0)
    if rating <= 0:
        rating = s35["sum_ra"] + s15["sum_ra"]
    nick = getattr(user, "nickname", None) or getattr(user, "username", None) or str(qqid)
    plate = getattr(user, "plate", None) or "无"

    mgr = get_token_manager()
    bound = bool(mgr and mgr.has_token(str(event.get_sender_id())))
    next_line = "发送「吃分推荐」获取下一首"
    try:
        from .mai_recommend import RECOMMEND_POOL_WEIGHT_STEP, _choose_candidate, _collect_candidates

        candidates, meta = await asyncio.to_thread(_collect_candidates, user)
        if candidates:
            cand = await asyncio.to_thread(_choose_candidate, candidates, RECOMMEND_POOL_WEIGHT_STEP)
            if cand:
                next_line = (
                    f"建议下一首：{cand['title']} [{cand['level']}/{cand['ds']}] "
                    f"({cand['bucket']} · SSS+约{cand['sssp_ra']})"
                )
    except Exception:
        pass

    lines = [
        f"【我的舞萌】{nick}",
        f"Rating：{rating}　牌子：{plate}",
        f"B35：{s35['count']} 首 · 合计 {s35['sum_ra']} · 地板 {s35['floor']} · SSS+ {s35['sssp']}",
        f"B15：{s15['count']} 首 · 合计 {s15['sum_ra']} · 地板 {s15['floor']} · SSS+ {s15['sssp']}",
        f"Import-Token：{'已绑定' if bound else '未绑定（绑定水鱼 <token>）'}",
        next_line,
        "",
        "快捷：b50 | 吃分推荐 | 帮助 推分",
    ]
    yield _reply_text(event, "\n".join(lines))
