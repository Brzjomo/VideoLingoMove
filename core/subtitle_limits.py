"""按语言给字幕长度档位：单行上限（显示宽度）+ 粗切词数上限。

为什么需要（2026-09-20 实测）
--------------------------
`subtitle.max_length` 原本是**所有语言共用**的一个数字（默认 75），而它在两条路径上
口径还不一样：

  * 源文侧：`len(src)`（**字符数**）
  * 译文侧：`calc_len(tr)`（**显示宽度**：中日 1.75 / 韩 1.5 / 其余 1）× `target_multiplier`

于是同一个 75：中文源文要 75 个字才触发切分（常规字幕是 12–16 字/行，约 5 倍），
中文译文 ≈36 字就触发，英文译文 ≈62 字符 —— **同一块屏幕、两套尺子**；换语言后这个
数字该填多少也完全靠猜。

本模块的做法：**统一度量（都用显示宽度）+ 两侧各按自己语言的档位取上限**。

  * 翻译模式：源文侧用**源语言**档、译文侧用**目标语言**档。
    双语字幕两行同宽，但两种语言"一行能放几个字"本来就不同（英文 40 字符 vs 中文 15 字），
    共用一个数必然是一边过碎、一边刚好。
  * 仅转录模式：只有一种语言，两侧都用识别语言档。
  * 识别不出语言 → 回退兜底档（42 / 16），`fallback=True` 会暴露给 UI 提示。

单位
----
`max_length` 的单位是**显示宽度**（≈ 半角字符宽）：中文/日文一字 1.75、韩文 1.5、
拉丁字母与半角符号 1（与 `core/step5_splitforsub.calc_len` 完全一致）。
所以 "26" 对中日文 ≈ 15 字/行、对拉丁文 = 26 字符/行；机械断行按**字符**计数时
用 `chars_cap()` 换算。

档位依据：Netflix 字幕规范——拉丁 ≤42 字符/行、中文/日文 ≤16 全角字/行、韩文 ≤16 字/行，
最多 2 行——向下取整留余量。`max_split_length` 是"粗切词数"（spaCy token ≈ 词，
同时作为 LLM 提示词里的 "each less than N words"），取值让一句约等于 2 行字幕。

开关语义（`subtitle.auto_length_by_language`）
------------------------------------------
  * **打开（默认）**：切语言即按档位覆盖 `max_length` / `max_split_length`；
    **运行期也按档位现算**（config 里那两个值只是"当前语言的落盘副本"，手改无效 ——
    要手填请先关掉开关）。
  * **关闭**：完全保留旧行为——单一 `max_length`，源文按字符数、译文按宽度 × multiplier。

批处理为什么也吃这套：`batch_processor` 是**逐行**把该行的 Source/Target Language 写进
config 再跑流水线（`batch/utils/batch_processor.py`），所以按"当前语言现算"天然逐行生效。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: 单行上限单位 = 显示宽度；max_split_length 单位 = spaCy token（≈ 词）
#:
#: ⚠️ 取值经过两次"实战回修"（2026-09-20，日语视频 ja→简体中文）：
#: 第一版按 Netflix TTSG 取"中日 26 宽度（≈15 字/行）"→ 过碎：本来一句话一条的
#: 被切成 2~3 条（кこんにちは、フリーのフィギュア原型師のミロです ≈ 39.5 也切）。
#: 第二版取 42 → 明显好转，但**45~80 宽度的句子仍在切**（用户反馈"还是不少比较碎的"）：
#:
#:     大学時代は彫刻を専攻しており3年生の時にガレージキットを作り始め   ← 34 字 ≈ 59.5
#:     フィギュアメーカーや原型製作会社に就職したことはありませんが、      ← 28 字 ≈ 49
#:     美少女フィギュアのデータ原型制作をメインに行っており                ← 24 字 ≈ 42
#:
#: 现在按"**整句优先**"校准：中日 60 宽度 ≈ 34 字/行（上面三句都不再切），
#: 只有真正长的句子（>69 宽度 ≈ 40 字）才切成 2 段；拉丁 70 字符、俄/RTL 65。
#: 另外 `exceeds()` 留了 15% 宽容量：只超一点点不切（切了观感更差）。
#:
#: 想更松/更严：
#:   * `config.yaml` 的 `subtitle.length_profiles` 覆盖（不用改代码），例如
#:     `zh: {max_length: 999}` 等于"这一档永不切句"；
#:   * 或关掉「按语言自动设置」，手填 `subtitle.max_length`。
LENGTH_PROFILES = {
    "zh": (60, 30),   # 60 / 1.75 ≈ 34 字/行
    "ja": (60, 30),
    "ko": (48, 28),   # 48 / 1.5 = 32 字/行
    "en": (70, 26),
    "es": (70, 26),
    "pt": (70, 26),
    "it": (70, 26),
    "fr": (70, 26),
    "de": (70, 26),
    "vi": (70, 26),
    "id": (70, 26),
    "ms": (70, 26),
    "tl": (70, 26),
    "ru": (65, 26),
    "ar": (65, 26),
    "fa": (65, 26),
    "ur": (65, 26),
    "he": (65, 26),
    "th": (65, 20),   # spaCy 无 th 模型，token 不可靠，机械路径按字符兜底
}

#: 识别不出语言时用它
FALLBACK_PROFILE = (70, 26)

#: 宽容量：只超过上限的 15% 以内都**不切**。
#: 例：中日上限 60 时，宽度 ≤69（≈39 字）的句子整条保留 —— 为了 1~2 个字符把一句话
#: 切成两条字幕，观感损失远大于"这一行略宽"。
GRACE = 1.15

#: 各语言的"每字符宽度"，与 step5.calc_len 保持一致（换算字符上限时用）
CHAR_WEIGHTS = {
    "zh": 1.75, "ja": 1.75, "ko": 1.5,
}

#: 语言码别名 → 规范码（项目的 UI/火山接口会给出 zh-CN / en-US 这类写法）
CODE_ALIASES = {
    "zh-cn": "zh", "zh-hans": "zh", "zh-sg": "zh", "zh-tw": "zh", "zh-hant": "zh",
    "zh-hk": "zh", "zh-min-nan": "zh", "cn": "zh", "chs": "zh", "cht": "zh",
    "jp": "ja", "jpn": "ja", "ja-jp": "ja",
    "kr": "ko", "kor": "ko", "ko-kr": "ko",
    "eng": "en", "en-us": "en", "en-gb": "en",
    "spa": "es", "es-mx": "es", "es-es": "es",
    "por": "pt", "pt-br": "pt", "pt-pt": "pt",
    "fra": "fr", "fre": "fr", "fr-fr": "fr",
    "deu": "de", "ger": "de", "de-de": "de",
    "ita": "it", "it-it": "it",
    "rus": "ru", "ru-ru": "ru",
    "ara": "ar", "ar-sa": "ar",
}

#: 自然语言描述 → 语言码。`target_language` 是**自由文本**（默认 '简体中文'），
#: 所以只能靠关键词命中；识别不出就不改值（返回 None），绝不猜错。
NAME_HINTS = (
    ("简体中文", "zh"), ("簡體中文", "zh"), ("繁体中文", "zh"), ("繁體中文", "zh"),
    ("中文", "zh"), ("汉语", "zh"), ("漢語", "zh"), ("普通话", "zh"), ("国语", "zh"),
    ("日语", "ja"), ("日語", "ja"), ("日文", "ja"), ("日本語", "ja"), ("日本语", "ja"),
    ("japanese", "ja"),
    ("韩语", "ko"), ("韓語", "ko"), ("韩文", "ko"), ("한국어", "ko"), ("korean", "ko"),
    ("英语", "en"), ("英語", "en"), ("英文", "en"), ("english", "en"),
    ("西班牙", "es"), ("spanish", "es"),
    ("葡萄牙", "pt"), ("portugu", "pt"),
    ("法语", "fr"), ("法語", "fr"), ("french", "fr"),
    ("德语", "de"), ("德語", "de"), ("german", "de"),
    ("意大利", "it"), ("italian", "it"),
    ("俄语", "ru"), ("俄語", "ru"), ("俄文", "ru"), ("russian", "ru"),
    ("越南", "vi"), ("vietnam", "vi"),
    ("印尼", "id"), ("印度尼西亚", "id"), ("indonesian", "id"),
    ("马来", "ms"), ("馬來", "ms"), ("malay", "ms"),
    ("菲律宾", "tl"), ("菲律賓", "tl"), ("tagalog", "tl"), ("filipino", "tl"),
    ("泰语", "th"), ("泰語", "th"), ("thai", "th"),
    ("阿拉伯", "ar"), ("arabic", "ar"),
    ("波斯", "fa"), ("persian", "fa"),
    ("乌尔都", "ur"), ("urdu", "ur"),
    ("希伯来", "he"), ("hebrew", "he"),
)

#: 视为"没指定"的值（识别语言的下拉框里 auto 就是这种）
_UNSET = {"", "auto", "自动", "自动检测", "none", "null", "unknown"}


@dataclass(frozen=True)
class SubtitleLimits:
    """一次解析出来的字幕长度限制（单位见模块 docstring）。"""

    auto: bool = True
    source_code: str = ""
    target_code: str = ""
    max_split_length: int = FALLBACK_PROFILE[1]
    src_limit: float = FALLBACK_PROFILE[0]
    tr_limit: float = FALLBACK_PROFILE[0]
    src_unit: str = "width"          # "width"（显示宽度）| "chars"（字符数，仅手动模式）
    src_char_cap: int = FALLBACK_PROFILE[0]
    tr_char_cap: int = FALLBACK_PROFILE[0]
    fallback: bool = False           # 语言没识别出来，用的兜底档
    label: str = ""

    def describe(self) -> str:
        """给日志/UI 用的一句话。"""
        if not self.auto:
            unit = "字符" if self.src_unit == "chars" else "宽度"
            return (f"手动模式：单行上限 {int(self.src_limit)}（源文按{unit}）/"
                    f"译文 {self.tr_limit:.0f} 宽度、粗切 {self.max_split_length}")
        src = self.source_code or "未知"
        if self.target_code and self.target_code != self.source_code:
            pair = f"源 {src} / 译 {self.target_code}"
        else:
            pair = src
        note = "，语言未识别→兜底档" if self.fallback else ""
        return (f"自动档位（{pair}）：单行 ≤{self.src_limit:.0f} 宽度"
                f"（源 {self.src_char_cap} 字 / 译 {self.tr_char_cap} 字）、"
                f"粗切 ≤{self.max_split_length} 词{note}")

    def _replace_label_and_describe(self) -> "SubtitleLimits":
        """把 `label` 填成按当前字段算出来的说明（frozen dataclass 只能重建）。"""
        return replace(self, label=self.describe())


def measure(text, unit="width") -> float:
    """按单位量一段文本：`width` = 显示宽度（中日 1.75 / 韩 1.5 / 其余 1）、`chars` = 字符数。

    与 `core/step5_splitforsub.calc_len` 同源（那边现在直接调本函数）。
    """
    text = str(text)
    if unit == "chars":
        return float(len(text))
    total = 0.0
    for char in text:
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF:      # 中日文
            total += 1.75
        elif 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:    # 韩文
            total += 1.5
        elif 0x0E00 <= code <= 0x0E7F:                                # 泰文
            total += 1
        elif 0xFF01 <= code <= 0xFF5E:                                # 全角符号
            total += 1.75
        else:                                                         # 拉丁/半角
            total += 1
    return total


def normalize_language(value) -> str | None:
    """语言码或自然语言描述 → 档位表里的语言码；识别不出返回 None（调用方不要乱猜）。"""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.lower() in _UNSET:
        return None
    low = raw.lower().replace("_", "-")
    for candidate in (low, low.split("-")[0]):
        code = CODE_ALIASES.get(candidate, candidate)
        if code in LENGTH_PROFILES:
            return code
    lowered = raw.lower()
    for hint, code in NAME_HINTS:
        if hint in lowered:
            return code
    return None


def chars_cap(language_code, limit_width) -> int:
    """把显示宽度上限换算成"该语言的字符上限"（机械断行按字符计数时需要）。"""
    weight = CHAR_WEIGHTS.get(language_code or "", 1.0)
    return max(1, int(round(float(limit_width) / weight)))


def profile_for(language_code, overrides=None) -> tuple[float, int, bool]:
    """取某语言的档位 → `(单行宽度上限, 粗切词数, 是否用了兜底档)`。

    `overrides` 来自 config 的 `subtitle.length_profiles`：
    `{语言码: {max_length: 30, max_split_length: 20}}`，只认这两个键、只认数字。
    """
    code = (language_code or "").strip().lower()
    if code in LENGTH_PROFILES:
        width, tokens = LENGTH_PROFILES[code]
        fallback = False
    else:
        width, tokens = FALLBACK_PROFILE
        code, fallback = "", True

    custom = (overrides or {}).get(code) if code else None
    if isinstance(custom, dict):
        if isinstance(custom.get("max_length"), (int, float)):
            width = float(custom["max_length"])
        if isinstance(custom.get("max_split_length"), (int, float)):
            tokens = int(custom["max_split_length"])
    return float(width), int(tokens), fallback


def exceeds(src_units, tr_units, limits: SubtitleLimits, grace: float = GRACE) -> bool:
    """任一侧超过**它自己那侧**上限（含宽容量）就要切。

    `grace`：只超一点点不切 —— 为了 1~2 个字符把一整句拆成两条字幕，观感更差。
    """
    return (src_units > limits.src_limit * grace
            or tr_units > limits.tr_limit * grace)


def parts_needed(src_units, tr_units, limits: SubtitleLimits, max_parts: int = 3) -> int:
    """一行需要切成几段：两侧各自算，取更严格的那个；夹在 [1, max_parts]。

    ⚠️ 只有 `exceeds()` 为真时调用方才会切；这里按**上限本身**（不含宽容量）算段数，
    所以刚过线的一行会被切成 2 段，而不是靠宽容量蒙过去。
    """
    need = 1
    if limits.src_limit > 0:
        need = max(need, int(-(-float(src_units) // limits.src_limit)))
    if limits.tr_limit > 0:
        need = max(need, int(-(-float(tr_units) // limits.tr_limit)))
    return max(1, min(int(max_parts), need))


def resolve_limits(source_language=None, target_language=None, transcription_only=None,
                   auto=None, manual=None, overrides=None) -> SubtitleLimits:
    """解析当前生效的字幕长度限制。

    参数为 None 的项从 config 读（UI / CLI 的日常调用方式）；显式传值便于测试。
    """
    if auto is None or manual is None or overrides is None or transcription_only is None \
            or source_language is None or target_language is None:
        from core.config_utils import load_key_or

        def _get(key, default=None):
            try:
                value = load_key_or(key, default)
            except Exception:
                return default
            return default if value is None else value

        if auto is None:
            auto = bool(_get("subtitle.auto_length_by_language", True))
        if transcription_only is None:
            transcription_only = bool(_get("transcription_only", False))
        if overrides is None:
            overrides = _get("subtitle.length_profiles", {}) or {}
        if source_language is None:
            source_language = _get("whisper.language") or _get("whisper.detected_language") or ""
        if target_language is None:
            target_language = _get("target_language") or ""
        if manual is None:
            manual = {
                "max_length": _get("subtitle.max_length", 75),
                "max_split_length": _get("max_split_length", 20),
                "target_multiplier": _get("subtitle.target_multiplier", 1.2),
            }

    if not auto:
        # 手动模式：完全保留旧行为（源文按字符数、译文按宽度 × multiplier）
        max_length = float(manual.get("max_length") or 75)
        multiplier = float(manual.get("target_multiplier") or 1.2) or 1.2
        tokens = int(manual.get("max_split_length") or 20)
        return replace(
            SubtitleLimits(), auto=False, max_split_length=tokens,
            src_limit=max_length, tr_limit=max_length / multiplier, src_unit="chars",
            src_char_cap=int(max_length), tr_char_cap=chars_cap(None, max_length / multiplier),
        )._replace_label_and_describe()

    source_code = normalize_language(source_language) or ""
    # 仅转录模式只有一种语言：目标侧也按识别语言取档
    target_code = source_code if transcription_only else (normalize_language(target_language) or "")

    src_width, src_tokens, src_fallback = profile_for(source_code, overrides)
    tr_width, _tr_tokens, tr_fallback = profile_for(target_code, overrides)
    limits = replace(
        SubtitleLimits(), auto=True, source_code=source_code, target_code=target_code,
        max_split_length=src_tokens,            # 粗切作用在**源文**句子上
        src_limit=src_width, tr_limit=tr_width, src_unit="width",
        src_char_cap=chars_cap(source_code, src_width),
        tr_char_cap=chars_cap(target_code, tr_width),
        fallback=bool(src_fallback or tr_fallback),
    )
    return limits._replace_label_and_describe()


def config_values(limits: SubtitleLimits) -> dict:
    """自动档位在 config 里"落盘"的两个值（面板显示与切语言写回都用它）。

    双语字幕的主行是译文，所以 `subtitle.max_length` 落译文侧的值；单语（源=目标，
    例如仅转录模式）自然就是同一个数。
    """
    primary = limits.tr_limit if limits.target_code != limits.source_code else limits.src_limit
    return {
        "subtitle.max_length": int(round(primary)),
        "max_split_length": int(limits.max_split_length),
    }


def apply_language_profile(source_language=None, target_language=None, transcription_only=None,
                           force=False) -> tuple[SubtitleLimits, dict]:
    """把当前语言的档位**写回 config**（切语言时调用）。返回 `(limits, 被改动的键)`。

    * 开关打开（`subtitle.auto_length_by_language: true`）：语言一变就覆盖，
      没有"保留手改值"的兜底 —— 想手填就关掉开关（这是刻意的，避免忘记后弄错）。
    * 开关关闭：除 `force=True`（UI 的"恢复当前语言推荐值"按钮）外不写。
    """
    limits = resolve_limits(source_language=source_language, target_language=target_language,
                            transcription_only=transcription_only)
    if not limits.auto and not force:
        return limits, {}
    if not limits.auto:
        # 手动模式下 resolve 给的是手填值，这里要的是"按语言算出来的档位"
        limits = resolve_limits(source_language=source_language, target_language=target_language,
                                transcription_only=transcription_only, auto=True, manual={})

    from core.config_utils import load_key_or, update_key

    changed = {}
    for key, value in config_values(limits).items():
        try:
            current = load_key_or(key)
        except Exception:
            current = None
        if current != value:
            update_key(key, value)
            changed[key] = value
    return limits, changed
