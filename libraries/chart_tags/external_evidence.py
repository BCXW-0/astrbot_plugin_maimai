from __future__ import annotations

"""Collect bounded, text-only external evidence for chart-tag review.

This module is used only while building training metadata. Runtime tagging
never performs network requests. The sources are deliberately kept as
provenance and weak supervision; a missing word in a video or comment is not
treated as proof that a configuration is absent.
"""

import html
import json
import concurrent.futures
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from .constants import ALLOWED_TAGS, TAG_ALIASES
from .model_consensus import is_accepted_model_review, parse_model_result
from .rule_tags import filter_allowed_tags

SEARCH_URL = "https://search.bilibili.com/all?keyword={}"
SEARCH_API_URL = "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={}&page=1"
VIEW_URL = "https://api.bilibili.com/x/web-interface/view?bvid={}"
REPLY_URL = "https://api.bilibili.com/x/v2/reply?type=1&oid={}&sort=2&ps=20"
REQUEST_TIMEOUT_SECONDS = 12
MAX_SEARCH_RESULTS = 40
MAX_CANDIDATES_PER_QUERY = 10
MAX_EVIDENCE_SOURCES = 8
MAX_COMMENTS = 20
MAX_SNIPPET_LENGTH = 360
EFFECTIVE_OVERLAP = 0.80
FALLBACK_SAMPLE_MIN = 100
FALLBACK_SAMPLE_TARGET = 125
EVIDENCE_VERSION = 1
EXTERNAL_REVIEW_WORKERS = 6
EXTERNAL_REVIEW_BATCH_SIZE = 8
EXTERNAL_REVIEW_KEY_ENV = "MAIMAI_EXTERNAL_REVIEW_KEY"
EXTERNAL_REVIEW_BASE_URL = "https://starmiaoa.top/v1"
EXTERNAL_REVIEW_MODEL = "gemini-2.5-pro"
EXTERNAL_REVIEW_TIMEOUT_SECONDS = 90

REFERENCE_SOURCES = (
    "https://www.bilibili.com/opus/978826006029664264",
    "https://www.bilibili.com/opus/912886214932037655",
    "https://www.bilibili.com/opus/29693067423444838",
    "https://w.atwiki.jp/simai/pages/1002.html",
)

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_VIDEO_LINK_RE = re.compile(
    r'href="(?:https?:)?//www\.bilibili\.com/video/(BV[0-9A-Za-z]{10})/?"[^>]*>',
    re.I,
)
_CARD_TITLE_RE = re.compile(
    r'<(?:img|[^>]+)\b(?:alt|title)\s*=\s*(["\'])(.*?)\1',
    re.I | re.S,
)
_TAG_TERMS: dict[str, tuple[str, ...]] = {
    "节奏": ("节奏", "节拍", "切分", "swing", "shuffle", "附点", "rhythm"),
    "跳拍": ("跳拍", "shuffle", "swing", "附点", "附点节奏"),
    "管子": ("管子", "hold链", "hold 链", "连hold", "连 hold", "hold密集", "hold 密集", "长条", "长押", "长按", "长条链", "绿条", "連hold", "ホールド"),
    "定位": ("定位", "定位练习", "卡手", "找位", "外键", "大位移", "大跨度", "位移稍大", "手位", "跨位", "移动配置"),
    "散打": ("散打", "散配置", "散键", "随机键位"),
    "飞手": ("飞手", "大跳", "飞键", "跨屏", "飞位"),
    "防蹭": ("防蹭", "蹭星", "蹭", "内屏无理", "跳区"),
    "留尾": ("留尾", "出张", "秒划", "短星", "快速星", "星尾"),
    "爆发": ("爆发", "爆发段", "burst"),
    "底力": ("底力", "体力", "长时间输出", "耐力"),
    "交互": ("交互", "大宇宙", "交替", "交互段", "互换", "换手段", "交互谱"),
    "轴交互": ("轴交互", "固定轴", "轴配置", "轴段"),
    "爬梯交互": ("爬梯", "梯子", "阶梯", "ladder", "楼梯", "阶梯交互", "爬梯交互"),
    "定拍": ("定拍", "锚定", "轴拍", "固定拍"),
    "双押": ("双押", "真双", "多押", "叠键", "双键", "押特化", "押配置", "两押", "二押", "同拍", "同時押し", "押し"),
    "扫键": ("扫键", "转圈", "同侧扫", "短扫", "连续扫", "扫星", "扫尾", "滑键", "连续划"),
    "死镰": ("死镰", "death scythe", "deathscythe"),
    "错位": ("错位", "隔拍slide", "隔拍 slide", "错位星", "错位配置", "偏移", "ズレ"),
    "手速": ("手速", "高速", "速刷", "速度段", "处理速度"),
    "纵连": ("纵连", "短纵", "长纵", "二纵", "三纵", "四纵", "纵向", "纵連", "连打"),
    "如龙": ("如龙", "如龍", "rulong", "rulong sweep", "同侧扫", "同側掃", "同侧连续", "同侧"),
    "协调": ("协调", "手序", "换手", "难协调", "手法", "交互手法", "位移交互", "手顺", "手順", "运指", "運指", "拆谱", "拆譜", "左右手"),
    "撞尾": ("撞尾", "撞尾无理", "卡尾", "尾无理", "尾杀", "星星撞尾", "尾巴无理"),
    "延迟星星": ("延迟星", "延遲星", "延迟星星", "慢星"),
    "拆弹": ("拆弹", "拆彈", "星星拆弹", "多星同时"),
}

