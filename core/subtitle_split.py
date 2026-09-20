"""字幕切分的"聪明一点"部分：句法边界优先 + 极短字幕合并。

为什么需要（2026-09-20 实测，日语视频 ja→简体中文）
--------------------------------------------------
Whisper 在 `initial_prompt=""` 下几乎不输出标点（该视频 6 分钟只有 27 个「。」、
50 个「、」），于是：
  * step3 的标点/接续切分几乎没有边界可用；
  * step5 只能**按显示宽度硬切**，切点落在句子中间 —— 用户看到的现象是
    "一句话被切成两半，前半接在上一条、后半接在下一条"（ABC 三句时 B 被劈开）。
本模块提供两件纯逻辑构件（不依赖 streamlit / 不联网，便于单测）：

1. `split_at_boundaries(text, n, window)`：按宽度算出理想切点后，在 ±window 窗口内
   **改挑最近的句法边界**（标点 → 日语接续/助词 → 拉丁连接词）；窗口内找不到就退回理想切点。
   窗口即 `subtitle.boundary_window`（默认 0.15，与 step5 的宽容量一致），
   因此**不会让任何一行比理想宽度长超过 15%**；也正因为与宽容量一致，切长一点的那段
   不会在 step5 下一轮又被判超限、再被切一刀。

2. `merge_short_cues(...)`：把**时长过短**的字幕并入相邻字幕 —— "哦/是的/原来如此"这种
   一闪而过的短句（用户 2026-09-20 要求：极短短句也并，否则没看清就消失了）。
   护栏：合并后不许超过 上限×宽容量；两条字幕的时间间隔不许超过 `max_gap`；
   没有合适的邻居就保持原样（宁可短，也不要把不相关的话并到一起）。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

#: 硬边界：出现在这些字符**之后**可以直接切（注意必须含「、」「，」「：」——
#: 2026-09-20 实测：漏了「、」会让日语长句找不到任何边界，退回按宽度硬切，
#: 正好切在「…専攻しており、3年生…」的「3年生」中间）
HARD_BOUNDARY = "。！？；：!?;:…、，"
#: 软边界（中日文没有标点时的次优选择）：出现在这些**词尾**之后可以切
CJK_SOFT_BOUNDARY = ("て", "で", "が", "ので", "から", "けど", "けれど", "たり", "し", "ます", "です")
#: 拉丁文的软边界：连接词/关系词前切开
LATIN_SOFT_BOUNDARY = (" and ", " but ", " so ", " because ", " which ", " that ", " when ", " while ")


def _is_cjk_language(language: str) -> bool:
    return language in ("zh", "ja", "ko")


def boundary_candidates(text: str, language: str = "") -> List[int]:
    """返回所有"可以切"的位置（= 下一段的起始下标）。硬边界在前、软边界在后。"""
    hard: List[int] = []
    soft: List[int] = []
    for index, char in enumerate(text):
        if char in HARD_BOUNDARY:
            hard.append(index + 1)
    if _is_cjk_language(language):
        for index in range(1, len(text)):
            window = text[max(0, index - 3):index]
            if any(window.endswith(mark) for mark in CJK_SOFT_BOUNDARY):
                soft.append(index)
    else:
        lowered = text.lower()
        for marker in LATIN_SOFT_BOUNDARY:
            start = 0
            while (found := lowered.find(marker, start)) != -1:
                soft.append(found + 1)          # 切在连接词前
                start = found + 1
    return hard + soft


def split_at_boundaries(text: str, n: int, window: float = 0.15,
                        language: str = "") -> List[str]:
    """把文本切成 n 段：每段的切点在"理想切点 ±window"内挑最近的句法边界。

    找不到边界 / 边界会把相邻段压得过短 → 该刀退回理想位置（即现在的行为）。
    """
    text = str(text)
    n = int(n)
    if n <= 1 or len(text) < n:
        return [text]
    candidates = boundary_candidates(text, language)
    ideal = [round(len(text) * k / n) for k in range(1, n)]
    ideal_part = len(text) / n
    min_part = max(2, int(ideal_part * 0.25))     # 别切出"两个字"的碎片
    cuts: List[int] = []
    for target in ideal:
        lo = max(1, int(target * (1 - window)))
        hi = min(len(text) - 1, int(target * (1 + window)) + 1)
        chosen = None
        options = [pos for pos in candidates if lo <= pos <= hi]
        if options:
            options.sort(key=lambda pos: (abs(pos - target), pos))
            for pos in options:
                previous = cuts[-1] if cuts else 0
                if pos - previous >= min_part and len(text) - pos >= min_part:
                    chosen = pos
                    break
        cuts.append(chosen if chosen is not None else target)
    parts: List[str] = []
    previous = 0
    for cut in cuts:
        parts.append(text[previous:cut])
        previous = cut
    parts.append(text[previous:])
    return parts


_TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)")


def parse_timestamps(value) -> Optional[tuple]:
    """解析 `"HH:MM:SS,mmm --> HH:MM:SS,mmm"` → `(start, end)` 秒；解析不出返回 None。"""
    text = str(value or "")
    found = _TS.findall(text)
    if len(found) < 2:
        return None

    def to_seconds(parts):
        hours, minutes, seconds, fraction = parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, "0")[:3]) / 1000

    return to_seconds(found[0]), to_seconds(found[1])


def merge_short_cues(rows: Sequence[dict], limits, *, min_duration: float = 0.8,
                     max_gap: float = 0.35, joiner: str = "",
                     grace: Optional[float] = None) -> List[dict]:
    """把"停留时间过短"的字幕并入相邻字幕。

    `rows` 每项：`{"source": str, "translation": str, "start": float, "end": float}`。
    合并规则（全部满足才并）：
      * 该条时长 < `min_duration`（默认 0.8 s；Netflix 的最短时长是 5/6 s ≈ 0.83 s）；
      * 与邻居的时间间隔 ≤ `max_gap`；
      * 合并后任一侧宽度不超过 `上限 × 宽容量`（`grace` 缺省用 subtitle_limits.GRACE）。
    优先与**后一条**合并（顺读顺序），后一条不合适才并到前一条；都不合适就保留原样。
    """
    from core import subtitle_limits as sl

    grace = sl.GRACE if grace is None else grace
    pending = [dict(row) for row in rows]
    merged: List[dict] = []
    index = 0
    while index < len(pending):
        row = pending[index]
        duration = row["end"] - row["start"]
        if duration >= min_duration or len(pending) == 1:
            merged.append(row)
            index += 1
            continue

        nxt = pending[index + 1] if index + 1 < len(pending) else None
        prev = merged[-1] if merged else None
        for candidate, forward in ((nxt, True), (prev, False)):
            if candidate is None:
                continue
            gap = (candidate["start"] - row["end"]) if forward else (row["start"] - candidate["end"])
            if gap > max_gap:
                continue
            source = (row["source"] + joiner + candidate["source"]) if forward \
                else (candidate["source"] + joiner + row["source"])
            translation = (row["translation"] + candidate["translation"]) if forward \
                else (candidate["translation"] + row["translation"])
            if sl.measure(source) > limits.src_limit * grace:
                continue
            if sl.measure(translation) > limits.tr_limit * grace:
                continue
            combined = {
                "source": source,
                "translation": translation,
                "start": min(row["start"], candidate["start"]),
                "end": max(row["end"], candidate["end"]),
            }
            if forward:
                pending[index + 1] = combined      # 后一条吸收本条
            else:
                merged[-1] = combined              # 并进已输出的上一条
            row = None                             # 已并走，别再单独输出
            break
        if row is not None:
            merged.append(row)
        index += 1
    return merged


def rows_from_pairs(sources: Iterable[str], translations: Iterable[str],
                    timestamps: Optional[Iterable] = None) -> List[dict]:
    """把 step5 的三列拼成 `merge_short_cues` 需要的行结构（时间戳缺失时给 0）。"""
    sources, translations = list(sources), list(translations)
    stamps = list(timestamps) if timestamps is not None else [None] * len(sources)
    rows = []
    for index, (source, translation) in enumerate(zip(sources, translations)):
        parsed = parse_timestamps(stamps[index]) if index < len(stamps) else None
        start, end = parsed if parsed else (0.0, 0.0)
        rows.append({"source": str(source), "translation": str(translation),
                     "start": start, "end": end})
    return rows
