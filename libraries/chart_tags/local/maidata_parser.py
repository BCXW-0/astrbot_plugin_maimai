from __future__ import annotations

"""simai / maidata.txt 解析。

语法要点（社区 simai 记法与本地 Levels 文件）：
- 元数据：&title &wholebpm &lv_N &des_N &inote_N
- 难度号：2=Basic 3=Advanced 4=Expert 5=Master 6=Re:Master
- (bpm) 变速；{n} 每小节 n 等分；逗号推进一格
- 1-8 键；/ 同时；b break；h[x:y] hold；滑键如 1-4[8:1]、1>5[8:1]、5w1[8:1]
- DX 触摸：A/B/D/E + 编号，C/Ch 中心
"""


import re
from dataclasses import dataclass, field
from typing import Any

DIFF_INDEX_MAP = {
    2: 0,
    3: 1,
    4: 2,
    5: 3,
    6: 4,
}

_META_RE = re.compile(r"&([A-Za-z0-9_]+)=([^\n\r]*)")
_INOTE_RE = re.compile(r"&inote_(\d+)=([\s\S]*?)(?=&inote_\d+=|$)")


@dataclass
class NoteEvent:
    time: float
    kind: str  # tap/hold/slide/touch/break
    buttons: tuple[str, ...] = ()
    shape: str = ""
    duration: float = 0.0
    is_break: bool = False
    raw: str = ""
    bpm: float = 0.0
    path: tuple[str, ...] = ()
    is_ex: bool = False


@dataclass
class MaidataChart:
    diff_id: int
    level_index: int
    ds: float
    designer: str = ""
    bpm: float = 0.0
    events: list[NoteEvent] = field(default_factory=list)
    raw: str = ""


@dataclass
class MaidataSong:
    title: str = ""
    artist: str = ""
    whole_bpm: float = 0.0
    short_id: str = ""
    version: str = ""
    charts: dict[int, MaidataChart] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def parse_maidata(text: str) -> MaidataSong:
    text = str(text or "").lstrip("\ufeff")
    if not text or text.lstrip().startswith("{"):
        return MaidataSong()

    meta: dict[str, str] = {}
    for key, value in _META_RE.findall(text):
        if key.startswith("inote_"):
            continue
        meta[key] = value.strip()

    song = MaidataSong(
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        whole_bpm=_f(meta.get("wholebpm") or meta.get("bpm")),
        short_id=str(meta.get("shortid") or meta.get("id") or ""),
        version=meta.get("version", ""),
        meta=meta,
    )

    for diff_s, body in _INOTE_RE.findall(text):
        diff_id = int(diff_s)
        level_index = DIFF_INDEX_MAP.get(diff_id)
        if level_index is None:
            continue
        events, bpm0 = parse_inote(body, default_bpm=song.whole_bpm or 120.0)
        song.charts[level_index] = MaidataChart(
            diff_id=diff_id,
            level_index=level_index,
            ds=_f(meta.get(f"lv_{diff_id}")),
            designer=meta.get(f"des_{diff_id}", ""),
            bpm=bpm0 or song.whole_bpm,
            events=events,
            raw=body,
        )
    return song


def _duration_seconds(bpm: float, spec: str) -> float:
    spec = (spec or "").strip()
    if not spec:
        return 0.0
    if spec.startswith("#"):
        return max(0.0, _f(spec[1:]))
    if ":" in spec:
        left, right = spec.split(":", 1)
        divisor = max(_f(left, 4.0), 1e-6)
        count = max(_f(right, 1.0), 0.0)
        return count * (60.0 / max(bpm, 1e-6)) * (4.0 / divisor)
    return _f(spec) * (60.0 / max(bpm, 1e-6))


