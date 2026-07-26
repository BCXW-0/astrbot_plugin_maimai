from __future__ import annotations

from .builder import build_chart_tag_payload, generate_chart_tags_file
from .constants import ALLOWED_TAGS, TAG_CATEGORIES, TAG_RULE_VERSION, TAG_WEIGHTS, TARGET_LEVEL_INDEXES
from .job import ChartTagUpdateJob
from .lookup import format_chart_tags, get_chart_tag_scores, get_chart_tags
from .storage import CHART_TAGS_FILE, TAGS_DIR, read_chart_tags
try:
    from .local import rebuild_tags_from_maidata, analyze_song_id
except Exception:  # pragma: no cover
    rebuild_tags_from_maidata = None  # type: ignore
    analyze_song_id = None  # type: ignore

__all__ = [
    "ALLOWED_TAGS",
    "TAG_CATEGORIES",
    "TAG_RULE_VERSION",
    "TAG_WEIGHTS",
    "TARGET_LEVEL_INDEXES",
    "TAGS_DIR",
    "CHART_TAGS_FILE",
    "ChartTagUpdateJob",
    "build_chart_tag_payload",
    "generate_chart_tags_file",
    "read_chart_tags",
    "get_chart_tags",
    "get_chart_tag_scores",
    "format_chart_tags",
]
