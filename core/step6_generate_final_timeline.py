import pandas as pd
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import time
from difflib import SequenceMatcher
import easy_util as eu
from core.config_utils import load_key, get_joiner
from rich.panel import Panel
from rich.console import Console
import autocorrect_py as autocorrect
from plyer import notification

console = Console()

CLEANED_CHUNKS_FILE = 'output/log/cleaned_chunks.xlsx'
TRANSLATION_RESULTS_FOR_SUBTITLES_FILE = 'output/log/translation_results_for_subtitles.xlsx'

OUTPUT_DIR = 'output'

SUBTITLE_OUTPUT_CONFIGS = [
    ('src.srt', ['Source']),
    ('trans.srt', ['Translation']),
    ('src_trans.srt', ['Source', 'Translation']),
    ('trans_src.srt', ['Translation', 'Source'])
]

def convert_to_srt_format(start_time, end_time):
    """Convert time (in seconds) to the format: hours:minutes:seconds,milliseconds"""
    def seconds_to_hmsm(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = seconds % 60
        milliseconds = int(seconds * 1000) % 1000
        return f"{hours:02d}:{minutes:02d}:{int(seconds):02d},{milliseconds:03d}"

    start_srt = seconds_to_hmsm(start_time)
    end_srt = seconds_to_hmsm(end_time)
    return f"{start_srt} --> {end_srt}"

def remove_punctuation(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()

def show_difference(str1, str2):
    """Show the difference positions between two strings"""
    min_len = min(len(str1), len(str2))
    diff_positions = []

    for i in range(min_len):
        if str1[i] != str2[i]:
            diff_positions.append(i)

    if len(str1) != len(str2):
        diff_positions.extend(range(min_len, max(len(str1), len(str2))))

    print("Difference positions:")
    print(f"Expected sentence: {str1}")
    print(f"Actual match: {str2}")
    print("Position markers: " + "".join("^" if i in diff_positions else " " for i in range(max(len(str1), len(str2)))))
    print(f"Difference indices: {diff_positions}")

def _fuzzy_find(full_words_str, clean_sentence, current_pos):
    """在 full_words_str 中模糊定位句子，返回 (start_pos, end_pos, ratio) 或 None。

    精确匹配失败通常有两类原因：
      A. 少数字符不同（LLM 改写、标点差异）→ 找最长公共块，按块的位置推断边界
      B. 词串里多了/少了整段内容 → 公共块会落在句子中部，此时用**句子前缀**锚定起点再
         按长度切一段

    因此这里先尝试「前缀锚定」，失败再退回「最长公共块」。
    """
    if not clean_sentence:
        return None

    window = full_words_str[current_pos:]
    if not window:
        return None

    def build(start_in_window, ratio):
        start_pos = current_pos + start_in_window
        end_pos = min(len(full_words_str), start_pos + len(clean_sentence))
        return start_pos, end_pos, ratio

    # 策略一：用句子前缀在词串中定位（对"词串里多了内容"最有效）
    for anchor_len in (24, 16, 8, 4):
        if anchor_len > len(clean_sentence):
            continue
        anchor = clean_sentence[:anchor_len]
        hit = full_words_str.find(anchor, current_pos)
        if hit != -1:
            start_pos, end_pos, _ = build(hit - current_pos, 1.0)
            # 用真实相似度作为可信度指标
            candidate = full_words_str[start_pos:end_pos]
            ratio = SequenceMatcher(None, candidate, clean_sentence, autojunk=False).ratio()
            return start_pos, end_pos, ratio

    # 策略二：最长公共块，按块在句子中的位置按比例外扩
    matcher = SequenceMatcher(None, window, clean_sentence, autojunk=False)
    block = matcher.find_longest_match(0, len(window), 0, len(clean_sentence))
    if block.size == 0:
        return None
    start_in_window = max(0, block.a - block.b)
    return build(start_in_window, matcher.ratio())


def get_sentence_timestamps(df_words, df_sentences, on_mismatch='warn'):
    """把句子级文本映射回词级时间戳。

    Args:
        on_mismatch:
            'warn'  —— 精确匹配失败时使用模糊兜底并记录告警（默认）。
                       好处：不会因一个字符差异中断整条流水线；
                       代价：模糊定位后 current_pos 可能前移过多，导致**后续句子连锁失配**，
                       此时函数会打印明确告警，请人工抽查 output/trans.srt。
            'strict' —— 精确匹配失败即抛异常（历史行为），适合批处理等"宁可不产出也不要错"的场景。
    """
    time_stamp_list = []
    fuzzy_fallbacks = []

    # Build complete string and position mapping
    full_words_str = ''
    position_to_word_idx = {}

    for idx, word in enumerate(df_words['text']):
        clean_word = remove_punctuation(word.lower())
        start_pos = len(full_words_str)
        full_words_str += clean_word
        for pos in range(start_pos, len(full_words_str)):
            position_to_word_idx[pos] = idx

    def word_idx_at(pos):
        """取得覆盖 pos 的词下标（越界时退到最近的已知位置）。"""
        if pos in position_to_word_idx:
            return position_to_word_idx[pos]
        if not position_to_word_idx:
            return 0
        return position_to_word_idx[min(position_to_word_idx, key=lambda p: abs(p - pos))]

    current_pos = 0
    for idx, sentence in df_sentences['Source'].items():
        clean_sentence = remove_punctuation(sentence.lower()).replace(" ", "")
        sentence_len = len(clean_sentence)

        match_found = False
        while sentence_len and current_pos <= len(full_words_str) - sentence_len:
            if full_words_str[current_pos:current_pos+sentence_len] == clean_sentence:
                start_word_idx = position_to_word_idx[current_pos]
                end_word_idx = position_to_word_idx[current_pos + sentence_len - 1]

                time_stamp_list.append((
                    float(df_words['start'][start_word_idx]),
                    float(df_words['end'][end_word_idx])
                ))

                current_pos += sentence_len
                match_found = True
                break
            current_pos += 1

        if match_found:
            continue

        if on_mismatch == 'strict':
            print(f"\n⚠️ 未找到与句子完全匹配的结果: {sentence}")
            show_difference(clean_sentence, full_words_str[current_pos:current_pos+len(clean_sentence)])
            print("\n原句:", df_sentences['Source'][idx])
            raise ValueError("❎ 未找到与句子匹配的内容。")

        # 兜底：模糊匹配，保证流程不中断（见 devdocs 已知问题 P1-3）
        fuzzy = _fuzzy_find(full_words_str, clean_sentence, current_pos)
        if fuzzy is None:
            # 句子清洗后为空（例如纯标点），沿用上一个词的结束时间
            if time_stamp_list:
                last_end = time_stamp_list[-1][1]
            else:
                last_end = float(df_words['start'][0]) if len(df_words) else 0.0
            time_stamp_list.append((last_end, last_end))
            fuzzy_fallbacks.append((idx, sentence, 0.0, "空句子"))
            continue

        start_pos, end_pos, ratio = fuzzy
        start_word_idx = word_idx_at(start_pos)
        end_word_idx = word_idx_at(max(start_pos, end_pos - 1))
        time_stamp_list.append((
            float(df_words['start'][start_word_idx]),
            float(df_words['end'][end_word_idx])
        ))
        current_pos = max(current_pos, end_pos)
        fuzzy_fallbacks.append((idx, sentence, ratio, "模糊匹配"))

    if fuzzy_fallbacks:
        print(f"\n⚠️ 共 {len(fuzzy_fallbacks)}/{len(df_sentences)} 条字幕未精确匹配，已使用兜底对齐。")
        print("   注意：兜底前进的词位可能偏移，可能造成后续句子连锁失配——"
              "建议人工抽查 output/trans.srt，或将 config.yaml 的 subtitle.align_on_mismatch 设为 strict。")
        for idx, sentence, ratio, kind in fuzzy_fallbacks[:10]:
            print(f"   - [#{idx}] ({kind}, 相似度 {ratio:.3f}) {sentence[:40]}...")
        if len(fuzzy_fallbacks) > 10:
            print(f"   ... 其余 {len(fuzzy_fallbacks) - 10} 条略")

    return time_stamp_list

def align_timestamp(df_text, df_translate, subtitle_output_configs: list, output_dir: str, for_display: bool = True):
    """Align timestamps and add a new timestamp column to df_translate"""
    df_trans_time = df_translate.copy()

    # Process timestamps ⏰
    on_mismatch = 'warn'
    try:
        on_mismatch = load_key("subtitle.align_on_mismatch")
    except KeyError:
        pass
    time_stamp_list = get_sentence_timestamps(df_text, df_translate, on_mismatch=on_mismatch)
    df_trans_time['timestamp'] = time_stamp_list
    df_trans_time['duration'] = df_trans_time['timestamp'].apply(lambda x: x[1] - x[0])

    # Remove gaps 🕳️
    for i in range(len(df_trans_time)-1):
        delta_time = df_trans_time.loc[i+1, 'timestamp'][0] - df_trans_time.loc[i, 'timestamp'][1]
        if 0 < delta_time < 1:
            df_trans_time.at[i, 'timestamp'] = (df_trans_time.loc[i, 'timestamp'][0], df_trans_time.loc[i+1, 'timestamp'][0])

    # Convert start and end timestamps to SRT format
    df_trans_time['timestamp'] = df_trans_time['timestamp'].apply(lambda x: convert_to_srt_format(x[0], x[1]))

    # Polish subtitles: replace punctuation in Translation if for_display
    if for_display:
        df_trans_time['Translation'] = df_trans_time['Translation'].apply(lambda x: re.sub(r'[，。]', ' ', x).strip())

    # Output subtitles 📜
    def generate_subtitle_string(df, columns):
        """生成标准 SRT 文本。

        单列（src.srt / trans.srt）时**不写空的第二行**：此前无条件写成
        `f"...\\n{line1}\\n{line2}\\n\\n"`，line2 为空就会产生 `\\n\\n\\n`，
        即两个换行 + 一个空行，导致「每个 block 多一个空行」，
        用 split('\\n\\n') 的解析器会得到首行为空的块（见 devdocs 已知问题 P3-9）。
        """
        subtitle_lines = []
        for i, row in df.iterrows():
            # 规范化空格：将多个空格替换为单个空格
            line1 = re.sub(r'\s+', ' ', str(row[columns[0]]).strip())
            title_and_time = f"{i+1}\n{row['timestamp']}\n{line1}"
            if len(columns) > 1:
                line2 = re.sub(r'\s+', ' ', str(row[columns[1]]).strip())
                subtitle_lines.append(f"{title_and_time}\n{line2}\n\n")
            else:
                subtitle_lines.append(f"{title_and_time}\n\n")
        return ''.join(subtitle_lines).strip()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for filename, columns in subtitle_output_configs:
            subtitle_str = generate_subtitle_string(df_trans_time, columns)
            with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
                f.write(subtitle_str)
    
    return df_trans_time

# ✨ Beautify the translation
def clean_translation(x):
    if pd.isna(x):
        return ''
    cleaned = str(x).strip('。').strip('，')
    return autocorrect.format(cleaned)

def align_timestamp_main():
    df_text = pd.read_excel(CLEANED_CHUNKS_FILE)
    df_text['text'] = df_text['text'].str.strip('"').str.strip()
    df_translate = pd.read_excel(TRANSLATION_RESULTS_FOR_SUBTITLES_FILE)
    df_translate['Translation'] = df_translate['Translation'].apply(clean_translation)

    align_timestamp(df_text, df_translate, SUBTITLE_OUTPUT_CONFIGS, OUTPUT_DIR)
    console.print(Panel("[bold green]🎉📝 Subtitles generation completed! Please check in the `output` folder 👀[/bold green]"))

    record_summary_info()
    console.print(Panel("[bold green]处理完成，耗时：{}\n消耗prompt tokens: {}\n消耗completion tokens: {}\n共消耗tokens: {}\n预计花费: {}[/bold green]"
                        .format(eu.convert_seconds(eu.time_duration), eu.prompt_tokens, eu.completion_tokens, 
                                eu.get_total_tokens(), eu.get_formated_estimated_cost())))
    eu.record_messages()
    send_tanslation_complete_notification()

def record_summary_info():
        eu.end_time = time.time()
        # eu.start_time 由入口（st.py / batch 的 record_start_time）设置。
        # 单步运行本模块时它是 0，直接相减会得到"从 1970 年至今"的巨大耗时，
        # 因此这里做一次兜底：未设置起始时间就只把本轮当作 0 秒，
        # 避免日志与批次统计出现 49 万小时这种荒谬数字。
        if not eu.start_time:
            eu.time_duration = 0
        else:
            eu.time_duration = eu.end_time - eu.start_time
        eu.total_time_duration += eu.time_duration
        eu.estimated_total_cost += eu.get_estimated_cost()

def read_time_duration():
    return eu.convert_seconds(eu.time_duration)

def send_tanslation_complete_notification():
    try:
        send_notification("字幕翻译完成", f"耗时：{read_time_duration()}")
    except Exception as e:
        # plyer 在无桌面/无通知后端的环境（Docker、无 GUI 的 Linux server）会抛异常。
        # 此时 SRT 已经写好，不应该因为一条通知打断整个流程（见 devdocs 已知问题 R9）。
        console.print(f"[yellow]⚠️ 桌面通知发送失败（不影响结果）: {e}[/yellow]")

def send_notification(title, message):
    notification.notify(
        title=title,
        message=message,
        timeout=6
    )

if __name__ == '__main__':
    # Windows 控制台默认是 GBK，本模块会打印 emoji 与中文，直接运行会抛
    # UnicodeEncodeError（实测 `python -m core.step2_whisperX` 会崩）。
    # rich 的终端编码是首次打印时才决定的，因此在入口处补一次即可修复。
    try:
        import easy_util as _eu
        _eu.ensure_utf8_console()
    except Exception:
        pass
    align_timestamp_main()