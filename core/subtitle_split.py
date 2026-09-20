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
from collections import Counter
from typing import Iterable, List, Optional, Sequence, Tuple

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


def translation_parts_ok(parts: Sequence[str], original: str, min_width: float = 6.0) -> bool:
    """校验 LLM 把整句译文切成若干段的结果是否可用（纯函数，便于单测）。

    只拦**确定坏了**的结果：
      * 段数少于 2 或与源文段数不符（调用方负责传对齐后的 parts）；
      * 任一段为空；
      * **归一化后拼接 ≠ 原译文**：重复连接词（用户实测：`…程度 同时` + `同时也…`，
        拼接出现两个"同时"）、漏词、改写，全都在这里被拦下；
      * 出现**孤立碎片**：某段宽度 < `min_width`（默认 6 ≈ 3~4 个汉字）。
        阈值刻意取"小绝对值"而**不是**"全行的百分比" —— 百分比会把
        `软件有好几款`（6 字）这种正常短段也误判，一误判就被降级成等分切分，反而更差。

    归一化：去掉空白与标点（`\\w` 之外全删），与 step6 的对齐口径一致。
    """
    return check_align_parts(parts, original, min_width=min_width, allow_rewrite=False)[0]


def normalize_text(text) -> str:
    """去掉空白与标点（`\w` 之外全删），与 step6 的对齐口径一致。"""
    return re.sub(r"[^\w]", "", str(text))


def normalized_concat_matches(parts: Sequence[str], original: str) -> bool:
    """拼接后是否**逐字等于**原译文（去空白/标点）。等于就说明 LLM 这一行没有改写。"""
    return normalize_text(join_parts(parts)) == normalize_text(original)


#: "拉丁/数字"字母：两侧都是这种字符时拼接必须补空格（CJK 之间不补）
_WORDISH = re.compile(r"[0-9A-Za-z\u00c0-\u024f']")


def join_parts(parts: Sequence[str]) -> str:
    """按字幕顺序拼接各段；只在"两侧都是拉丁/数字字母"时补一个空格。

    直接 `"".join` 会把英文粘成 `…garage kitsalso also…` 这种假词，既掩盖了边界重复
    （"also also" 变成 `kitsalso` + `also`），又让 n-gram 计数失真。CJK 之间**不能**补空格，
    否则 n-gram 会被空格切断、计数全部对不上。
    """
    out = ""
    tail = ""
    for raw in parts:
        part = str(raw)
        if not part:
            continue
        if tail and _WORDISH.match(tail) and _WORDISH.match(part[0]):
            out += " "
        out += part
        tail = part[-1]
    return out


#: 中日韩字符（含假名、谚文）：逐字成 token
_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
#: 在"含中日韩字符"的片段里再切：CJK 逐字，拉丁/数字整体保留
_TOKEN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]|[A-Za-z0-9\u00c0-\u024f']+")
#: 允许轻改写时的护栏（见 `check_align_parts`）
REWRITE_COVERAGE_MIN = 0.85
REWRITE_LENGTH_RATIO = (0.8, 1.3)
#: 拉丁文单词重复的最小长度 + 例外表：顺句时补一个 the/of/to 属正常，补一个 also 就要拦
_LATIN_DUP_MIN_LEN = 4
_LATIN_STOPWORDS = frozenset("""
that this with they have from will been were your what when which their there then than them these
those some more most much many very just only over into about after before because while where
""".split())


def tokenize(text) -> List[str]:
    """把文本切成可比较的 token 流：中日韩逐字，其余按词（小写、去标点）。

    n-gram 计数都在这个流上做，因此 `用 Python 写的数字软件` 这类混排也能一起比较。
    """
    tokens: List[str] = []
    for word in re.findall(r"\w+", str(text), re.UNICODE):
        if _CJK_CHAR.search(word):
            tokens.extend(_TOKEN_RE.findall(word))
        else:
            tokens.append(word.lower())
    return tokens


def _ngram_counts(tokens: Sequence[str], n: int) -> Counter:
    return Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))


def _gram_label(gram: Sequence[str]) -> str:
    """把 n-gram 拼成人能读的形式：`('同','时')` → `同时`；`('also','also')` → `also also`。"""
    if all(len(token) == 1 and _CJK_CHAR.match(token) for token in gram):
        return "".join(gram)
    return " ".join(gram)


def _adjacent_repeat(tokens: Sequence[str], original_tokens: Sequence[str]) -> Optional[str]:
    """拼接里"连着重复同一个 token"、而原文没有这种相邻重复 → 典型切点重复（同时同时 / also also）。"""
    original_pairs = set(zip(original_tokens, original_tokens[1:]))
    for pair in zip(tokens, tokens[1:]):
        if pair[0] == pair[1] and pair not in original_pairs:
            return pair[0]
    return None


