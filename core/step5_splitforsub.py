import sys, os
import math
import threading
import pandas as pd
from typing import List, Optional, Tuple
import concurrent.futures
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.step3_2_splitbymeaning import split_sentence
from core.ask_gpt import ask_gpt
from core.prompts_storage import get_align_prompt
from core.config_utils import load_key, get_joiner, get_source_language, use_llm_sentence_split
from core import config_utils
from core import subtitle_limits
from core import subtitle_split
from core.subtitle_limits import resolve_limits
import easy_util as eu
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

console = Console()


def split_text_evenly(text: str, n: int) -> Optional[List[str]]:
    """把文本近似等分成**恰好 n 段**；做不到就返回 `None`（由调用方放弃切这一行）。

    对齐由 step6 负责（它用 Source 拼串做精确匹配取时间戳），所以这里只要求两件事：
      1. 拼接回去等于原文（等分即可满足）；
      2. **段数恰好是 n** —— 调用方是把源文/译文两个列表按位置配对的。

    旧实现在 `len(text) < n` 以及"某段 strip 后为空"时返回**少于 n 段**
    （`return [text]` / `if seg:` 丢掉空段），于是拍平后源/译两个列表长度不等：
    所有后续行错位，`pd.DataFrame({'Source':…, 'Translation':…})` 还会直接抛
    `ValueError: arrays must all be same length`（见 devdocs 已知问题）。
    返回 None 就是"别假装成功"，调用方会整行保留、保持两列成对。
    """
    text = str(text)
    n = int(n)
    if n <= 1:
        return None
    visible = [index for index, char in enumerate(text) if not char.isspace()]
    if len(visible) < n:
        # 可见字符不足 n 个：再切必然出现空段（那就是"只显示一侧"的字幕）
        return None
    # 按"可见字符数"等分：第 k 段的边界落在第 ceil(total*k/n) 个可见字符之后
    total = len(visible)
    parts: List[str] = []
    previous = 0
    for k in range(1, n + 1):
        if k < n:
            cut = visible[math.ceil(total * k / n) - 1] + 1
            parts.append(text[previous:cut])
            previous = cut
        else:
            parts.append(text[previous:])
    if len(parts) != n or any(not part.strip() for part in parts):
        return None
    return [part.strip() for part in parts]


def calc_len(text: str) -> float:
    """文本的显示宽度（中日 1.75 / 韩 1.5 / 其余 1）。

    实现已搬到 `core/subtitle_limits.measure`，这里保留同名包装：一是历史调用，
    二是"字幕长度"相关的一切度量都应只有一处定义。
    """
    return subtitle_limits.measure(text, "width")


# Constants
INPUT_FILE = "output/log/translation_results.xlsx"
OUTPUT_SPLIT_FILE = "output/log/translation_results_for_subtitles.xlsx"
OUTPUT_REMERGED_FILE = "output/log/translation_results_remerged.xlsx"


def measure_for(text: str, limits: subtitle_limits.SubtitleLimits) -> float:
    """按当前档位约定的单位量一行文本（自动=显示宽度；手动模式源文按字符数）。"""
    return subtitle_limits.measure(text, limits.src_unit)


def row_overflows(src: str, tr: str, limits: subtitle_limits.SubtitleLimits) -> bool:
    """这一行是否需要再切：任一侧超过**它自己那一侧**的上限就要切。"""
    return subtitle_limits.exceeds(measure_for(src, limits), calc_len(tr), limits)

#: 孤立碎片阈值（显示宽度）：≈3~4 个汉字。刻意取**小的绝对值**而不是"全行百分比"——
#: 百分比会把 `软件有好几款`（6 字）这种正常短段也误判成碎片，一误判就被降级成等分切分。
_MIN_PART_WIDTH = 6.0


def _align_validation_enabled() -> bool:
    """是否校验 LLM 的对齐结果（`subtitle.align_validate`，默认开，见 core/subtitle_split.py）。"""
    try:
        return bool(load_key("subtitle.align_validate"))
    except Exception:
        return True