_TAG_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "双押": ("双押", "押特化", "双押位移"),
    "管子": ("管子", "长条", "长押"),
    "协调": ("协调", "位移交互", "交互手序"),
    "定位": ("定位", "大位移", "卡手"),
    "飞手": ("飞手", "飞键", "跨屏"),
    "爬梯交互": ("爬梯", "梯子", "阶梯"),
    "如龙": ("如龙", "同侧扫", "同侧"),
    "扫键": ("扫键", "连续扫", "转圈"),
    "错位": ("错位", "偏移", "隔拍"),
    "纵连": ("纵连", "短纵", "连打"),
    "撞尾": ("撞尾", "尾杀", "星星撞尾"),
    "留尾": ("留尾", "出张", "秒划"),
}

_DIFFICULTY_MARKERS: dict[str, tuple[str, ...]] = {
    "Expert": ("expert", "红谱", "红谱面", "红"),
    "Master": ("master", "紫谱", "紫谱面", "紫"),
    "Re:Master": ("re:master", "re master", "remaster", "白谱", "白谱面", "白"),
}

_CACHE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int = MAX_SNIPPET_LENGTH) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _compact(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value, 1000)).casefold()
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", text)


def _request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read(4 * 1024 * 1024)


def _request_json(url: str) -> dict[str, Any]:
    payload = json.loads(_request_bytes(url).decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError("外部响应不是 JSON 对象")
    return payload


def _request_search_payload(url: str) -> dict[str, Any]:
    """Use the public API, with a curl fallback for its 412 response."""
    try:
        return _request_json(url)
    except Exception:
        completed = subprocess.run(
            [
                "curl", "-sS", "--max-time", str(REQUEST_TIMEOUT_SECONDS),
                "-A", _USER_AGENT,
                "-H", "Accept: application/json",
                "-H", "Referer: https://www.bilibili.com/",
                url,
            ],
            check=True,
            capture_output=True,
            timeout=REQUEST_TIMEOUT_SECONDS + 2,
        )
        payload = json.loads(completed.stdout.decode("utf-8", "replace"))
        if not isinstance(payload, dict):
            raise ValueError("搜索响应不是 JSON 对象")
        return payload


def _search_results(text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _VIDEO_LINK_RE.finditer(text):
        bvid = match.group(1)
        if bvid in seen:
            continue
        end = text.find("</a>", match.end())
        fragment = text[match.end():end if end >= 0 else match.end() + 12000]
        title = ""
        for title_match in _CARD_TITLE_RE.finditer(fragment):
            candidate = _clean_text(title_match.group(2), 240)
            if candidate:
                title = candidate
                break
        if not title:
            title = _clean_text(fragment, 240)
        if not title:
            continue
        seen.add(bvid)
        results.append({
            "bvid": bvid,
            "title": title,
            "url": f"https://www.bilibili.com/video/{bvid}/",
        })
        if len(results) >= MAX_SEARCH_RESULTS:
            break
    return results


def _search_page_candidates(query: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    url = SEARCH_URL.format(quote(query))
    try:
        body = _request_bytes(url).decode("utf-8", "replace")
        candidates = _search_results(body)
        return candidates, {
            "url": url,
            "status": "completed",
            "candidate_count": len(candidates),
            "response_bytes": len(body.encode("utf-8", "replace")),
        }
    except Exception as exc:
        return [], {
            "url": url,
            "status": "error",
            "candidate_count": 0,
            "error": f"{type(exc).__name__}: {exc}"[:240],
        }


def _search_api_results(payload: dict[str, Any]) -> list[dict[str, str]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_results = data.get("result") if isinstance(data.get("result"), list) else []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        bvid = str(item.get("bvid") or "").strip()
        if not bvid or bvid in seen:
            continue
        title = _clean_text(item.get("title"), 240)
        if not title:
            continue
        seen.add(bvid)
        results.append({"bvid": bvid, "title": title, "url": f"https://www.bilibili.com/video/{bvid}/"})
        if len(results) >= MAX_SEARCH_RESULTS:
            break
    return results


def _title_score(chart_title: str, result_title: str, difficulty: str) -> float:
    wanted = _compact(chart_title)
    found = _compact(result_title)
    if not wanted or not found:
        return 0.0
    if wanted in found or found in wanted:
        score = 1.0
    else:
        wanted_chars = set(wanted)
        found_chars = set(found)
        score = len(wanted_chars & found_chars) / max(len(wanted_chars), 1)
    if difficulty.casefold() in result_title.casefold() or difficulty == "Re:Master" and "re" in found:
        score += 0.08
    return min(score, 1.0)


def _difficulty_matches(difficulty: str, *texts: str) -> bool:
    """Reject a source only when its text explicitly names another chart level."""
    wanted = str(difficulty or "").strip()
    def levels(value: str) -> set[str]:
        combined = str(value or "").casefold()
        if not combined:
            return set()
        explicit: set[str] = set()
        has_re_master = any(marker.casefold() in combined for marker in _DIFFICULTY_MARKERS["Re:Master"])
        has_expert = any(marker.casefold() in combined for marker in _DIFFICULTY_MARKERS["Expert"])
        has_master = any(marker.casefold() in combined for marker in _DIFFICULTY_MARKERS["Master"])
        if has_re_master:
            explicit.add("Re:Master")
        elif has_master:
            explicit.add("Master")
        if has_expert:
            explicit.add("Expert")
        return explicit

    title_levels = levels(texts[0] if texts else "")
    if title_levels:
        return wanted in title_levels
    combined_levels: set[str] = set()
    for text in texts[1:]:
        combined_levels.update(levels(text))
    if not combined_levels:
        return True
    explicit = combined_levels
    return wanted in explicit


def _evidence_terms(texts: list[dict[str, str]]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    votes: dict[str, dict[str, Any]] = {}
    for item in texts:
        source_type = str(item.get("kind") or "unknown")
        text = _clean_text(item.get("text"), MAX_SNIPPET_LENGTH).casefold()
        if not text:
            continue
        for tag, terms in _TAG_TERMS.items():
            matched = [term for term in terms if term.casefold() in text]
            if not matched:
                continue
            entry = votes.setdefault(tag, {"mentions": 0, "sources": [], "snippets": []})
            entry["mentions"] += len(matched)
            if source_type not in entry["sources"]:
                entry["sources"].append(source_type)
            if len(entry["snippets"]) < 3:
                entry["snippets"].append({"kind": source_type, "text": _clean_text(item.get("text"))})

    # A title/description mention is enough; comment-only mentions need two
    # independent comments to reduce accidental keyword matches.
    accepted: list[str] = []
    for tag, entry in votes.items():
        sources = set(entry["sources"])
        if sources & {"video_title", "video_description"} or entry["mentions"] >= 2:
            accepted.append(tag)
    return filter_allowed_tags(sorted(accepted)), votes


def _fetch_video_evidence(ref: dict[str, Any], result: dict[str, str], title_score: float) -> dict[str, Any]:
    bvid = result["bvid"]
    view = _request_json(VIEW_URL.format(bvid))
    if int(view.get("code", -1)) != 0 or not isinstance(view.get("data"), dict):
        raise ValueError(f"Bilibili 视频信息不可用: {bvid}")
    data = view["data"]
    texts: list[dict[str, str]] = [
        {"kind": "video_title", "text": _clean_text(data.get("title") or result.get("title"))},
        {"kind": "video_description", "text": _clean_text(data.get("desc"))},
    ]
    aid = int(data.get("aid", 0) or 0)
    comments: list[str] = []
    if aid:
        try:
            reply = _request_json(REPLY_URL.format(aid))
            reply_data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
            for item in reply_data.get("replies") or []:
                content = item.get("content") if isinstance(item, dict) else {}
                message = _clean_text(content.get("message") if isinstance(content, dict) else "")
                if message:
                    comments.append(message)
                    texts.append({"kind": "comment", "text": message})
                if len(comments) >= MAX_COMMENTS:
                    break
        except Exception:
            # Comments are supplementary. A valid video title/description is
            # still usable evidence when the public reply endpoint is limited.
            pass
    external_tags, votes = _evidence_terms(texts)
    return {
        "kind": "bilibili_video",
        "url": result["url"],
        "bvid": bvid,
        "aid": aid,
        "title": _clean_text(data.get("title") or result.get("title"), 240),
        "description": _clean_text(data.get("desc")),
        "owner": _clean_text((data.get("owner") or {}).get("name"), 80),
        "title_match_score": round(title_score, 4),
        "comments_checked": len(comments),
        "texts": texts[:22],
        "external_tags": external_tags,
        "tag_votes": votes,
        "checked_at": _now(),
    }


def _response_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        return ""
    if message.get("tool_calls") or message.get("function_call"):
        return "custom_tool_call"
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"tool_use", "tool_call"}:
                return "custom_tool_call"
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _source_text(source: dict[str, Any]) -> str:
    pieces = [str(source.get("title", "")), str(source.get("description", ""))]
    for item in source.get("texts") or []:
        if isinstance(item, dict):
            pieces.append(str(item.get("text", "")))
    return " ".join(piece for piece in pieces if piece)


def _review_external_tags(ref: dict[str, Any], baseline: list[str], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Use the configured review model only to ground labels in fetched text."""
    api_key = os.environ.get(EXTERNAL_REVIEW_KEY_ENV, "").strip()
    if not api_key or not sources:
        return {"status": "skipped", "tags": [], "evidence": [], "reason": "external_review_key_or_sources_missing"}
    allowed = json.dumps(ALLOWED_TAGS, ensure_ascii=False, separators=(",", ":"))
    system = (
        "你是舞萌谱面外部证据审查员。输入包含已通过双模型一致性审核的候选标签，"
        "以及 Bilibili 搜索页命中的同曲同难度视频标题、简介和评论。只能根据这些文本中明确描述的谱面配置判断；"
        "“长条/长押”可作为管子证据，“位移交互手序/换手”可作为协调证据，"
        "“双押位移/押特化”可作为双押证据，“撞尾/尾杀/星星撞尾”可作为撞尾证据。"
        "不能从曲名、定数、搜索关键词或仅有游玩成绩推断。候选标签之外的标签只有在原文明确支持时才允许输出。"
        "每个输出标签都必须有一个能在输入文本中找到的短摘录。只返回一个 JSON 对象："
        '{"tags":["标签"],"evidence":[{"tag":"标签","bvid":"BV号","text":"原文短摘录"}]}。'
        "标签最多 5 个；禁止 Markdown、解释文字和工具调用。允许标签：" + allowed
    )
    payload_sources = [
        {
            "bvid": source.get("bvid", ""),
            "title": source.get("title", ""),
            "description": source.get("description", ""),
            "texts": (source.get("texts") or [])[:22],
        }
        for source in sources
    ]
    prompt = json.dumps(
        {
            "chart": {
                "key": ref.get("key", ""),
                "title": ref.get("title", ""),
                "difficulty": ref.get("difficulty", ""),
            },
            "candidate_tags": baseline,
            "sources": payload_sources,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    body = json.dumps(
        {
            "model": EXTERNAL_REVIEW_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 4096,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{EXTERNAL_REVIEW_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AstrBot/4.26.7",
        },
    )
    try:
        with urlopen(request, timeout=EXTERNAL_REVIEW_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8", "replace"))
        parsed = parse_model_result(_response_content(payload), provider=EXTERNAL_REVIEW_MODEL)
        if parsed.get("status") != "completed":
            return {"status": "invalid_response", "tags": [], "evidence": [], "reason": parsed.get("error", "invalid_response")}
        source_texts = {str(source.get("bvid", "")): _compact(_source_text(source)) for source in sources}
        valid_evidence: list[dict[str, str]] = []
        grounded_tags: list[str] = []
        for item in parsed.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag", "")).strip()
            bvid = str(item.get("bvid", "")).strip()
            excerpt = _clean_text(item.get("text", ""), 360)
            if tag not in baseline or tag not in parsed.get("tags", []) or not bvid or not excerpt:
                continue
            source_text = source_texts.get(bvid, "")
            if not source_text or _compact(excerpt) not in source_text:
                continue
            if tag not in grounded_tags:
                grounded_tags.append(tag)
                valid_evidence.append({"tag": tag, "bvid": bvid, "text": excerpt})
        return {
            "status": "completed",
            "tags": filter_allowed_tags(grounded_tags),
            "evidence": valid_evidence[:5],
            "model": EXTERNAL_REVIEW_MODEL,
        }
    except urllib.error.HTTPError as exc:
        return {"status": "unavailable", "tags": [], "evidence": [], "reason": f"HTTPError: {exc.code}"}
    except Exception as exc:
        return {"status": "unavailable", "tags": [], "evidence": [], "reason": f"{type(exc).__name__}: {exc}"[:240]}


def collect_external_evidence(ref: dict[str, Any]) -> dict[str, Any]:
    """Find one title-matched public Bilibili chart source for ``ref``."""
    title = str(ref.get("title") or "").strip()
    difficulty = str(ref.get("difficulty") or "").strip()
    baseline_tags = filter_allowed_tags(ref.get("consensus_tags") or [])
    base_query = f"maimai {title} {difficulty} 谱面".strip()
    query_terms: list[str] = []
    for tag in baseline_tags:
        for term in _TAG_QUERY_TERMS.get(tag, (tag,)):
            if term not in query_terms:
                query_terms.append(term)
    queries = [base_query]
    for term in query_terms:
        queries.append(f"{base_query} {term}")
        # Some search pages rank a title-plus-feature query more accurately
        # than the longer level-qualified query.
        queries.append(f"{title} maimai {term}".strip())
    queries = list(dict.fromkeys(queries))
    result: dict[str, Any] = {
        "version": EVIDENCE_VERSION,
        "query": base_query,
        "queries": queries,
        "search_page": {"status": "pending", "queries": []},
        "reference_sources": list(REFERENCE_SOURCES),
        "external_tags": [],
        "sources": [],
        "status": "unavailable",
        "error": "",
    }
    try:
        sources: list[dict[str, Any]] = []
        seen_bvids: set[str] = set()
        page_checks: list[dict[str, Any]] = []
        for query in queries:
            page_candidates, page_check = _search_page_candidates(query)
            page_checks.append(page_check)
            candidates: list[dict[str, str]] = list(page_candidates)
            try:
                search_payload = _request_search_payload(SEARCH_API_URL.format(quote(query)))
                api_candidates = _search_api_results(search_payload)
                known = {item["bvid"] for item in candidates}
                candidates.extend(item for item in api_candidates if item["bvid"] not in known)
            except Exception:
                pass
            ranked = sorted(
                ((candidate, _title_score(title, candidate["title"], difficulty)) for candidate in candidates),
                key=lambda item: (-item[1], item[0]["bvid"]),
            )
            fetched_for_query = 0
            for candidate, score in ranked:
                if score < 0.72 or candidate["bvid"] in seen_bvids:
                    continue
                if fetched_for_query >= MAX_CANDIDATES_PER_QUERY:
                    break
                try:
                    source = _fetch_video_evidence(ref, candidate, score)
                except Exception:
                    continue
                if not _difficulty_matches(
                    difficulty,
                    source.get("title", ""),
                    source.get("description", ""),
                    *(item.get("text", "") for item in source.get("texts", [])),
                ):
                    continue
                fetched_for_query += 1
                source["search_page_url"] = page_check.get("url", "")
                source["search_query"] = query
                seen_bvids.add(candidate["bvid"])
                sources.append(source)
                if source.get("external_tags") and len(sources) >= MAX_EVIDENCE_SOURCES:
                    break
            if len(sources) >= MAX_EVIDENCE_SOURCES and any(source.get("external_tags") for source in sources):
                break
        if not sources:
            result["search_page"] = {"status": "completed", "queries": page_checks}
            result["status"] = "no_title_match"
            return result
        external_tags = sorted({tag for source in sources for tag in source.get("external_tags") or []})
        result["search_page"] = {"status": "completed", "queries": page_checks}
        result["sources"] = sources
        keyword_tags = filter_allowed_tags(external_tags)
        model_review = _review_external_tags(ref, baseline_tags, sources)
        result["keyword_tags"] = keyword_tags
        result["external_model_review"] = model_review
        if os.environ.get(EXTERNAL_REVIEW_KEY_ENV, "").strip():
            result["external_tags"] = filter_allowed_tags(
                model_review.get("tags") if model_review.get("status") == "completed" else []
            )
        else:
            result["external_tags"] = keyword_tags
        result["status"] = "completed"
        return result
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return result


def _overlap(baseline: list[str], external: list[str]) -> tuple[float, list[str], float]:
    baseline_set = set(filter_allowed_tags(baseline))
    external_set = set(filter_allowed_tags(external))
    union = baseline_set | external_set
    if not union:
        # Empty model output and empty external output do not constitute
        # evidence of agreement. Treating them as 100% overlap would select
        # unlabelled charts ahead of charts with actual corroboration.
        return 0.0, [], 0.0
    intersection = sorted(baseline_set & external_set)
    symmetric_jaccard = len(intersection) / len(union)
    model_coverage = len(intersection) / max(len(baseline_set), 1)
    return model_coverage, intersection, symmetric_jaccard


def traceable_sources(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return chart-specific sources that can be audited after selection."""
    if not isinstance(evidence, dict):
        return []
    result: list[dict[str, Any]] = []
    for source in evidence.get("sources") or []:
        if not isinstance(source, dict):
            continue
        bvid = str(source.get("bvid") or "").strip()
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        search_page_url = str(source.get("search_page_url") or "").strip()
        search_query = str(source.get("search_query") or "").strip()
        if not bvid or not url or not title or not (search_page_url or search_query):
            continue
        result.append(source)
    return result


def _cached_external_evidence(key: str) -> dict[str, Any] | None:
    raw_path = str(os.environ.get("MAIMAI_EXTERNAL_EVIDENCE_CACHE", "")).strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get(str(key)) if isinstance(payload, dict) else None
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _cache_external_evidence(key: str, evidence: dict[str, Any]) -> None:
    raw_path = str(os.environ.get("MAIMAI_EXTERNAL_EVIDENCE_CACHE", "")).strip()
    if not raw_path:
        return
    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            payload = {}
        payload[str(key)] = evidence
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def select_effective_samples(
    refs: list[dict[str, Any]],
    *,
    target: int = 200,
    seed: int = 20260805,
    annotate: Callable[[dict[str, Any]], dict[str, Any]],
    consensus: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Select reviewed charts, falling back to confidence-ranked sources.

    The strict path requires the historical 80% model-coverage threshold. If
    fewer than target samples pass it, the fallback still requires a
    traceable chart-specific source and ranks the complete reviewed pool by
    overlap confidence. It never manufactures a source for a missing result.
    """
    shuffled = list(refs)
    random.Random(seed).shuffle(shuffled)
    selected: list[dict[str, Any]] = []
    total = len(shuffled)
    agreed: list[tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for index, ref in enumerate(shuffled, start=1):
        analysis = annotate(ref)
        model_consensus = consensus(ref, analysis) if consensus else {
            "status": "unavailable",
            "consistent": False,
            "reason": "model_callback_missing",
            "first_tags": [],
            "second_tags": [],
            "third_tags": [],
        }
        if not is_accepted_model_review(model_consensus, allow_legacy_three=True):
            item = {
                "ref": ref,
                "analysis": analysis,
                "model_consensus": model_consensus,
                "external_evidence": {"status": "skipped_model_disagreement", "external_tags": [], "sources": []},
                "validation": {
                    "baseline_tags": filter_allowed_tags(analysis.get("difficulty_tags") or analysis.get("tags") or []),
                    "external_tags": [],
                    "intersection_tags": [],
                    "overlap": 0.0,
                    "threshold": EFFECTIVE_OVERLAP,
                    "effective": False,
                    "definition": "symmetric_jaccard_after_exact_model_agreement",
                },
            }
            if progress:
                progress(index, total, item)
            continue
        baseline = filter_allowed_tags(model_consensus.get("first_tags") or analysis.get("difficulty_tags") or analysis.get("tags") or [])
        agreed.append((index, ref, analysis, model_consensus))

    def review_one(entry: tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        index, ref, analysis, model_consensus = entry
        baseline = filter_allowed_tags(model_consensus.get("first_tags") or analysis.get("difficulty_tags") or analysis.get("tags") or [])
        cache_key = str(ref.get("key", ""))
        evidence = _cached_external_evidence(cache_key)
        if evidence is None:
            evidence = collect_external_evidence({**ref, "consensus_tags": baseline})
            _cache_external_evidence(cache_key, evidence)
        external = filter_allowed_tags(evidence.get("external_tags") or [])
        overlap, intersection, symmetric_jaccard = _overlap(baseline, external)
        return {
            "index": index,
            "item": {
                "ref": ref,
                "analysis": analysis,
                "model_consensus": model_consensus,
                "external_evidence": evidence,
                "validation": {
                    "baseline_tags": baseline,
                    "external_tags": external,
                    "intersection_tags": intersection,
                    "overlap": round(overlap, 6),
                    "symmetric_jaccard": round(symmetric_jaccard, 6),
                    "threshold": EFFECTIVE_OVERLAP,
                    "effective": bool(evidence.get("sources") and overlap >= EFFECTIVE_OVERLAP),
                    "definition": "model_tag_coverage_after_exact_model_agreement",
                },
            },
        }

    reviewed_items: list[dict[str, Any]] = []

    # Network work runs in small batches, while results are consumed in the
    # seeded order so the selected dataset remains reproducible.
    for start in range(0, len(agreed), EXTERNAL_REVIEW_BATCH_SIZE):
        batch = agreed[start:start + EXTERNAL_REVIEW_BATCH_SIZE]
        try:
            worker_count = max(1, int(os.environ.get("MAIMAI_EXTERNAL_REVIEW_WORKERS", EXTERNAL_REVIEW_WORKERS)))
        except (TypeError, ValueError):
            worker_count = EXTERNAL_REVIEW_WORKERS
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(review_one, entry) for entry in batch]
            for future in futures:
                result = future.result()
                item = result["item"]
                evidence = item.get("external_evidence") if isinstance(item.get("external_evidence"), dict) else {}
                sources = traceable_sources(evidence)
                validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
                validation["evidence_source_count"] = len(sources)
                validation["confidence"] = float(validation.get("overlap", 0.0) or 0.0)
                validation["training_eligible"] = bool(sources)
                item["validation"] = validation
                evidence["traceable_source_count"] = len(sources)
                item["external_evidence"] = evidence
                reviewed_items.append(item)
                if progress:
                    progress(result["index"], total, item)
        if len(selected) >= target:
            break
        time.sleep(0.03)

    strict = [
        item for item in reviewed_items
        if bool((item.get("validation") or {}).get("effective"))
        and bool((item.get("validation") or {}).get("training_eligible"))
    ]
    if len(strict) >= target:
        for item in strict[:target]:
            validation = item["validation"]
            validation["selection_mode"] = "external_threshold"
            validation["selected_for_training"] = True
        return strict[:target]

    fallback = [
        item for item in reviewed_items
        if bool((item.get("validation") or {}).get("training_eligible"))
        and bool((item.get("validation") or {}).get("baseline_tags"))
    ]
    fallback.sort(
        key=lambda item: (
            -float((item.get("validation") or {}).get("confidence", 0.0) or 0.0),
            -float((item.get("validation") or {}).get("symmetric_jaccard", 0.0) or 0.0),
            -len((item.get("validation") or {}).get("intersection_tags") or []),
            -int((item.get("validation") or {}).get("evidence_source_count", 0) or 0),
            int(item.get("index", 0) or 0),
        )
    )
    if len(fallback) < FALLBACK_SAMPLE_MIN:
        raise ValueError(
            "联网校验未达到严格阈值，且可追溯证据样本不足 "
            f"{FALLBACK_SAMPLE_MIN} 条：{len(fallback)}"
        )
    selected = fallback[:FALLBACK_SAMPLE_TARGET]
    for item in selected:
        validation = item["validation"]
        validation["selection_mode"] = "confidence_fallback"
        validation["selected_for_training"] = True
        validation["effective"] = False
    return selected


__all__ = [
    "EFFECTIVE_OVERLAP",
    "FALLBACK_SAMPLE_MIN",
    "FALLBACK_SAMPLE_TARGET",
    "EVIDENCE_VERSION",
    "REFERENCE_SOURCES",
    "collect_external_evidence",
    "select_effective_samples",
    "traceable_sources",
]
