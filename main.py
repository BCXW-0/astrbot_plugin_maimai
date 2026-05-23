from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.star import Context, Star, register
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import traceback
import json

from . import log, ratingdir, platedir, plate_to_dx_version, platecn, static, user_tokens_json, arcade_credentials_json, roast_persona_json, webui_config_overrides_json
from .libraries.maimai_best_50 import ScoreBaseImage
from .libraries.maimaidx_api_data import maiApi
from .libraries.maimaidx_music import mai
import sys

@register("astrbot_plugin_maimai", "Xiawan", "maimaiDX插件", "1.4.1")
class MaimaiDXPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.config.update(self._load_webui_config_overrides())
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        self._startup_tasks: set[asyncio.Task] = set()
        
        # 群组启用状态（存储禁用的群组ID）
        self.disabled_groups = set()  # 禁用插件的群组ID集合
        self.data_file = static / "disabled_groups.json"  # 数据文件路径
        
        # 从插件配置中读取 bot 名称并设置到 __init__.py
        bot_name = self.config.get("bot_name", "")
        enable_reply = bool(self.config.get("enable_reply", True))
        # 从插件配置中读取开发者 token（优先于 static/config.json），避免将 token 写入仓库文件
        plugin_token = str(self.config.get("maimaidxtoken", "") or "").strip()
        pkg_name = __name__.rsplit('.', 1)[0]  # 获取包名，例如 'myplugins.astrbot_plugin_maimai'
        if pkg_name in sys.modules:
            module = sys.modules[pkg_name]
            # 更新内部变量和公共变量
            setattr(module, '_BOTNAME', bot_name)
            setattr(module, 'BOTNAME', bot_name)
            setattr(module, '_ENABLE_REPLY', enable_reply)
            log.info(f'已设置 bot 名称: {bot_name}')
            log.info(f'引用回复（Reply）: {"开启" if enable_reply else "关闭"}')

        # 注入 token 并立即生效（不落盘）
        if plugin_token:
            maiApi.config.maimaidxtoken = plugin_token
        
        # 初始化用户 Import-Token 管理器
        from .libraries.user_token_manager import init_token_manager
        user_tokens_json.parent.mkdir(parents=True, exist_ok=True)
        init_token_manager(user_tokens_json)
        log.info('用户 Import-Token 管理器初始化完成')

        # 初始化用户机台凭据管理器
        from .libraries.arcade_credential_manager import init_arcade_credential_manager
        arcade_credentials_json.parent.mkdir(parents=True, exist_ok=True)
        init_arcade_credential_manager(arcade_credentials_json)
        log.info('用户机台凭据管理器初始化完成')

        from .libraries.roast_persona_manager import init_roast_persona_manager
        prompt_sample_limit = int(self.config.get('roast_persona_prompt_sample_limit', 120) or 120)
        self.roast_persona_manager = init_roast_persona_manager(roast_persona_json, max_prompt_samples=prompt_sample_limit)
        self.roast_persona_webui = None
        log.info('锐评人格管理器初始化完成')

        # 从 astrbot 配置文件中获取管理员ID列表
        # 根据文档：https://docs.astrbot.app/dev/star/plugin.html
        # 使用 context.get_config() 获取配置，字段名为 admins_id
        bot_config = context.get_config()
        admins = bot_config.get("admins_id", [])
        # 确保所有ID都是字符串格式
        self.superusers = [str(admin) for admin in admins] if admins else []
        
        if self.superusers:
            log.info(f'从 astrbot 配置中获取到管理员ID列表: {self.superusers}')
        else:
            log.warning('未找到任何管理员ID，某些需要管理员权限的命令可能无法使用')

    def _load_webui_config_overrides(self) -> dict:
        try:
            if not webui_config_overrides_json.exists():
                return {}
            with open(webui_config_overrides_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.error(f'加载 WebUI 配置覆盖失败: {e}')
            return {}

    async def initialize(self):
        """插件初始化，快速完成注册并将耗时资源加载移至后台。"""
        for step_name, step_func in [
            ("加载禁用群组列表", self._load_disabled_groups),
            ("设置配置", self._setup_configuration),
            ("设置定时任务", self._setup_schedulers),
        ]:
            try:
                step_func()
            except Exception as e:
                log.error(f'{step_name}失败: {e}')
                log.error(traceback.format_exc())

        try:
            await self._load_local_maimai_cache()
        except Exception as e:
            log.warning(f'本地maimai缓存加载失败，将继续后台刷新: {type(e).__name__}: {e}')
            log.warning(traceback.format_exc())

        self._schedule_startup_background_tasks()

        log.info('maimaiDX插件初始化完成')
        log.info('命令注册完成，耗时资源将在后台加载')

    def _schedule_startup_background_tasks(self):
        self._create_startup_task(self._load_startup_data(), "maimai数据后台加载")

    def _create_startup_task(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._startup_tasks.add(task)
        task.add_done_callback(lambda finished: self._finish_startup_task(finished, name))

    def _finish_startup_task(self, task: asyncio.Task, name: str) -> None:
        self._startup_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error(f'{name}失败: {type(exc).__name__}: {exc}')
            log.error(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    async def _load_startup_data(self):
        try:
            await self._load_maimai_data()
        except Exception as e:
            log.error(f'maimai数据加载流程出现未捕获异常: {e}')
            log.error(traceback.format_exc())
        try:
            self._perform_initial_checks()
        except Exception as e:
            log.error(f'执行初始检查失败: {e}')
            log.error(traceback.format_exc())

    async def _load_local_maimai_cache(self):
        log.info('正在从本地缓存恢复maimai运行数据')
        try:
            await mai.load_local_cache()
            count = len(mai.total_list) if hasattr(mai, "total_list") and mai.total_list else 0
            log.info(f'本地maimai缓存恢复完成，歌曲数量: {count}')
        except Exception as e:
            log.warning(f'本地maimai缓存恢复失败: {e}')
            raise

    def _load_disabled_groups(self):
        """加载禁用群组列表"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.disabled_groups = set(data.get('disabled_groups', []))
                log.info(f'已加载禁用群组列表，共 {len(self.disabled_groups)} 个群组')
            else:
                log.info('禁用群组列表文件不存在，创建新文件')
                self._save_disabled_groups()
        except Exception as e:
            log.error(f'加载禁用群组列表失败: {e}')
            self.disabled_groups = set()
    
    def _save_disabled_groups(self):
        """保存禁用群组列表"""
        try:
            data = {'disabled_groups': list(self.disabled_groups)}
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info('禁用群组列表已保存')
        except Exception as e:
            log.error(f'保存禁用群组列表失败: {e}')
    
    def _is_group_enabled(self, group_id: str) -> bool:
        """检查群组是否启用插件"""
        return group_id not in self.disabled_groups
    
    def _setup_configuration(self):
        """处理配置相关的初始化"""
        if maiApi.config.maimaidxproberproxy:
            log.info('正在使用代理服务器访问查分器')
        maiApi.load_token_proxy()

        if bool(self.config.get('roast_persona_webui_enabled', False)):
            try:
                from .libraries.roast_persona_webui import start_roast_persona_webui
                host = str(self.config.get('roast_persona_webui_host', '127.0.0.1') or '127.0.0.1')
                port = int(self.config.get('roast_persona_webui_port', 8796) or 8796)
                token = str(self.config.get('roast_persona_webui_token', '') or '')
                if not self._can_start_webui(host, token):
                    return
                self.roast_persona_webui = start_roast_persona_webui(self.roast_persona_manager, host, port, token, self.config, self.context)
                log.info(f'插件管理 WebUI 已开启: http://{host}:{port}/')
            except Exception as e:
                log.error(f'插件管理 WebUI 启动失败: {e}')

    def _can_start_webui(self, host: str, token: str) -> bool:
        if token.strip():
            return True
        local_hosts = {'127.0.0.1', 'localhost', '::1'}
        if host.strip().lower() in local_hosts:
            log.warning('插件管理 WebUI 未设置访问 Token，仅允许本机监听场景使用')
            return True
        log.error('插件管理 WebUI 未启动：监听地址非本机时必须配置 roast_persona_webui_token')
        return False

    async def _load_maimai_data(self):
        """负责加载舞萌曲库、别名、牌子和猜歌数据"""
        music_loaded = False
        try:
            log.info('正在获取maimai所有曲目信息')
            await mai.get_music()
            music_loaded = bool(hasattr(mai, "total_list") and mai.total_list)
            log.info(f'歌曲数据获取完成，数量: {len(mai.total_list) if hasattr(mai, "total_list") and mai.total_list else 0}')
        except Exception as e:
            log.error(f'加载maimai曲库失败: {e}')
            log.error(traceback.format_exc())

        try:
            log.info('正在获取maimai所有曲目别名信息')
            await mai.get_music_alias()
            log.info(f'别名数据获取完成，数量: {len(mai.total_alias_list) if hasattr(mai, "total_alias_list") and mai.total_alias_list else 0}')
        except Exception as e:
            log.error(f'加载maimai别名失败: {e}')
            log.error(traceback.format_exc())

        try:
            log.info('正在获取maimai牌子数据')
            await mai.get_plate_json()
            log.info('牌子数据获取完成')
        except Exception as e:
            fallback_count = len(getattr(mai, "total_plate_id_list", {}) or {})
            if fallback_count:
                log.warning(f'在线牌子数据加载失败，已使用本地曲库生成兜底牌子数据（{fallback_count} 组）: {type(e).__name__}')
            else:
                log.warning(f'在线牌子数据加载失败，且本地曲库兜底不可用；完成表/牌子进度可能暂不可用: {type(e).__name__}')

        if not music_loaded:
            log.warning('maimai曲库未加载，跳过猜歌数据初始化；依赖曲库的命令会提示稍后再试')
            return

        try:
            log.info('正在初始化猜歌数据')
            if hasattr(mai, 'hot_music_ids'):
                mai.hot_music_ids = []
            mai.guess()
            log.info('猜歌数据初始化完成')
        except Exception as e:
            log.error(f'初始化猜歌数据失败: {e}')
            log.error(traceback.format_exc())

        log.info('maimai数据加载流程结束')

    def _load_images_to_memory(self):
        """如果配置了，将图片加载到内存中"""
        if maiApi.config.saveinmem:
            try:
                ScoreBaseImage._load_image()
                log.info('已将图片保存在内存中')
            except Exception as e:
                log.error(f'加载图片到内存失败: {e}')
                log.error(traceback.format_exc())

    def _perform_initial_checks(self):
        """执行对目录和数据的初始检查"""
        # 检查定数表文件夹
        ratingdir.mkdir(parents=True, exist_ok=True)
        platedir.mkdir(parents=True, exist_ok=True)
        if not list(ratingdir.iterdir()):
            log.warning(
                '注意！注意！检测到定数表文件夹为空！'
                '可能导致「定数表」「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新定数表」进行生成。'
            )
        
        # 检查完成表文件夹
        plate_list = [name for name in list(plate_to_dx_version.keys())[1:]]
        platedir_list = [f.name.split('.')[0] for f in platedir.iterdir()]
        cn_list = [name for name in list(platecn.keys())]
        notin = set(plate_list) - set(platedir_list) - set(cn_list)
        if notin:
            anyname = '，'.join(notin)
            log.warning(
                f'注意！注意！未检测到牌子文件夹中的牌子：{anyname}，'
                '可能导致这些牌子的「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新完成表」进行生成。'
            )
        
        # 检查数据是否加载成功
        try:
            if hasattr(mai, 'total_list') and mai.total_list:
                log.info(f'歌曲数据数量: {len(mai.total_list)}')
            else:
                log.error('歌曲数据未加载！')
        except Exception as e:
            log.warning(f'检查数据状态时出错: {e}')
            log.error(traceback.format_exc())

    def _setup_schedulers(self):
        """专门用于设置所有 apscheduler 定时任务"""
        # 设置定时任务：每天凌晨4点更新数据
        self.scheduler.add_job(
            self._daily_update,
            trigger=CronTrigger(hour=4),
            id="maimai_daily_update",
            name="maimai_daily_update",
            replace_existing=True,
            misfire_grace_time=300
        )

    async def _daily_update(self):
        """定时任务：每日更新数据"""
        try:
            await mai.get_music()
            if hasattr(mai, 'hot_music_ids'):
                mai.hot_music_ids = []
            mai.guess()
            log.info('maimaiDX数据更新完毕')
        except Exception as e:
            log.error(f'定时更新数据失败: {e}')
    
    # 注册命令处理函数
    # 群组开关命令（超级管理员专用）
    @filter.regex(r'^(开启|关闭)舞萌功能$')
    async def toggle_maimai_feature(self, event: AstrMessageEvent):
        """开启/关闭舞萌功能（超级管理员专用）"""
        # 检查是否为超级管理员
        sender_id = str(event.get_sender_id())
        if sender_id not in self.superusers:
            yield event.plain_result('仅允许超级管理员执行此操作')
            return
        
        # 检查是否在群聊中
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result('此命令仅在群聊中可用')
            return
        
        gid = str(group_id)
        message_str = event.message_str.strip()
        
        if message_str == '开启舞萌功能':
            if gid in self.disabled_groups:
                self.disabled_groups.remove(gid)
                self._save_disabled_groups()
                yield event.plain_result(f'已开启本群的舞萌功能')
            else:
                yield event.plain_result('本群舞萌功能已经是开启状态')
        elif message_str == '关闭舞萌功能':
            if gid not in self.disabled_groups:
                self.disabled_groups.add(gid)
                self._save_disabled_groups()
                yield event.plain_result(f'已关闭本群的舞萌功能')
            else:
                yield event.plain_result('本群舞萌功能已经是关闭状态')
    
    # 基础命令
    @filter.command("更新maimai数据")
    async def update_data(self, event: AstrMessageEvent):
        """更新maimai数据"""
        from .command.mai_base import update_data_handler
        async for result in update_data_handler(event, self.superusers):
            yield result

    @filter.command("更新别名库")
    async def update_alias(self, event: AstrMessageEvent):
        """更新别名库"""
        from .command.mai_base import update_alias_handler
        async for result in update_alias_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(帮助|help)$')
    async def maimaidxhelp(self, event: AstrMessageEvent):
        """帮助maimaiDX"""
        # 检查群组是否启用
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        
        from .command.mai_base import maimaidxhelp_handler
        async for result in maimaidxhelp_handler(event):
            yield result

    @filter.regex(r'^(今日mai|今日舞萌|今日运势|jrys)$')
    async def mai_today(self, event: AstrMessageEvent):
        """今日运势"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import mai_today_handler
        async for result in mai_today_handler(event):
            yield result

    @filter.regex(r'.*mai.*什么(.+)?')
    async def mai_what(self, event: AstrMessageEvent):
        """mai什么"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import mai_what_handler
        async for result in mai_what_handler(event):
            yield result

    @filter.regex(r'^[来随给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$')
    async def random_song(self, event: AstrMessageEvent):
        """随机歌曲"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import random_song_handler
        async for result in random_song_handler(event):
            yield result

    @filter.regex(r'^(查看排名|查看排行)$')
    async def rating_ranking(self, event: AstrMessageEvent):
        """查看排名"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import rating_ranking_handler
        async for result in rating_ranking_handler(event):
            yield result

    @filter.regex(r'^(我的排名)$')
    async def my_rating_ranking(self, event: AstrMessageEvent):
        """我的排名"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_base import my_rating_ranking_handler
        async for result in my_rating_ranking_handler(event):
            yield result

    # Import-Token 绑定管理
    @filter.regex(r'^(绑定水鱼|/绑定水鱼)\s*(.*)$')
    async def bind_token(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import bind_token_handler
        async for result in bind_token_handler(event):
            yield result

    @filter.regex(r'^(解绑水鱼|/解绑水鱼)$')
    async def unbind_token(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import unbind_token_handler
        async for result in unbind_token_handler(event):
            yield result

    @filter.regex(r'^(查看水鱼|/查看水鱼)$')
    async def check_token(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import check_token_handler
        async for result in check_token_handler(event):
            yield result

    # 成绩查询命令
    @filter.regex(r'^(更新[bB]50|导)(?:[\s:：]+.*)?$')
    async def score_update(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import score_update_handler
        async for result in score_update_handler(event, self.context, self.config):
            yield result

    @filter.regex(r'^(b50|B50|ccb|CCB)\s*(.*)$')
    async def best50(self, event: AstrMessageEvent):
        """b50/B50 命令"""
        # 检查群组是否启用
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        
        from .command.mai_score import best50_handler
        async for result in best50_handler(event):
            yield result

    @filter.regex(r'^/?(?:吃分推荐|吃分|推分建议)\s*(.*)$')
    async def score_recommend(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_recommend import score_recommend_handler
        async for result in score_recommend_handler(event):
            yield result

    @filter.regex(r'^(?:/?吃粪推荐|/?吃分不推荐|我要吃大粪)\s*(.*)$')
    async def bad_score_recommend(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_recommend import bad_score_recommend_handler
        async for result in bad_score_recommend_handler(event):
            yield result

    @filter.regex(r'^/?锐评[bB]50\s*(.*)$')
    async def b50_analysis(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .libraries.b50_analysis import b50_analysis_handler
        async for result in b50_analysis_handler(event, self.context, self.config):
            yield result

    @filter.regex(r'^(minfo|Minfo|MINFO|info|Info|INFO)\s*(.*)$')
    async def minfo(self, event: AstrMessageEvent):
        """minfo/info 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import minfo_handler
        async for result in minfo_handler(event):
            yield result

    @filter.regex(r'^(ginfo|Ginfo|GINFO)\s*(.*)$')
    async def ginfo(self, event: AstrMessageEvent):
        """ginfo 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import ginfo_handler
        async for result in ginfo_handler(event):
            yield result

    @filter.regex(r'^分数线\s*(.*)$')
    async def score(self, event: AstrMessageEvent):
        """分数线命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import score_handler
        async for result in score_handler(event):
            yield result

    @filter.regex(r'^([0-9]*\.?[0-9]+)的([0-9]*\.?[0-9]+)是多少分$')
    async def calculate_score(self, event: AstrMessageEvent):
        """计算分数命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_score import mai_score_calculate_handler
        async for result in mai_score_calculate_handler(event):
            yield result

    # 搜索命令
    @filter.regex(r'^(查歌|search)\s*(.*)$')
    async def search_music(self, event: AstrMessageEvent):
        """查歌/search 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_music_handler
        async for result in search_music_handler(event):
            yield result

    @filter.regex(r'^(定数查歌|search base)\s*(.*)$')
    async def search_base(self, event: AstrMessageEvent):
        """定数查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_base_handler
        async for result in search_base_handler(event):
            yield result

    @filter.regex(r'^(bpm查歌|search bpm)\s*(.*)$')
    async def search_bpm(self, event: AstrMessageEvent):
        """bpm查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_bpm_handler
        async for result in search_bpm_handler(event):
            yield result

    @filter.regex(r'^(曲师查歌|search artist)\s*(.*)$')
    async def search_artist(self, event: AstrMessageEvent):
        """曲师查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_artist_handler
        async for result in search_artist_handler(event):
            yield result

    @filter.regex(r'^(谱师查歌|search charter)\s*(.*)$')
    async def search_charter(self, event: AstrMessageEvent):
        """谱师查歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import search_charter_handler
        async for result in search_charter_handler(event):
            yield result

    @filter.regex(r'^id\s?([0-9]+)$')
    async def query_chart(self, event: AstrMessageEvent):
        """id 命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_search import query_chart_handler
        async for result in query_chart_handler(event):
            yield result

    # 猜歌命令
    @filter.regex(r'^猜歌$')
    async def guess_music(self, event: AstrMessageEvent):
        """猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_music_handler
        async for result in guess_music_handler(event):
            yield result

    @filter.regex(r'^猜曲绘$')
    async def guess_pic(self, event: AstrMessageEvent):
        """猜曲绘命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_pic_handler
        async for result in guess_pic_handler(event):
            yield result

    @filter.regex(r'^重置猜歌$')
    async def reset_guess(self, event: AstrMessageEvent):
        """重置猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import reset_guess_handler
        async for result in reset_guess_handler(event):
            yield result

    @filter.regex(r'^(开启|关闭)mai猜歌$')
    async def guess_on_off(self, event: AstrMessageEvent):
        """开启/关闭mai猜歌命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_on_off_handler
        async for result in guess_on_off_handler(event):
            yield result

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_music_solve(self, event: AstrMessageEvent):
        """猜歌答案监听（监听所有群消息）"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_guess import guess_music_solve_handler
        async for result in guess_music_solve_handler(event):
            yield result

    # 定数表/完成表命令
    @filter.command("更新定数表")
    async def update_table(self, event: AstrMessageEvent):
        """更新定数表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import update_table_handler
        async for result in update_table_handler(event, self.superusers):
            yield result

    @filter.command("更新完成表")
    async def update_plate(self, event: AstrMessageEvent):
        """更新完成表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import update_plate_handler
        async for result in update_plate_handler(event, self.superusers):
            yield result

    @filter.regex(r'^(?!更新)(.+?)定数表$')
    async def rating_table(self, event: AstrMessageEvent):
        """定数表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import rating_table_handler
        async for result in rating_table_handler(event):
            yield result

    @filter.regex(r'^(?!更新)(.+?)完成表$')
    async def table_pfm(self, event: AstrMessageEvent):
        """完成表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import table_pfm_handler
        async for result in table_pfm_handler(event):
            yield result

    @filter.regex(r'^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分\s?(.+)?$')
    async def rise_score(self, event: AstrMessageEvent):
        """我要在x+上x分命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import rise_score_handler
        async for result in rise_score_handler(event):
            yield result

    @filter.regex(r'^([真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉舞霸熊華华爽煌星宙祭祝双宴镜])([極极将舞神者]舞?)进度\s?(.+)?$')
    async def plate_process(self, event: AstrMessageEvent):
        """牌子进度命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import plate_process_handler
        async for result in plate_process_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\+?)\s?([abcdsfxp\+]+)\s?([\u4e00-\u9fa5]+)?进度\s?([0-9]+)?\s?(.+)?$')
    async def level_process(self, event: AstrMessageEvent):
        """等级进度命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import level_process_handler
        async for result in level_process_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\.?[0-9]?\+?)分数列表\s?([0-9]+)?\s?(.+)?$')
    async def level_achievement_list(self, event: AstrMessageEvent):
        """分数列表命令"""
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        from .command.mai_table import level_achievement_list_handler
        async for result in level_achievement_list_handler(event):
            yield result

    async def terminate(self):
        if self._startup_tasks:
            tasks = list(self._startup_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._startup_tasks.clear()
        if self.roast_persona_webui:
            await self.roast_persona_webui.stop()
        try:
            from .command.mai_guess import cancel_guess_tasks
            await cancel_guess_tasks()
        except Exception as e:
            log.error(f'清理猜歌后台任务失败: {e}')
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