def _align_rewrite_allowed() -> bool:
    """是否允许对齐时**轻改写**（`subtitle.align_allow_rewrite`，默认开）。

    开：模型可以在切点处移动边界/虚词、补一个虚词，让两条字幕各自不悬空，护栏见
    `subtitle_split.check_align_parts`（拦重复、漏译、长度暴涨、孤立碎片、悬空开头）。
    关：提示词换成严格模式（`core/prompts_storage._ALIGN_RULE3_STRICT`，一个字都不许动），
    校验也退回"拼接必须逐字等于原译文"。两处共用 `config_utils.align_allow_rewrite()`，
    避免"开关只切换校验、提示词还在允许改写"造成的无谓重试。
    """
    return config_utils.align_allow_rewrite()


#: 对齐统计：跑完汇总打印，用来判断"允许轻改写"的真实拦下率（用户 2026-09-20 批准的 B 方案）。
#: 多线程（max_workers 个 process 并发）→ 必须加锁。
_ALIGN_STATS = {"rows": 0, "rewritten": 0, "rejected": 0, "fallback": 0}
_ALIGN_STATS_LOCK = threading.Lock()
_ALIGN_STATS_REASONS: List[str] = []


def reset_align_stats() -> None:
    with _ALIGN_STATS_LOCK:
        for key in _ALIGN_STATS:
            _ALIGN_STATS[key] = 0
        _ALIGN_STATS_REASONS.clear()


def _bump_align_stat(key: str, reason: str = "") -> None:
    with _ALIGN_STATS_LOCK:
        _ALIGN_STATS[key] = _ALIGN_STATS.get(key, 0) + 1
        if reason and len(_ALIGN_STATS_REASONS) < 5:
            _ALIGN_STATS_REASONS.append(reason)


def align_stats_summary() -> str:
    """一行汇总；没有改写、没拦下、没回退时返回空串（正常片子不该多出噪音）。"""
    with _ALIGN_STATS_LOCK:
        stats = dict(_ALIGN_STATS)
        reasons = list(_ALIGN_STATS_REASONS)
    if not (stats["rewritten"] or stats["rejected"] or stats["fallback"]):
        return ""
    text = (f"📊 对齐统计：LLM 对齐 {stats['rows']} 行，其中轻改写 {stats['rewritten']} 行 / "
            f"校验拦下 {stats['rejected']} 次（带原因重试）/ 退回机械切分 {stats['fallback']} 行")
    if reasons:
        text += "\n   原因示例：" + "；".join(reasons[:3])
    return text


def align_subs(src_sub: str, tr_sub: str, src_part: str) -> Tuple[List[str], List[str], str]:
    align_prompt = get_align_prompt(src_sub, tr_sub, src_part)
    num_parts = len([part for part in str(src_part).split('\n') if part.strip()])
    validate = _align_validation_enabled()
    allow_rewrite = _align_rewrite_allowed()
    _bump_align_stat("rows")

    def valid_align(response_data):
        if 'align' not in response_data:
            return {"status": "error", "message": "Missing required key: `align`"}
        align = response_data['align']
        if len(align) < 2:
            return {"status": "error", "message": "Align does not contain more than 1 part as expected!"}
        if validate:
            # ① 段数必须与源文一致；② 护栏交给 subtitle_split.check_align_parts：
            #    严格模式要求拼接逐字等于整句译文；轻改写模式允许在切点顺句，但仍拦住
            #    重复（"同时同时"/原文已有的说法变多）、漏译、长度暴涨、孤立碎片。
            # 校验不过 → ask_gpt 会把这段 message 回注给模型重试（同一 prompt，命中缓存不重复付费）。
            parts = [str(item.get(f'target_part_{i+1}', '')) for i, item in enumerate(align)]
            if len(align) != num_parts:
                return {"status": "error",
                        "message": f"expected exactly {num_parts} parts, got {len(align)}"}
            ok, reason = subtitle_split.check_align_parts(
                parts, tr_sub, min_width=_MIN_PART_WIDTH, allow_rewrite=allow_rewrite)
            if not ok:
                _bump_align_stat("rejected", reason)
                return {"status": "error", "message": reason}
        return {"status": "success", "message": "Align completed"}

    parsed = ask_gpt(align_prompt, response_json=True, valid_def=valid_align, log_title='align_subs')
    
    align_data = parsed['align']
    src_parts = src_part.split('\n')
    tr_parts = [item[f'target_part_{i+1}'].strip() for i, item in enumerate(align_data)]
    
    language = get_source_language()
    joiner = get_joiner(language)
    tr_remerged = joiner.join(tr_parts)
    rewritten = not subtitle_split.normalized_concat_matches(tr_parts, tr_sub)
    if rewritten:
        _bump_align_stat("rewritten")
    
    table = Table(title="🔗 Aligned parts（含轻改写，已过重复/漏译校验）" if rewritten
                  else "🔗 Aligned parts")
    table.add_column("Language", style="cyan")
    table.add_column("Parts", style="magenta")
    table.add_row("SRC_LANG", "\n".join(src_parts))
    table.add_row("TARGET_LANG", "\n".join(tr_parts))
    table.add_row("REMERGED", tr_remerged)
    console.print(table)
    
    return src_parts, tr_parts, tr_remerged

