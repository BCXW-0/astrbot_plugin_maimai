import random
import re
import traceback
from re import Match
from PIL import Image
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import Root, log, get_botname
from ..libraries.image import image_to_base64, music_picture
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_info
from ..libraries.maimaidx_player_score import rating_ranking_data
from .. import static
from ..libraries.tool import qqhash


MAIMAIDX_HELP_TEXT = """舞萌 DX 插件帮助

基础：
帮助 / help：发送帮助图片
今日mai / 今日舞萌 / 今日运势 / jrys：今日运势与推荐歌曲
来个<难度>：随机一首指定等级歌曲，如 来个13+
mai什么：随机推荐歌曲；包含推分语义时会尝试结合 B50 推荐

管理员：
开启舞萌功能 / 关闭舞萌功能：当前群功能开关
更新maimai数据：刷新曲库、拟合定数和牌子数据
更新别名库：刷新查歌使用的曲目别名缓存
更新定数表：生成或更新等级定数表图片
更新完成表：生成或更新牌子完成表图片

查歌：
查歌 <关键词> / search <关键词>：按标题或别名搜索歌曲
id <歌曲ID>：按 ID 查询歌曲信息
定数查歌 <定数>：按定数查询
定数查歌 <下限> <上限> [页数]：按定数范围查询
bpm查歌 <BPM>：按 BPM 查询
bpm查歌 <下限> <上限> [页数]：按 BPM 范围查询
曲师查歌 <曲师> [页数]：按曲师查询
谱师查歌 <谱师> [页数]：按谱师查询

成绩：
b50 / B50 / ccb [水鱼用户名或@用户]：查询 Best 50
info / minfo <曲名或ID>：查询自己的单曲成绩详情
ginfo <曲名或ID>：查询全局谱面统计
ginfo <绿黄红紫白><曲名或ID>：查询指定难度全局谱面统计
查看排名 / 查看排行：查询公开 Rating 排名
我的排名：查询自己在公开 Rating 排名中的位置
分数线 <难度+歌曲ID> <目标达成率>：计算达成率容错
<定数>的<达成率>是多少分：计算 Rating

表格与进度：
<等级>定数表：查看等级定数表，如 13+定数表
<等级>完成表：查看等级完成表，如 13+完成表
<牌子>完成表：查看牌子完成表，如 祭将完成表
<牌子>进度：查询牌子进度，如 祭将进度
<等级><评价>进度：查询等级评价进度，如 13+sss进度
<定数>分数列表：查看指定定数或等级的成绩列表
我要在<等级>上<分数>分：查找可提升 Rating 的谱面

锐评与推荐：
锐评b50 [人格或要求]：生成 B50 锐评
/吃分推荐 [@用户]：按 B50 擅长标签、拟合定数、实际定数和 B35/B15 最低分推荐吃分曲

成绩同步：
绑定水鱼 <水鱼token>：绑定水鱼 Import-Token
查看水鱼：查看绑定状态
解绑水鱼：解除绑定
更新b50 <SGWCMAID识别码> / 导 <SGWCMAID识别码>：首次或重新绑定机台用户信息并同步成绩
更新b50 / 导：复用已保存机台用户信息同步成绩

猜歌：
猜歌 / 猜曲绘：开始猜歌
重置猜歌：重置当前猜歌
开启mai猜歌 / 关闭mai猜歌：群猜歌开关
"""


def extract_at_qqid(event: AstrMessageEvent):
    """
    从消息中提取 @ 的 QQ ID
    
    Args:
        event: AstrMessageEvent 对象
    
    Returns:
        被 @ 的 QQ ID（字符串），如果没有 @ 消息则返回 None
    """
    if not event.message_obj or not event.message_obj.message:
        return None
    
    # 遍历消息链，查找 At 组件
    for component in event.message_obj.message:
        # 检查是否是 At 组件
        # Comp.At 组件可能有 qq 属性，或者通过 type 和 data 访问
        if hasattr(component, 'qq'):
            qq_id = component.qq
            if qq_id:
                return str(qq_id)
        elif hasattr(component, 'type') and component.type == 'at':
            # 通过 data 字典访问
            if hasattr(component, 'data') and 'qq' in component.data:
                qq_id = component.data['qq']
                if qq_id:
                    return str(qq_id)
            # 或者直接有 qq 属性
            elif hasattr(component, 'qq'):
                qq_id = component.qq
                if qq_id:
                    return str(qq_id)
    
    return None


