import sys,os,math,re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import concurrent.futures
from core.ask_gpt import ask_gpt
from core.prompts_storage import get_split_prompt
from difflib import SequenceMatcher
import math
from core.spacy_utils.load_nlp_model import init_nlp
from core.config_utils import load_key, get_joiner, get_source_language, use_llm_sentence_split
from rich.console import Console
from rich.table import Table

console = Console()

def tokenize_sentence(sentence, nlp):
    # tokenizer counts the number of words in the sentence
    doc = nlp(sentence)
    return [token.text for token in doc]

def find_split_positions(original, modified):
    split_positions = []
    parts = modified.split('[br]')
    start = 0
    language = get_source_language()
    joiner = get_joiner(language)

    for i in range(len(parts) - 1):
        max_similarity = 0
        best_split = None

        for j in range(start, len(original)):
            original_left = original[start:j]
            modified_left = joiner.join(parts[i].split())

            left_similarity = SequenceMatcher(None, original_left, modified_left).ratio()

            if left_similarity > max_similarity:
                max_similarity = left_similarity
                best_split = j

        if max_similarity < 0.9:
            console.print(f"[yellow]Warning: low similarity found at the best split point: {max_similarity}[/yellow]")
        if best_split is not None:
            split_positions.append(best_split)
            start = best_split
        else:
            console.print(f"[yellow]Warning: Unable to find a suitable split point for the {i+1}th part.[/yellow]")

    return split_positions

def split_sentence(sentence, num_parts, word_limit=18, index=-1, retry_attempt=0):
    """Split a long sentence using GPT and return the result as a string。

    retry_attempt > 0 表示这是同一句的第 N 轮重试：用 bypass_cache 真正重新请求，
    而不是靠 `prompt + ' ' * retry_attempt` 改变 prompt 字符串去绕过缓存（见 devdocs R14）。
    """
    split_prompt = get_split_prompt(sentence, num_parts, word_limit)

    def valid_split(response_data):
        # 提示词要求模型给出两个候选并自行选定：choice ∈ {1, 2}，选中项在 split{choice}。
        # 与提示词必须成对修改 —— 只改一边会让每个长句都校验失败并重试 3 次。
        choice = str(response_data.get("choice", "")).strip()
        if choice not in ("1", "2"):
            return {"status": "error", "message": "Missing or invalid `choice` (expected 1 or 2)"}
        if f"split{choice}" not in response_data:
            return {"status": "error", "message": f"Missing required key: `split{choice}`"}
        if "[br]" not in str(response_data[f"split{choice}"]):
            return {"status": "error", "message": f"Split failed, no [br] found in `split{choice}`"}
        return {"status": "success", "message": "Split completed"}

    response_data = ask_gpt(split_prompt, response_json=True, valid_def=valid_split,
                            log_title='sentence_splitbymeaning',
                            bypass_cache=retry_attempt > 0)
    # 归一化后再取键：choice 可能返回数字 1 或字符串 "1"
    best_split = response_data[f"split{str(response_data.get('choice', '')).strip()}"]
    split_points = find_split_positions(sentence, best_split)
    # split the sentence based on the split points
    for i, split_point in enumerate(split_points):
        if i == 0:
            best_split = sentence[:split_point] + '\n' + sentence[split_point:]
        else:
            parts = best_split.split('\n')
            last_part = parts[-1]
            parts[-1] = last_part[:split_point - split_points[i-1]] + '\n' + last_part[split_point - split_points[i-1]:]
            best_split = '\n'.join(parts)
    if index != -1:
        console.print(f'[green]✅ Sentence {index} has been successfully split[/green]')
    table = Table(title="")
    table.add_column("Type", style="cyan")
    table.add_column("Sentence")
    table.add_row("Original", sentence, style="yellow")
    table.add_row("Split", best_split.replace('\n', ' ||'), style="yellow")
    console.print(table)
    
    return best_split

