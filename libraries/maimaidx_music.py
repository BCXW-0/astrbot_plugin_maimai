import asyncio
import json
import random
import traceback
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from .. import *
from .image import image_to_base64, music_picture
from .maimaidx_api_data import maiApi
from .maimaidx_error import *
from .maimaidx_model import *
from .tool import openfile, writefile


def cross(
    checker: Union[List[str], List[float]], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]], 
    diff: List[int]
) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    if isinstance(elem, List):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if __e in elem:
                diff_ret.append(_j)
                ret = True
    elif isinstance(elem, Tuple):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem[0] <= __e <= elem[1]:
                diff_ret.append(_j)
                ret = True
    else:
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem == __e:
                diff_ret.append(_j)
                ret = True
    return ret, diff_ret


def in_or_equal(
    checker: Union[str, int], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]]
) -> bool:
    if elem is Ellipsis:
        return True
    if isinstance(elem, List):
        return checker in elem
    elif isinstance(elem, Tuple):
        return elem[0] <= checker <= elem[1]
    else:
        return checker == elem


class MusicList(List[Music]):
    def __init__(self, iterable=()) -> None:
        super().__init__(iterable)
        self._id_index: Dict[str, Music] = {}
        self._title_index: Dict[str, Music] = {}
        self._index_dirty = True

    def append(self, item: Music) -> None:
        super().append(item)
        self._index_dirty = True

    def extend(self, iterable) -> None:
        super().extend(iterable)
        self._index_dirty = True

    def insert(self, index: int, item: Music) -> None:
        super().insert(index, item)
        self._index_dirty = True

    def pop(self, index: int = -1) -> Music:
        item = super().pop(index)
        self._index_dirty = True
        return item

    def remove(self, item: Music) -> None:
        super().remove(item)
        self._index_dirty = True

    def clear(self) -> None:
        super().clear()
        self._index_dirty = True

    def sort(self, *args, **kwargs) -> None:
        super().sort(*args, **kwargs)
        self._index_dirty = True

    def reverse(self) -> None:
        super().reverse()
        self._index_dirty = True

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self._index_dirty = True

    def __delitem__(self, key) -> None:
        super().__delitem__(key)
        self._index_dirty = True

    def __iadd__(self, value):
        result = super().__iadd__(value)
        self._index_dirty = True
        return result

    def _ensure_index(self) -> None:
        if not self._index_dirty:
            return
        self._id_index = {}
        self._title_index = {}
        for music in self:
            self._id_index.setdefault(str(music.id), music)
            self._title_index.setdefault(music.title, music)
        self._index_dirty = False
    
    def by_id(self, music_id: Union[str, int]) -> Optional[Music]:
        self._ensure_index()
        return self._id_index.get(str(music_id))

    def by_title(self, music_title: str) -> Optional[Music]:
        self._ensure_index()
        return self._title_index.get(music_title)
    
    def by_plan(
        self, 
        level: str
    ) -> Dict[str, Union[PlanInfo, RaMusic, Dict[int, Union[PlanInfo, RaMusic]]]]:
        lv = defaultdict(dict)
        
        def create_ra_music(music: Music, index: int) -> RaMusic:
            return RaMusic(
                id=music.id, 
                ds=music.ds[index], 
                lv=str(index), 
                lvp=music.level[index], 
                type=music.type
            )
        
        for music in self:
            if level not in music.level:
                continue
            if int(music.id) >= 100000:
                continue
            if music.level.count(level) > 1: # 同曲有相同等级
                lv[music.id] = { 
                    index: create_ra_music(music, index)
                    for index, _lv in enumerate(music.level) 
                    if _lv == level 
                }
            else:
                index = music.level.index(level)
                lv[music.id] = create_ra_music(music, index)
        return dict(lv)
    
    def by_level_list(self) -> Dict[str, Dict[str, List[RaMusic]]]:
        
        def level_range(lv: str) -> range:
            if lv == '15':
                return range(1)
            if lv.endswith('+'):
                return range(9, 5, -1)
            return range(9, -1, -1) if int(lv) <= 5 else range(5, -1, -1)
        
        _level = {
            lv: {f"{lv.rstrip('+')}.{i}": [] for i in level_range(lv)} for lv in levelList
        }
        for music in self:
            if int(music.id) >= 100000:
                continue
            for index, ds in enumerate(music.ds):
                if ds < 7:
                    continue
                ra = RaMusic(
                    id=music.id,
                    ds=ds,
                    lv=str(index),
                    lvp=music.level[index],
                    type=music.type
                )
                _level[music.level[index]][str(ds)].append(ra)
        return _level
    
    def by_id_list(self, music_id_list: List[int]) -> Optional[List[Music]]:
        musicList = []
        for music in self:
            if int(music.id) in music_id_list:
                musicList.append(music)
        return musicList
    
    def random(self) -> Music:
        return random.choice(self)

    def filter(
        self,
        *,
        level: Optional[Union[str, List[str]]] = ...,
        ds: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        title_search: Optional[str] = ...,
        artist_search: Optional[str] = ...,
        charter_search: Optional[str] = ...,
        genre: Optional[Union[str, List[str]]] = ...,
        bpm: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        type: Optional[Union[str, List[str]]] = ...,
        diff: List[int] = ...,
        version: Union[str, List[str]] = ...
    ) -> 'MusicList':
        new_list = MusicList()
        for music in self:
            diff2 = diff
            music = deepcopy(music)
            ret, diff2 = cross(music.level, level, diff2)
            if not ret:
                continue
            ret, diff2 = cross(music.ds, ds, diff2)
            if not ret:
                continue
            ret, diff2 = search_charts(music.charts, charter_search, diff2)
            if not ret:
                continue
            if not in_or_equal(music.basic_info.genre, genre):
                continue
            if not in_or_equal(music.type, type):
                continue
            if not in_or_equal(music.basic_info.bpm, bpm):
                continue
            if not in_or_equal(music.basic_info.version, version):
                continue
            if title_search is not Ellipsis and title_search.lower() not in music.title.lower():
                continue
            if artist_search is not Ellipsis and artist_search.lower() not in music.basic_info.artist.lower():
                continue
            music.diff = diff2
            new_list.append(music)
        return new_list


