import re
from re import Match
from textwrap import dedent
from typing import List, Tuple

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import SONGS_PER_PAGE, diffs, log
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_model import Music
from ..libraries.maimaidx_music import guess, mai
from ..libraries.maimaidx_music_info import draw_music_info


BASE_SEARCH_USAGE = dedent('''
        命令格式：
        定数查歌 「定数」「页数」
        定数查歌 「定数下限」「定数上限」「页数」
    ''').strip()
BPM_SEARCH_USAGE = '命令格式：\nbpm查歌 「bpm」\nbpm查歌 「bpm下限」「bpm上限」「页数」'


def image_chain_from_text(text: str) -> list[Comp.Image]:
    img_base64 = image_to_base64(text_to_image(text))
    if img_base64.startswith('base64://'):
        img_base64 = img_base64[9:]
    return [Comp.Image.fromBase64(img_base64)]


def song_level(ds1: float, ds2: float) -> List[Tuple[str, str, float, str]]:
    """
    查询定数范围内的乐曲
    
    Params:
        `ds1`: 定数下限
        `ds2`: 定数上限
    Return:
        `result`: 查询结果
    """
    result: List[Tuple[str, str, float, str]] = []
    music_data = mai.total_list.filter(ds=(ds1, ds2))
    for music in sorted(music_data, key=lambda x: int(x.id)):
        if int(music.id) >= 100000:
            continue
        for i in music.diff:
            result.append((music.id, music.title, music.ds[i], diffs[i]))
    return result


async def search_music_handler(event: AstrMessageEvent):
    """查歌/search 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['查歌', 'search']:
        if message_str.lower().startswith(prefix.lower()):
            name = message_str[len(prefix):].strip()
            break
    else:
        name = message_str
    
    page = 1
    if not name:
        yield event.plain_result('请输入关键词')
        return
    
    result = mai.total_list.filter(title_search=name)
    if len(result) == 0:
        # 尝试别名搜索
        if hasattr(mai, 'total_alias_list') and mai.total_alias_list:
            alias_result = mai.total_alias_list.by_alias(name)
            if alias_result:
                if len(alias_result) == 1:
                    music = mai.total_list.by_id(str(alias_result[0].SongID))
                    if music:
                        pic = await draw_music_info(music, event.get_sender_id())
                        chain = convert_message_segment_to_chain(pic)
                        chain.insert(0, Comp.Plain('您要找的是不是：'))
                        yield event.chain_result(chain)
                        return
                else:
                    msg = f'找到{len(alias_result)}个相同别名的曲目：\n'
                    for songs in alias_result:
                        msg += f'{songs.SongID}：{songs.Name}\n'
                    msg += '请使用「id xxxxx」查询指定曲目'
                    yield event.plain_result(msg.strip())
                    return
        yield event.plain_result('没有找到这样的乐曲。请尝试使用歌名的一部分，或使用「id xxxxx」查询。')
        return
    
    if len(result) == 1:
        pic = await draw_music_info(result.random(), event.get_sender_id())
        chain = convert_message_segment_to_chain(pic)
        yield event.chain_result(chain)
        return
        
    search_result = ''
    result.sort(key=lambda i: int(i.id))
    for i, music in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{music.id}」":<7} {music.title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    yield event.chain_result(image_chain_from_text(search_result))


async def search_base_handler(event: AstrMessageEvent):
    """定数查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['定数查歌', 'search base']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if len(args) > 3 or len(args) == 0:
        yield event.plain_result(BASE_SEARCH_USAGE)
        return
    
    page = 1
    if len(args) == 1:
        ds1, ds2 = args[0], args[0]
    elif len(args) == 2:
        if '.' in args[1]:
            ds1, ds2 = args
        else:
            ds1, ds2 = args[0], args[0]
            page = args[1]
    else:
        ds1, ds2, page = args
    try:
        page = int(page)
        result = song_level(float(ds1), float(ds2))
    except (TypeError, ValueError):
        yield event.plain_result(BASE_SEARCH_USAGE)
        return
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    search_result = ''
    for i, _result in enumerate(result):
        id, title, ds, diff = _result
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{id}」":<7}{f"「{diff}」":<11}{f"「{ds}」"} {title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    yield event.chain_result(image_chain_from_text(search_result))


async def search_bpm_handler(event: AstrMessageEvent):
    """bpm查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['bpm查歌', 'search bpm']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args = args_str.split()
    page = 1
    try:
        if len(args) == 1:
            result = mai.total_list.filter(bpm=int(args[0]))
        elif len(args) == 2:
            if (bpm := int(args[0])) > int(args[1]):
                page = int(args[1])
                result = mai.total_list.filter(bpm=bpm)
            else:
                result = mai.total_list.filter(bpm=(bpm, int(args[1])))
        elif len(args) == 3:
            result = mai.total_list.filter(bpm=(int(args[0]), int(args[1])))
            page = int(args[2])
        else:
            yield event.plain_result(BPM_SEARCH_USAGE)
            return
    except (TypeError, ValueError):
        yield event.plain_result(BPM_SEARCH_USAGE)
        return
    
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    result.sort(key=lambda x: int(x.basic_info.bpm))
    
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{m.id}」":<7}{f"「BPM {m.basic_info.bpm}」":<9} {m.title} \n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    yield event.chain_result(image_chain_from_text(search_result))


async def search_artist_handler(event: AstrMessageEvent):
    """曲师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['曲师查歌', 'search artist']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    page = 1
    if len(args) == 1:
        name: str = args[0]
    elif len(args) == 2:
        name: str = args[0]
        if args[1].isdigit():
            page = int(args[1])
        else:
            yield event.plain_result('命令格式：\n曲师查歌「曲师名称」「页数」')
            return
    else:
        yield event.plain_result('命令格式：\n曲师查歌「曲师名称」「页数」')
        return
    
    result = mai.total_list.filter(artist_search=name)
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{m.id}」":<7}{f"「{m.basic_info.artist}」"} - {m.title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    yield event.chain_result(image_chain_from_text(search_result))


async def search_charter_handler(event: AstrMessageEvent):
    """谱师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['谱师查歌', 'search charter']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    page = 1
    if len(args) == 1:
        name: str = args[0]
    elif len(args) == 2:
        name: str = args[0]
        if args[1].isdigit():
            page = int(args[1])
        else:
            yield event.plain_result('命令格式：\n谱师查歌「谱师名称」「页数」')
            return
    else:
        yield event.plain_result('命令格式：\n谱师查歌「谱师名称」「页数」')
        return
    
    result = mai.total_list.filter(charter_search=name)
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            diff_charter = zip([diffs[d] for d in m.diff], [m.charts[d].charter for d in m.diff])
            diff_parts = [
                f"{f'「{d}」':<9}{f'「{c}」'}"
                for d, c in diff_charter
            ]
            diff_str = " ".join(diff_parts)
            line = f"{f'「{m.id}」':<7}{diff_str} {m.title}\n"
            search_result += line
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    yield event.chain_result(image_chain_from_text(search_result))


async def query_chart_handler(event: AstrMessageEvent):
    """id 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 匹配 id xxxxx 格式
    match = re.match(r'^id\s?([0-9]+)$', message_str, re.IGNORECASE)
    if not match:
        return  # 不匹配则不处理
    
    id = match.group(1)
    music = mai.total_list.by_id(id)
    if not music:
        yield event.plain_result(f'未找到ID为「{id}」的乐曲')
        return
    
    pic = await draw_music_info(music, event.get_sender_id())
    chain = convert_message_segment_to_chain(pic)
    yield event.chain_result(chain)
