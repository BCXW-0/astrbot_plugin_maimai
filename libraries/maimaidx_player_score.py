import time
import traceback
from typing import Callable, Dict, List, Optional, Tuple, Union

import pyecharts.options as opts
from pyecharts.charts import Pie

from .. import *
from .image import *
from .maimai_best_50 import ScoreBaseImage, computeRa
from .maimaidx_api_data import *
from .maimaidx_model import PlanInfo, PlayInfoDefault, PlayInfoDev, RaMusic
from .maimaidx_music import Music, mai
from .tool import run_chrome_to_base64

Filter = Tuple[
    List[PlayInfoDefault],
    List[PlayInfoDefault],
    List[PlayInfoDefault],
    List[PlayInfoDefault],
    List[PlayInfoDefault]
]
Condition = Callable[[PlayInfoDefault], bool]


async def music_global_data(music: Music, level_index: int) -> MessageSegment:
    """
    绘制曲目游玩详情
    
    Params:
        `music`: :class:Music
        `level_index`: 难度
    Returns:
        `MessageSegment`
    """
    stats = music.stats[level_index]
    fc_data_pair = [list(z) for z in zip([c.upper() if c else 'Not FC' for c in [''] + comboRank], stats.fc_dist)]
    acc_data_pair = [list(z) for z in zip([s.upper() for s in scoreRank], stats.dist)]

    initopts = opts.InitOpts(width='1000px', height='800px', bg_color='#fff', js_host='./')
    labelopts = opts.LabelOpts(
        position='outside',
        formatter='{a|{a}}{abg|}\n{hr|}\n {b|{b}: }{c}  {per|{d}%}  ',
        background_color='#eee',
        border_color='#aaa',
        border_width=1,
        border_radius=4,
        rich={
            'a': {'color': '#999', 'lineHeight': 22, 'align': 'center'},
            'abg': {
                'backgroundColor': '#e3e3e3',
                'width': '100%',
                'align': 'right',
                'height': 22,
                'borderRadius': [4, 4, 0, 0],
            },
            'hr': {
                'borderColor': '#aaa',
                'width': '100%',
                'borderWidth': 0.5,
                'height': 0,
            },
            'b': {'fontSize': 16, 'lineHeight': 33},
            'per': {
                'color': '#eee',
                'backgroundColor': '#334455',
                'padding': [2, 4],
                'borderRadius': 2,
            },
        },
    )
    titleopts = opts.TitleOpts(
        title=f'{music.id} {music.title} 「{diffs[level_index]}」',
        pos_left='center',
        pos_top='20',
        title_textstyle_opts=opts.TextStyleOpts(color='#2c343c'),
    )
    legendopts = opts.LegendOpts(pos_left=15, pos_top=10, orient='vertical')

    pie = Pie(initopts)
    pie.add('全连等级', fc_data_pair, radius=[0, '30%'], label_opts=labelopts)
    pie.add('达成率等级', acc_data_pair, radius=['50%', '70%'], is_clockwise=True, label_opts=labelopts)
    pie.set_global_opts(title_opts=titleopts, legend_opts=legendopts)
    pie.set_series_opts(tooltip_opts=opts.TooltipOpts(trigger='item', formatter='{a} <br/>{b}: {c} ({d}%)'))
    pie.render(str(pie_html_file))
    base64 = await run_chrome_to_base64()

    return MessageSegment.image(base64)