def search_charts(checker: List[Chart], elem: str, diff: List[int]) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    for _j in (range(len(checker)) if diff is Ellipsis else diff):
        if elem.lower() in checker[_j].charter.lower():
            diff_ret.append(_j)
            ret = True
    return ret, diff_ret


dataerror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/music_data" 
    将内容保存为 "music_data.json" 存放在 "static" 目录下并重启bot
''').strip()
charterror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
    将内容保存为 "music_chart.json" 存放在 "static" 目录下并重启bot
''').strip()
aliaserror = dedent('''
    本地暂存别名文件为空，请自行使用浏览器访问 "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias" 
    获取别名数据并保存在 "static/music_alias.json" 文件中并重启bot
''').strip()


class AliasList(List[Alias]):

    def by_id(self, music_id: Union[str, int]) -> Optional[List[Alias]]:
        alias_music = []
        for music in self:
            if music.SongID == int(music_id):
                alias_music.append(music)
        return alias_music
    
    def by_alias(self, music_alias: str) -> Optional[List[Alias]]:
        alias_list = []
        for music in self:
            if music_alias in music.Alias:
                alias_list.append(music)
        return alias_list


def _build_alias_list(
    alias_data: List[Dict[str, Union[int, str, List[str]]]],
    local_alias_data: Dict[str, List[str]],
) -> AliasList:
    total_alias_list = AliasList()
    for _a in filter(lambda x: mai.total_list.by_id(x['SongID']), alias_data):
        if (song_id := str(_a['SongID'])) in local_alias_data:
            _a['Alias'].extend(local_alias_data[song_id])
        total_alias_list.append(Alias.model_validate(_a))
    return total_alias_list


async def get_music_alias_list_from_cache() -> AliasList:
    """从本地暂存文件加载所有别名。"""
    if local_alias_file.exists():
        local_alias_data = await openfile(local_alias_file)
    else:
        local_alias_data = {}
    try:
        alias_data = await openfile(alias_file)
    except FileNotFoundError:
        log.error(aliaserror)
        raise
    if not alias_data:
        log.error(aliaserror)
        raise ValueError
    return _build_alias_list(alias_data, local_alias_data)


