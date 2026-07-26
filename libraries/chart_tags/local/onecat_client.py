from __future__ import annotations

"""OneCat 官谱 maidata 下载（只取谱面文本，不下载 BGA/音频）。"""


import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE = "https://dw.moant.cn:34225"
USER_AGENT = "astrbot-maimai-chart-tags/1.8 (+local-maidata-analyzer)"


class OneCatClient:
    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def get_music_data(self) -> list[dict[str, Any]]:
        raw = self._get("/api/getMusicData")
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return data if isinstance(data, list) else []

    def chart_file_list(self, music_id: str | int, bga: bool = False) -> list[dict[str, Any]]:
        raw = self._get("/api/chartFileList", {"musicId": str(music_id), "bga": "1" if bga else "0"})
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        files = data.get("files") if isinstance(data, dict) else None
        return files if isinstance(files, list) else []

    def download_maidata(self, music_id: str | int) -> str:
        raw = self._get("/api/chartFile", {"musicId": str(music_id), "file": "maidata.txt"})
        text = raw.decode("utf-8", errors="ignore")
        if text.lstrip().startswith("{"):
            try:
                err = json.loads(text)
            except Exception:
                err = {"message": text[:200]}
            raise FileNotFoundError(f"maidata unavailable for {music_id}: {err}")
        return text

    def iter_high_level_songs(self, min_ds: float = 12.6) -> list[dict[str, Any]]:
        out = []
        for song in self.get_music_data():
            ds_list = song.get("ds") or []
            max_ds = 0.0
            for value in ds_list:
                try:
                    max_ds = max(max_ds, float(value))
                except Exception:
                    continue
            if max_ds < min_ds:
                continue
            item = dict(song)
            item["_max_ds"] = max_ds
            out.append(item)
        out.sort(key=lambda s: (-float(s.get("_max_ds") or 0), str(s.get("id"))))
        return out


def download_maidata(
    music_id: str | int,
    dest_dir: str | Path | None = None,
    client: OneCatClient | None = None,
) -> Path | str:
    client = client or OneCatClient()
    text = client.download_maidata(music_id)
    if dest_dir is None:
        return text
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{music_id}.txt"
    path.write_text(text, encoding="utf-8")
    return path