def split_align_subs(src_lines: List[str], tr_lines: List[str],
                     limits: Optional[subtitle_limits.SubtitleLimits] = None
                     ) -> Tuple[List[str], List[str], List[str]]:
    # 长度限制统一由 core/subtitle_limits 解析：打开"按语言自动档位"时两侧各用自己语言的
    # 上限（显示宽度）；关闭时退回旧行为（单一 max_length，源文按字符数）。
    limits = limits or resolve_limits()
    remerged_tr_lines = tr_lines.copy()

    # 直通模式（transcription_only）下 Source 与 Translation 是同一份文本，
    # 此时 align_subs 等于"让 LLM 把一段文本与它自己对- 齐"，结果必然等于输入。
    # 直接按行数机械切分即可，省掉每条超长字幕的一次 LLM 调用（见 devdocs R16）。
    identical_src_trans = src_lines == tr_lines
    # 是否用 LLM 切分由 use_llm_sentence_split() 统一判定（翻译模式强制开启）
    use_llm = use_llm_sentence_split()

    to_split = []
    for i, (src, tr) in enumerate(zip(src_lines, tr_lines)):
        src, tr = str(src), str(tr)
        # 清理换行影响（异常数据兜底）
        if "\n" in src or "\n" in tr:
            src = src.replace("\n", " ")
            tr = tr.replace("\n", " ")
            src_lines[i], tr_lines[i] = src, tr
        if row_overflows(src, tr, limits):
            to_split.append(i)
            table = Table(title=f"📏 Line {i} needs to be split")
            table.add_column("Type", style="cyan")
            table.add_column("Content", style="magenta")
            table.add_row("Source Line", src)
            table.add_row("Target Line", tr)
            console.print(table)

    def process(i):
        # 每行的入口：暂停时在此阻塞、停止时抛 StopTask。
        # 没有这个钩子的话，"步骤 5 期间点停止"没有任何反应。
        eu.check_cancel()
        try:
            if not use_llm:
                # 纯本地切分：源文按**句法边界**切成 need 段（标点优先、无标点时退到
                # 日语接续/助词边界，窗口 ±boundary_window 内挑最接近理想宽度的那个），
                # 译文同步等分（保持行数一致以便对齐）。零 LLM 调用。
                need = subtitle_limits.parts_needed(
                    measure_for(src_lines[i], limits), calc_len(tr_lines[i]), limits)
                if need <= 1:
                    src_parts, tr_parts = [src_lines[i]], [tr_lines[i]]
                else:
                    src_parts = subtitle_split.split_at_boundaries(
                        src_lines[i], need, window=_boundary_window(),
                        language=get_source_language())
                    if len(src_parts) < 2:
                        src_parts = [src_lines[i]]
                    tr_parts = split_text_evenly(tr_lines[i], len(src_parts)) \
                        if len(src_parts) > 1 else [tr_lines[i]]
                    if tr_parts is None:
                        raise ValueError(
                            f"译文无法切成 {len(src_parts)} 段（内容太短），本行保持原样以免两侧错位")
                tr_remerged = tr_lines[i]
            elif identical_src_trans:
                # LLM 切源文，译文按行数机械等分（无需再问 LLM 对齐）
                split_src = split_sentence(src_lines[i], num_parts=2).strip()
                src_parts = [p for p in split_src.split('\n') if p.strip()]
                tr_parts = split_text_evenly(tr_lines[i], len(src_parts))
                if tr_parts is None:
                    raise ValueError(
                        f"文本无法切成 {len(src_parts)} 段（内容太短），本行保持原样")
                tr_remerged = tr_lines[i]
            else:
                split_src = split_sentence(src_lines[i], num_parts=2).strip()
                try:
                    src_parts, tr_parts, tr_remerged = align_subs(src_lines[i], tr_lines[i], split_src)
                except Exception as exc:  # noqa: BLE001 - 对齐不可用就降级，别让整行丢内容
                    # LLM 对齐连续校验失败/网络失败 → 退回**机械等分**：拼接一定等于原译文、
                    # 无重复、无漏词、无碎片；代价只是那一条的切点不如 LLM 精修漂亮。
                    src_parts = [p for p in split_src.split('\n') if p.strip()] or [src_lines[i]]
                    tr_parts = split_text_evenly(tr_lines[i], len(src_parts))
                    if tr_parts is None:
                        raise
                    tr_remerged = tr_lines[i]
                    _bump_align_stat("fallback", f"第 {i} 行 {type(exc).__name__}")
                    console.print(f"[yellow]⚠️ 第 {i} 行 LLM 对齐不可用（{type(exc).__name__}），"
                                  f"已退回机械等分切分[/yellow]")
        except Exception as e:
            # 单行切分失败不应该让整批静默失败：记录告警并保留原始行
            console.print(f"[yellow]⚠️ 第 {i} 行切分失败（保留原行）: {type(e).__name__}: {e}[/yellow]")
            return
        # 出口不变量：两侧段数必须相等。少了它，拍平后源/译列表长度不等会让**后面所有行**
        # 错位，并在写 xlsx 时抛 ValueError: arrays must all be same length。
        if len(src_parts) != len(tr_parts) or not src_parts:
            console.print(f"[yellow]⚠️ 第 {i} 行切分后两侧段数不一致"
                          f"（源 {len(src_parts)} / 译 {len(tr_parts)}），保留原行[/yellow]")
            return
        src_lines[i] = src_parts
        tr_lines[i] = tr_parts
        remerged_tr_lines[i] = tr_remerged

    with concurrent.futures.ThreadPoolExecutor(max_workers=load_key("max_workers")) as executor:
        # 必须消费 map 的返回值：executor.map 是惰性的，不迭代就不会取回结果，
        # 工作线程里的异常会被完全吞掉（见 devdocs 已知问题 P3-6）。
        list(executor.map(process, to_split))
    
    # Flatten `src_lines` and `tr_lines`
    src_lines = [item for sublist in src_lines for item in (sublist if isinstance(sublist, list) else [sublist])]
    tr_lines = [item for sublist in tr_lines for item in (sublist if isinstance(sublist, list) else [sublist])]
    
    return src_lines, tr_lines, remerged_tr_lines