async def get_music_alias_list() -> AliasList:
    """获取所有别名"""
    if local_alias_file.exists():
        local_alias_data = await openfile(local_alias_file)
    else:
        local_alias_data = {}
    alias_data: List[Dict[str, Union[int, str, List[str]]]] = []
    try:
        alias_data = await maiApi.get_alias()
        await writefile(alias_file, alias_data)
    except asyncio.exceptions.TimeoutError:
        log.error('获取别名超时。已切换至本地暂存文件')
        alias_data = await openfile(alias_file)
        if not alias_data:
            log.error(aliaserror)
            raise ValueError
    except ServerError as e:
        log.error(e)
        alias_data = await openfile(alias_file)
    except UnknownError:
        log.error('获取所有曲目别名信息错误，请检查网络环境。已切换至本地暂存文件')
        alias_data = await openfile(alias_file)
        if not alias_data:
            log.error(aliaserror)
            raise ValueError

    return _build_alias_list(alias_data, local_alias_data)


def _build_music_list(music_data: List[Dict], chart_stats: Dict) -> MusicList:
    total_list = MusicList()
    for music in music_data:
        if music['id'] in chart_stats['charts']:
            _stats = [
                _data if _data else None
                for _data in chart_stats['charts'][music['id']]
            ] if {} in chart_stats['charts'][music['id']] else \
            chart_stats['charts'][music['id']]
        else:
            _stats = None
        total_list.append(Music(stats=_stats, **music))
    return total_list


async def get_music_list_from_cache() -> MusicList:
    """从本地暂存文件加载所有曲目数据。"""
    try:
        music_data = await openfile(music_file)
    except FileNotFoundError:
        log.error(dataerror)
        raise

    try:
        chart_stats = await openfile(chart_file)
    except FileNotFoundError:
        log.error(charterror)
        raise

    return _build_music_list(music_data, chart_stats)


async def get_music_list() -> MusicList:
    """获取所有数据"""
    # MusicData
    try:
        try: 
            music_data = await maiApi.music_data()
            await writefile(music_file, music_data)
        except asyncio.exceptions.TimeoutError:
            log.error('maimaiDX曲库数据获取失败，请检查网络环境。已切换至本地暂存文件')
            music_data = await openfile(music_file)
    except FileNotFoundError:
        log.error(dataerror)
        raise FileNotFoundError
    
    # ChartStats
    try:
        try:
            chart_stats = await maiApi.chart_stats()
            await writefile(chart_file, chart_stats)
        except asyncio.exceptions.TimeoutError:
            log.error('maimaiDX数据获取错误，请检查网络环境，已切换至本地暂存文件')
            chart_stats = await openfile(chart_file)
    except FileNotFoundError:
        log.error(charterror)
        raise FileNotFoundError

    return _build_music_list(music_data, chart_stats)


