from __future__ import annotations

import io
import json
import os
import re
import threading
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ... import Root
from .local.maidata_parser import parse_maidata

ONECAT_ORIGIN = "https://dw.moant.cn:34225"
MUSIC_DATA_URL = f"{ONECAT_ORIGIN}/api/getMusicData"
CHART_FILE_LIST_URL = f"{ONECAT_ORIGIN}/api/chartFileList"
DEFAULT_LEVELS_PATH = Root / "static" / "Levels"
MIN_DOWNLOAD_DS = 10.0
MAX_DOWNLOAD_DS = 15.0
DEFAULT_MIN_DS = 12.6
DEFAULT_MAX_DS = 15.0
DOWNLOAD_MODES = frozenset({"all", "missing", "search"})
USER_AGENT = "astrbot-plugin-maimai/auto-chart-tags"


ProgressCallback = Callable[[dict[str, Any]], None]
StopCallback = Callable[[], bool]


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def validate_range(min_ds: Any, max_ds: Any) -> tuple[float, float]:
    low = _as_float(min_ds)
    high = _as_float(max_ds)
    if low is None or high is None:
        raise ValueError("定数范围必须是数字")
    low = round(low, 1)
    high = round(high, 1)
    if not MIN_DOWNLOAD_DS <= low <= MAX_DOWNLOAD_DS:
        raise ValueError("最低定数必须在 10.0 至 15.0 之间")
    if not MIN_DOWNLOAD_DS <= high <= MAX_DOWNLOAD_DS:
        raise ValueError("最高定数必须在 10.0 至 15.0 之间")
    if low > high:
        raise ValueError("最低定数不能高于最高定数")
    return low, high


def validate_mode(mode: Any, query: Any = "") -> tuple[str, str]:
    value = str(mode or "all").strip().lower()
    if value not in DOWNLOAD_MODES:
        raise ValueError("下载方式不正确")
    text = str(query or "").strip()
    if value == "search" and not text:
        raise ValueError("搜索下载需要填写搜索词")
    if len(text) > 120:
        raise ValueError("搜索词过长")
    return value, text


def is_utage_title(title: Any, genre: Any = "") -> bool:
    title_text = str(title or "").strip()
    genre_text = str(genre or "").strip()
    return bool(re.match(r"^\[[^\]]+\]", title_text) or "宴" in genre_text)


def _safe_filename_part(value: Any, fallback: str) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    text = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    if text.upper() in {"CON", "PRN", "AUX", "NUL"}:
        text = f"_{text}"
    return text[:180].rstrip(" .") or fallback


def chart_output_name(short_id: Any, title: Any) -> str:
    song_id = _safe_filename_part(short_id, "unknown")
    song_title = _safe_filename_part(title, "untitled")
    return f"{song_id}_{song_title}.txt"


