import sys, os
import pandas as pd
from typing import List, Tuple
import concurrent.futures
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.step3_2_splitbymeaning import split_sentence, split_by_punctuation
from core.ask_gpt import ask_gpt
from core.prompts_storage import get_align_prompt
from core.config_utils import load_key, get_joiner, get_source_language, use_llm_sentence_split
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

console = Console()


def split_text_evenly(text: str, n: int) -> List[str]:
    """把文本按字符数近似等分成 n 段（用于源/译文本相同的场景，避免调 LLM 对齐）。

    对齐仍由 step6 负责：它用 Source 拼串做精确匹配取时间戳，
    因此这里只要求"拼接回去等于原文"，等分即可满足。
    """
    text = str(text)
    if n <= 1 or len(text) < n:
        return [text]
    size = len(text) / n
    parts = []
    for i in range(n):
        start = int(round(i * size))
        end = int(round((i + 1) * size)) if i < n - 1 else len(text)
        seg = text[start:end].strip()
        if seg:
            parts.append(seg)
    return parts or [text]

# Constants
INPUT_FILE = "output/log/translation_results.xlsx"
OUTPUT_SPLIT_FILE = "output/log/translation_results_for_subtitles.xlsx"
OUTPUT_REMERGED_FILE = "output/log/translation_results_remerged.xlsx"

# ! You can modify your own weights here
# Chinese and Japanese 2.5 characters, Korean 2 characters, Thai 1.5 characters, full-width symbols 2 characters, other English-based and half-width symbols 1 character
def calc_len(text: str) -> float:
    text = str(text) # force convert
    def char_weight(char):
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF:  # Chinese and Japanese
            return 1.75
        elif 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:  # Korean
            return 1.5
        elif 0x0E00 <= code <= 0x0E7F:  # Thai
            return 1
        elif 0xFF01 <= code <= 0xFF5E:  # full-width symbols
            return 1.75
        else:  # other characters (e.g. English and half-width symbols)
            return 1

    return sum(char_weight(char) for char in text)

def align_subs(src_sub: str, tr_sub: str, src_part: str) -> Tuple[List[str], List[str], str]:
    align_prompt = get_align_prompt(src_sub, tr_sub, src_part)
    
    def valid_align(response_data):
        if 'align' not in response_data:
            return {"status": "error", "message": "Missing required key: `align`"}
        if len(response_data['align']) < 2:
            return {"status": "error", "message": "Align does not contain more than 1 part as expected!"}
        return {"status": "success", "message": "Align completed"}

    parsed = ask_gpt(align_prompt, response_json=True, valid_def=valid_align, log_title='align_subs')
    
    align_data = parsed['align']
    src_parts = src_part.split('\n')
    tr_parts = [item[f'target_part_{i+1}'].strip() for i, item in enumerate(align_data)]
    
    language = get_source_language()
    joiner = get_joiner(language)
    tr_remerged = joiner.join(tr_parts)
    
    table = Table(title="🔗 Aligned parts")
    table.add_column("Language", style="cyan")
    table.add_column("Parts", style="magenta")
    table.add_row("SRC_LANG", "\n".join(src_parts))
    table.add_row("TARGET_LANG", "\n".join(tr_parts))
    table.add_row("REMERGED", tr_remerged)
    console.print(table)
    
    return src_parts, tr_parts, tr_remerged

def split_align_subs(src_lines: List[str], tr_lines: List[str]) -> Tuple[List[str], List[str], List[str]]:
    subtitle_set = load_key("subtitle")
    MAX_SUB_LENGTH = subtitle_set["max_length"]
    TARGET_SUB_MULTIPLIER = subtitle_set["target_multiplier"]
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
        if len(src) > MAX_SUB_LENGTH or calc_len(tr) * TARGET_SUB_MULTIPLIER > MAX_SUB_LENGTH:
            to_split.append(i)
            table = Table(title=f"📏 Line {i} needs to be split")
            table.add_column("Type", style="cyan")
            table.add_column("Content", style="magenta")
            table.add_row("Source Line", src)
            table.add_row("Target Line", tr)
            console.print(table)

    def process(i):
        try:
            if not use_llm:
                # 纯本地切分：源文按标点就近断开，译文同步等分（保持行数一致以便对齐）
                src_parts = split_by_punctuation(src_lines[i], MAX_SUB_LENGTH)
                n = len(src_parts)
                tr_remerged = tr_lines[i]
                if n > 1:
                    tr_parts = split_text_evenly(tr_lines[i], n)
                else:
                    tr_parts = [tr_lines[i]]
            elif identical_src_trans:
                # LLM 切源文，译文按行数机械等分（无需再问 LLM 对齐）
                split_src = split_sentence(src_lines[i], num_parts=2).strip()
                src_parts = [p for p in split_src.split('\n') if p.strip()]
                tr_parts = split_text_evenly(tr_lines[i], len(src_parts))
                tr_remerged = tr_lines[i]
            else:
                split_src = split_sentence(src_lines[i], num_parts=2).strip()
                src_parts, tr_parts, tr_remerged = align_subs(src_lines[i], tr_lines[i], split_src)
        except Exception as e:
            # 单行切分失败不应该让整批静默失败：记录告警并保留原始行
            console.print(f"[yellow]⚠️ 第 {i} 行切分失败（保留原行）: {type(e).__name__}: {e}[/yellow]")
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

def split_for_sub_main():
    console.print("[bold green]🚀 Start splitting subtitles...[/bold green]")

    df = pd.read_excel(INPUT_FILE)
    src = df['Source'].tolist()
    trans = df['Translation'].tolist()
    
    subtitle_set = load_key("subtitle")
    MAX_SUB_LENGTH = subtitle_set["max_length"]
    TARGET_SUB_MULTIPLIER = subtitle_set["target_multiplier"]
    
    MAX_SPLIT_ATTEMPTS = 3
    for attempt in range(MAX_SPLIT_ATTEMPTS):  # 固定的 3 轮：每轮只把超长行再切一次
        console.print(Panel(f"🔄 Split attempt {attempt + 1}/{MAX_SPLIT_ATTEMPTS}", expand=False))
        split_src, split_trans, remerged = split_align_subs(src.copy(), trans)
        
        # 检查是否所有字幕都符合长度要求
        if all(len(src) <= MAX_SUB_LENGTH for src in split_src) and \
           all(calc_len(tr) * TARGET_SUB_MULTIPLIER <= MAX_SUB_LENGTH for tr in split_trans):
            break
        
        # 更新源数据继续下一轮分割
        src = split_src
        trans = split_trans

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