class MaiMusic:
    
    total_list: MusicList
    """曲目数据"""
    total_alias_list: AliasList
    """别名数据"""
    total_plate_id_list: Dict[str, List[int]]
    """牌子ID列表数据"""
    total_level_data: Dict[str, Dict[str, List[RaMusic]]]
    """等级列表数据"""
    hot_music_ids: List = []
    """游玩次数超过1w次的曲目数据"""
    guess_data: List[Music]
    """猜歌数据"""

    def __init__(self) -> None:
        """封装所有曲目信息以及猜歌数据，便于更新"""

    async def get_music(self) -> None:
        """获取所有曲目数据"""
        total_list = await get_music_list()
        total_level_data = total_list.by_level_list()
        self.total_list = total_list
        self.total_level_data = total_level_data

    async def get_music_alias(self) -> None:
        """获取所有曲目别名"""
        self.total_alias_list = await get_music_alias_list()

    async def load_local_cache(self) -> None:
        """优先用本地暂存数据恢复运行态，避免启动等待在线接口。"""
        total_list = await get_music_list_from_cache()
        total_level_data = total_list.by_level_list()
        self.total_list = total_list
        self.total_level_data = total_level_data
        try:
            self.total_alias_list = await get_music_alias_list_from_cache()
        except Exception as e:
            log.warning(f'本地别名缓存加载失败，稍后将尝试后台刷新: {type(e).__name__}')
        self.total_plate_id_list = self.build_plate_id_list_from_music()
        self.hot_music_ids = []
        self.guess()

    async def get_plate_json(self) -> None:
        """获取所有牌子数据"""
        try:
            total_plate_id_list = await maiApi.get_plate_json()
        except Exception:
            self.total_plate_id_list = self.build_plate_id_list_from_music()
            raise
        self.total_plate_id_list = total_plate_id_list

    def build_plate_id_list_from_music(self) -> Dict[str, List[int]]:
        """从已加载曲库生成牌子需求兜底数据。"""
        result: Dict[str, List[int]] = defaultdict(list)
        version_ids: Dict[str, List[int]] = defaultdict(list)
        remaster_ids: List[int] = []
        if not getattr(self, "total_list", None):
            return {}
        for music in self.total_list:
            try:
                music_id = int(music.id)
            except Exception:
                continue
            if music_id >= 100000:
                continue
            version = str(music.basic_info.version or "")
            if not version:
                continue
            if music_id not in version_ids[version]:
                version_ids[version].append(music_id)
            if len(music.ds) > 4 and len(music.charts) > 4:
                remaster_ids.append(music_id)
        for key, mapped_version in plate_to_dx_version.items():
            if key in platecn:
                continue
            result[key].extend(version_ids.get(mapped_version, []))
        for _, (versions, storage_key) in version_map.items():
            for version in versions:
                result[storage_key].extend(version_ids.get(version, []))
        dx_groups = {
            "熊&华": ["熊", "華"],
            "爽&煌": ["爽", "煌"],
            "宙&星": ["宙", "星"],
            "祭&祝": ["祭", "祝"],
            "双&宴": ["双", "宴"],
            "镜&彩": ["镜", "彩"],
        }
        for storage_key, keys in dx_groups.items():
            result[storage_key] = [
                music_id
                for key in keys
                for music_id in version_ids.get(plate_to_dx_version.get(key, ""), [])
            ]
        for key in list(result.keys()):
            result[key] = sorted(set(result[key]))
        result["舞ReMASTER"] = sorted(set(remaster_ids))
        return dict(result)

    def guess(self):
        """初始化猜歌数据"""
        for music in self.total_list:
            if music.stats:
                count = 0
                for stats in music.stats:
                    if stats:
                        count += stats.cnt if stats.cnt else 0
                if count > 10000:
                    self.hot_music_ids.append(music.id)
        self.guess_data = list(filter(lambda x: x.id in self.hot_music_ids, self.total_list))


mai = MaiMusic()


