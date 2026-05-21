from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import unicodedata
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import quote_plus, urlparse

import aiohttp

from ... import log
from .constants import ALLOWED_TAGS, TAG_CATEGORIES, TAG_RULE_VERSION
from .rule_tags import filter_allowed_tags
from .storage import CHART_TAGS_FILE, JOB_STATE_FILE, read_chart_tags, read_job_state, write_json_atomic, write_job_state

CN_TZ = timezone(timedelta(hours=8))
CHART_SEARCH_MAX_SECONDS = 28.0
CHART_SEARCH_MAX_REQUESTS = 16
HIGH_DS_SEARCH_MAX_SECONDS = 42.0
HIGH_DS_SEARCH_MAX_REQUESTS = 28
SEARCH_REQUEST_MAX_SECONDS = 6.0
GAMERCH_REQUEST_MAX_SECONDS = 24.0
SEARCH_CACHE_MAX_ENTRIES = 512
CHART_SEARCH_CACHE_MAX_ENTRIES = 512
GAMERCH_INDEX_CACHE_SECONDS = 86400
SOURCE_COOLDOWN_SECONDS = 180
BILIBILI_BAN_COOLDOWN_SECONDS = 900
CHART_UPDATE_PAUSE_SECONDS = 0.03
HIGH_PRIORITY_DS = 13.2
NOTES_TOTAL_BURST_THRESHOLD = 950
NOTES_TOTAL_STAMINA_THRESHOLD = 1050
NOTES_DENSITY_STAMINA_THRESHOLD = 4.8
NOTES_DENSITY_SPEED_THRESHOLD = 5.1

TAG_KEYWORD_RULES = [
    ("节奏", [r"节奏\s*(怪|难|難|复杂|複雑)", r"(怪|变|變)\s*节奏", r"(怪|变|變)\s*拍", r"リズム\s*(難|むず|複雑)", r"変則\s*リズム", r"tricky\s*rhythm"]),
    ("背谱", [r"背谱", r"背譜", r"记忆", r"記憶", r"暗记", r"暗記", r"初见杀", r"初見殺", r"覚えゲー", r"譜面を覚"]),
    ("管子", [r"管子", r"管子海", r"slide\s*(多|地帯|festival)", r"スライド\s*(多|地帯)", r"Slide\s*(多|地帯)"]),
    ("定位", [r"定位", r"键盘定位", r"按键定位", r"手位", r"按区", r"按區", r"分区", r"分區"]),
    ("散打", [r"散打", r"散点", r"散點", r"散键", r"散鍵", r"乱打", r"亂打", r"乱れ打ち", r"Tap\s*(多|地帯)"]),
    ("手序", [r"手序", r"运指", r"運指", r"骗手", r"騙手", r"骗招", r"换手", r"換手", r"交叉手"]),
    ("飞手", [r"飞手", r"飛手", r"飞键", r"飛鍵", r"大位移", r"位移散点", r"位移交互", r"远距离", r"遠距離", r"出张", r"出張"]),
    ("防蹭", [r"防蹭", r"防擦", r"蹭星", r"星星.*定位", r"星星.*防", r"avoid\s*touch"]),
    ("留尾", [r"留尾", r"留尾巴", r"尾巴", r"hold\s*尾", r"slide\s*尾", r"尾判", r"片手.*拘束", r"拘束されながら"]),
    ("爆发", [r"爆发", r"爆發", r"发狂", r"發狂", r"尾杀", r"尾殺", r"瞬间密度", r"瞬間密度", r"局部高密度", r"局所高密度", r"高密度地帯"]),
    ("底力", [r"底力", r"综合力", r"綜合力", r"総合力", r"高物量", r"物量譜面", r"物量", r"高ノーツ", r"高総数", r"耐力", r"持久力", r"体力", r"體力", r"硬抗", r"硬扛", r"休息少", r"持续高密度", r"持續高密度"]),
    ("交互", [r"交互", r"trill", r"トリル"]),
    ("定拍", [r"定拍", r"正拍", r"等拍", r"均等拍", r"オンビート", r"on\s*beat"]),
    ("一笔划", [r"一笔划", r"一笔画", r"一筆画", r"一筆書き"]),
    ("双押", [r"双押", r"雙押", r"双押海", r"同押", r"同時押し", r"大位移双押"]),
    ("扫键", [r"扫键", r"掃鍵", r"扫圈", r"掃圈", r"转圈", r"轉圈", r"回转", r"回転", r"旋转", r"旋轉", r"流し", r"回転配置", r"半回転"]),
    ("死镰", [r"死镰", r"死鎌", r"镰刀", r"鎌刀"]),
    ("错位", [r"错位", r"错拍", r"对拍", r"對拍", r"伪对拍", r"不匀速", r"不均匀", r"拍划", r"拍画", r"一拍划", r"一拍画", r"\d+\s*分划", r"\d+\s*分\s*slide"]),
    ("手速", [r"手速", r"键密度", r"鍵密度", r"高速配置", r"高\s*bpm", r"高速\s*bpm", r"bpm\s*(2[4-9]\d|[3-9]\d\d)", r"(240|25\d|26\d|27\d|28\d|29\d|[3-9]\d\d)\s*bpm", r"(2[4-9]\d|[3-9]\d\d)\s*bpm", r"(2[4-9]\d|[3-9]\d\d)\s*BPM", r"(2[4-9]\d|[3-9]\d\d)\s*以上.*bpm", r"(2[4-9]\d|[3-9]\d\d)\s*\\+", r"(2[4-9]|[3-9]\d|[1-9]\d{2,})\s*分\s*交互", r"24分", r"32分", r"64分", r"素早い動き", r"高速の", r"高密度"]),
    ("纵连", [r"长纵", r"長縦", r"長纵", r"长縦", r"纵连", r"縦連", r"縱連", r"竖连", r"竪連", r"微縦連"]),
    ("子弹", [r"子弹", r"短纵", r"短縦", r"短い縦連", r"叠键", r"叠押", r"2\s*连纵", r"3\s*连纵", r"二连纵", r"三连纵"]),
    ("跳拍", [r"跳拍", r"跳\s*拍", r"跳节奏", r"跳節奏", r"リズム\s*飛び", r"拍が飛ぶ"]),
    ("延迟星星", [r"延迟星星", r"延迟星", r"延遲星星", r"延遲星", r"延迟.*星", r"延遲.*星", r"遅延.*星", r"遅れて.*星"]),
    ("如龙", [r"如龙", r"如龍", r"如龙扫", r"如龍掃", r"如龙\s*扫", r"如龍\s*掃", r"龍.*掃", r"龙.*扫"]),
    ("秒划", [r"秒划", r"秒画", r"秒畫", r"秒划星星", r"秒画星星", r"秒畫星星", r"即划", r"即画", r"瞬間.*スライド"]),
    ("拆谱", [r"拆谱", r"拆譜", r"拆解", r"拆分", r"分解", r"拆配置", r"譜面.*分解"]),
]


