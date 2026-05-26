import asyncio
import json
from typing import Any, Dict

from aiohttp import ClientSession, ClientTimeout

from .. import config_json, log
from .maimaidx_error import *
from .maimaidx_model import *  # noqa: F403


class MaiConfig(BaseModel):
    
    maimaidxtoken: Optional[str] = None
    maimaidxproberproxy: bool = False
    maimai_http_proxy: Optional[str] = None
    saveinmem: Optional[bool] = True


class MaimaiAPI:
    
    MaiProxyAPI = 'https://proxy.yuzuchan.site'
    
    MaiProberAPI = 'https://www.diving-fish.com/api/maimaidxprober'
    MaiCover = 'https://www.diving-fish.com/covers'
    MaiAliasAPI = 'https://www.yuzuchan.moe/api/maimaidx'
    QQAPI = 'http://q1.qlogo.cn/g'
    
    def __init__(self) -> None:
        """封装Api"""
        self.config: MaiConfig = self.load_config()
        self.headers = None
        self.token = None
        self.MaiAliasProxyAPI = None
        self._session: ClientSession | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None
    
    def load_config(self) -> MaiConfig:
        try:
            if not config_json.exists():
                log.warning(f'配置文件不存在，使用默认配置: {config_json}')
                return MaiConfig()
            with open(config_json, 'r', encoding='utf-8') as f:
                return MaiConfig.model_validate(json.load(f))
        except Exception as e:
            log.warning(f'读取配置文件失败，使用默认配置: {type(e).__name__}: {e}')
            return MaiConfig()
    
    def load_token_proxy(self) -> None:
        self.MaiProberProxyAPI = self.MaiProberAPI if not self.config.maimaidxproberproxy else self.MaiProxyAPI + '/maimaidxprober'
        self.MaiAliasProxyAPI = self.MaiAliasAPI
        self.token = self.config.maimaidxtoken
        self.headers = {'developer-token': self.token} if self.token else None

    async def _get_session(self) -> ClientSession:
        loop = asyncio.get_running_loop()
        if self._session is None or self._session.closed or self._session_loop is not loop:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = ClientSession(timeout=ClientTimeout(total=30))
            self._session_loop = loop
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._session_loop = None
    
    async def _requestalias(self, method: str, endpoint: str, **kwargs) -> APIResult:
        """
        别名库通用请求

        Params:
            `method`: 请求方式
            `endpoint`: 请求接口
            `kwargs`: 其它参数
        Returns:
            `APIResult` 返回结果
        """
        session = await self._get_session()
        async with session.request(method, self.MaiAliasProxyAPI + endpoint, **kwargs) as res:
            if res.status == 200:
                data = await res.json()
                return APIResult.model_validate(data)
            elif res.status == 500:
                raise ServerError
            else:
                raise UnknownError

    async def _requestmai(
        self, 
        method: str, 
        endpoint: str, 
        **kwargs
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        查分器通用请求

        Params:
            `method`: 请求方式
            `endpoint`: 请求接口
            `kwargs`: 其它参数
        Returns:
            `Dict[str, Any]` 返回结果
        """
        session = await self._get_session()
        async with session.request(
            method,
            self.MaiProberProxyAPI + endpoint,
            headers=self.headers,
            **kwargs
        ) as res:
            if res.status == 200:
                data = await res.json()
            elif res.status == 400:
                error: Dict = await res.json()
                if 'message' in error:
                    if error['message'] == 'no such user':
                        raise UserNotFoundError
                    elif error['message'] == 'user not exists':
                        raise UserNotExistsError
                    else:
                        raise UserNotFoundError
                elif 'msg' in error:
                    if error['msg'] == '开发者token有误':
                        raise TokenError
                    elif error['msg'] == '开发者token被禁用':
                        raise TokenDisableError
                    else:
                        raise TokenNotFoundError
                else:
                    raise UserNotFoundError
            elif res.status == 403:
                raise UserDisabledQueryError
            else:
                raise UnknownError
        return data
    
    async def music_data(self):
        """获取曲目数据"""
        return await self._requestmai('GET', '/music_data')

    async def chart_stats(self):
        """获取单曲数据"""
        return await self._requestmai('GET', '/chart_stats')

    async def query_user_b50(
        self, 
        *, 
        qqid: Optional[int] = None, 
        username: Optional[str] = None
    ) -> UserInfo:
        """
        获取玩家B50
        
        Params:
            `qqid`: QQ号
            `username`: 用户名
        Returns:
            `UserInfo` b50数据模型
        """
        json = {}
        if qqid:
            json['qq'] = qqid
        if username:
            json['username'] = username
        json['b50'] = True

        return UserInfo.model_validate(await self._requestmai('POST', '/query/player', json=json))

    async def query_user_plate(
        self,
        *,
        qqid: Optional[int] = None,
        username: Optional[str] = None,
        version: Optional[List[str]] = None
    ) -> List[PlayInfoDefault]:
        """
        请求用户数据

        Params:
            `qqid`: 用户QQ
            `username`: 查分器用户名
            `version`: 版本
        Returns:
            `List[PlayInfoDefault]` 数据列表
        """
        json = {}
        if qqid:
            json['qq'] = qqid
        if username:
            json['username'] = username
        if version:
            json['version'] = version
        result = await self._requestmai('POST', '/query/plate', json=json)
        return [PlayInfoDefault.model_validate(d) for d in result['verlist']]

    async def query_user_get_dev(
        self, 
        *, 
        qqid: Optional[int] = None, 
        username: Optional[str] = None
    ) -> UserInfoDev:
        """
        使用开发者接口获取用户数据，请确保拥有和输入了开发者 `token`

        Params:
            qqid: 用户QQ
            username: 查分器用户名
        Returns:
            `UserInfoDev` 开发者用户信息
        """
        params = {}
        if qqid:
            params['qq'] = qqid
        if username:
            params['username'] = username
        
        result = await self._requestmai('GET', '/dev/player/records', params=params)
        return UserInfoDev.model_validate(result)

    async def query_user_post_dev(
        self,
        *,
        qqid: Optional[int] = None,
        username: Optional[str] = None,
        music_id: Union[str, int, List[Union[str, int]]]
    ) -> List[PlayInfoDev]:
        """
        使用开发者接口获取用户指定曲目数据，请确保拥有和输入了开发者 `token`

        Params:
            `qqid`: 用户QQ
            `username`: 查分器用户名
            `music_id`: 曲目id，可以为单个ID或者列表
        Returns:
            `List[PlayInfoDev]` 开发者成绩列表
        """
        json = {}
        if qqid:
            json['qq'] = qqid
        if username:
            json['username'] = username
        json['music_id'] = music_id
        
        result = await self._requestmai('POST', '/dev/player/record', json=json)
        if result == {}:
            raise MusicNotPlayError
        
        if isinstance(music_id, list):
            return [PlayInfoDev.model_validate(d) for k, v in result.items() for d in v]
        return [PlayInfoDev.model_validate(d) for d in result[str(music_id)]]

    async def rating_ranking(self) -> List[UserRanking]:
        """
        获取查分器排行榜
        
        Returns:
            `List[UserRanking]` 按`ra`从高到低排序后的查分器排行模型列表
        """
        result = await self._requestmai('GET', '/rating_ranking')
        return sorted([UserRanking.model_validate(u) for u in result], key=lambda x: x.ra, reverse=True)

    async def get_plate_json(self) -> Dict:
        """获取所有版本牌子完成需求"""
        result = await self._requestalias('GET', '/maimaidxplate')
        if result.code == 0:
            return result.content
        raise UnknownError

    async def get_alias(self) -> Dict[str, Union[str, int, List[str]]]:
        """获取所有别名"""
        result = await self._requestalias('GET', '/maimaidxalias')
        if result.code == 0:
            return result.content
        raise UnknownError

    async def get_songs(self, name: str) -> Union[List[AliasStatus], List[Alias]]:
        """
        使用别名查询曲目。
        `code` 为 `0` 时返回值为 `List[Alias]`。
        `code` 为 `3006` 时返回值为 `List[AliasStatus]`。
        
        Params:
            `name`: 别名
        Returns:
            `Union[List[AliasStatus], List[Alias]]`
        """
        result = await self._requestalias('GET', '/getsongs', params={'name': name})
        if result.code == 3006:
            return [AliasStatus.model_validate(s) for s in result.content]
        elif result.code == 1004:
            return []
        elif result.code == 0:
            return [Alias.model_validate(s) for s in result.content]
        else:
            raise UnknownError

    async def qqlogo(self, qqid: int = None, icon: str = None) -> Optional[bytes]:
        """获取QQ头像"""
        session = await self._get_session()
        if qqid:
            params = {
                'b': 'qq',
                'nk': qqid,
                's': 100
            }
            async with session.get(self.QQAPI, params=params) as res:
                if res.status != 200:
                    return None
                return await res.read()
        elif icon:
            async with session.get(icon) as res:
                if res.status != 200:
                    return None
                return await res.read()
        else:
            return None


maiApi = MaimaiAPI()