class Guess:
    
    Group: Dict[str, Union[GuessDefaultData, GuessPicData]] = {}  # 使用字符串类型作为键，group_id 在 AstrBotMessage 中是字符串
    switch: GuessSwitch

    def __init__(self) -> None:
        """猜歌类"""
        if not guess_file.exists():
            self.switch = GuessSwitch()
        else:
            try:
                with open(guess_file, 'r', encoding='utf-8') as f:
                    self.switch = GuessSwitch.model_validate(json.load(f))
            except Exception as e:
                log.warning(f'读取猜歌群开关文件失败，使用默认配置: {type(e).__name__}: {e}')
                self.switch = GuessSwitch()
        # 清理数据，确保 enable 和 disable 列表中的值都是字符串类型（兼容旧数据，自动转换）
        try:
            self.switch.enable = [str(x) for x in self.switch.enable if x is not None]
        except (ValueError, TypeError):
            self.switch.enable = []
        try:
            self.switch.disable = [str(x) for x in self.switch.disable if x is not None]
        except (ValueError, TypeError):
            self.switch.disable = []
    
    def start(self, group_id: str):
        """开始猜歌"""
        self.Group[group_id] = self.guessData()

    def startpic(self, group_id: str):
        """开始猜曲绘"""
        self.Group[group_id] = self.guesspicdata()
        
    def calculate_frequency_weights(self, image: Image.Image) -> np.ndarray:
        """
        计算图像的频率权重，用于在图像中选择裁剪区域
        
        Params:
            `image`: PIL.Image.Image, 输入图像
        Returns:
            `np.ndarray` 频率权重矩阵
        """
        gray_image = np.array(image.convert('L'))
        freq = np.fft.fft2(gray_image)
        freq_shift = np.fft.fftshift(freq)
        magnitude = np.abs(freq_shift)
        normalized_magnitude = magnitude / magnitude.max()
        weights = normalized_magnitude ** 2
        return weights
    
    def select_crop_region(
        self, 
        weights: np.ndarray, 
        crop_width: int, 
        crop_height: int, 
        top_p: int
    ) -> Tuple[int, int]:
        h, w = weights.shape
        valid_regions = weights[:h - crop_height + 1, :w - crop_width + 1]
        flattened_weights = valid_regions.flatten()
        threshold = np.percentile(flattened_weights, top_p)
        valid_indices = np.where(flattened_weights >= threshold)[0]
        probabilities = flattened_weights[valid_indices]
        probabilities /= probabilities.sum()
        chosen_index = np.random.choice(valid_indices, p=probabilities)
        top_left_y = chosen_index // valid_regions.shape[1]
        top_left_x = chosen_index % valid_regions.shape[1]
        return top_left_x, top_left_y

    def pic(self, music: Music) -> Image.Image:
        """裁切曲绘"""
        im = Image.open(music_picture(music.id))
        w, h = im.size
        weights = self.calculate_frequency_weights(im)
        scale = random.uniform(0.15, 0.4)  # 裁剪尺寸范围 可在此修改
        w2, h2 = int(w * scale), int(h * scale)
        top_p = min(1.3 - np.power(scale, 0.4), 0.95) * 100
        x, y = self.select_crop_region(weights, w2, h2, top_p)
        im = im.crop((x, y, x + w2, y + h2))
        return im

    def guesspicdata(self) -> GuessPicData:
        """猜曲绘数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        pic = self.pic(music)
        answer = [str(music.id), music.title]
        return GuessPicData(music=music, img=image_to_base64(pic), answer=answer, end=False)

    def guessData(self) -> GuessDefaultData:
        """猜歌数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        guess_options = random.sample([
            f'的 Expert 难度是 {music.level[2]}',
            f'的 Master 难度是 {music.level[3]}',
            f'的分类是 {music.basic_info.genre}',
            f'的版本是 {music.basic_info.version}',
            f'的艺术家是 {music.basic_info.artist}',
            f'{"不" if music.type == "SD" else ""}是 DX 谱面',
            f'{"没" if len(music.ds) == 4 else ""}有白谱',
            f'的 BPM 是 {music.basic_info.bpm}'
        ], 6)
        pic = self.pic(music)
        return GuessDefaultData(
            music=music, 
            img=image_to_base64(pic), 
            answer=[str(music.id), music.title], 
            end=False, 
            options=guess_options
        )

    def end(self, group_id: str):
        """结束猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        if group_id in self.Group:
            del self.Group[group_id]

    async def on(self, group_id: str) -> str:
        """开启猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        # 清理 enable 列表，确保所有值都是字符串类型（兼容旧数据）
        self.switch.enable = [str(x) for x in self.switch.enable if x is not None]
        if group_id not in self.switch.enable:
            self.switch.enable.append(group_id)
        # 清理 disable 列表，确保所有值都是字符串类型
        self.switch.disable = [str(x) for x in self.switch.disable if x is not None]
        if group_id in self.switch.disable:
            self.switch.disable.remove(group_id)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已开启'

    async def off(self, group_id: str) -> str:
        """关闭猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        if group_id not in self.switch.disable:
            self.switch.disable.append(group_id)
        if group_id in self.switch.enable:
            self.switch.enable.remove(group_id)
        if group_id in self.Group:
            self.end(group_id)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已关闭'


guess = Guess()
