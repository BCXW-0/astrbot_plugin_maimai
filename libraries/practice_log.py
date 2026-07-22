"""本地练习打卡（免费、隐私本地存储）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from .. import log, static

PRACTICE_FILE = static / 'user_practice_log.json'


def _load() -> Dict[str, Any]:
    if not PRACTICE_FILE.exists():
        return {}
    try:
        with open(PRACTICE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning(f'读取练习打卡失败: {type(exc).__name__}: {exc}')
        return {}


def _save(data: Dict[str, Any]) -> None:
    PRACTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRACTICE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_practice(qqid: str, song_id: str, note: str = '', achievements: str = '') -> Dict[str, Any]:
    data = _load()
    user = data.setdefault(str(qqid), {'items': []})
    items: List[dict] = user.setdefault('items', [])
    entry = {
        'song_id': str(song_id),
        'note': note or '',
        'achievements': achievements or '',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    items.append(entry)
    # 仅保留最近 50 条
    user['items'] = items[-50:]
    _save(data)
    return entry


def list_practice(qqid: str, limit: int = 5) -> List[Dict[str, Any]]:
    data = _load()
    items = list((data.get(str(qqid)) or {}).get('items') or [])
    return items[-limit:]


def today_count(qqid: str) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    return sum(1 for item in list_practice(qqid, limit=50) if str(item.get('time', '')).startswith(today))