def convert_message_segment_to_chain(msg):
    """将 MessageSegment 转换为 astrbot 的 MessageChain"""
    if isinstance(msg, str):
        # 避免出现“只回复了引用/At，但正文为空”的情况
        text = msg if msg.strip() else '发生错误：返回内容为空'
        return [Comp.Plain(text)]
    
    # 如果是 MessageSegment 对象
    if hasattr(msg, 'type') and hasattr(msg, 'data'):
        if msg.type == 'image':
            # 处理图片
            file_data = msg.data.get('file', '')
            if file_data.startswith('base64://'):
                base64_data = file_data.replace('base64://', '')
                return [Comp.Image.fromBase64(base64_data)]
            elif file_data.startswith('http://') or file_data.startswith('https://'):
                return [Comp.Image.fromURL(file_data)]
            else:
                # 文件路径
                return [Comp.Image.fromFileSystem(file_data)]
        elif msg.type == 'text':
            return [Comp.Plain(msg.data.get('text', ''))]
    
    # 如果是列表，递归处理
    if isinstance(msg, list):
        chain = []
        for item in msg:
            chain.extend(convert_message_segment_to_chain(item))
        return chain
    
    # 默认返回文本
    return [Comp.Plain(str(msg))]


async def update_data_handler(event: AstrMessageEvent, superusers: list = None):
    """更新maimai数据"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    await mai.get_music()
    yield event.plain_result('maimai数据更新完成')


async def update_alias_handler(event: AstrMessageEvent, superusers: list = None):
    """更新别名库"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
        yield event.plain_result('仅允许超级管理员执行此操作')
        return

    try:
        await mai.get_music_alias()
        log.info('手动更新别名库成功')
        yield event.plain_result('手动更新别名库成功')
    except Exception as e:
        log.error(f'手动更新别名库失败: {e}')
        log.error(traceback.format_exc())
        yield event.plain_result('手动更新别名库失败')


async def maimaidxhelp_handler(event: AstrMessageEvent):
    """帮助maimaiDX"""
    from pathlib import Path
    help_image_path = static / "help.png"
    
    if not help_image_path.exists():
        yield event.plain_result("帮助图片未找到，请联系管理员")
        return
    
    yield event.chain_result([Comp.Image.fromFileSystem(str(help_image_path))])