class DrawScore(ScoreBaseImage):
    
    def __init__(self, image: Image.Image = None) -> None:
        if maiApi.config.saveinmem:
            ScoreBaseImage.ensure_loaded()
        super().__init__(image)
        self._im.alpha_composite(self.aurora_bg)
        self._im.alpha_composite(self.shines_bg, (34, 0))
        self._im.alpha_composite(self.rainbow_bg, (319, self._im.size[1] - 643))
        self._im.alpha_composite(self.rainbow_bottom_bg, (100, self._im.size[1] - 343))
        for h in range((self._im.size[1] // 358) + 1):
            self._im.alpha_composite(self.pattern_bg, (0, (358 + 7) * h))

    def whilepic(self, data: List[RaMusic], y: int = 200):
        """
        循环绘制谱面
        
        Params:
            `data`: `谱面数据`
            `y`: `Y轴偏移`
        """
        dy = 65
        x = 0
        for n, v in enumerate(data):
            if n % 20 == 0:
                x = 55
                y += dy if n != 0 else 0
            else:
                x += 65
            cover = Image.open(music_picture(v.id)).resize((55, 55))
            self._im.alpha_composite(cover, (x, y))
            self._im.alpha_composite(self.id_diff[int(v.lv)], (x, y + 45))
            self._tb.draw(x + 27, y + 50, 10, v.id, self.t_color[int(v.lv)], 'mm')
    
    def draw_plan(
        self,
        completed: Union[List[PlayInfoDefault], List[PlayInfoDev]],
        completed_y: int,
        unfinished: Union[List[PlayInfoDefault], List[PlayInfoDev]],
        unfinished_y: int,
        notstarted: List[RaMusic],
        plan: str,
        completed_len: int
    ) -> Image.Image:
        """
        绘制进度表
        
        Params:
            `completed`: `已完成谱面`
            `completed_y`: `已完成谱面高度`
            `unfinished`: `未完成谱面`
            `unfinished_y`: `未完成谱面高度`
            `notstarted`: `未游玩谱面`
            `plan`: `目标`
            `completed_len`: `已完成谱面数量`
        Returns:
            `Image.Image`
        """
        max = len(completed + unfinished + notstarted)

        self._im.alpha_composite(self.title_lengthen_bg, (475, 30))
        self._im.alpha_composite(self.title_lengthen_bg, (475, 30 + completed_y))
        self._im.alpha_composite(self.title_lengthen_bg, (475, 30 + completed_y + unfinished_y))
        
        self._sy.draw(700, 77, 22, f'已完成谱面「{len(completed)}」个', self.text_color, 'mm')
        self._sy.draw(700, 77 + completed_y, 22, f'未完成谱面「{len(unfinished)}」个', self.text_color, 'mm')
        self._sy.draw(700, 77 + completed_y + unfinished_y, 22, f'未游玩谱面「{len(notstarted)}」个', self.text_color, 'mm')
        
        self.whiledraw(completed[:completed_len], True, 140)
        self.whiledraw(unfinished[:30], True, 140 + completed_y)
        self.whilepic(notstarted[:100], 140 + completed_y + unfinished_y)

        self._im.alpha_composite(self.design_bg, (200, self._im.size[1] - 113))
        pagemsg = f'共计「{max}」个谱面，剩余「{len(unfinished + notstarted)}」个谱面未完成「{plan.upper()}」'
        self._sy.draw(700, self._im.size[1] - 70, 25, pagemsg, self.text_color, 'mm')
        return self._im

    def draw_category(
        self, 
        category: str, 
        data: Union[List[PlayInfoDefault], List[PlayInfoDev], List[RaMusic]],
        page: int = 1, 
        end_page: int = 1
    ) -> Image.Image:
        """
        绘制指定进度表
        
        Params:
            `category`: `类别`
            `data`: `数据`
            `page`: `页数`
            `end_page`: `总页数`
        Returns:
            `Image.Image`
        """
        lendata = len(data)
        newdata = data[(page - 1) * 80: page * 80]
        self._im.alpha_composite(self.title_lengthen_bg, (475, 30))
        if category == 'completed' or category == 'unfinished':
            txt = '已完成' if category == 'completed' else '未完成'
            self._sy.draw(700, 77, 28, f'{txt}谱面', self.text_color, 'mm')
            self.whiledraw(newdata, True, 140)
            self._im.alpha_composite(self.design_bg, (200, self._im.size[1] - 113))
            
            pagemsg = f'{txt}谱面共计「{lendata}」个，'
            pagemsg += f'展示第「{(page - 1) * 80 + 1}-{80 * (page - 1) + len(newdata)}」个，'
            pagemsg += f'当前第「{page} / {end_page}」页'
            self._sy.draw(700, self._im.size[1] - 70, 25, pagemsg, self.text_color, 'mm')
        else:
            self._sy.draw(700, 77, 28, '未游玩谱面', self.text_color, 'mm')
            self.whilepic(data)
            self._im.alpha_composite(self.design_bg, (200, self._im.size[1] - 113))
            self._sy.draw(700, self._im.size[1] - 70, 25, f'未游玩谱面共计「{len(data)}」个', self.text_color, 'mm')
        return self._im
    
    def draw_scorelist(
        self, 
        rating: Union[str, float], 
        data: Union[List[PlayInfoDefault], List[PlayInfoDev]], 
        page: int = 1, 
        end_page: int = 1
    ) -> Image.Image:
        """
        绘制分数列表
        
        Params:
            `rating`: `定数`
            `data`: `数据`
            `page`: `页数`
            `end_page`: `总页数`
        Returns:
            `Image.Image`
        """
        lendata = len(data)
        newdata = data[(page - 1) * 80: page * 80]
        r = len(newdata) // 20 + (0 if len(newdata) % 20 == 0 else 1)
        for n in range(r):
            y = (109 * 4 + 140) * n
            self._im.alpha_composite(self.title_lengthen_bg, (475, 30 + y))
            start = (20 * n + 1) + 80 * (page - 1)
            self._sy.draw(700, 77 + y, 28, f'No.{start}- No.{start + len(newdata[n * 20: (n + 1) * 20]) - 1}', self.text_color, 'mm')
            self.whiledraw(newdata[n * 20: (n + 1) * 20], True, 140 + y)
        self._im.alpha_composite(self.design_bg, (200, self._im.size[1] - 113))
        
        pagemsg = f'「{rating}」共计「{lendata}」个成绩，'
        pagemsg += f'展示第「{(page - 1) * 80 + 1}-{80 * (page - 1) + len(newdata)}」个，'
        pagemsg += f'当前第「{page} / {end_page}」页'
        self._sy.draw(700, self._im.size[1] - 70, 25, pagemsg, self.text_color, 'mm')
        return self._im


def plate_message(
    result: str, 
    plan: str, 
    music_list: List[PlayInfoDefault], 
    played: List[Tuple[int, int]]
) -> Union[MessageSegment, str]:
    """
    Params:
        `result`: 结果
        `plan`: 目标
        `music_list`: 谱面列表
        `played`: 已游玩谱面
    Returns:
        `Union[MessageSegment, str]`
    """
    for n, m in enumerate(music_list):
        self_record = ''
        if (m.song_id, m.level_index) in played:
            if plan in ['将', '者']:
                self_record = f'{m.achievements}%'
            if plan in ['極', '极', '神']:
                self_record = m.fc
            if plan in '舞舞':
                self_record = m.fs
        result += f'No.{n + 1:02d} {f"「{m.song_id}」":>7} {f"「{diffs[m.level_index]}」":>11} 「{m.ds}」 {m.title}  {self_record}\n'
    if len(music_list) > 10:
        result = MessageSegment.image(image_to_base64(text_to_image(result.strip())))
    return result


async def player_plate_data(
    qqid: int, 
    username: str, 
    version: str, 
    plan: str
) -> Union[MessageSegment, str]:
    """
    查看牌子进度
    
    Params:
        `qqid`: 用户QQ
        `username`: 查分器用户名
        `version`: 版本
        `plan`: 目标
    Returns:
        `Union[MessageSegment, str]`
    """
    if version in platecn:
        version = platecn[version]
    ver, _ver = version_map.get(version, ([plate_to_dx_version.get(version)], version))
    
    try:
        verlist = await maiApi.query_user_plate(qqid=qqid, username=username, version=ver)
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
    ) as e:
        return str(e)
    
    if plan in ['将', '者']:
        achievement = 100 if plan == '将' else 80
        callable_: Condition = lambda x: x.achievements < achievement
    elif plan in ['極', '极']:
        callable_: Condition = lambda x: not x.fc
    elif plan == '舞舞':
        callable_: Condition = lambda x: x.fs not in ['fsd', 'fsdp']
    elif plan  == '神':
        callable_: Condition = lambda x: x.fc not in ['ap', 'app']
    else:
        raise ValueError
    
    unfinished_model_list: Filter = ([], [], [], [], [])
    unfinished: List[Tuple[int, int]] = []
    played: List[Tuple[int, int]] = []
    remaster: List[int] = []
    
    # 已游玩未完成曲目
    plate_id_list = mai.total_plate_id_list[_ver]
    if version in ['舞', '霸']:
        remaster = mai.total_plate_id_list['舞ReMASTER']
        for music in verlist:
            if music.song_id not in plate_id_list:
                continue
            if music.level_index == 4 and music.song_id not in remaster:
                continue
            if callable_(music):
                unfinished.append((music.song_id, music.level_index))
            played.append((music.song_id, music.level_index))
    else:
        for music in verlist:
            if music.song_id not in plate_id_list:
                continue
            if callable_(music):
                unfinished.append((music.song_id, music.level_index))
            played.append((music.song_id, music.level_index))
    
    # 未游玩未完成曲目
    for music in mai.total_list:
        if int(music.id) not in plate_id_list:
            continue
        info = PlayInfoDefault(
            achievements=0,
            level='',
            level_index=0,
            title=music.title,
            type=music.type,
            id=int(music.id)
        )
        range_ = range(5 if version in ['舞', '霸'] and int(music.id) in remaster else 4)
        for level_index in range_:
            if (m := (info.song_id, level_index)) not in played or m in unfinished:
                _info = info.model_copy()
                _info.level = music.level[level_index]
                _info.ds = music.ds[level_index]
                _info.level_index = level_index
                unfinished_model_list[level_index].append(_info)

    basic, advanced, expert, master, re_master = unfinished_model_list
    
    ramain = basic + advanced + expert + master + re_master
    ramain.sort(key=lambda x: x.ds, reverse=True)
    difficult = [_m for _m in ramain if _m.ds > 13.6]

    appellation = username if username else '您'
    result = dedent(f'''\
        {appellation}的「{version}{plan}」剩余进度如下：
        Basic剩余「{len(basic)}」首
        Advanced剩余「{len(advanced)}」首
        Expert剩余「{len(expert)}」首
        Master剩余「{len(master)}」首
    ''')
    if version in ['舞', '霸']:
        result += f'Re:Master剩余「{len(re_master)}」首\n'
    
    if len(difficult) > 0:
        if len(difficult) < 60:
            result += '剩余定数大于13.6的曲目：\n'
            result = plate_message(result, plan, difficult, played)
        else:
            result += f'还有{len(difficult)}首大于13.6定数的曲目，加油推分捏！\n'
    elif len(ramain) > 0:
        if len(ramain) < 60:
            result += '剩余曲目：\n'
            result = plate_message(result, plan, ramain, played)
        else:
            result += '已经没有定数大于13.6的曲目了，加油清谱捏！\n'
    else:
        result = f'已经没有剩余的的曲目了，恭喜{appellation}完成「{version}{plan}」！'
    return result


async def level_process_data(
    qqid: int, 
    username: Optional[str], 
    level: str, 
    plan: str, 
    category: str = 'default', 
    page: int = 1
) -> Union[MessageSegment, str]:
    """
    查看谱面等级进度

    Params:
        `qqid`: 用户QQ
        `username`: 查分器用户名
        `level`: 定数
        `plan`: 评价等级
    Returns:
        `Union[MessageSegment, str]`
    """
    try:
        if maiApi.token:
            devobj = await maiApi.query_user_get_dev(qqid=qqid, username=username)
            obj = devobj.records
        else:
            version = list(set(_v for _v in list(plate_to_dx_version.values())))
            obj = await maiApi.query_user_plate(qqid=qqid, username=username, version=version)
        music = mai.total_list.by_plan(level)

        planlist = [0, 0, 0]
        plannum = 0
        if plan.lower() in scoreRank:
            plannum = 0
            planlist[0] = achievementList[scoreRank.index(plan.lower()) - 1]
        elif plan.lower() in comboRank:
            plannum = 1
            planlist[1] = comboRank.index(plan.lower())
        elif plan.lower() in syncRank:
            plannum = 2
            planlist[2] = syncRank.index(plan.lower())
        else:
            raise
        
        plan_value = planlist[plannum]
        
        def is_completed(plannum: int, _d: Union[PlayInfoDefault, PlayInfoDev]) -> bool:
            if plannum == 0:
                return _d.achievements >= plan_value
            elif plannum == 1:
                return bool(_d.fc and combo_rank.index(_d.fc) >= plan_value)
            elif plannum == 2:
                return bool(_d.fs and (
                    sync_rank.index(_d.fs) >= plan_value 
                    if _d.fs in sync_rank else sync_rank_p.index(_d.fs) >= plan_value
                ))
            return False
        
        for _d in obj:
            if isinstance(_d, PlayInfoDefault):
                _m = mai.total_list.by_id(_d.song_id)
                ds: float = _m.ds[_d.level_index]
                a: float = _d.achievements
                ra, rate = computeRa(ds, a, israte=True)
                _d.ra = ra
                _d.rate = rate
            if (song_id := str(_d.song_id)) in music and _d.level == level:
                if isinstance(music[song_id], Dict):
                    music[song_id][_d.level_index] = PlanInfo()
                    _p = music[song_id][_d.level_index]
                else:
                    music[song_id] = PlanInfo()
                    _p = music[song_id]
                
                if is_completed(plannum, _d):
                    _p.completed = _d
                else:
                    _p.unfinished = _d

        notplayed: List[RaMusic] = []
        completed: Union[List[PlayInfoDefault], List[PlayInfoDev]] = []
        unfinished: Union[List[PlayInfoDefault], List[PlayInfoDev]] = []
        for m in music:
            play = music[m]
            if isinstance(play, Dict):
                for index, p in play.items():
                    if isinstance(p, RaMusic):
                        notplayed.append(p)
                    elif p.completed:
                        completed.append(p.completed)
                    elif p.unfinished:
                        unfinished.append(p.unfinished)
            elif isinstance(play, PlanInfo):
                if play.completed:
                    completed.append(play.completed)
                if play.unfinished:
                    unfinished.append(play.unfinished)
            else:
                notplayed.append(play)
        completed.sort(key=lambda x: x.achievements if plannum == 0 else x.fc if plannum == 1 else x.fs, reverse=True)
        unfinished.sort(key=lambda x: x.achievements if plannum == 0 else x.fc if plannum == 1 else x.fs, reverse=True)
        notplayed.sort(key=lambda x: x.ds, reverse=True)

        if category == 'default':
            completed_len = 60 if len(unfinished) == 0 and len(notplayed) == 0 else 30
            clen = len(completed[:completed_len])
            completed_y = (clen // 5 + (0 if clen % 5 == 0 else 1)) * 109 + 140
            ulen = len(unfinished[:30])
            unfinished_y = (ulen // 5 + (0 if ulen % 5 == 0 else 1)) * 109 + 140
            nlen = len(notplayed[:100])
            notstarted_y = (nlen // 20 + (0 if nlen % 20 == 0 else 1)) * 65 + 140
            image = tricolor_gradient(1400, 150 + completed_y + unfinished_y + notstarted_y)
            dp = DrawScore(image)
            im = dp.draw_plan(completed, completed_y, unfinished, unfinished_y, notplayed, plan, completed_len)
        elif category == 'completed' or category == 'unfinished':
            data = completed if category == 'completed' else unfinished
            lendata = len(data)
            end_page_num = lendata // 80 + 1
            if page > end_page_num:
                return f'超出页数，您的成绩共计「{end_page_num}」页，请重新输入'
            topage = len(data[(page - 1) * 80: page * 80])
            plc = (topage // 5 + (0 if topage % 5 == 0 else 1)) * 109
            image = tricolor_gradient(1400, 240 + plc + 120)
            dp = DrawScore(image)
            im = dp.draw_category(category, data, page, end_page_num)
        else:
            lennotstarted = len(notplayed)
            pln = (lennotstarted // 20 + (0 if lennotstarted % 20 == 0 else 1)) * 65
            image = tricolor_gradient(1400, 240 + pln + 120)
            dp = DrawScore(image)
            im = dp.draw_category(category, notplayed)
        
        msg = MessageSegment.image(image_to_base64(im))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
    ) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


async def level_achievement_list_data(
    qqid: int, 
    username: Optional[str], 
    rating: Union[str, float], 
    page: int = 1
) -> Union[MessageSegment, str]:
    """
    查看分数列表

    Params:
        `qqid` : 用户QQ
        `username` : 查分器用户名
        `rating` : 定数
        `page` : 页数
        `nickname` : 用户昵称
    Returns:
        `Union[MessageSegment, str]
    """
    try:
        data: Union[List[PlayInfoDefault], List[PlayInfoDev]] = []
        if maiApi.token:
            obj = await maiApi.query_user_get_dev(qqid=qqid, username=username)
            data = obj.records
        else:
            version = list(set(_v for _v in list(plate_to_dx_version.values())))
            obj = await maiApi.query_user_plate(qqid=qqid, username=username, version=version)
            for _d in obj:
                music = mai.total_list.by_id(_d.song_id)
                _d.ds = music.ds[_d.level_index]
                _d.ra, _d.rate = computeRa(_d.ds, _d.achievements, israte=True)
            data = obj

        if isinstance(rating, str):
            newdata = sorted(list(filter(lambda x: x.level == rating, data)), key=lambda z: z.achievements, reverse=True)
        else:
            newdata = sorted(list(filter(lambda x: x.ds == rating, data)), key=lambda z: z.achievements, reverse=True)
        
        lendata = len(newdata)
        end_page_num = lendata // 80 + 1
        if page > end_page_num:
            return f'超出页数，您的成绩共计「{end_page_num}」页，请重新输入'
        
        topage = len(newdata[(page - 1) * 80: page * 80])
        line = topage // 5 + (0 if topage % 5 == 0 else 1)
        if page < end_page_num:
            plc = line * 109 + 140 * 4
        elif topage <= 20:
            plc = 4 * 109 + 140
        elif topage <= 40:
            plc = line * 109 + 140 * 2
        elif topage <= 60:
            plc = line * 109 + 140 * 3
        else:
            plc = line * 109 + 140 * 4
        
        image = tricolor_gradient(1400, 150 + plc)

        sc = DrawScore(image)
        im = sc.draw_scorelist(rating, newdata, page, end_page_num)
        msg = MessageSegment.image(image_to_base64(im))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
    ) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


async def rating_ranking_data(name: str, page: int) -> Union[MessageSegment, str]:
    """
    查看查分器排行榜
    
    Params:
        `name`: 指定用户名
        `page`: 页数
    Returns:
        `Union[MessageSegment, str]`
    """
    try:
        rank_data = await maiApi.rating_ranking()

        _time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if name != '':
            if name in [r.username.lower() for r in rank_data]:
                rank_index = [r.username.lower() for r in rank_data].index(name) + 1
                nickname = rank_data[rank_index - 1].username
                data = f'截止至 {_time}\n玩家 {nickname} 在查分器已注册用户ra排行第{rank_index}'
            else:
                data = '未找到该玩家'
        else:
            user_num = len(rank_data)
            msg = f'截止至 {_time}，查分器已注册用户ra排行：\n'
            if page * 50 > user_num:
                page = user_num // 50 + 1
            end = page * 50 if page * 50 < user_num else user_num
            for i, ranker in enumerate(rank_data[(page - 1) * 50:end]):
                msg += f'No.{i + 1 + (page - 1) * 50:02d}.「{ranker.ra}」 {ranker.username} \n'
            msg += f'第「{page}」页，共「{user_num // 50 + 1}」页'
            data = MessageSegment.image(image_to_base64(text_to_image(msg.strip())))
    except Exception as e:
        log.error(traceback.format_exc())
        data = f'未知错误：{type(e)}\n请联系Bot管理员'
    return data
