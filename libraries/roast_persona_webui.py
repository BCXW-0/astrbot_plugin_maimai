import asyncio
import ipaddress
import json
import re
from pathlib import Path
from contextlib import suppress
from typing import Any, Optional

from aiohttp import web

from .. import Root, log, webui_config_overrides_json
from .arcade_credential_manager import get_arcade_credential_manager
from .chart_tags import ChartTagUpdateJob, generate_chart_tags_file
from .chart_tags.constants import ALLOWED_TAGS, TAG_CATEGORIES
from .chart_tags.rule_tags import filter_allowed_tags
from .chart_tags.storage import read_chart_tags, write_json_atomic, CHART_TAGS_FILE
from .roast.llm_client import resolve_roast_provider_id
from .roast_persona_manager import RoastPersonaManager
from .user_token_manager import get_token_manager


class RoastPersonaWebUI:
    CONFIG_FIELDS = {
        "bot_name": {"label": "机器人名称", "type": "string"},
        "enable_reply": {"label": "引用回复", "type": "bool"},
        "maimaidxtoken": {"label": "水鱼 Developer-Token", "type": "string", "secret": True},
        "roast_b50_provider_id": {"label": "锐评B50专用模型", "type": "string"},
        "roast_persona_prompt_sample_limit": {"label": "锐评人格注入样本上限", "type": "int", "min": 1},
        "roast_persona_webui_enabled": {"label": "插件管理 WebUI", "type": "bool"},
        "sgid_max_age_seconds": {"label": "SGID 有效窗口", "type": "int", "min": 30},
        "request_timeout_seconds": {"label": "更新b50 请求超时", "type": "int", "min": 3},
        "maimai_http_proxy": {"label": "更新b50 HTTP 代理", "type": "string"},
        "warn_unsupported_recall": {"label": "敏感消息撤回提示", "type": "bool"},
    }

    def __init__(self, manager: RoastPersonaManager, host: str, port: int, access_token: str, config: dict | None = None, context: Any | None = None):
        self.manager = manager
        self.host = host
        self.port = port
        self.access_token = access_token.strip()
        self.config = config or {}
        self.context = context
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_task: asyncio.Task | None = None
        self.assets_dir = Root / "static" / "plugin_webui"
        self.chart_tag_job = ChartTagUpdateJob(context=context, config=self.config)

    async def start(self) -> None:
        if self.runner:
            return
        app = web.Application(client_max_size=30 * 1024 * 1024)
        app.router.add_get("/", self.index)
        app.router.add_static("/assets", self.assets_dir, show_index=False)
        app.router.add_get("/api/overview", self.overview)
        app.router.add_get("/api/commands", self.commands)
        app.router.add_get("/api/config_summary", self.config_summary)
        app.router.add_post("/api/config", self.save_config)
        app.router.add_get("/api/chart_tags/status", self.chart_tags_status)
        app.router.add_post("/api/chart_tags/generate", self.chart_tags_generate)
        app.router.add_post("/api/chart_tags/start", self.chart_tags_start)
        app.router.add_post("/api/chart_tags/stop", self.chart_tags_stop)
        app.router.add_get("/api/chart_tags/search", self.chart_tags_search)
        app.router.add_get("/api/chart_tags/{key}", self.chart_tags_get)
        app.router.add_post("/api/chart_tags/{key}", self.chart_tags_save)
        app.router.add_get("/api/personas", self.list_personas)
        app.router.add_post("/api/persona", self.save_persona)
        app.router.add_post("/api/import_json", self.import_json)
        app.router.add_get("/api/persona/{name}", self.get_persona)
        app.router.add_delete("/api/persona/{name}", self.delete_persona)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

    async def stop(self) -> None:
        await self.chart_tag_job.shutdown()
        if self.start_task and not self.start_task.done():
            self.start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.start_task
        self.start_task = None
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None

    def _check_auth(self, request: web.Request) -> bool:
        if not self.access_token:
            remote = request.remote or ""
            try:
                return ipaddress.ip_address(remote).is_loopback
            except ValueError:
                return remote in {"localhost", "::1"}
        token = request.query.get("token") or request.headers.get("X-Access-Token", "")
        return token == self.access_token

    async def index(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return self._auth_prompt_response(request)
        return web.FileResponse(self.assets_dir / "index.html")

    def _auth_prompt_response(self, request: web.Request) -> web.Response:
        invalid_token = bool(request.query.get("token"))
        if self.access_token:
            error_html = (
                '<p class="error">Token 不正确，请检查大小写、前后空格或重新从插件配置复制。</p>'
                if invalid_token
                else ""
            )
            body = f"""
            <h1>需要访问 Token</h1>
            <p>请输入插件配置项 <code>roast_persona_webui_token</code> 的值。</p>
            {error_html}
            <form id="authForm">
              <input id="tokenInput" type="password" autocomplete="current-password" placeholder="WebUI Token" autofocus>
              <button type="submit">进入 WebUI</button>
            </form>
            <p class="hint">也可以直接访问 <code>?token=你的Token</code>。</p>
            """
            script = """
            <script>
            document.getElementById('authForm').addEventListener('submit', event => {
              event.preventDefault();
              const token = document.getElementById('tokenInput').value.trim();
              if (!token) return;
              const url = new URL(window.location.href);
              url.searchParams.set('token', token);
              window.location.replace(url.pathname + url.search + url.hash);
            });
            </script>
            """
        else:
            body = """
            <h1>仅允许本机访问</h1>
            <p>当前 WebUI 未配置访问 Token，只允许从本机地址访问。</p>
            <p class="hint">如果需要公网或 NAT 访问，请先配置 <code>roast_persona_webui_token</code> 并重载插件。</p>
            """
            script = ""

        html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>舞萌 DX WebUI 认证</title>
<style>
:root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f7fb; color: #20242c; }}
main {{ width: min(440px, calc(100vw - 32px)); box-sizing: border-box; padding: 28px; border: 1px solid #d9deea; border-radius: 8px; background: #fff; box-shadow: 0 20px 48px rgba(32, 36, 44, .12); }}
h1 {{ margin: 0 0 12px; font-size: 24px; line-height: 1.25; }}
p {{ margin: 0 0 16px; line-height: 1.7; }}
code {{ padding: 2px 5px; border-radius: 4px; background: #eef1f7; }}
form {{ display: grid; gap: 12px; margin-top: 18px; }}
input {{ height: 42px; padding: 0 12px; border: 1px solid #c8cfdd; border-radius: 6px; font-size: 15px; }}
button {{ height: 42px; border: 0; border-radius: 6px; background: #3457d5; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; }}
button:hover {{ background: #2447c4; }}
.error {{ padding: 10px 12px; border-radius: 6px; background: #fff0f0; color: #b42318; }}
.hint {{ margin-top: 16px; color: #687083; font-size: 13px; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #15171c; color: #eef1f7; }}
  main {{ background: #1f232b; border-color: #333946; box-shadow: none; }}
  code {{ background: #303644; }}
  input {{ background: #15171c; border-color: #454c5c; color: #eef1f7; }}
  .hint {{ color: #aab2c2; }}
  .error {{ background: #3a2022; color: #ffb4ad; }}
}}
</style>
</head>
<body>
<main>
{body}
</main>
{script}
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def overview(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        personas = self.manager.list_personas()
        sample_count = sum(int(item.get("sample_count", "0") or 0) for item in personas)
        token_mgr = get_token_manager()
        credential_mgr = get_arcade_credential_manager()
        import_token_count = len(getattr(token_mgr, "_tokens", {}) or {}) if token_mgr else 0
        arcade_credential_count = credential_mgr.count() if credential_mgr else 0
        return web.json_response({
            "ok": True,
            "persona_count": len(personas),
            "sample_count": sample_count,
            "import_token_count": import_token_count,
            "arcade_credential_count": arcade_credential_count,
            "webui": f"http://{self.host}:{self.port}/",
        })

    async def commands(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        commands = [
            {"command": "帮助 / help", "description": "发送帮助图片。"},
            {"command": "b50 [QQ号或@用户]", "description": "查询 Best 50 成绩图。"},
            {"command": "锐评b50 [风格或补充需求]", "description": "从水鱼拉取 B50 并调用 LLM 生成锐评图。"},
            {"command": "/吃分推荐 [@用户]", "description": "按当前 Rating 区间、B35/B15 地板和拟合定数推荐吃分曲。"},
            {"command": "绑定水鱼 <水鱼token>", "description": "绑定用户个人水鱼 Import-Token。"},
            {"command": "更新b50 <SGWCMAID识别码>", "description": "首次或重新绑定机台用户信息，并同步成绩到水鱼。"},
            {"command": "更新b50", "description": "已有机台用户信息绑定后，复用旧绑定同步成绩。"},
            {"command": "导 <SGWCMAID识别码> / 导", "description": "更新b50 的别名。"},
            {"command": "info / minfo <曲名或ID>", "description": "查询自己的单曲成绩详情。"},
        ]
        return web.json_response({"ok": True, "commands": commands})

    async def config_summary(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        schema = self._load_config_schema()
        overrides = self._load_config_overrides()
        items = []
        for key, meta in self.CONFIG_FIELDS.items():
            schema_item = schema.get(key, {}) if isinstance(schema, dict) else {}
            value = self.config.get(key, schema_item.get("default", ""))
            items.append({
                "key": key,
                "label": meta.get("label", key),
                "type": meta.get("type", schema_item.get("type", "string")),
                "value": value,
                "default": schema_item.get("default", ""),
                "hint": schema_item.get("hint", ""),
                "description": schema_item.get("description", meta.get("label", key)),
                "secret": bool(meta.get("secret", False)),
                "overridden": key in overrides,
            })
        return web.json_response({"ok": True, "items": items})

    async def save_config(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        data = await request.json()
        values = data.get("values", {}) if isinstance(data, dict) else {}
        if not isinstance(values, dict):
            return web.json_response({"ok": False, "message": "配置数据格式不正确"}, status=400)
        overrides = self._load_config_overrides()
        changed = []
        for key, raw_value in values.items():
            if key not in self.CONFIG_FIELDS:
                continue
            value = self._coerce_config_value(key, raw_value)
            overrides[key] = value
            self.config[key] = value
            changed.append(key)
        self._save_config_overrides(overrides)
        self._apply_runtime_config(changed)
        restart_fields = {"roast_persona_webui_enabled", "roast_persona_webui_host", "roast_persona_webui_port", "roast_persona_webui_token"}
        restart_required = any(key in restart_fields for key in changed)
        message = f"已保存 {len(changed)} 项配置"
        if restart_required:
            message += "；WebUI 开关、监听地址、端口或访问 Token 需重启插件后生效"
        else:
            message += "；运行期配置已尽量即时生效"
        return web.json_response({"ok": True, "message": message, "items": changed, "restart_required": restart_required})

    async def chart_tags_status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        return web.json_response(await asyncio.to_thread(self.chart_tag_job.status))

    @staticmethod
    def _parse_int_param(raw: Any, default: int | None = None) -> tuple[int | None, str | None]:
        try:
            value = default if raw is None or raw == "" else int(raw)
        except (TypeError, ValueError):
            return default, "参数必须是整数"
        return value, None

    async def chart_tags_search(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        query = str(request.query.get("q", "") or "").strip().lower()
        limit, error = self._parse_int_param(request.query.get("limit"), default=30)
        if error:
            return web.json_response({"ok": False, "message": f"limit {error}"}, status=400)
        limit = max(1, min(80, limit))
        items = await asyncio.to_thread(self._search_chart_tag_items, query, limit)
        return web.json_response({"ok": True, "items": items, "allowed_tags": ALLOWED_TAGS})

    async def chart_tags_get(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        key = request.match_info["key"]
        item = await asyncio.to_thread(self._get_chart_tag_detail_item, key)
        if item is None:
            return web.json_response({"ok": False, "message": "谱面不存在"}, status=404)
        return web.json_response({"ok": True, "item": item, "allowed_tags": ALLOWED_TAGS})

    async def chart_tags_save(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        key = request.match_info["key"]
        payload = await request.json()
        tags = payload.get("manual_tags", []) if isinstance(payload, dict) else []
        if not isinstance(tags, list):
            return web.json_response({"ok": False, "message": "manual_tags 必须是数组"}, status=400)
        manual_tags = filter_allowed_tags(str(item) for item in tags)
        result = await asyncio.to_thread(self._save_chart_tags_sync, key, manual_tags)
        if result is None:
            return web.json_response({"ok": False, "message": "谱面不存在"}, status=404)
        return web.json_response({"ok": True, "message": f"已保存 {len(manual_tags)} 个手动标签", **result, "status": await asyncio.to_thread(self.chart_tag_job.status)})

    async def chart_tags_generate(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        try:
            result = await asyncio.to_thread(generate_chart_tags_file)
            status = self.chart_tag_job.status()
            return web.json_response({"ok": True, "message": "基础谱面标签文件已生成", **result, "status": status})
        except Exception as exc:
            log.error(f"生成基础谱面标签失败: {type(exc).__name__} - {exc}")
            return web.json_response({"ok": False, "message": "生成基础谱面标签失败，请查看插件日志"}, status=500)

    async def chart_tags_start(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        data = await request.json() if request.can_read_body else {}
        batch_size, error = self._parse_int_param((data or {}).get("batch_size"), default=50)
        if error:
            return web.json_response({"ok": False, "message": f"batch_size {error}"}, status=400)
        result = await self.chart_tag_job.start(batch_size=batch_size)
        return web.json_response(result)

    async def chart_tags_stop(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        return web.json_response(await self.chart_tag_job.stop())

    def _search_chart_tag_items(self, query: str, limit: int) -> list[dict[str, Any]]:
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        items = []
        for key, chart in charts.items():
            if not isinstance(chart, dict):
                continue
            haystack = " ".join(str(chart.get(field, "") or "") for field in ("song_id", "title", "difficulty", "level", "type", "charter")).lower()
            if query and query not in haystack:
                continue
            items.append(self._chart_tag_summary(key, chart))
            if len(items) >= limit:
                break
        return items

    def _get_chart_tag_detail_item(self, key: str) -> dict[str, Any] | None:
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        chart = charts.get(key)
        if not isinstance(chart, dict):
            return None
        return self._chart_tag_detail(key, chart)

    def _save_chart_tags_sync(self, key: str, manual_tags: list[str]) -> dict[str, Any] | None:
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        chart = charts.get(key)
        if not isinstance(chart, dict):
            return None
        chart["manual_tags"] = manual_tags
        llm_tags = filter_allowed_tags(chart.get("llm_tags", []))
        final_tags = filter_allowed_tags([*llm_tags, *manual_tags])
        chart["llm_tags"] = llm_tags
        chart["final_tags"] = final_tags
        chart["tags"] = final_tags
        chart["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES}
        if final_tags:
            chart["tag_status"] = "done"
            chart["tag_error"] = ""
        elif chart.get("tag_status") == "done":
            chart["tag_status"] = ""
        chart["updated_at"] = self._now_text()
        charts[key] = chart
        data["charts"] = charts
        data["updated_at"] = self._now_text()
        write_json_atomic(CHART_TAGS_FILE, data)
        return {"item": self._chart_tag_detail(key, chart)}

    def _chart_tag_summary(self, key: str, chart: dict[str, Any]) -> dict[str, Any]:
        manual_tags = filter_allowed_tags(chart.get("manual_tags", []))
        llm_tags = filter_allowed_tags(chart.get("llm_tags", []))
        final_tags = filter_allowed_tags(chart.get("final_tags") or chart.get("tags") or [*llm_tags, *manual_tags])
        return {
            "key": key,
            "song_id": chart.get("song_id", ""),
            "title": chart.get("title", ""),
            "difficulty": chart.get("difficulty", ""),
            "level": chart.get("level", ""),
            "type": chart.get("type", ""),
            "manual_tags": manual_tags,
            "llm_tags": llm_tags,
            "final_tags": final_tags,
            "tag_status": chart.get("tag_status", ""),
            "tag_error": chart.get("tag_error", ""),
        }

    def _chart_tag_detail(self, key: str, chart: dict[str, Any]) -> dict[str, Any]:
        item = self._chart_tag_summary(key, chart)
        item.update({
            "ds": chart.get("ds"),
            "fit_diff": chart.get("fit_diff"),
            "bpm": chart.get("bpm"),
            "artist": chart.get("artist", ""),
            "genre": chart.get("genre", ""),
            "version": chart.get("version", ""),
            "charter": chart.get("charter", ""),
            "notes": chart.get("notes", {}),
            "evidence": chart.get("evidence", []),
            "updated_at": chart.get("updated_at", ""),
        })
        return item

    def _now_text(self) -> str:
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

    async def save_persona(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        data = await request.json()
        name = str(data.get("name", "")).strip()
        taste_roast = str(data.get("taste_roast", "") or "").strip()
        special_note = str(data.get("special_note", "") or "").strip()
        samples = data.get("samples", [])
        if not name:
            return web.json_response({"ok": False, "message": "人格名称不能为空"}, status=400)
        if not isinstance(samples, list):
            return web.json_response({"ok": False, "message": "samples 必须是数组"}, status=400)
        non_empty_count = len([item for item in samples if str(item).strip()])
        added, skipped, count = self.manager.set_persona(name, [str(item) for item in samples], taste_roast=taste_roast, special_note=special_note)
        filtered_count, filter_note = await self._filter_persona_samples_if_needed(name)
        msg = f"已保存人格「{name}」：新增 {added} 条，跳过 {skipped} 条，当前累计 {filtered_count} 条"
        if filter_note:
            msg += f"；{filter_note}"
        if non_empty_count > 0 and non_empty_count < self.manager.min_samples:
            msg += f"（建议至少 {self.manager.min_samples} 条以获得更好的人格效果）"
        return web.json_response({"ok": True, "message": msg})

    async def import_json(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        reader = await request.multipart()
        name = ""
        target_qq = ""
        raw = b""
        async for part in reader:
            if part.name == "name":
                name = (await part.text()).strip()
            elif part.name == "target_qq":
                target_qq = (await part.text()).strip()
            elif part.name == "file":
                raw = await part.read(decode=False)
        if not name:
            return web.json_response({"ok": False, "message": "导入人格名称不能为空"}, status=400)
        if not target_qq:
            return web.json_response({"ok": False, "message": "目标 QQ 不能为空"}, status=400)
        if not raw:
            return web.json_response({"ok": False, "message": "JSON 文件为空"}, status=400)
        try:
            data = await asyncio.to_thread(lambda: json.loads(raw.decode("utf-8-sig")))
        except Exception as exc:
            return web.json_response({"ok": False, "message": f"JSON 解析失败：{exc}"}, status=400)
        samples, matched, scanned = await asyncio.to_thread(self._extract_samples_from_json, data, target_qq)
        if not samples:
            return web.json_response({"ok": False, "message": f"未从 JSON 中提取到 QQ {target_qq} 的纯文本消息；已扫描 {scanned} 条记录，匹配 {matched} 条"}, status=400)
        added, skipped, count = self.manager.set_persona(name, samples)
        filtered_count, filter_note = await self._filter_persona_samples_if_needed(name)
        msg = f"JSON 导入完成：扫描 {scanned} 条，匹配 QQ {target_qq} 的记录 {matched} 条，提取纯文本 {len(samples)} 条，新增 {added} 条，跳过 {skipped} 条，当前累计 {filtered_count} 条"
        if filter_note:
            msg += f"；{filter_note}"
        if len(samples) < self.manager.min_samples:
            msg += f"（建议至少 {self.manager.min_samples} 条以获得更好的人格效果）"
        return web.json_response({"ok": True, "message": msg})

    async def get_persona(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        name = request.match_info["name"]
        limit_raw = request.query.get("limit")
        offset, error = self._parse_int_param(request.query.get("offset"), default=0)
        if error:
            return web.json_response({"ok": False, "message": f"offset {error}"}, status=400)
        limit, error = self._parse_int_param(limit_raw, default=None)
        if error:
            return web.json_response({"ok": False, "message": f"limit {error}"}, status=400)
        return web.json_response({"ok": True, "name": name, "sample_count": self.manager.get_sample_count(name), "taste_roast": self.manager.get_taste_roast(name), "special_note": self.manager.get_special_note(name), "samples": self.manager.get_samples(name, limit=limit, offset=offset)})

    async def list_personas(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        return web.json_response({"ok": True, "personas": self.manager.list_personas()})

    async def delete_persona(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "message": "Forbidden"}, status=403)
        name = request.match_info["name"]
        self.manager.remove_persona(name)
        return web.json_response({"ok": True, "message": f"已删除锐评人格「{name}」与样本"})

    def _load_config_schema(self) -> dict:
        try:
            schema_path = Root / "_conf_schema.json"
            if not schema_path.exists():
                return {}
            return json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error(f"读取 WebUI 配置 schema 失败: {exc}")
            return {}

    def _load_config_overrides(self) -> dict:
        try:
            if not webui_config_overrides_json.exists():
                return {}
            data = json.loads(webui_config_overrides_json.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            log.error(f"读取 WebUI 配置覆盖失败: {exc}")
            return {}

    def _save_config_overrides(self, overrides: dict) -> None:
        webui_config_overrides_json.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = webui_config_overrides_json.with_suffix(webui_config_overrides_json.suffix + ".tmp")
        tmp_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(webui_config_overrides_json)

    def _coerce_config_value(self, key: str, value: Any) -> Any:
        meta = self.CONFIG_FIELDS[key]
        value_type = meta.get("type")
        if value_type == "bool":
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on", "开启")
            return bool(value)
        if value_type == "int":
            number = int(value or 0)
            return max(int(meta.get("min", 0)), number)
        return str(value or "").strip()

    def _apply_runtime_config(self, changed: list[str]) -> None:
        if "maimaidxtoken" in changed and str(self.config.get("maimaidxtoken", "") or "").strip():
            maiApi.config.maimaidxtoken = str(self.config.get("maimaidxtoken", "") or "").strip()
        if "roast_persona_prompt_sample_limit" in changed:
            self.manager.update_prompt_sample_limit(int(self.config.get("roast_persona_prompt_sample_limit", 120) or 120))
        if "maimai_http_proxy" in changed:
            maiApi.config.maimai_http_proxy = str(self.config.get("maimai_http_proxy", "") or "").strip()

    async def _filter_persona_samples_if_needed(self, name: str) -> tuple[int, str]:
        limit = max(1, int(self.config.get("roast_persona_prompt_sample_limit", self.manager.max_prompt_samples) or self.manager.max_prompt_samples))
        self.manager.update_prompt_sample_limit(limit)
        samples = self.manager.get_samples(name)
        if len(samples) <= limit:
            return len(samples), ""
        selected = await self._select_best_samples_with_llm(name, samples, limit)
        note = "LLM 已筛选最优注入样本"
        if not selected:
            selected = self._select_best_samples_locally(samples, limit)
            note = "LLM 筛选不可用，已使用本地规则筛选注入样本"
        count = self.manager.replace_samples(name, selected)
        return count, f"{note}，保留 {count} 条，舍弃 {max(0, len(samples) - count)} 条"

    async def _select_best_samples_with_llm(self, name: str, samples: list[str], limit: int) -> list[str]:
        if not self.context:
            return []
        candidates = self._sample_candidates_for_llm(samples, max(limit * 3, limit))
        numbered = "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(candidates))
        prompt = (
            f"你正在为 maimai B50 锐评人格「{name}」筛选聊天样本。"
            f"请从下面 {len(candidates)} 条候选中选择最能体现该人格语言风格、口癖、吐槽节奏且适合注入提示词的 {limit} 条以内。"
            "排除无意义、过短、重复、纯情绪、隐私风险高或不适合模型学习语气的内容。"
            "只输出 JSON 数组，数组元素为候选编号，不要输出解释。\n"
            f"候选样本：\n{numbered}"
        )
        try:
            provider_id = await resolve_roast_provider_id(self.context, self.config)
            response = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            text = response.completion_text if response else ""
            indexes = self._parse_llm_indexes(text)
            selected = []
            seen = set()
            for index in indexes:
                if 0 <= index < len(candidates) and candidates[index] not in seen:
                    selected.append(candidates[index])
                    seen.add(candidates[index])
                if len(selected) >= limit:
                    break
            return selected
        except Exception as exc:
            log.error(f"锐评人格样本 LLM 筛选失败: {exc}")
            return []

    def _sample_candidates_for_llm(self, samples: list[str], max_count: int) -> list[str]:
        if len(samples) <= max_count:
            return samples
        recent_count = max(1, int(max_count * 0.65))
        spread_count = max_count - recent_count
        recent = samples[-recent_count:]
        older = samples[:-recent_count]
        step = max(1, len(older) // max(1, spread_count))
        return older[::step][:spread_count] + recent

    def _parse_llm_indexes(self, text: str) -> list[int]:
        raw = str(text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [int(item) - 1 for item in data]
        except Exception:
            pass
        return [int(item) - 1 for item in re.findall(r"\d+", raw)]

    def _select_best_samples_locally(self, samples: list[str], limit: int) -> list[str]:
        scored = []
        for idx, text in enumerate(samples):
            length = len(text)
            score = min(length, 80)
            score += len(set(text)) * 0.4
            if re.search(r"[？！!?~～…]", text):
                score += 8
            if re.search(r"[，。、“”‘’：；,.]", text):
                score += 6
            if length < 8:
                score -= 20
            scored.append((score, idx, text))
        selected_indexes = sorted(idx for _, idx, _ in sorted(scored, reverse=True)[:limit])
        return [samples[idx] for idx in selected_indexes]

    def _extract_samples_from_json(self, data: Any, target_qq: str) -> tuple[list[str], int, int]:
        samples: list[str] = []
        matched = 0
        scanned = 0
        for item in self._iter_message_like_items(data):
            scanned += 1
            if not self._is_target_sender(item, target_qq):
                continue
            matched += 1
            if self._has_filtered_content(item):
                continue
            text = self._extract_plain_text(item)
            if text:
                samples.append(text)
        return samples, matched, scanned

    def _iter_message_like_items(self, value: Any):
        if isinstance(value, list):
            for item in value:
                yield from self._iter_message_like_items(item)
            return
        if not isinstance(value, dict):
            return
        if self._has_sender_hint(value) and any(key in value for key in ("message", "raw_message", "content", "text", "msg", "elements")):
            yield value
        for key in ("messages", "message_list", "data", "items", "records", "list", "result"):
            child = value.get(key)
            if isinstance(child, (list, dict)):
                yield from self._iter_message_like_items(child)

    def _has_sender_hint(self, item: dict[str, Any]) -> bool:
        if any(key in item for key in ("user_id", "qq", "sender_id", "from_uin", "from", "uin")):
            return True
        sender = item.get("sender")
        return isinstance(sender, dict) and any(key in sender for key in ("user_id", "qq", "id", "uin"))

    def _is_target_sender(self, item: dict[str, Any], target_qq: str) -> bool:
        candidates = [item.get(key) for key in ("user_id", "qq", "sender_id", "from_uin", "from", "uin")]
        sender = item.get("sender")
        if isinstance(sender, dict):
            candidates.extend(sender.get(key) for key in ("user_id", "qq", "id", "uin"))
        return any(str(value).strip() == target_qq for value in candidates if value is not None)

    def _has_filtered_content(self, item: dict[str, Any]) -> bool:
        if item.get("reply") or item.get("quote") or item.get("source"):
            return True
        content = item.get("content")
        if isinstance(content, dict):
            if content.get("reply") or content.get("quote") or content.get("source"):
                return True
            if content.get("resources") or content.get("mentions"):
                return True
            elements = content.get("elements", [])
            if isinstance(elements, list):
                for element in elements:
                    if self._is_filtered_segment(element):
                        return True
        for key in ("message", "content", "msg", "text", "raw_message"):
            value = item.get(key)
            if isinstance(value, list) and any(self._is_filtered_segment(segment) for segment in value):
                return True
            if isinstance(value, str) and self._looks_like_non_text(value):
                return True
        return False

    def _is_filtered_segment(self, segment: Any) -> bool:
        if not isinstance(segment, dict):
            return False
        msg_type = str(segment.get("type", segment.get("msg_type", ""))).lower()
        filtered_types = {"at", "image", "pic", "face", "mface", "reply", "quote", "record", "voice", "video", "file", "json", "xml", "forward", "node", "markdown", "ark"}
        if msg_type in filtered_types:
            return True
        data = segment.get("data")
        if isinstance(data, dict):
            if any(key in data for key in ("url", "file", "filename", "file_id", "resource_id")) and msg_type != "text":
                return True
        return False

    def _looks_like_non_text(self, text: str) -> bool:
        text = str(text or "")
        patterns = (r"\[图片[:：]", r"\[动画表情[:：]", r"\[表情[:：]", r"\[语音[:：]", r"\[视频[:：]", r"\[文件[:：]", r"\[CQ:(image|at|reply|face|record|video|file|json|xml)")
        return any(re.search(pattern, text, re.I) for pattern in patterns)

    def _extract_plain_text(self, item: dict[str, Any]) -> str:
        for key in ("message", "content", "msg", "text", "raw_message"):
            if key in item:
                return self._message_to_text(item.get(key))
        return ""

    def _message_to_text(self, message: Any) -> str:
        if isinstance(message, str):
            return self._clean_import_text(message)
        if isinstance(message, list):
            parts = []
            for segment in message:
                text = self._segment_to_text(segment)
                if text:
                    parts.append(text)
            return self._clean_import_text("".join(parts))
        if isinstance(message, dict):
            msg_type = str(message.get("type", message.get("msg_type", ""))).lower()
            if msg_type and msg_type not in ("text", "plain"):
                return ""
            data = message.get("data")
            if isinstance(data, dict):
                return self._clean_import_text(str(data.get("text", data.get("content", ""))))
            return self._clean_import_text(str(message.get("text", message.get("content", ""))))
        return ""

    def _segment_to_text(self, segment: Any) -> str:
        if isinstance(segment, str):
            return segment
        if not isinstance(segment, dict):
            return ""
        msg_type = str(segment.get("type", segment.get("msg_type", ""))).lower()
        if msg_type not in ("text", "plain"):
            return ""
        data = segment.get("data")
        if isinstance(data, dict):
            return str(data.get("text", data.get("content", "")))
        return str(segment.get("text", segment.get("content", "")))

    def _clean_import_text(self, text: str) -> str:
        text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        text = re.sub(r"\[CQ:at,[^\]]*\]|@\S+", " ", text)
        text = re.sub(r"\[CQ:(?!text)[^\]]*\]", " ", text)
        text = " ".join(text.split())
        return text[:120]


def start_roast_persona_webui(manager: RoastPersonaManager, host: str, port: int, access_token: str, config: dict | None = None, context: Any | None = None) -> RoastPersonaWebUI:
    webui = RoastPersonaWebUI(manager, host, port, access_token, config=config, context=context)
    webui.start_task = asyncio.create_task(webui.start())

    def _log_start_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            log.info("插件管理 WebUI 启动任务已取消")
        except Exception as exc:
            log.error(f"插件管理 WebUI 启动失败: {type(exc).__name__} - {exc}")

    webui.start_task.add_done_callback(_log_start_result)
    return webui