def parallel_split_sentences(sentences, max_length, max_workers, nlp, retry_attempt=0):
    """Split sentences in parallel using a thread pool."""
    new_sentences = [None] * len(sentences)
    futures = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, sentence in enumerate(sentences):
            # Use tokenizer to split the sentence
            tokens = tokenize_sentence(sentence, nlp)
            # print("Tokenization result:", tokens)
            num_parts = math.ceil(len(tokens) / max_length)
            if len(tokens) > max_length:
                future = executor.submit(split_sentence, sentence, num_parts, max_length, index=index, retry_attempt=retry_attempt)
                futures.append((future, index, num_parts, sentence))
            else:
                new_sentences[index] = [sentence]

        for future, index, num_parts, sentence in futures:
            split_result = future.result()
            if split_result:
                split_lines = split_result.strip().split('\n')
                new_sentences[index] = [line.strip() for line in split_lines]
            else:
                new_sentences[index] = [sentence]

    return [sentence for sublist in new_sentences for sentence in sublist]

def split_by_punctuation(text, max_length, joiner=""):
    """不调用 LLM 的兜底切分：在标点/连接处就近把长句切成若干段。

    仅在 `llm_sentence_split = false` 时使用。策略：
      1. 按句末/句中标点切成候选片段
      2. 贪心合并到接近 max_length（按字符数近似，调用方若需要精确长度可自行再校验）
      3. 单个片段本身就超长时，硬按 max_length 切
    """
    if not text or len(text) <= max_length:
        return [text] if text else []

    # 标点后保留分隔符（lookbehind 分词）
    parts = [p for p in re.split(r'(?<=[。！？!?；;，,、：:\.])\s*', text) if p]
    if not parts:
        parts = [text]

    chunks, cur = [], ''
    for p in parts:
        # 单段超长：先把它硬切
        while len(p) > max_length:
            room = max_length - len(cur)
            if room > 0:
                chunks.append((cur + p[:room]).strip())
                p = p[room:]
            else:
                if cur.strip():
                    chunks.append(cur.strip())
                cur = ''
                room = max_length
                chunks.append(p[:room].strip())
                p = p[room:]
            cur = ''
        if len(cur) + len(p) <= max_length:
            cur += p
        else:
            if cur.strip():
                chunks.append(cur.strip())
            cur = p
    if cur.strip():
        chunks.append(cur.strip())

    # 兜底：清理空段，并保证至少返回一段
    chunks = [c for c in chunks if c]
    return chunks or [text]


def split_sentences_mechanically(sentences, max_length):
    """对超长句做纯本地切分（不调 LLM）。"""
    result = []
    for s in sentences:
        if len(s) > max_length:
            result.extend(split_by_punctuation(s, max_length))
        else:
            result.append(s)
    return result


def split_sentences_by_meaning():
    """The main function to split sentences by meaning.

    是否调 LLM 由 `core.config_utils.use_llm_sentence_split()` 统一判定：
      - 正常翻译模式：强制使用 LLM（保证按意群断句）
      - 仅转录模式  ：受 `llm_sentence_split` 控制，可关闭以省 token
    """
    # read input sentences
    with open('output/log/sentence_splitbynlp.txt', 'r', encoding='utf-8') as f:
        sentences = [line.strip() for line in f.readlines() if line.strip()]

    if not use_llm_sentence_split():
        console.print('[yellow]⏭️ 已关闭 LLM 断句优化：直接使用 spaCy 切分结果（零 LLM 调用）[/yellow]')
        with open('output/log/sentence_splitbymeaning.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(sentences))
        console.print(f'[green]✅ 已写出 {len(sentences)} 句（未经 LLM 优化）[/green]')
        return

    nlp = init_nlp()
    # 🔄 process sentences multiple times to ensure all are split
    for retry_attempt in range(3):
        sentences = parallel_split_sentences(sentences, max_length=load_key("max_split_length"), max_workers=load_key("max_workers"), nlp=nlp, retry_attempt=retry_attempt)

    # 💾 save results
    with open('output/log/sentence_splitbymeaning.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(sentences))
    console.print('[green]✅ All sentences have been successfully split![/green]')

if __name__ == '__main__':
    # Windows 控制台默认是 GBK，本模块会打印 emoji 与中文，直接运行会抛
    # UnicodeEncodeError（实测 `python -m core.step2_whisperX` 会崩）。
    # rich 的终端编码是首次打印时才决定的，因此在入口处补一次即可修复。
    try:
        import easy_util as _eu
        _eu.ensure_utf8_console()
    except Exception:
        pass
    # print(split_sentence('Which makes no sense to the... average guy who always pushes the character creation slider all the way to the right.', 2, 22))
    split_sentences_by_meaning()