def _boundary_window() -> float:
    """切点允许偏离理想位置的比例（`subtitle.boundary_window`，默认 0.15 = 宽容量）。

    与 `subtitle_limits.GRACE` 保持一致：如果窗口比宽容量还大，为了落在句法边界而切长的
    那段会在下一轮又被判超限、再被切一刀，反而更碎。
    """
    try:
        value = float(load_key("subtitle.boundary_window"))
    except Exception:
        return subtitle_limits.GRACE
    return max(0.0, min(0.4, value))


def merge_short_cues_in_place(sources, translations, timestamps, limits):
    """把极短字幕并入相邻字幕（见 core/subtitle_split.merge_short_cues）。

    开关：`subtitle.merge_short_cues`（默认 true）；阈值 `subtitle.short_cue_min_duration`
    （默认 0.8 s —— Netflix 规定最短 5/6 s ≈ 0.83 s）、`subtitle.merge_max_gap`（默认 0.35 s）。
    """
    try:
        if not load_key("subtitle.merge_short_cues"):
            return list(sources), list(translations)
        min_duration = float(load_key("subtitle.short_cue_min_duration"))
        max_gap = float(load_key("subtitle.merge_max_gap"))
    except Exception:
        min_duration, max_gap = 0.8, 0.35
    rows = subtitle_split.rows_from_pairs(sources, translations, timestamps)
    try:
        joiner = get_joiner(get_source_language())
    except Exception:
        joiner = ""
    merged = subtitle_split.merge_short_cues(rows, limits, min_duration=min_duration,
                                             max_gap=max_gap, joiner=joiner)
    before, after = len(rows), len(merged)
    if after < before:
        console.print(f"[cyan]🧩 合并极短字幕：{before} → {after} 条"
                      f"（时长 < {min_duration}s 且间隔 ≤ {max_gap}s）[/cyan]")
    return [row["source"] for row in merged], [row["translation"] for row in merged]