async def mai_today_handler(event: AstrMessageEvent):
    """今日mai/今日舞萌/今日运势/jrys"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    wm_list = [
        '拼机', 
        '推分', 
        '越级', 
        '下埋', 
        '夜勤', 
        '练底力', 
        '练手法', 
        '打旧框', 
        '干饭', 
        '抓绝赞', 
        '收歌'
    ]
    uid = event.get_sender_id()
    # 确保 uid 是整数类型
    try:
        uid_int = int(uid) if uid else 0
    except (ValueError, TypeError):
        uid_int = 0
    h = qqhash(uid_int)
    rp = h % 100
    wm_value = []
    for i in range(11):
        wm_value.append(h & 3)
        h >>= 2
    msg = f'\n今日人品值：{rp}\n'
    for i in range(11):
        if wm_value[i] == 3:
            msg += f'宜 {wm_list[i]}\n'
        elif wm_value[i] == 0:
            msg += f'忌 {wm_list[i]}\n'
    music = mai.total_list[h % len(mai.total_list)]
    ds = '/'.join([str(_) for _ in music.ds])
    # 动态获取 BOTNAME，确保获取最新值
    from .. import get_botname
    botname = get_botname()
    msg += f'{botname} Bot提醒您：打机时不要大力拍打或滑动哦\n今日推荐歌曲：\n'
    msg += f'ID.{music.id} - {music.title}\n'
    msg += ds
    
    # 构建消息链：文本 + 图片
    chain = [Comp.Plain(msg)]
    
    # 添加图片
    music_img_path = music_picture(music.id)
    if music_img_path.exists():
        chain.append(Comp.Image.fromFileSystem(str(music_img_path)))
    
    yield event.chain_result(chain)


async def mai_what_handler(event: AstrMessageEvent):
    """mai什么"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str
    match = re.search(r'.*mai.*什么(.+)?', message_str, re.IGNORECASE)
    
    music = mai.total_list.random()
    user = None
    if match and match.group(1):
        point = match.group(1)
        if '推分' in point or '上分' in point or '加分' in point:
            try:
                user = await maiApi.query_user_b50(qqid=event.get_sender_id())
                r = random.randint(0, 1)
                _ra = 0
                ignore = []
                if r == 0:
                    if sd := user.charts.sd:
                        ignore = [m.song_id for m in sd if m.achievements < 100.5]
                        _ra = sd[-1].ra
                else:
                    if dx := user.charts.dx:
                        ignore = [m.song_id for m in dx if m.achievements < 100.5]
                        _ra = dx[-1].ra
                if _ra != 0:
                    ds = round(_ra / 22.4, 1)
                    musiclist = mai.total_list.filter(ds=(ds, ds + 1))
                    for _m in musiclist:
                        if int(_m.id) in ignore:
                            musiclist.remove(_m)
                    music = musiclist.random()
            except (UserNotFoundError, UserDisabledQueryError):
                pass
    
    result = await draw_music_info(music, event.get_sender_id(), user)
    # 将 MessageSegment 转换为 MessageChain
    chain = convert_message_segment_to_chain(result)
    yield event.chain_result(chain)


async def random_song_handler(event: AstrMessageEvent):
    """随机歌曲"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str
    match = re.match(r'^[来随给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$', message_str)
    
    try:
        if not match:
            yield event.plain_result('随机命令错误，请检查语法')
            return
            
        diff = match.group(1)
        if diff == 'dx':
            tp = ['DX']
        elif diff == 'sd' or diff == '标准':
            tp = ['SD']
        else:
            tp = ['SD', 'DX']
        level = match.group(3)
        if match.group(2) == '':
            music_data = mai.total_list.filter(level=level, type=tp)
        else:
            music_data = mai.total_list.filter(level=level, diff=['绿黄红紫白'.index(match.group(2))], type=tp)
        if len(music_data) == 0:
            msg = '没有这样的乐曲哦。'
            yield event.plain_result(msg)
        else:
            result = await draw_music_info(music_data.random(), event.get_sender_id())
            # 将 MessageSegment 转换为 MessageChain
            chain = convert_message_segment_to_chain(result)
            yield event.chain_result(chain)
    except Exception as e:
        log.error(f'随机命令错误: {e}')
        yield event.plain_result('随机命令错误，请检查语法')


async def rating_ranking_handler(event: AstrMessageEvent):
    """查看排名/查看排行"""
    message_str = event.message_str.strip()
    # 移除命令前缀
    args = message_str.replace('查看排名', '').replace('查看排行', '').strip()
    
    page = 1
    name = ''
    if args.isdigit():
        page = int(args)
    else:
        name = args.lower()
    
    pic = await rating_ranking_data(name, page)
    # 将 MessageSegment 转换为 MessageChain
    chain = convert_message_segment_to_chain(pic)
    yield event.chain_result(chain)


async def my_rating_ranking_handler(event: AstrMessageEvent):
    """我的排名"""
    try:
        user = await maiApi.query_user_b50(qqid=event.get_sender_id())
        rank_data = await maiApi.rating_ranking()
        for num, rank in enumerate(rank_data):
            if rank.username == user.username:
                result = f'您的Rating为「{rank.ra}」，排名第「{num + 1}」名'
                yield event.plain_result(result)
                return
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        yield event.plain_result(str(e))