def parse_inote(body: str, default_bpm: float = 120.0) -> tuple[list[NoteEvent], float]:
    raw = re.sub(r"\s+", "", body or "")
    if not raw:
        return [], default_bpm

    bpm = default_bpm
    first_bpm = default_bpm
    divisor = 4.0
    t = 0.0
    events: list[NoteEvent] = []
    i = 0
    n = len(raw)

    def unit_dt() -> float:
        return (60.0 / max(bpm, 1e-6)) * (4.0 / max(divisor, 1e-6))

    while i < n:
        ch = raw[i]
        if ch == ",":
            t += unit_dt()
            i += 1
            continue
        if ch == "(":
            j = raw.find(")", i)
            if j < 0:
                break
            m = re.match(r"([0-9]+(?:\.[0-9]+)?)", raw[i + 1 : j])
            if m:
                bpm = _f(m.group(1), bpm)
                if abs(first_bpm - default_bpm) < 1e-9:
                    first_bpm = bpm
            i = j + 1
            continue
        if ch == "{":
            j = raw.find("}", i)
            if j < 0:
                break
            divisor = max(_f(raw[i + 1 : j], divisor), 1e-6)
            i = j + 1
            continue

        j = i
        depth = 0
        while j < n:
            c = raw[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth = max(0, depth - 1)
            elif c == "," and depth == 0:
                break
            elif c in "({" and depth == 0 and j > i:
                break
            j += 1
            if j - i > 800:
                break
        token = raw[i:j]
        if token:
            events.extend(_parse_group(token, t, bpm))
        i = j if j > i else i + 1

    return events, first_bpm


def _ring_segment(start: str, end: str, shape: str) -> tuple[str, ...]:
    """Return one ring segment, including both endpoints."""
    try:
        first = int(start)
        last = int(end)
    except (TypeError, ValueError):
        return (str(start), str(end))
    if first == last:
        return (str(first),)
    shape = str(shape or "")
    if "<" in shape or "q" in shape.lower():
        direction = -1
    elif ">" in shape or "p" in shape.lower():
        direction = 1
    else:
        clockwise = (last - first) % 8
        counter = (first - last) % 8
        direction = 1 if clockwise <= counter else -1
    result = [first]
    current = first
    for _ in range(8):
        current = ((current - 1 + direction) % 8) + 1
        result.append(current)
        if current == last:
            return tuple(str(value) for value in result)
    return (str(first), str(last))


def _slide_path_for_anchors(anchors: tuple[str, ...], shape: str) -> tuple[str, ...]:
    """Join every explicit simai slide anchor instead of dropping V-shaped points."""
    if not anchors:
        return ()
    result = [str(anchors[0])]
    for start, end in zip(anchors, anchors[1:]):
        segment = _ring_segment(str(start), str(end), shape)
        result.extend(segment[1:])
    return tuple(result)


def _slide_path(start: str, end: str, shape: str) -> tuple[str, ...]:
    return _slide_path_for_anchors((str(start), str(end)), shape)


def _parse_group(token: str, time: float, bpm: float) -> list[NoteEvent]:
    if not token or token == "E":
        return []
    out: list[NoteEvent] = []
    for part in token.split("/"):
        part = part.strip()
        if not part or part == "E":
            continue
        out.extend(_parse_atom(part, time, bpm))
    return out


def _parse_atom(atom: str, time: float, bpm: float) -> list[NoteEvent]:
    raw = atom
    is_break = "b" in atom and not atom.startswith("B")  # B1 touch vs break flag rough
    if re.fullmatch(r"B[1-8].*", atom):
        is_break = "b" in atom[2:]

    if "*" in atom and re.search(r"[1-8].*\*[1-8]", atom):
        left, right = atom.split("*", 1)
        return _parse_atom(left, time, bpm) + _parse_atom(right, time, bpm)

    m = re.match(r"^([1-8])(?:b|x|f)*h\[([^\]]+)\]$", atom)
    if m:
        return [NoteEvent(time=time, kind="hold", buttons=(m.group(1),), duration=_duration_seconds(bpm, m.group(2)), is_break=is_break, raw=raw, bpm=bpm, is_ex=("x" in atom))]

    slide_match = re.match(r"^([^\[]+)\[([^\]]+)\]$", atom)
    if slide_match:
        prefix = slide_match.group(1)
        anchors = tuple(re.findall(r"[1-8]", prefix))
    else:
        anchors = ()
    if len(anchors) >= 2:
        shape = re.sub(r"[1-8bxfh]", "", prefix, flags=re.IGNORECASE) or "-"
        return [NoteEvent(
            time=time,
            kind="slide",
            buttons=anchors,
            shape=shape,
            duration=_duration_seconds(bpm, slide_match.group(2)),
            is_break=is_break,
            raw=raw,
            bpm=bpm,
            path=_slide_path_for_anchors(anchors, shape),
            is_ex=("x" in atom),
        )]

    m = re.match(r"^((?:Ch|C)|(?:[ABDE][1-8]))(?:b|x|f)*h?\[([^\]]+)\]$", atom)
    if m:
        return [NoteEvent(time=time, kind="touch", buttons=(m.group(1),), duration=_duration_seconds(bpm, m.group(2)), is_break=is_break, raw=raw, bpm=bpm, is_ex=("x" in atom))]

    m = re.match(r"^((?:Ch|C)|(?:[ABDE][1-8]))(?:b|x|f)*$", atom)
    if m:
        return [NoteEvent(time=time, kind="touch", buttons=(m.group(1),), is_break=is_break, raw=raw, bpm=bpm, is_ex=("x" in atom))]

    m = re.match(r"^([1-8])(?:b|x|f)*$", atom)
    if m:
        kind = "break" if "b" in atom else "tap"
        return [NoteEvent(time=time, kind=kind, buttons=(m.group(1),), is_break=("b" in atom), raw=raw, bpm=bpm, is_ex=("x" in atom))]

    buttons = tuple(re.findall(r"[1-8]", atom))
    if not buttons:
        return []
    sm = re.search(r"\[([^\]]+)\]", atom)
    dur = _duration_seconds(bpm, sm.group(1)) if sm else 0.0
    kind = "slide" if sm and len(buttons) >= 2 else ("hold" if "h[" in atom else "tap")
    return [NoteEvent(time=time, kind=kind, buttons=buttons, duration=dur, is_break=("b" in atom), raw=raw, bpm=bpm, path=_slide_path_for_anchors(buttons, "-") if kind == "slide" and len(buttons) >= 2 else (), is_ex=("x" in atom))]