def now_text() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def strip_html(text: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return " ".join(value.split())


def strip_html_lines(text: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
    value = re.sub(r"<\s*(br|/p|/li|/h[1-6]|/tr|/div)\b[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


class ChartTagUpdateJob:
    def __init__(self, context: Any | None = None, config: dict | None = None):
        self.context = context
        self.config = config or {}
        self.worker_thread: threading.Thread | None = None
        self.stop_requested = False
        self.lock = asyncio.Lock()
        self._bilibili_detail_cache: dict[str, dict[str, str]] = {}
        self._gamerch_page_cache: dict[str, dict[str, str]] = {}
        self._gamerch_song_index: dict[str, list[dict[str, str]]] = {}
        self._gamerch_song_index_loaded_at = 0.0
        self._search_result_cache: dict[tuple[str, str], list[dict[str, str]]] = {}
        self._chart_search_cache: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
        self._source_cooldowns: dict[str, float] = {}

    def status(self) -> dict[str, Any]:
        state = read_job_state()
        data = read_chart_tags()
        charts = data.get("charts", {}) if isinstance(data, dict) else {}
        total = len(charts)
        tagged = len([item for item in charts.values() if self._valid_tags(item)])
        failed = len([item for item in charts.values() if item.get("tag_status") == "failed"])
        no_evidence = len([item for item in charts.values() if item.get("tag_status") == "no_evidence"])
        pending = len([item for item in charts.values() if not self._is_done(item)])
        running = bool(self.worker_thread and self.worker_thread.is_alive())
        state.update({
            "ok": True,
            "running": running,
            "stop_requested": self.stop_requested,
            "total": total,
            "tagged": tagged,
            "untagged": max(0, total - tagged),
            "failed": failed,
            "no_evidence": no_evidence,
            "pending": pending,
            "path": str(CHART_TAGS_FILE),
            "state_path": str(JOB_STATE_FILE),
        })
        return state

    async def start(self, batch_size: int = 50) -> dict[str, Any]:
        async with self.lock:
            if self.worker_thread and self.worker_thread.is_alive():
                status = await asyncio.to_thread(self.status)
                return {"ok": True, "message": "谱面标签更新任务已经在运行", **status}
            status = await asyncio.to_thread(self.status)
            if int(status.get("pending", 0) or 0) <= 0:
                message = "没有待处理谱面，标签更新完成"
                state = read_job_state()
                state.update({"running": False, "completed_at": now_text(), "last_error": "", "message": message, "next_run_at": ""})
                write_job_state(state)
                status = await asyncio.to_thread(self.status)
                status["message"] = message
                return status
            self.stop_requested = False
            batch_size = max(1, min(50, int(batch_size or 50)))
            self.worker_thread = threading.Thread(
                target=self._run_in_worker_thread,
                args=(batch_size,),
                name="maimai-chart-tags",
                daemon=True,
            )
            self.worker_thread.start()
            state = read_job_state()
            message = f"谱面标签更新任务已启动，每批最多 {batch_size} 个谱面，批次之间连续执行"
            state.update({"running": True, "batch_size": batch_size, "interval_seconds": 0, "started_at": now_text(), "last_error": "", "message": message, "next_run_at": ""})
            write_job_state(state)
            result = await asyncio.to_thread(self.status)
            result["message"] = message
            return result

    async def stop(self) -> dict[str, Any]:
        self.stop_requested = True
        state = read_job_state()
        message = "已请求停止，当前谱面处理完成后停止"
        state.update({"running": bool(self.worker_thread and self.worker_thread.is_alive()), "stopped_at": now_text(), "message": message})
        write_job_state(state)
        result = await asyncio.to_thread(self.status)
        result["message"] = message
        return result

    async def shutdown(self) -> None:
        self.stop_requested = True
        thread = self.worker_thread
        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 20)
        if self.worker_thread and not self.worker_thread.is_alive():
            self.worker_thread = None
        state = read_job_state()
        state.update({"running": False, "stopped_at": now_text(), "message": "谱面标签更新任务已随插件停止"})
        write_job_state(state)

    def _run_in_worker_thread(self, batch_size: int) -> None:
        try:
            asyncio.run(self._run(batch_size))
        except Exception as exc:
            log.error(f"谱面标签更新线程失败: {type(exc).__name__} - {exc}")
            state = read_job_state()
            state.update({"running": False, "last_error": f"{type(exc).__name__}: {exc}", "failed_at": now_text()})
            write_job_state(state)

    async def _run(self, batch_size: int) -> None:
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
                state.update({"running": True, "next_run_at": "", "message": f"本批已处理 {processed} 个谱面，继续下一批"})
                write_job_state(state)
                await asyncio.sleep(0)
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
        keys = sorted(charts.keys(), key=lambda key: self._chart_sort_key(key, charts.get(key, {})))
        processed = 0
        state = read_job_state()
        for key in keys:
            if self.stop_requested or processed >= batch_size:
                break
            snapshot_chart = charts.get(key, {})
            if not isinstance(snapshot_chart, dict):
                continue
            if self._is_done(snapshot_chart):
                continue
            current_data = read_chart_tags()
            current_charts = current_data.get("charts", {}) if isinstance(current_data, dict) else {}
            chart = current_charts.get(key, {}) if isinstance(current_charts, dict) else {}
            if not isinstance(chart, dict):
                continue
            if self._is_done(chart):
                continue
            state.update({"running": True, "current_key": key, "current_title": chart.get("title", ""), "updated_at": now_text()})
            write_job_state(state)
            handled = False
            last_error = ""
            for attempt in range(2):
                try:
                    tagged = await self._tag_chart(chart)
                    if not tagged:
                        last_error = str(chart.get("tag_error", "") or "未找到可用的中文平台玩家资料")
                    handled = True
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    log.error(f"谱面标签抽取失败 {key} attempt={attempt + 1}: {last_error}")
                    if attempt == 0:
                        await asyncio.sleep(2)
            if handled:
                if chart.get("tag_status") != "no_evidence":
                    chart["tag_status"] = "done"
                chart["tag_error"] = "" if chart.get("tag_status") == "done" else last_error
                chart["updated_at"] = now_text()
            else:
                chart["tag_status"] = "failed"
                chart["tag_error"] = last_error
                chart["updated_at"] = now_text()
            latest_data = read_chart_tags()
            latest_charts = latest_data.get("charts", {}) if isinstance(latest_data, dict) else {}
            if not isinstance(latest_charts, dict):
                latest_charts = {}
            latest_chart = latest_charts.get(key, {})
            latest_charts[key] = self._merge_chart_update(latest_chart if isinstance(latest_chart, dict) else {}, chart)
            latest_data["charts"] = latest_charts
            latest_data["generated_at"] = latest_data.get("generated_at") or now_text()
            latest_data["updated_at"] = now_text()
            latest_data["tag_rule_version"] = TAG_RULE_VERSION
            latest_data["allowed_tags"] = ALLOWED_TAGS
            write_json_atomic(CHART_TAGS_FILE, latest_data)
            processed += 1
            state = read_job_state()
            state.update({
                "processed_total": int(state.get("processed_total", 0) or 0) + 1,
                "last_key": key,
                "last_title": chart.get("title", ""),
                "last_error": "" if handled and chart.get("tag_status") == "done" else last_error,
                "updated_at": now_text(),
            })
            write_job_state(state)
            await asyncio.sleep(CHART_UPDATE_PAUSE_SECONDS)
        return processed

    def _merge_chart_update(self, current: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        for field in ("evidence", "llm_tags", "tag_status", "tag_error", "tag_rule_version", "updated_at"):
            if field in updated:
                merged[field] = updated[field]
        manual_tags = filter_allowed_tags(current.get("manual_tags", []))
        llm_tags = filter_allowed_tags(updated.get("llm_tags", []))
        if not llm_tags and updated.get("tag_status") in {"no_evidence", "failed"}:
            llm_tags = filter_allowed_tags(current.get("llm_tags") or current.get("final_tags") or current.get("tags") or [])
        final_tags = filter_allowed_tags([*llm_tags, *manual_tags])
        merged["manual_tags"] = manual_tags
        merged["llm_tags"] = llm_tags
        merged["final_tags"] = final_tags
        merged["tags"] = final_tags
        merged["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES}
        if final_tags:
            merged["tag_status"] = "done"
            merged["tag_error"] = ""
        return merged

    async def _tag_chart(self, chart: dict[str, Any]) -> bool:
        evidence = await self._search_chart(chart)
        chart["evidence"] = evidence
        if not evidence:
            chart["llm_tags"] = []
            chart["final_tags"] = filter_allowed_tags(chart.get("manual_tags", []))
            chart["tags"] = chart["final_tags"]
            chart["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in chart["final_tags"] if tag in TAG_CATEGORIES}
            chart["tag_status"] = "done" if chart["final_tags"] else "no_evidence"
            chart["tag_rule_version"] = TAG_RULE_VERSION
            return False
        llm_tags = self._extract_tags_from_evidence(evidence)
        manual_tags = filter_allowed_tags(chart.get("manual_tags", []))
        final_tags = filter_allowed_tags([*llm_tags, *manual_tags])
        chart["llm_tags"] = llm_tags
        chart["manual_tags"] = manual_tags
        chart["final_tags"] = final_tags
        chart["tags"] = final_tags
        chart["tag_categories"] = {tag: TAG_CATEGORIES[tag] for tag in final_tags if tag in TAG_CATEGORIES}
        if not final_tags:
            chart["tag_status"] = "no_evidence"
            chart["tag_error"] = "中文平台资料不足，未抽取到白名单标签"
            chart["tag_rule_version"] = TAG_RULE_VERSION
            return False
        chart["tag_status"] = "done"
        chart["tag_error"] = ""
        chart["tag_rule_version"] = TAG_RULE_VERSION
        return True

    async def _search_chart(self, chart: dict[str, Any]) -> list[dict[str, str]]:
        title = str(chart.get("title", "") or "").strip()
        difficulty = str(chart.get("difficulty", "") or "").strip()
        chart_cache_key = self._chart_search_cache_key(chart)
        cached = self._chart_search_cache.get(chart_cache_key)
        if cached is not None:
            return [dict(item) for item in cached]
        results: list[dict[str, str]] = []
        budget = self._new_search_budget(chart)
        high_priority = self._is_high_priority_chart(chart)
        timeout = aiohttp.ClientTimeout(total=14 if high_priority else 10, connect=3, sock_read=10 if high_priority else 7)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            gamerch_direct = await self._search_gamerch_direct(session, chart, budget)
            results = self._merge_results(results, gamerch_direct)
            prepared = await self._prepare_evidence(session, results, title, difficulty, budget)
            if self._extract_tags_from_evidence(prepared):
                return self._remember_chart_search(chart_cache_key, prepared)
            for tier in self._search_query_tiers(chart):
                for query in tier:
                    if not self._has_search_budget(budget):
                        return self._remember_chart_search(chart_cache_key, await self._prepare_evidence(session, results, title, difficulty, budget))
                    results = self._merge_results(results, await self._cached_search("bilibili_api", query, lambda: self._search_bilibili_api(session, query, limit=8), budget))
                    results = self._merge_results(results, await self._cached_search("bilibili_html", query, lambda: self._search_bilibili_html(session, query, limit=8), budget))
                    prepared = await self._prepare_evidence(session, results, title, difficulty, budget)
                    if self._extract_tags_from_evidence(prepared):
                        return self._remember_chart_search(chart_cache_key, prepared)
                if len(results) < 8 and self._has_search_budget(budget):
                    for query in tier:
                        results = self._merge_results(results, await self._cached_search("gamerch_html", query, lambda: self._search_gamerch_html(session, query, limit=6), budget))
                        prepared = await self._prepare_evidence(session, results, title, difficulty, budget)
                        if self._extract_tags_from_evidence(prepared):
                            return self._remember_chart_search(chart_cache_key, prepared)
                        if not self._has_search_budget(budget):
                            return self._remember_chart_search(chart_cache_key, prepared)
            for query in self._youtube_queries(chart):
                if not self._has_search_budget(budget):
                    break
                results = self._merge_results(results, await self._cached_search("youtube_html", query, lambda: self._search_youtube_html(session, query, limit=6), budget))
                prepared = await self._prepare_evidence(session, results, title, difficulty, budget)
                if self._extract_tags_from_evidence(prepared):
                    return self._remember_chart_search(chart_cache_key, prepared)
            return self._remember_chart_search(chart_cache_key, await self._prepare_evidence(session, results, title, difficulty, budget))

    def _new_search_budget(self, chart: dict[str, Any] | None = None) -> dict[str, float | int]:
        high_priority = bool(chart and self._is_high_priority_chart(chart))
        return {
            "deadline": time.monotonic() + (HIGH_DS_SEARCH_MAX_SECONDS if high_priority else CHART_SEARCH_MAX_SECONDS),
            "requests": 0,
            "max_requests": HIGH_DS_SEARCH_MAX_REQUESTS if high_priority else CHART_SEARCH_MAX_REQUESTS,
        }

    def _has_search_budget(self, budget: dict[str, float | int]) -> bool:
        return (
            not self.stop_requested
            and int(budget.get("requests", 0) or 0) < int(budget.get("max_requests", CHART_SEARCH_MAX_REQUESTS) or CHART_SEARCH_MAX_REQUESTS)
            and time.monotonic() < float(budget.get("deadline", 0) or 0)
        )

    def _search_time_remaining(self, budget: dict[str, float | int]) -> float:
        return max(0.0, float(budget.get("deadline", 0) or 0) - time.monotonic())

    async def _cached_search(self, source: str, query: str, fetcher: Any, budget: dict[str, float | int]) -> list[dict[str, str]]:
        cache_key = (source, " ".join(str(query or "").split()).lower())
        cached = self._search_result_cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]
        if self._source_is_cooling_down(source) or not self._has_search_budget(budget):
            return []
        budget["requests"] = int(budget.get("requests", 0) or 0) + 1
        try:
            timeout = min(SEARCH_REQUEST_MAX_SECONDS, self._search_time_remaining(budget))
            results = await asyncio.wait_for(fetcher(), timeout=timeout)
        except Exception as exc:
            self._cool_down_source(source, SOURCE_COOLDOWN_SECONDS, f"{type(exc).__name__}: {exc}")
            results = []
        self._remember_search_result(cache_key, results)
        return [dict(item) for item in results]

    def _remember_search_result(self, cache_key: tuple[str, str], results: list[dict[str, str]]) -> None:
        if len(self._search_result_cache) >= SEARCH_CACHE_MAX_ENTRIES:
            self._search_result_cache.pop(next(iter(self._search_result_cache)), None)
        self._search_result_cache[cache_key] = [dict(item) for item in results]

    def _chart_search_cache_key(self, chart: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(chart.get("song_id", "") or ""),
            str(chart.get("title", "") or "").strip().lower(),
            str(chart.get("difficulty", "") or "").strip().lower(),
            str(chart.get("level", "") or "").strip(),
        )

    def _is_high_priority_chart(self, chart: dict[str, Any]) -> bool:
        try:
            return float(chart.get("ds", 0) or 0) >= HIGH_PRIORITY_DS
        except Exception:
            return False

    def _chart_ds(self, chart: dict[str, Any]) -> float:
        try:
            return float(chart.get("ds", 0) or 0)
        except Exception:
            return 0.0

    def _remember_chart_search(self, cache_key: tuple[str, str, str, str], evidence: list[dict[str, str]]) -> list[dict[str, str]]:
        if len(self._chart_search_cache) >= CHART_SEARCH_CACHE_MAX_ENTRIES:
            self._chart_search_cache.pop(next(iter(self._chart_search_cache)), None)
        self._chart_search_cache[cache_key] = [dict(item) for item in evidence]
        return [dict(item) for item in evidence]

    def _merge_results(self, old_results: list[dict[str, str]], new_results: list[dict[str, str]]) -> list[dict[str, str]]:
        merged = list(old_results)
        seen = {(item.get("source", ""), item.get("url", ""), item.get("title", "")) for item in merged}
        for item in new_results:
            key = (item.get("source", ""), item.get("url", ""), item.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    def _source_is_cooling_down(self, source: str) -> bool:
        return time.monotonic() < self._source_cooldowns.get(source, 0)

    def _cool_down_source(self, source: str, seconds: int, reason: str) -> None:
        until = time.monotonic() + seconds
        if until <= self._source_cooldowns.get(source, 0):
            return
        self._source_cooldowns[source] = until
        log.debug(f"{source} 暂时跳过 {seconds} 秒: {reason}")

    async def _prepare_evidence(
        self,
        session: aiohttp.ClientSession,
        results: list[dict[str, str]],
        title: str,
        difficulty: str,
        budget: dict[str, float | int] | None = None,
    ) -> list[dict[str, str]]:
        results = await self._enrich_bilibili_results(session, results, max_videos=5, budget=budget)
        results = [item for item in results if self._matches_chart(item, title, difficulty)]
        results.sort(key=lambda item: self._evidence_rank(item, title, difficulty))
        deduped = []
        seen = set()
        for item in results:
            key = (item.get("title", ""), item.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 10:
                break
        return deduped

    def _search_query_tiers(self, chart: dict[str, Any]) -> list[list[str]]:
        title = str(chart.get("title", "") or "").strip()
        difficulty = str(chart.get("difficulty", "") or "").strip()
        level = str(chart.get("level", "") or "").strip()
        diff_aliases = self._difficulty_aliases(difficulty)
        config_terms = "配置 难点 攻略 手元"
        jp_config_terms = "譜面攻略 難所 配置 個人差"
        tiers = [
            [
                f"舞萌DX {title} {' '.join(diff_aliases)} {level} AP 手元 配置 难点",
                f"maimai {title} {' '.join(diff_aliases)} {level} {jp_config_terms}",
                f"{title} {' '.join(diff_aliases)} {level} 譜面データ 譜面・ゲーム面",
            ],
            [
                f"maimai 舞萌DX {title} {difficulty} {level} 谱面 手元 攻略 难点",
                f"site:bilibili.com/video {title} {difficulty} maimai",
                f"site:bilibili.com/read {title} {difficulty} maimai 攻略",
                f"{title} {difficulty} {level} {config_terms}",
            ],
            [
                f"maimai {title} {' '.join(diff_aliases)} 譜面攻略 譜面確認 個人差",
                f"site:gamerch.com/maimai {title} {difficulty}",
                f"maimai {title} {' '.join(diff_aliases)} トリル 乱打 縦連 スライド",
                f"maimai {title} {' '.join(diff_aliases)} 物量 高密度 回転 同時押し",
            ],
        ]
        if self._is_high_priority_chart(chart):
            tiers.append([
                f"舞萌DX {title} {difficulty} {level} 配置 难点 爆发 底力 手速",
                f"maimai {title} {' '.join(diff_aliases)} {level} 難所 乱打 縦連 回転",
                f"maimai {title} {' '.join(diff_aliases)} {level} 高ノーツ 物量 譜面データ",
            ])
        seen = set()
        output: list[list[str]] = []
        for tier in tiers:
            clean_tier = []
            for query in tier:
                key = " ".join(query.split()).lower()
                if key in seen:
                    continue
                seen.add(key)
                clean_tier.append(query)
            if clean_tier:
                output.append(clean_tier)
        return output

    def _youtube_queries(self, chart: dict[str, Any]) -> list[str]:
        title = str(chart.get("title", "") or "").strip()
        difficulty = str(chart.get("difficulty", "") or "").strip()
        level = str(chart.get("level", "") or "").strip()
        diff_aliases = self._difficulty_aliases(difficulty)
        queries = [
            f"maimai {title} {' '.join(diff_aliases)} {level} 譜面確認",
            f"maimai {title} {difficulty} AP 手元 外部出力",
            f"maimai {title} {' '.join(diff_aliases)} {level} chart view",
        ]
        if self._is_high_priority_chart(chart):
            queries.extend([
                f"maimai {title} {' '.join(diff_aliases)} {level} 譜面攻略",
                f"maimai {title} {difficulty} {level} ALL PERFECT",
            ])
        seen = set()
        output = []
        for query in queries:
            key = " ".join(query.split()).lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(query)
        return output

    def _difficulty_aliases(self, difficulty: str) -> list[str]:
        value = str(difficulty or "").lower()
        if value.startswith("re:"):
            return ["Re:MASTER", "ReMaster", "白谱", "白", "白谱面"]
        if value.startswith("master"):
            return ["MASTER", "紫谱", "紫", "紫谱面"]
        if value.startswith("expert"):
            return ["EXPERT", "红谱", "红", "红谱面"]
        return [difficulty] if difficulty else []

    async def _search_bilibili_api(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[dict[str, str]]:
        url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&page=1&keyword={quote_plus(query)}"
        try:
            async with session.get(url) as resp:
                text = await resp.text(errors="ignore")
            data = json.loads(text)
            if int(data.get("code", -1)) != 0:
                code = data.get("code")
                self._log_bilibili_api_unavailable(code, data.get("message", ""))
                if int(code or 0) == -412:
                    self._cool_down_source("bilibili_api", BILIBILI_BAN_COOLDOWN_SECONDS, "request was banned")
                return []
            items = (data.get("data") or {}).get("result") or []
            return self._parse_bilibili_items(items, url, limit=limit)
        except Exception as exc:
            self._log_bilibili_api_unavailable(type(exc).__name__, str(exc))
            return []

    def _log_bilibili_api_unavailable(self, code: Any, message: Any) -> None:
        reason = f"{code} {message}".strip()
        if getattr(self, "_bilibili_api_unavailable_reason", "") == reason:
            log.debug(f"Bilibili 搜索 API 仍不可用，继续使用搜索页 fallback: {reason}")
            return
        self._bilibili_api_unavailable_reason = reason
        log.info(f"Bilibili 搜索 API 暂不可用，继续使用搜索页 fallback: {reason}")

    async def _enrich_bilibili_results(
        self,
        session: aiohttp.ClientSession,
        results: list[dict[str, str]],
        max_videos: int = 8,
        budget: dict[str, float | int] | None = None,
    ) -> list[dict[str, str]]:
        unique_bvids: list[str] = []
        for item in results:
            if item.get("source") != "bilibili":
                continue
            bvid = self._extract_bvid(item.get("url", ""))
            if not bvid or bvid in unique_bvids:
                continue
            unique_bvids.append(bvid)
            if len(unique_bvids) >= max_videos:
                break
        missing = [bvid for bvid in unique_bvids if bvid not in self._bilibili_detail_cache]
        if budget is not None:
            allowed_missing = []
            for bvid in missing:
                if not self._has_search_budget(budget):
                    break
                budget["requests"] = int(budget.get("requests", 0) or 0) + 1
                allowed_missing.append(bvid)
            missing = allowed_missing
        if missing:
            semaphore = asyncio.Semaphore(2)

            async def fetch_one(bvid: str) -> None:
                async with semaphore:
                    try:
                        if budget is None:
                            self._bilibili_detail_cache[bvid] = await self._fetch_bilibili_video_detail(session, bvid)
                        else:
                            timeout = min(SEARCH_REQUEST_MAX_SECONDS, self._search_time_remaining(budget))
                            self._bilibili_detail_cache[bvid] = await asyncio.wait_for(self._fetch_bilibili_video_detail(session, bvid), timeout=timeout)
                    except Exception as exc:
                        log.debug(f"Bilibili 视频详情补全失败 {bvid}: {type(exc).__name__} - {exc}")
                        self._bilibili_detail_cache[bvid] = {}

            await asyncio.gather(*(fetch_one(bvid) for bvid in missing))
        enriched: list[dict[str, str]] = []
        for item in results:
            bvid = self._extract_bvid(item.get("url", "")) if item.get("source") == "bilibili" else ""
            detail = self._bilibili_detail_cache.get(bvid, {}) if bvid else {}
            if detail:
                item = {**item, **{key: value for key, value in detail.items() if value}}
            enriched.append(item)
        return enriched

    def _extract_bvid(self, url: str) -> str:
        match = re.search(r"(BV[0-9A-Za-z]+)", str(url or ""))
        return match.group(1) if match else ""

    async def _fetch_bilibili_video_detail(self, session: aiohttp.ClientSession, bvid: str) -> dict[str, str]:
        view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={quote_plus(bvid)}"
        tags_url = f"https://api.bilibili.com/x/tag/archive/tags?bvid={quote_plus(bvid)}"
        title = ""
        desc = ""
        tags = ""
        try:
            async with session.get(view_url, headers={"Referer": "https://www.bilibili.com/"}) as resp:
                view_text = await resp.text(errors="ignore")
            view_data = json.loads(view_text)
            if int(view_data.get("code", -1)) == 0:
                data = view_data.get("data") or {}
                title = strip_html(str(data.get("title", "")))
                desc = strip_html(str(data.get("desc", "")))
        except Exception as exc:
            log.debug(f"Bilibili 视频详情补全失败 {bvid}: {type(exc).__name__} - {exc}")
        try:
            async with session.get(tags_url, headers={"Referer": "https://www.bilibili.com/"}) as resp:
                tags_text = await resp.text(errors="ignore")
            tags_data = json.loads(tags_text)
            if int(tags_data.get("code", -1)) == 0:
                tags = " | ".join(strip_html(str(item.get("tag_name", ""))) for item in (tags_data.get("data") or []) if isinstance(item, dict))
        except Exception as exc:
            log.debug(f"Bilibili 视频标签补全失败 {bvid}: {type(exc).__name__} - {exc}")
        summary = " | ".join(part for part in [desc, tags] if part)
        if not title and not summary:
            return {}
        return {"title": title[:160], "summary": summary[:1000]}

    async def _search_bilibili_html(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[dict[str, str]]:
        url = f"https://search.bilibili.com/all?keyword={quote_plus(query)}"
        try:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    self._cool_down_source("bilibili_html", SOURCE_COOLDOWN_SECONDS, f"HTTP {resp.status}")
                    return []
                text = await resp.text(errors="ignore")
            return self._parse_search_results(text, url, "bilibili", limit=limit)
        except Exception as exc:
            self._cool_down_source("bilibili_html", SOURCE_COOLDOWN_SECONDS, f"{type(exc).__name__}: {exc}")
            log.warning(f"Bilibili 搜索页失败: {type(exc).__name__} - {exc}")
            return []

    async def _search_gamerch_html(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[dict[str, str]]:
        url = f"https://gamerch.com/maimai/search?keyword={quote_plus(query)}"
        try:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    self._cool_down_source("gamerch_html", SOURCE_COOLDOWN_SECONDS, f"HTTP {resp.status}")
                    return []
                text = await resp.text(errors="ignore")
            return self._parse_search_results(text, url, "gamerch", limit=limit)
        except Exception as exc:
            self._cool_down_source("gamerch_html", SOURCE_COOLDOWN_SECONDS, f"{type(exc).__name__}: {exc}")
            log.warning(f"Gamerch 搜索页失败: {type(exc).__name__} - {exc}")
            return []

    async def _search_gamerch_direct(
        self,
        session: aiohttp.ClientSession,
        chart: dict[str, Any],
        budget: dict[str, float | int],
    ) -> list[dict[str, str]]:
        if not self._has_search_budget(budget):
            return []
        title = str(chart.get("title", "") or "").strip()
        if not title:
            return []
        index = await self._get_gamerch_song_index(session, budget)
        candidates = self._gamerch_index_candidates(index, title)
        if not candidates:
            return []
        results = []
        for candidate in candidates[:3]:
            if not self._has_search_budget(budget):
                break
            url = candidate.get("url", "")
            if not url:
                continue
            budget["requests"] = int(budget.get("requests", 0) or 0) + 1
            page = await self._fetch_gamerch_song_page(session, url, budget)
            if not page:
                continue
            results.append({
                "source": "gamerch",
                "title": page.get("title") or candidate.get("title", "")[:160],
                "url": url,
                "summary": self._gamerch_chart_summary(page.get("content", ""), chart)[:1400],
                "search_url": "https://gamerch.com/maimai/545589",
            })
            notes_summary = self._gamerch_notes_summary(page.get("content", ""), chart)
            if notes_summary:
                results.append({
                    "source": "gamerch",
                    "title": f"{page.get('title') or candidate.get('title', '')} 譜面データ",
                    "url": url,
                    "summary": notes_summary[:1400],
                    "search_url": "https://gamerch.com/maimai/545589",
                })
        return [item for item in results if item.get("summary")]

    def _gamerch_index_candidates(self, index: dict[str, list[dict[str, str]]], title: str) -> list[dict[str, str]]:
        key = self._normalize_match_text(title)
        if not key:
            return []
        candidates = list(index.get(key, []))
        if candidates:
            return candidates
        title_aliases = [
            key,
            key.replace("reborn", ""),
            self._normalize_match_text(re.sub(r"\([^)]*\)", "", str(title or ""))),
            self._normalize_match_text(re.sub(r"\[[^]]*\]", "", str(title or ""))),
        ]
        aliases = [alias for alias in dict.fromkeys(title_aliases) if len(alias) >= 2]
        seen = set()
        fuzzy: list[dict[str, str]] = []
        for entry_key, entries in index.items():
            if not any(alias == entry_key or (len(alias) >= 5 and (alias in entry_key or entry_key in alias)) for alias in aliases):
                continue
            for entry in entries:
                url = entry.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                fuzzy.append(entry)
                if len(fuzzy) >= 3:
                    return fuzzy
        return fuzzy

    async def _get_gamerch_song_index(self, session: aiohttp.ClientSession, budget: dict[str, float | int]) -> dict[str, list[dict[str, str]]]:
        if self._gamerch_song_index and time.monotonic() - self._gamerch_song_index_loaded_at < GAMERCH_INDEX_CACHE_SECONDS:
            return self._gamerch_song_index
        if self._source_is_cooling_down("gamerch_index") or not self._has_search_budget(budget):
            return self._gamerch_song_index
        budget["requests"] = int(budget.get("requests", 0) or 0) + 1
        url = "https://gamerch.com/maimai/545589"
        try:
            timeout = min(GAMERCH_REQUEST_MAX_SECONDS, self._search_time_remaining(budget))
            async with session.get(url, headers={"Accept-Language": "ja,en;q=0.8"}) as resp:
                if resp.status >= 400:
                    self._cool_down_source("gamerch_index", SOURCE_COOLDOWN_SECONDS, f"HTTP {resp.status}")
                    return self._gamerch_song_index
                html = await asyncio.wait_for(resp.text(errors="ignore"), timeout=timeout)
        except Exception as exc:
            self._cool_down_source("gamerch_index", SOURCE_COOLDOWN_SECONDS, f"{type(exc).__name__}: {exc}")
            log.debug(f"Gamerch 曲目索引读取失败: {type(exc).__name__} - {exc}")
            return self._gamerch_song_index
        index: dict[str, list[dict[str, str]]] = {}
        for href, title_html in re.findall(r"<a[^>]+href=[\"'](https://gamerch\.com/maimai/\d+|/maimai/\d+)[\"'][^>]*>([\s\S]{0,300}?)</a>", html, flags=re.I):
            title = strip_html(title_html)
            if not title or len(title) < 2:
                continue
            key = self._normalize_match_text(title)
            if not key:
                continue
            url = self._normalize_result_url(href, "https://gamerch.com/maimai/")
            entry = {"source": "gamerch", "title": title[:160], "url": url}
            bucket = index.setdefault(key, [])
            if all(item.get("url") != url for item in bucket):
                bucket.append(entry)
        if index:
            self._gamerch_song_index = index
            self._gamerch_song_index_loaded_at = time.monotonic()
        return self._gamerch_song_index

    async def _fetch_gamerch_song_page(self, session: aiohttp.ClientSession, url: str, budget: dict[str, float | int]) -> dict[str, str]:
        cached = self._gamerch_page_cache.get(url)
        if cached is not None:
            return dict(cached)
        try:
            timeout = min(GAMERCH_REQUEST_MAX_SECONDS, self._search_time_remaining(budget))
            async with session.get(url, headers={"Accept-Language": "ja,en;q=0.8"}) as resp:
                if resp.status >= 400:
                    self._cool_down_source("gamerch_page", SOURCE_COOLDOWN_SECONDS, f"HTTP {resp.status}")
                    return {}
                html = await asyncio.wait_for(resp.text(errors="ignore"), timeout=timeout)
        except Exception as exc:
            log.debug(f"Gamerch 曲目页读取失败 {url}: {type(exc).__name__} - {exc}")
            return {}
        title = self._html_title(html)
        content = self._extract_gamerch_markup_text(html)
        page = {"title": title[:160], "content": content[:60000]}
        self._gamerch_page_cache[url] = dict(page)
        return page

    def _html_title(self, html: str) -> str:
        match = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", html, flags=re.I)
        if match:
            return strip_html(match.group(1))
        match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, flags=re.I)
        return strip_html(match.group(1)).replace(" - maimai攻略wiki | Gamerch", "") if match else ""

    def _extract_gamerch_markup_text(self, html: str) -> str:
        match = re.search(r"<div class=[\"']markup mu[\"'][^>]*>([\s\S]*?)(?:<div class=[\"']mu__footnotes|<script|\Z)", html, flags=re.I)
        block = match.group(1) if match else html
        return strip_html_lines(block)

    def _gamerch_chart_summary(self, content: str, chart: dict[str, Any]) -> str:
        difficulty = str(chart.get("difficulty", "") or "").strip()
        gameplay = self._slice_section_between_markers(content, "譜面・ゲーム面", ("関連動画", "脚注", "コメント"))
        search_text = gameplay or content
        markers = self._difficulty_section_markers(difficulty)
        snippets = []
        for marker in markers:
            section = self._slice_text_after_marker(search_text, marker)
            if section:
                snippets.append(section)
        if not snippets:
            section = gameplay or self._slice_text_after_marker(content, "譜面データ")
            if section:
                snippets.append(section)
        return " ".join(snippets) if snippets else content[:1200]

    def _gamerch_notes_summary(self, content: str, chart: dict[str, Any]) -> str:
        rows = self._parse_gamerch_notes_rows(content, str(chart.get("type", "") or ""))
        level_index = int(chart.get("level_index", 0) or 0)
        if level_index >= len(rows):
            return ""
        row = rows[level_index]
        ds = self._chart_ds(chart)
        bpm = self._parse_bpm_value(content, chart.get("bpm"))
        total = row.get("total", 0)
        tap = row.get("tap", 0)
        hold = row.get("hold", 0)
        slide = row.get("slide", 0)
        touch = row.get("touch", 0)
        brk = row.get("break", 0)
        density = self._notes_density(total, bpm)
        tags = []
        if total >= NOTES_TOTAL_BURST_THRESHOLD or (ds >= 13.2 and total >= 880):
            tags.append("爆发")
        if total >= NOTES_TOTAL_STAMINA_THRESHOLD or density >= NOTES_DENSITY_STAMINA_THRESHOLD or (ds >= 13.2 and total >= 930):
            tags.append("底力")
        if bpm >= 240 or density >= NOTES_DENSITY_SPEED_THRESHOLD or (ds >= 13.2 and bpm >= 210 and total >= 850):
            tags.append("手速")
        if slide >= max(64, int(total * 0.09)):
            tags.append("管子")
        if tap >= max(600, int(total * 0.62)):
            tags.append("散打")
        if touch >= max(70, int(total * 0.08)):
            tags.append("定位")
        tag_text = " ".join(tags)
        return (
            f"譜面データ {chart.get('difficulty', '')} 定数 {ds:.1f} BPM {bpm:g} "
            f"総数 {total} Tap {tap} Hold {hold} Slide {slide} Touch {touch} Break {brk} "
            f"ノーツ密度 {density:.2f}/秒 {tag_text}"
        ).strip()

    def _parse_gamerch_notes_rows(self, content: str, chart_type: str = "") -> list[dict[str, int]]:
        rows: list[dict[str, int]] = []
        marker, table_type = self._gamerch_notes_table_marker(content, chart_type)
        if marker < 0:
            return rows
        block = content[marker:marker + 2200]
        for stop_marker in ("\n譜面作者", "\n定数調査", "\nレベル変更履歴"):
            stop = block.find(stop_marker)
            if stop >= 0:
                block = block[:stop]
                break
        for line in block.splitlines():
            row = self._parse_gamerch_notes_row(line, table_type)
            if row is None:
                continue
            rows.append(row)
            if len(rows) >= 5:
                break
        return rows

    def _parse_gamerch_notes_row(self, line: str, table_type: str) -> dict[str, int] | None:
        tokens = line.split()
        if not tokens or not re.fullmatch(r"\d+\+?", tokens[0]):
            return None
        values = [token.replace(",", "") for token in tokens]
        try:
            if table_type == "DX":
                if len(values) < 8:
                    return None
                total_index = 2
                touch_index = 6
                break_index = 7
            else:
                has_constant = len(values) >= 7 and re.fullmatch(r"\d+(?:\.\d+)?", values[1]) and "." in values[1]
                total_index = 2 if has_constant else 1
                touch_index = -1
                break_index = total_index + 4
                if len(values) <= break_index:
                    return None
            total = int(float(values[total_index]))
            row = {
                "total": total,
                "tap": int(float(values[total_index + 1])),
                "hold": int(float(values[total_index + 2])),
                "slide": int(float(values[total_index + 3])),
                "touch": int(float(values[touch_index])) if touch_index >= 0 else 0,
                "break": int(float(values[break_index])),
            }
        except Exception:
            return None
        if row["total"] <= 0:
            return None
        return row

    def _gamerch_notes_table_marker(self, content: str, chart_type: str = "") -> tuple[int, str]:
        type_value = str(chart_type or "").upper()
        candidates = self._gamerch_notes_table_candidates(content)
        if not candidates:
            return -1, type_value if type_value in {"DX", "SD"} else "DX"
        if type_value in {"DX", "SD"}:
            for table_start, table_type in candidates:
                if table_type == type_value:
                    return table_start, table_type
        return candidates[0]

    def _gamerch_notes_table_candidates(self, content: str) -> list[tuple[int, str]]:
        table_marker = "Lv 定数 総数 内訳"
        positions = [match.start() for match in re.finditer(re.escape(table_marker), content)]
        candidates: list[tuple[int, str]] = []
        previous = 0
        for position in positions:
            before = content[previous:position]
            local = before[-800:]
            table_type = "DX"
            if "スタンダード譜面" in local or "STD譜面" in local or "スタンダード 譜面" in local:
                table_type = "SD"
            elif "でらっくす譜面" in local or "DX譜面" in local or "でらっくす 譜面" in local:
                table_type = "DX"
            candidates.append((position, table_type))
            previous = position + len(table_marker)
        return candidates

    def _parse_bpm_value(self, content: str, fallback: Any = 0) -> float:
        match = re.search(r"BPM\s+([0-9]+(?:\.[0-9]+)?)(?:\s*[-~～]\s*([0-9]+(?:\.[0-9]+)?))?", content)
        if match:
            try:
                values = [float(item) for item in match.groups() if item]
                if values:
                    return max(values)
            except Exception:
                pass
        try:
            return float(fallback or 0)
        except Exception:
            return 0.0

    def _notes_density(self, total: int, bpm: float) -> float:
        if total <= 0 or bpm <= 0:
            return 0.0
        # 外部曲目页没有谱面时长，按常见 120 秒级曲长估算，只作为保守密度证据。
        estimated_seconds = max(90.0, min(150.0, 120.0 * 150.0 / bpm))
        return total / estimated_seconds

    def _difficulty_section_markers(self, difficulty: str) -> list[str]:
        value = str(difficulty or "").lower()
        if value.startswith("re:"):
            return ["Re:MASTER", "ReMASTER", "Re:M"]
        if value.startswith("master"):
            return ["MASTER", "MST"]
        if value.startswith("expert"):
            return ["EXPERT", "EXP"]
        return [difficulty] if difficulty else []

    def _slice_text_after_marker(self, text: str, marker: str, limit: int = 900) -> str:
        if not marker:
            return ""
        index = self._find_heading_marker(text, marker)
        if index < 0:
            return ""
        stop = len(text)
        for next_marker in ("BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER", "ReMASTER", "関連動画", "脚注", "コメント"):
            next_index = self._find_heading_marker(text, next_marker, index + len(marker))
            if next_index >= 0:
                stop = min(stop, next_index)
        return text[index:stop][:limit]

    def _find_heading_marker(self, text: str, marker: str, start: int = 0) -> int:
        for match in re.finditer(rf"(?m)(?:^|\n)\s*{re.escape(marker)}(?:\s|$|/|（|\()", text[start:]):
            return start + match.start()
        return text.find(marker, start)

    def _slice_section_between_markers(self, text: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
        start = text.find(start_marker)
        if start < 0:
            return ""
        end = len(text)
        for marker in end_markers:
            index = text.find(marker, start + len(start_marker))
            if index >= 0:
                end = min(end, index)
        return text[start:end]

    async def _search_youtube_html(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[dict[str, str]]:
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        try:
            async with session.get(url, headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7"}) as resp:
                if resp.status >= 400:
                    self._cool_down_source("youtube_html", SOURCE_COOLDOWN_SECONDS, f"HTTP {resp.status}")
                    return []
                text = await resp.text(errors="ignore")
            return self._parse_youtube_results(text, url, limit=limit)
        except Exception as exc:
            self._cool_down_source("youtube_html", SOURCE_COOLDOWN_SECONDS, f"{type(exc).__name__}: {exc}")
            log.debug(f"YouTube 搜索页失败: {type(exc).__name__} - {exc}")
            return []

    def _parse_bilibili_items(self, items: list[Any], source_url: str, limit: int = 8) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = strip_html(str(item.get("title", "")))
            description = strip_html(str(item.get("description", "")))
            tags = strip_html(str(item.get("tag", "")))
            url = str(item.get("arcurl") or "")
            bvid = str(item.get("bvid") or "")
            if bvid:
                url = f"https://www.bilibili.com/video/{bvid}/"
            if url.startswith("http://"):
                url = "https://" + url.removeprefix("http://")
            haystack = f"{title} {description} {tags}"
            if not self._looks_like_player_chart_evidence(haystack, url):
                continue
            summary_parts = [part for part in [description, tags] if part]
            results.append({
                "source": "bilibili",
                "title": title[:160],
                "url": url,
                "summary": " | ".join(summary_parts)[:700],
                "search_url": source_url,
            })
            if len(results) >= limit:
                break
        return results

    def _parse_search_results(self, html: str, source_url: str, source: str, limit: int = 8) -> list[dict[str, str]]:
        blocks = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]{0,500}?)</a>", html, flags=re.I)
        results = []
        for href, title_html in blocks:
            title = strip_html(title_html)
            if not title or len(title) < 4:
                continue
            if any(skip in href for skip in ("javascript:",)):
                continue
            url = self._normalize_result_url(href, source_url)
            if not self._looks_like_player_chart_evidence(title, url):
                continue
            results.append({"source": source, "title": title[:160], "url": url, "summary": "", "search_url": source_url})
            if len(results) >= limit:
                break
        return results

    def _parse_youtube_results(self, html: str, source_url: str, limit: int = 8) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        data = self._extract_embedded_json(html, "ytInitialData")
        if isinstance(data, dict):
            for renderer in self._walk_json_key(data, "videoRenderer"):
                if not isinstance(renderer, dict):
                    continue
                video_id = str(renderer.get("videoId", "") or "").strip()
                title = self._youtube_text(renderer.get("title"))
                summary_parts = [
                    self._youtube_text(renderer.get("descriptionSnippet")),
                    self._youtube_text(renderer.get("detailedMetadataSnippets")),
                ]
                summary = " | ".join(part for part in summary_parts if part)
                if not video_id or not title:
                    continue
                url = f"https://www.youtube.com/watch?v={video_id}"
                if not self._looks_like_player_chart_evidence(f"{title} {summary}", url):
                    continue
                results.append({"source": "youtube", "title": title[:160], "url": url, "summary": summary[:700], "search_url": source_url})
                if len(results) >= limit:
                    return results
        if results:
            return results
        seen_video_ids = set()
        for match in re.finditer(r'"videoId":"([^"]+)"[\s\S]{0,1000}?"title":\{"runs":\[\{"text":"([^"]+)"', html):
            video_id, raw_title = match.groups()
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            title = self._decode_json_string(raw_title)
            summary = self._youtube_fallback_summary(html[match.start():match.start() + 2500])
            url = f"https://www.youtube.com/watch?v={video_id}"
            if not self._looks_like_player_chart_evidence(f"{title} {summary}", url):
                continue
            results.append({"source": "youtube", "title": title[:160], "url": url, "summary": summary[:700], "search_url": source_url})
            if len(results) >= limit:
                break
        return results

    def _extract_embedded_json(self, html: str, variable_name: str) -> Any:
        marker = variable_name
        marker_index = html.find(marker)
        if marker_index < 0:
            return None
        start = html.find("{", marker_index)
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(html)):
            char = html[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:index + 1])
                    except Exception:
                        return None
        return None

    def _decode_json_string(self, value: str) -> str:
        try:
            return strip_html(str(json.loads(f'"{value}"')))
        except Exception:
            return strip_html(value)

    def _youtube_fallback_summary(self, block: str) -> str:
        parts = []
        for pattern in (
            r'"descriptionSnippet":\{"runs":\[\{"text":"([^"]+)"',
            r'"detailedMetadataSnippets":\[[\s\S]{0,500}?"snippetText":\{"runs":\[\{"text":"([^"]+)"',
            r'"simpleText":"([^"]+)"',
        ):
            for match in re.finditer(pattern, block):
                text = self._decode_json_string(match.group(1))
                if text and text not in parts:
                    parts.append(text)
                if len(parts) >= 3:
                    break
            if len(parts) >= 3:
                break
        return " | ".join(parts)

    def _walk_json_key(self, value: Any, target_key: str) -> list[Any]:
        found = []
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    if key == target_key:
                        found.append(child)
                    stack.append(child)
            elif isinstance(item, list):
                stack.extend(item)
        return found

    def _youtube_text(self, value: Any) -> str:
        if isinstance(value, str):
            return strip_html(value)
        if isinstance(value, dict):
            if "simpleText" in value:
                return strip_html(str(value.get("simpleText", "")))
            if isinstance(value.get("runs"), list):
                return strip_html("".join(str(item.get("text", "")) for item in value["runs"] if isinstance(item, dict)))
            if isinstance(value.get("snippetText"), dict):
                return self._youtube_text(value.get("snippetText"))
        if isinstance(value, list):
            return strip_html(" ".join(self._youtube_text(item) for item in value))
        return ""

    def _normalize_result_url(self, href: str, source_url: str) -> str:
        value = str(href or "").strip()
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            parsed = urlparse(source_url)
            return f"{parsed.scheme}://{parsed.netloc}{value}"
        return value

    def _looks_like_player_chart_evidence(self, text: str, url: str = "") -> bool:
        value = str(text or "")
        if "gamerch.com/maimai" in url and re.search(r"譜面データ|譜面・ゲーム面|総数|定数|Tap|Slide", value, re.I):
            return True
        if not re.search(r"maimai|舞萌|舞\d|mai\s*mai|MASTER|Re:MASTER|MAS|EXP|谱面|譜面", value, re.I):
            return False
        if not re.search(r"谱面|譜面|手元|攻略|难点|配置|外部出力|确认|確認|AP|FC|ALL PERFECT|MASTER|Re:MASTER", value, re.I):
            return False
        if re.search(r"创作谱面|自制谱|創作譜面|simai|maidata", value, re.I):
            return False
        if "bilibili.com" in url and not re.search(r"/video/|/read/|/festival/|search\.bilibili\.com", url):
            return False
        if ("youtube.com" in url or "youtu.be" in url) and not re.search(r"/watch|/shorts|youtu\.be/", url):
            return False
        return True

    def _matches_chart(self, item: dict[str, str], title: str, difficulty: str) -> bool:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        normalized_text = self._normalize_match_text(text)
        normalized_title = self._normalize_match_text(title)
        if normalized_title and normalized_title not in normalized_text:
            return False
        difficulty_text = str(difficulty or "").lower()
        if difficulty_text.startswith("re:"):
            return "re:master" in text or "remaster" in normalized_text or "白谱" in text or "白谱面" in text
        if difficulty_text.startswith("master"):
            if "re:master" in text or "remaster" in normalized_text or "白谱" in text:
                return False
            return "master" in text or "紫谱" in text or "紫谱面" in text
        if difficulty_text.startswith("expert"):
            if "master" in text or "紫谱" in text or "白谱" in text:
                return False
            return "expert" in text or "红谱" in text or "红谱面" in text
        return True

    def _evidence_rank(self, item: dict[str, str], title: str, difficulty: str) -> tuple[int, int, str]:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        score = 0
        normalized_title = self._normalize_match_text(title)
        if normalized_title and normalized_title in self._normalize_match_text(item.get("title", "")):
            score -= 6
        if "谱面" in text or "譜面" in text or "手元" in text:
            score -= 3
        if str(difficulty or "").lower().startswith("master") and ("master" in text or "紫谱" in text):
            score -= 2
        if str(difficulty or "").lower().startswith("re:") and ("re:master" in text or "remaster" in text):
            score -= 2
        return (score, len(item.get("title", "")), item.get("title", ""))

    def _normalize_match_text(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "").lower())
        return "".join(ch for ch in normalized if unicodedata.category(ch)[0] in {"L", "N"})

    def _extract_tags_from_evidence(self, evidence: list[dict[str, str]]) -> list[str]:
        text = "\n".join(f"{item.get('title', '')} {item.get('summary', '')}" for item in evidence)
        matched = []
        for tag, patterns in TAG_KEYWORD_RULES:
            if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
                matched.append(tag)
        return filter_allowed_tags(matched)

    def _valid_tags(self, chart: dict[str, Any]) -> list[str]:
        if not isinstance(chart, dict):
            return []
        tags = chart.get("final_tags") or chart.get("tags") or chart.get("llm_tags") or chart.get("manual_tags") or []
        return filter_allowed_tags(tags if isinstance(tags, list) else [])

    def _is_done(self, chart: dict[str, Any]) -> bool:
        if int(chart.get("tag_rule_version", 0) or 0) < TAG_RULE_VERSION:
            return False
        if self._valid_tags(chart):
            return True
        if chart.get("tag_status") == "no_evidence" and int(chart.get("tag_rule_version", 0) or 0) >= TAG_RULE_VERSION:
            return True
        return False

    def _chart_sort_key(self, key: str, chart: dict[str, Any] | None = None) -> tuple[int, float, int, int, str]:
        song_id, _, level = str(key).partition(":")
        try:
            song_num = int(song_id)
        except Exception:
            song_num = 10**12
        try:
            level_num = int(level)
        except Exception:
            level_num = 99
        chart_ds = self._chart_ds(chart or {})
        priority = 0 if chart_ds >= HIGH_PRIORITY_DS else 1
        return priority, -chart_ds, level_num, song_num, key