def _inflated_ngram(tokens: Sequence[str], original_tokens: Sequence[str],
                    sizes: Sequence[int]) -> Optional[Tuple[str, int, int]]:
    """找"原文里出现过、拼接里却变多"的 n-gram —— 重复连接词/重复短语的核心判据。

    只比较**原文里出现过**（次数 ≥ 1）的 n-gram：改写在切点补一个原文没有的新词会产生原文
    计数为 0 的新 n-gram，这是"轻改写"允许的代价；但原文已有的说法在拼接里变多，就意味着
    观众连着两条字幕把同一句读了两遍 —— 这正是要拦的。
    """
    for n in sizes:
        counts = _ngram_counts(tokens, n)
        original_counts = _ngram_counts(original_tokens, n)
        for gram, count in counts.items():
            seen = original_counts.get(gram, 0)
            if seen < 1 or count <= seen:
                continue
            if n == 1 and (len(gram[0]) < _LATIN_DUP_MIN_LEN or gram[0] in _LATIN_STOPWORDS):
                continue
            return _gram_label(gram), count, seen
    return None


def _spurious_repeat(tokens: Sequence[str], original_tokens: Sequence[str],
                     sizes: Sequence[int]) -> Optional[Tuple[str, int]]:
    """找"原文里**根本没有**、拼接里却出现 ≥2 次"的 n-gram。

    为什么还要这条：`同时同时` 这种重复里的"同时"在原文里可能一次都没出现（原文写的是"同样"），
    于是"和原文比计数"的 `_inflated_ngram` 抓不到它。改写在切点补一个原文没有的新词是允许的
    （所以出现 **1** 次不拦），但同一个新说法出现 **2** 次就只可能是切点重复 —— 观众连着两条
    字幕读两遍，必须拦。
    """
    for n in sizes:
        counts = _ngram_counts(tokens, n)
        original_counts = _ngram_counts(original_tokens, n)
        for gram, count in counts.items():
            if count < 2 or original_counts.get(gram, 0) > 0:
                continue
            if n == 1 and (len(gram[0]) < _LATIN_DUP_MIN_LEN or gram[0] in _LATIN_STOPWORDS):
                continue
            return _gram_label(gram), count
    return None


def _coverage(tokens: Sequence[str], original_tokens: Sequence[str]) -> float:
    """原译文的 token 有多大比例出现在拼接结果里（多重集计数：原文重复的 token 也要重复出现）。"""
    if not original_tokens:
        return 1.0
    have, want = Counter(tokens), Counter(original_tokens)
    kept = sum(min(count, want[token]) for token, count in have.items())
    return kept / len(original_tokens)


def check_align_parts(parts: Sequence[str], original: str, min_width: float = 6.0, *,
                      allow_rewrite: bool = True) -> Tuple[bool, str]:
    """校验 LLM 的译文对齐结果，返回 `(是否可用, 失败原因)`（纯函数，便于单测）。

    失败原因由 `ask_gpt` 回注给模型重试（见 core/ask_gpt.py:145-151），因此文案必须能直接
    指导它"怎么重切"，不要写成用户侧的黑话。

    两档严格度（开关 `subtitle.align_allow_rewrite`）：
      * `allow_rewrite=False`（2026-09-20 之前的旧行为）：拼接必须**逐字等于**原译文；
      * `allow_rewrite=True`（用户 2026-09-20 批准的 B 方案）：允许在切点处轻改写，让每一行
        单独看是通顺的句子（补/删连接词、补足助词），但四类"确定坏了"的结果一律拦下：
        ① 段数 < 2 / 空段 / 孤立碎片；② 长度比越界（增删信息）；③ **原文已有的说法在拼接里
        变多**（重复，含 `同时同时` 这类相邻重复）；④ 覆盖率 < `REWRITE_COVERAGE_MIN`（漏译）。

    为什么不能只"放开改写"：用户实测旧提示词允许改写后出现过 `…程度 同时` + `同时也…`，
    观众连着两条字幕读到两个"同时"。这里用**计数对比**而不是"是否出现"，因此既能抓住它，
    又不会误伤原文本身就重复的句子（"去了东京，东京很大"）。
    """
    from core import subtitle_limits as sl

    parts = [str(part).strip() for part in parts]
    if len(parts) < 2:
        return False, f"expected at least 2 parts, got {len(parts)}"
    for index, part in enumerate(parts):
        if not part:
            return False, f"target_part_{index + 1} is empty"
    for index, part in enumerate(parts):
        width = sl.measure(part)
        if width < min_width:
            return False, (f"target_part_{index + 1} ('{part}') is an isolated fragment "
                           f"(only {width:.1f} display columns): merge it with the neighbouring part")

    concat = join_parts(parts)
    if normalized_concat_matches(parts, original):
        return True, "verbatim split"

    if not allow_rewrite:
        return False, ("concatenating target_part_* must reproduce the original translation exactly: "
                       "no added/duplicated/dropped words (e.g. don't repeat a connective like 同时/also), "
                       "no empty part, and no isolated fragment. Re-split without rewriting.")

    # —— 允许轻改写：只拦"确定坏了"的改写 ——
    tokens, original_tokens = tokenize(concat), tokenize(original)
    sizes = (2, 3) if _CJK_CHAR.search(concat + str(original)) else (1, 2, 3)

    if original_tokens:
        ratio = len(tokens) / len(original_tokens)
        low, high = REWRITE_LENGTH_RATIO
        if not (low <= ratio <= high):
            return False, (f"the rewritten parts are {ratio:.0%} of the original translation's length; "
                           f"light rewriting is allowed but adding/dropping information is not — "
                           f"keep it within {low:.0%}-{high:.0%}")

    repeated = _adjacent_repeat(tokens, original_tokens)
    if repeated:
        return False, (f"'{repeated}' is repeated twice in a row at the boundary; the audience reads "
                       f"both cues in a row, so remove the duplicated word (it is not in the original)")

    inflated = _inflated_ngram(tokens, original_tokens, sizes)
    if inflated:
        gram, count, seen = inflated
        return False, (f"'{gram}' appears {count} times after concatenation but only {seen} time(s) in "
                       f"the original translation: don't repeat what the neighbouring part already "
                       f"says, especially connectives like 同时/also/そして. Split literally instead")

    spurious = _spurious_repeat(tokens, original_tokens, sizes)
    if spurious:
        gram, count = spurious
        return False, (f"'{gram}' appears {count} times in your parts although the original translation "
                       f"does not contain it at all: you duplicated what the neighbouring part already "
                       f"says across the boundary. Split literally instead")

    coverage = _coverage(tokens, original_tokens)
    if coverage < REWRITE_COVERAGE_MIN:
        return False, (f"the parts only keep {coverage:.0%} of the original translation's content; "
                       f"rewriting must not drop information — split literally instead")

    return True, "rewritten but content preserved"


