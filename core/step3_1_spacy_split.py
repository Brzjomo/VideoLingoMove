import sys
import os
# 与其它 step 保持一致：把项目根加入 sys.path，使 `core.*` 可被导入
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.spacy_utils.split_by_comma import split_by_comma_main
from core.spacy_utils.split_by_connector import split_sentences_main
from core.spacy_utils.split_by_mark import split_by_mark
from core.spacy_utils.split_long_by_root import split_long_by_root_main
from core.spacy_utils.load_nlp_model import init_nlp

SPLIT_FILE = 'output/log/sentence_splitbynlp.txt'


def merge_broken_cuts_in_file(nlp):
    """出口守卫：把"被切在词中"的行并回去（见 `core/subtitle_split.merge_broken_cuts`）。

    这一层（step3_1）是**纯规则切分、没有任何提示词**，所以提示词规则管不到它 ——
    2026-09-20 实测事故就是它把 `…フィギュアとし | てその…` 从「として」中间劈开，
    下游 step4 只能把两个半句各自翻译完整，于是出现
    "…同时注重这一点" / "同时也注重…" 的重复。这里用 spaCy 的 token 边界判定：
    拼接点必须落在 token 起点上，否则合并回去。
    """
    from core import subtitle_split
    from core.config_utils import load_key_or

    if not os.path.exists(SPLIT_FILE):
        return
    try:
        if not load_key_or('subtitle.merge_broken_lines', True):
            return
    except Exception:
        pass
    with open(SPLIT_FILE, 'r', encoding='utf-8') as handle:
        lines = [line.rstrip('\n') for line in handle]
    before = len([line for line in lines if line.strip()])
    merged = subtitle_split.merge_broken_cuts(
        lines, lambda text: subtitle_split.spaCy_boundaries(nlp, text))
    if len(merged) != before:
        print(f"[cyan]🧩 合并被切在词中的行：{before} → {len(merged)}[/cyan]")
        with open(SPLIT_FILE, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(merged) + '\n')


def split_by_spacy():
    if os.path.exists(SPLIT_FILE):
        print("File 'sentence_splitbynlp.txt' already exists. Skipping split_by_spacy.")
        return
    
    nlp = init_nlp()
    split_by_mark(nlp)
    split_by_comma_main(nlp)
    split_sentences_main(nlp)
    split_long_by_root_main(nlp)
    merge_broken_cuts_in_file(nlp)      # ← 出口守卫：不许把词切开
    return

if __name__ == '__main__':
    split_by_spacy()