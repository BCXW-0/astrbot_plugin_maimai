from __future__ import annotations

from .maimai_adapter import MaimaiAdapter, MaimaiDependencyError
from .result import SyncResult
from ... import log


class MaimaiUpdateService:
    def __init__(self, timeout: float = 30.0, http_proxy: str = ""):
        self.adapter = MaimaiAdapter(timeout=timeout, http_proxy=http_proxy)

    @property
    def client(self):
        return self.adapter.client

    def _load_imports(self):
        return self.adapter.load_imports()

    async def arcade_identifier_from_sgid(self, sgid: str):
        return await self.adapter.arcade_identifier_from_sgid(sgid)

    def arcade_identifier_from_credentials(self, credentials: str):
        return self.adapter.identifier(credentials)

    async def sync_to_divingfish(self, arcade_identifier, import_token: str) -> SyncResult:
        arcade_provider = self.adapter.arcade_provider()
        player_name = ""
        player_rating = 0
        player_warning = ""
        if hasattr(arcade_provider, "get_player"):
            try:
                player = await self.client.players(arcade_identifier, provider=arcade_provider)
                player_name = str(getattr(player, "name", "") or "")
                player_rating = int(getattr(player, "rating", 0) or 0)
            except Exception as exc:
                log.warning(f"机台玩家信息获取失败: {exc}")
                player_warning = "当前数据源不提供官方玩家名预览。"
        else:
            player_warning = "当前数据源不提供官方玩家名预览。"
        await self.adapter.prepare_song_cache_without_aliases()
        scores = await self.client.scores(arcade_identifier, provider=arcade_provider)
        score_list = list(getattr(scores, "scores", []) or [])
        score_rating = int(getattr(scores, "rating", 0) or 0)
        b35_scores = list(getattr(scores, "scores_b35", []) or [])
        b15_scores = list(getattr(scores, "scores_b15", []) or [])
        rating_b35 = int(getattr(scores, "rating_b35", 0) or 0)
        rating_b15 = int(getattr(scores, "rating_b15", 0) or 0)
        max_score = max(score_list, key=lambda item: int(getattr(item, "dx_rating", 0) or 0), default=None)
        max_score_rating = int(getattr(max_score, "dx_rating", 0) or 0) if max_score else 0
        max_score_title = str(getattr(max_score, "title", "") or "") if max_score else ""
        max_score_achievements = float(getattr(max_score, "achievements", 0) or 0) if max_score else 0.0
        await self.client.updates(self.adapter.identifier(import_token), score_list, provider=self.adapter.divingfish_provider())
        return SyncResult(
            player_name=player_name,
            rating=player_rating or score_rating,
            score_count=len(score_list),
            rating_b35=rating_b35,
            rating_b15=rating_b15,
            b35_count=len(b35_scores),
            b15_count=len(b15_scores),
            max_score_rating=max_score_rating,
            max_score_title=max_score_title,
            max_score_achievements=max_score_achievements,
            player_warning=player_warning,
        )

    async def sync_from_sgid_to_divingfish(self, sgid: str, import_token: str) -> SyncResult:
        arcade_identifier = await self.arcade_identifier_from_sgid(sgid)
        return await self.sync_to_divingfish(arcade_identifier, import_token)

    async def sync_from_credentials_to_divingfish(self, credentials: str, import_token: str) -> SyncResult:
        arcade_identifier = self.arcade_identifier_from_credentials(credentials)
        return await self.sync_to_divingfish(arcade_identifier, import_token)

    def describe_error(self, exc: BaseException) -> str:
        if isinstance(exc, MaimaiDependencyError):
            return str(exc)
        imports = self.adapter._imports or {}
        checks = (
            ("AimeServerError", "二维码无效或已过期，请重新从官方公众号获取二维码后再试。"),
            ("TitleServerBlockedError", "舞萌标题服务器拒绝了当前请求，可能是当前 IP 暂时被限制，请稍后再试或更换网络。"),
            ("TitleServerNetworkError", "舞萌标题服务器网络请求失败，请稍后再试。"),
            ("TitleServerError", "舞萌标题服务器请求失败，可能是网络波动或当前 IP 暂时被限制，请稍后再试。"),
            ("ArcadeIdentifierError", "官方二维码凭据无效或已过期，请重新从官方公众号获取二维码后再试。"),
            ("ArcadeError", "机台数据源返回异常，可能是二维码过期、官方服务波动或账号状态异常。"),
            ("InvalidPlayerIdentifierError", "水鱼 Import-Token 无效，或水鱼账号不允许导入，请重新绑定 Token。"),
            ("InvalidDeveloperTokenError", "水鱼接口拒绝了请求，请检查 Token 或稍后再试。"),
            ("PrivacyLimitationError", "水鱼账号未允许第三方访问，请先在水鱼查分器中开启相关权限。"),
            ("HTTPError", "网络请求失败，请检查 AstrBot 所在机器的网络或代理设置；如使用 socks5 代理，请安装 socksio 或改用 HTTP 代理。"),
        )
        for class_name, message in checks:
            cls = imports.get(class_name)
            if cls and isinstance(exc, cls):
                return message
        return f"操作失败：{exc.__class__.__name__}。请联系管理员查看日志。"
