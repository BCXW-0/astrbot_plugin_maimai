from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import aiohttp

from ... import log
from ..roast.llm_client import resolve_roast_provider_id
from .constants import ALLOWED_TAGS, TAG_CATEGORIES
from .rule_tags import filter_allowed_tags
from .storage import CHART_TAGS_FILE, JOB_STATE_FILE, read_chart_tags, read_job_state, write_json_atomic, write_job_state

CN_TZ = timezone(timedelta(hours=8))


def now_text() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def strip_html(text: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return " ".join(value.split())


class ChartTagUpdateJob:
    def __init__(self, context: Any | None = None, config: dict | None = None):
        self.context = context
        self.config = config or {}
        self.task: asyncio.Task | None = None
        self.stop_requested = False
        self.lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        state = read_job_state()
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        total = len(charts)
        tagged = len([item for item in charts.values() if item.get("llm_tags") or item.get("manual_tags") or item.get("final_tags")])
        failed = len([item for item in charts.values() if item.get("tag_status") == "failed"])
        pending = max(0, total - tagged - failed)
        running = bool(self.task and not self.task.done())
        state.update({
            "ok": True,
            "running": running,
            "total": total,
            "tagged": tagged,
            "failed": failed,
            "pending": pending,
            "path": str(CHART_TAGS_FILE),
            "state_path": str(JOB_STATE_FILE),
        })
        return state

    async def start(self, batch_size: int = 50, interval_seconds: int | None = None) -> dict[str, Any]:
        async with self.lock:
            if self.task and not self.task.done():
                return {"ok": True, "message": "谱面标签更新任务已经在运行", **self.status()}
            self.stop_requested = False
            batch_size = max(1, min(50, int(batch_size or 50)))
            interval = int(interval_seconds if interval_seconds is not None else self.config.get("chart_tag_batch_interval_seconds", 300) or 300)
            interval = max(30, interval)
            self.task = asyncio.create_task(self._run(batch_size, interval))
            state = read_job_state()
            state.update({"running": True, "batch_size": batch_size, "interval_seconds": interval, "started_at": now_text(), "last_error": ""})
            write_job_state(state)
            return {"ok": True, "message": f"谱面标签更新任务已启动，每批最多 {batch_size} 个谱面，批次间隔 {interval} 秒", **self.status()}

    async def stop(self) -> dict[str, Any]:
        self.stop_requested = True
        state = read_job_state()
        state.update({"running": False, "stopped_at": now_text(), "message": "已请求停止，当前谱面处理完成后停止"})
        write_job_state(state)
        return {"ok": True, "message": "已请求停止谱面标签更新任务", **self.status()}

    async def shutdown(self) -> None:
        self.stop_requested = True
        task = self.task
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.task = None
        state = read_job_state()
        state.update({"running": False, "stopped_at": now_text(), "message": "谱面标签更新任务已随插件停止"})
        write_job_state(state)

    async def _run(self, batch_size: int, interval_seconds: int) -> None:
        try:
            while not self.stop_requested:
                processed = await self._process_batch(batch_size)
                state = read_job_state()
                if processed <= 0:
                    state.update({"running": False, "completed_at": now_text(), "message": "没有待处理谱面，标签更新完成", "next_run_at": ""})
                    write_job_state(state)
                    return
                if self.stop_requested:
                    break
                next_time = datetime.now(CN_TZ) + timedelta(seconds=interval_seconds)
                state.update({"running": True, "next_run_at": next_time.isoformat(timespec="seconds"), "message": f"本批已处理 {processed} 个谱面，等待下一批"})
                write_job_state(state)
                await asyncio.sleep(interval_seconds)
        except Exception as exc:
            log.error(f"谱面标签更新任务失败: {type(exc).__name__} - {exc}")
            state = read_job_state()
            state.update({"running": False, "last_error": f"{type(exc).__name__}: {exc}", "failed_at": now_text()})
            write_job_state(state)
        finally:
            state = read_job_state()
            state["running"] = False
            write_job_state(state)

    async def _process_batch(self, batch_size: int) -> int:
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        if not charts:
            from .builder import generate_chart_tags_file
            generate_chart_tags_file()
            data = read_chart_tags()
            charts = data.get("charts", {}) if isinstance(data, dict) else {}
        keys = sorted(charts.keys(), key=self._chart_sort_key)
        processed = 0
        state = read_job_state()
        for key in keys:
            if self.stop_requested or processed >= batch_size:
                break
            chart = charts.get(key, {})
            if self._is_done(chart):
                continue
            state.update({"running": True, "current_key": key, "current_title": chart.get("title", ""), "updated_at": now_text()})
            write_job_state(state)
            ok = False
            last_error = ""
            for attempt in range(2):
                try:
                    await self._tag_chart(chart)
                    ok = True
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    log.error(f"谱面标签抽取失败 {key} attempt={attempt + 1}: {last_error}")
                    if attempt == 0:
                        await asyncio.sleep(2)
            if ok:
                chart["tag_status"] = "done"
                chart["tag_error"] = ""
                chart["updated_at"] = now_text()
            else:
                chart["tag_status"] = "failed"
                chart["tag_error"] = last_error
                chart["updated_at"] = now_text()
            charts[key] = chart
            data["charts"] = charts
            data["generated_at"] = data.get("generated_at") or now_text()
            data["updated_at"] = now_text()
            write_json_atomic(CHART_TAGS_FILE, data)
            processed += 1
            state = read_job_state()
            state.update({
                "processed_total": int(state.get("processed_total", 0) or 0) + 1,
                "last_key": key,
                "last_title": chart.get("title", ""),
                "last_error": "" if ok else last_error,
                "updated_at": now_text(),
            })
            write_job_state(state)
        return processed

    async def _tag_chart(self, chart: dict[str, Any]) -> None:
        evidence = await self._search_chart(chart)
        llm_tags = await self._extract_tags(chart, evidence)
        manual_tags = filter_allowed_tags(chart.get("manual_tags", []))
        final_tags = filter_allowed_tags([*llm_tags, *manual_tags])
        chart["evidence"] = evidence
        chart["llm_tags"] = llm_tags
        chart["manual_tags"] = manual_tags
        chart["final_tags"] = final_tags
        chart["tags"] = final_tags
        chart["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES}

    async def _search_chart(self, chart: dict[str, Any]) -> list[dict[str, str]]:
        query = f"maimai {chart.get('title', '')} {chart.get('difficulty', '')} 谱面 攻略 手元 难点"
        urls = [
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            f"https://www.bing.com/search?q={quote_plus(query)}",
        ]
        results: list[dict[str, str]] = []
        timeout = aiohttp.ClientTimeout(total=12)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for url in urls:
                try:
                    async with session.get(url) as resp:
                        text = await resp.text(errors="ignore")
                    results.extend(self._parse_search_results(text, url))
                except Exception as exc:
                    log.error(f"谱面标签搜索失败: {type(exc).__name__} - {exc}")
                if len(results) >= 6:
                    break
        deduped = []
        seen = set()
        for item in results:
            key = (item.get("title", ""), item.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 6:
                break
        return deduped

    def _parse_search_results(self, html: str, source_url: str) -> list[dict[str, str]]:
        blocks = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]{0,500}?)</a>", html, flags=re.I)
        results = []
        for href, title_html in blocks:
            title = strip_html(title_html)
            if not title or len(title) < 4:
                continue
            if any(skip in href for skip in ("duckduckgo.com", "bing.com/search", "javascript:")):
                continue
            if not re.search(r"maimai|舞萌|mai|MASTER|Re:MASTER|MAS|EXP|谱面|手元|攻略", title, re.I):
                continue
            results.append({"source": "web_search", "title": title[:160], "url": href, "summary": "", "search_url": source_url})
            if len(results) >= 8:
                break
        if results:
            return results
        text = strip_html(html)
        return [{"source": "web_search", "title": "搜索结果摘要", "url": source_url, "summary": text[:700], "search_url": source_url}] if text else []

    async def _extract_tags(self, chart: dict[str, Any], evidence: list[dict[str, str]]) -> list[str]:
        if not self.context:
            return []
        evidence_text = "\n".join(f"{idx + 1}. {item.get('title', '')} {item.get('summary', '')} {item.get('url', '')}" for idx, item in enumerate(evidence))
        prompt = (
            "你正在为舞萌 DX 谱面抽取谱面标签。只能根据搜索资料与谱面基础信息判断，资料不足就输出空数组。"
            "只能从以下白名单选择标签，不允许创造新标签："
            f"{', '.join(ALLOWED_TAGS)}。短纵归一为叠键。"
            "输出严格 JSON：{\"tags\":[...]}，不要解释。\n"
            f"歌曲：{chart.get('title')}\n类型：{chart.get('type')}\n难度：{chart.get('difficulty')}\n等级：{chart.get('level')} 定数：{chart.get('ds')} 拟合定数：{chart.get('fit_diff')}\n"
            f"BPM：{chart.get('bpm')} 谱师：{chart.get('charter')} 物量：{json.dumps(chart.get('notes', {}), ensure_ascii=False)}\n"
            f"搜索资料：\n{evidence_text or '无'}"
        )
        provider_id = await resolve_roast_provider_id(self.context, self.config)
        response = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
        text = response.completion_text if response else ""
        return self._parse_tags(text)

    def _parse_tags(self, text: str) -> list[str]:
        raw = str(text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        try:
            data = json.loads(raw)
        except Exception:
            match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
            data = json.loads(match.group(0)) if match else []
        if isinstance(data, dict):
            tags = data.get("tags", [])
        else:
            tags = data
        return filter_allowed_tags(tags if isinstance(tags, list) else [])

    def _is_done(self, chart: dict[str, Any]) -> bool:
        return chart.get("tag_status") == "done" or bool(chart.get("llm_tags"))

    def _chart_sort_key(self, key: str) -> tuple[int, int, str]:
        song_id, _, level = str(key).partition(":")
        try:
            song_num = int(song_id)
        except Exception:
            song_num = 10**12
        try:
            level_num = int(level)
        except Exception:
            level_num = 99
        return song_num, level_num, key