#: 行尾要抹掉的标点：句末标点 + 续句逗号（句中一律不动）
_TERMINAL_MARKS = set("。．.!！?？;；:：…、，,")
#: 收尾的引号/括号：只有在紧跟标点时才算"句末标点的一部分"（`…です。」` → `…です`），
#: 单独出现时保留（`「はい」` 不该被削成 `「はい`）
_CLOSING_MARKS = set("」』】）)〉》”’")


def strip_terminal_punctuation(text) -> str:
    """去掉**行尾**的句末标点，句中/行首的标点保持原样。

    2026-09-20 用户要求：最终字幕的原文与译文都不要行尾标点，句中维持现状。
    反复剥离直到稳定（`…です。」` / `…です！！` 都能一次到位）。
    """
    if text is None:
        return ""
    if isinstance(text, float) and text != text:      # NaN：别把它变成字符串 "nan" 写进字幕
        return ""
    value = str(text).rstrip()
    while value:
        last = value[-1]
        if last in _TERMINAL_MARKS:
            value = value[:-1].rstrip()
            continue
        if (last in _CLOSING_MARKS and len(value) > 1
                and (value[-2] in _TERMINAL_MARKS or value[-2] in _CLOSING_MARKS)):
            value = value[:-1].rstrip()
            continue
        break
    return value


def merge_broken_cuts(lines: Iterable[str], boundary_offsets, *,
                      join_leading_particles: bool = True) -> List[str]:
    """把"切在词中"的相邻行并回去（2026-09-20 实测事故的**治本**守卫）。

    事故链条：`step3_1`（纯规则切分，**没有提示词可管**）把
    `…そのキャラクターやイラストの魅力を探り、フィギュアとし | てその…`
    从「として」中间劈开 → step4 只能把两个半句**各自翻译完整** → 出现
    "…同时注重这一点" 与下一条"同时也注重…"的重复。提示词改不动这一层。

    判定：相邻两行 `a + b` 的拼接点上，`len(a)` 必须是**合法切点**
    （`boundary_offsets(combined)` 给出；调用方用 spaCy 的 `token.idx` 实现，
    测试可注入假实现）—— 否则说明切在词里，合并回去。
    辅助信号：下一行以日语助词/接续（`て/で/に/を/は/が/と/も/…`）开头时，即使
    看似在 token 边界上也判为坏切点（"…とし | て…" 这种正是如此）。
    """
    result: List[str] = []
    for raw in lines:
        line = str(raw).strip()
        if not line:
            continue
        if not result:
            result.append(line)
            continue
        previous = result[-1]
        combined = previous + line
        cut = len(previous)
        try:
            ok = cut in set(boundary_offsets(combined))
        except Exception:
            ok = True                       # 拿不到边界信息就不动（宁可少并）
        if ok and join_leading_particles and _starts_with_particle(line):
            ok = False
        if ok:
            result.append(line)
        else:
            result[-1] = combined
    return result


#: 日语里"以此开头的行 = 从词中间被劈开"的字符（助词/接续/促音/长音/小假名）
_LEADING_PARTICLES = set("てでにをはがともへのやかねよなずせさりっーゃゅょぁぃぅぇぉ")


def _starts_with_particle(line: str) -> bool:
    first = line[:1]
    return bool(first) and first in _LEADING_PARTICLES


def spaCy_boundaries(nlp, text: str) -> set:
    """用 spaCy 的 token 起始偏移构造"合法切点"集合（供 step3_1 / step3_2 调用）。"""
    return {token.idx for token in nlp(text)} | {len(text)}


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