def _decode_chart(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _is_safe_archive_member(name: str) -> bool:
    path = Path(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def extract_text_payloads(name: str, raw: bytes) -> list[tuple[str, bytes]]:
    """Return only direct txt files or safe txt members from an archive."""
    stream = io.BytesIO(raw)
    if zipfile.is_zipfile(stream):
        result: list[tuple[str, bytes]] = []
        with zipfile.ZipFile(stream) as archive:
            for info in archive.infolist():
                member = str(info.filename or "")
                if info.is_dir() or not _is_safe_archive_member(member):
                    continue
                if not member.lower().endswith(".txt"):
                    continue
                result.append((Path(member).name, archive.read(info)))
        return result
    if str(name or "").lower().endswith(".txt"):
        return [(Path(str(name)).name, raw)]
    return []


def _json_request(url: str, timeout: float = 35.0) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _binary_request(url: str, timeout: float = 45.0) -> bytes:
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname != urlparse(ONECAT_ORIGIN).hostname:
        raise ValueError("谱面文件地址不是 OneCat 官方地址")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _music_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _song_matches_range(song: dict[str, Any], min_ds: float, max_ds: float) -> bool:
    values = song.get("ds") if isinstance(song.get("ds"), list) else []
    return any(min_ds <= value <= max_ds for item in values if (value := _as_float(item)) is not None)


def _song_search_text(song: dict[str, Any]) -> str:
    basic = song.get("basic_info") if isinstance(song.get("basic_info"), dict) else {}
    values: list[str] = [str(song.get("id", "")), str(song.get("title", ""))]
    values.extend(str(item) for item in song.get("aliases", []) if item is not None)
    values.extend(str(basic.get(key, "")) for key in ("title", "artist", "genre", "from"))
    return " ".join(values).lower()


def _existing_short_ids(directory: Path) -> set[str]:
    result: set[str] = set()
    for path in directory.glob("*.txt"):
        try:
            song = parse_maidata(_decode_chart(path.read_bytes()))
        except Exception:
            continue
        if song.short_id:
            result.add(str(song.short_id))
    return result


def _eligible_text(text: str, min_ds: float, max_ds: float) -> tuple[Any, bool]:
    song = parse_maidata(text)
    if not song.short_id or not song.title or is_utage_title(song.title, song.meta.get("genre", "")):
        return song, False
    eligible = any(min_ds <= float(chart.ds or 0.0) <= max_ds for chart in song.charts.values())
    return song, eligible


def _write_chart(directory: Path, text: str, song: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / chart_output_name(song.short_id, song.title)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(text.rstrip() + "\n", encoding="utf-8")
    temp.replace(destination)
    return destination


class OfficialChartDownloader:
    def __init__(self, directory: str | Path = DEFAULT_LEVELS_PATH):
        self.directory = Path(directory).resolve()
        root = Root.resolve()
        try:
            self.directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("谱面目录必须位于插件根目录内") from exc

    def download(
        self,
        *,
        min_ds: Any = DEFAULT_MIN_DS,
        max_ds: Any = DEFAULT_MAX_DS,
        mode: Any = "all",
        query: Any = "",
        should_stop: StopCallback | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        low, high = validate_range(min_ds, max_ds)
        mode_value, query_value = validate_mode(mode, query)
        should_stop = should_stop or (lambda: False)
        progress = progress or (lambda _state: None)
        self.directory.mkdir(parents=True, exist_ok=True)

        payload = _json_request(MUSIC_DATA_URL)
        rows = []
        for song in _music_rows(payload):
            basic = song.get("basic_info") if isinstance(song.get("basic_info"), dict) else {}
            title = song.get("title") or basic.get("title", "")
            if is_utage_title(title, basic.get("genre", "")):
                continue
            if not _song_matches_range(song, low, high):
                continue
            if mode_value == "search" and query_value.lower() not in _song_search_text(song):
                continue
            rows.append(song)

        existing_ids = _existing_short_ids(self.directory) if mode_value == "missing" else set()
        selected_count = len(rows)
        state: dict[str, Any] = {
            "mode": mode_value,
            "min_ds": low,
            "max_ds": high,
            "query": query_value,
            "selected": selected_count,
            "total": selected_count,
            "processed": 0,
            "downloaded": 0,
            "skipped_existing": 0,
            "skipped_invalid": 0,
            "failed": 0,
            "current": "",
        }
        progress(state.copy())

        for index, song in enumerate(rows, start=1):
            if should_stop():
                state["stopped"] = True
                break
            song_id = str(song.get("id", "")).strip()
            title = str(song.get("title", "") or "").strip()
            state["processed"] = index
            state["current"] = f"{song_id} {title}".strip()
            if mode_value == "missing" and song_id in existing_ids:
                state["skipped_existing"] += 1
                progress(state.copy())
                continue
            try:
                list_url = f"{CHART_FILE_LIST_URL}?musicId={song_id}&bga=0"
                listing = _json_request(list_url)
                files = listing.get("files", []) if isinstance(listing, dict) else []
                text_files = [
                    item
                    for item in files
                    if isinstance(item, dict)
                    and (
                        str(item.get("name", "")).lower().endswith(".txt")
                        or str(item.get("name", "")).lower().endswith(".zip")
                    )
                ]
                if not text_files:
                    state["skipped_invalid"] += 1
                    progress(state.copy())
                    continue
                written = 0
                for item in text_files:
                    file_url = urljoin(ONECAT_ORIGIN + "/", str(item.get("url", "")))
                    raw = _binary_request(file_url)
                    for member_name, member_raw in extract_text_payloads(str(item.get("name", "")), raw):
                        text = _decode_chart(member_raw)
                        parsed, eligible = _eligible_text(text, low, high)
                        if not eligible:
                            state["skipped_invalid"] += 1
                            continue
                        _write_chart(self.directory, text, parsed)
                        written += 1
                        existing_ids.add(str(parsed.short_id))
                if written:
                    state["downloaded"] += written
                else:
                    state["skipped_invalid"] += 1
            except Exception as exc:
                state["failed"] += 1
                state["last_error"] = f"{type(exc).__name__}: {exc}"
            progress(state.copy())

        state["current"] = ""
        state["stopped"] = bool(state.get("stopped", False))
        state["completed"] = not state["stopped"]
        progress(state.copy())
        return state


__all__ = [
    "CHART_FILE_LIST_URL",
    "DEFAULT_LEVELS_PATH",
    "DEFAULT_MAX_DS",
    "DEFAULT_MIN_DS",
    "MAX_DOWNLOAD_DS",
    "MIN_DOWNLOAD_DS",
    "OfficialChartDownloader",
    "chart_output_name",
    "extract_text_payloads",
    "is_utage_title",
    "validate_mode",
    "validate_range",
]