def split_for_sub_main():
    console.print("[bold green]🚀 Start splitting subtitles...[/bold green]")

    df = pd.read_excel(INPUT_FILE)
    src = df['Source'].tolist()
    trans = df['Translation'].tolist()

    limits = resolve_limits()
    console.print(f"[cyan]📐 字幕长度档位：[/cyan]{limits.label}")
    reset_align_stats()

    MAX_SPLIT_ATTEMPTS = 3
    for attempt in range(MAX_SPLIT_ATTEMPTS):  # 固定的 3 轮：每轮只把超长行再切一次
        console.print(Panel(f"🔄 Split attempt {attempt + 1}/{MAX_SPLIT_ATTEMPTS}", expand=False))
        split_src, split_trans, remerged = split_align_subs(src.copy(), trans, limits)
        
        # 检查是否所有字幕都符合长度要求（源/译两侧各按自己语言的上限）
        if all(not row_overflows(s, t, limits) for s, t in zip(split_src, split_trans)):
            break
        
        # 更新源数据继续下一轮分割
        src = split_src
        trans = split_trans

    if len(split_src) != len(split_trans):
        # 正常情况下这条永远不该触发（split_align_subs 出口已有不变量）；留着是为了
        # 把 pandas 那句 arrays must all be same length 换成能看懂的话。
        raise ValueError(
            f"源文/译文行数不一致（{len(split_src)} vs {len(split_trans)}），"
            f"拒绝写出，以免字幕整体错位")

    # 极短字幕并入相邻条（"哦/是的/原来如此"一闪而过，看不清）
    stamps = df['timestamp'].tolist() if 'timestamp' in df.columns else None
    split_src, split_trans = merge_short_cues_in_place(split_src, split_trans, stamps, limits)

    # 对齐质量自检：拦下/回退的真实数量，用来判断"允许轻改写"值不值（无异常则不打印）
    summary = align_stats_summary()
    if summary:
        console.print(f"[cyan]{summary}[/cyan]")

    pd.DataFrame({'Source': split_src, 'Translation': split_trans}).to_excel(OUTPUT_SPLIT_FILE, index=False)
    # pd.DataFrame({'Source': src, 'Translation': remerged}).to_excel(OUTPUT_REMERGED_FILE, index=False)

if __name__ == '__main__':
    # Windows 控制台默认是 GBK，本模块会打印 emoji 与中文，直接运行会抛
    # UnicodeEncodeError（实测 `python -m core.step2_whisperX` 会崩）。
    # rich 的终端编码是首次打印时才决定的，因此在入口处补一次即可修复。
    try:
        import easy_util as _eu
        _eu.ensure_utf8_console()
    except Exception:
        pass
    split_for_sub_main()
