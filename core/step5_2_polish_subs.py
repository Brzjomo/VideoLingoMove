"""step5.2：字幕润色（译后顺句）。开关 `subtitle.polish_translation`，**默认关**。

为什么要单独一步（2026-09-21 用户要求"要的，但你要在侧边栏合适的位置加个开关，打开后才做"）：

* step4 的意译是**按整句**做的，step5 又把它切成行，于是"没错但不够顺"的措辞会留在成片里
  （用户实例：`作为自由原型师约6年，一直从事手办造型工作` 被切在两条 cue 上，读起来是断的）；
* 对齐阶段（step5）只被允许"移动边界/虚词"，**不许改写措辞** —— 这是用户在同一天亲自纠正过的
  设计约束（"翻译字幕往往几句连起来看才是完整的句子"，要求每行自足会诱发跨行重复与凭空增补），
  所以"改措辞"只能发生在专门的一步里，且必须是用户显式打开的可选步骤。

流程（每批 `BATCH_SIZE` 行一次调用）：

    读 translation_results_for_subtitles.xlsx
      → 分批问 LLM 润色（逐行对应，只改措辞）
      → 逐行机械护栏 subtitle_split.polish_ok（长度/覆盖率/数字/重复）
      → 对"确实改动过"的行做一次批量审校（是否增删/篡改信息）
      → 审校不过的行回退原译文
      → 写 output/log/translation_results_polished.xlsx

step6 只有在**开关打开且行数与 step5 产物一致**时才优先读润色版（见
`step6_generate_final_timeline.pick_translation_file`），所以关掉开关即恢复未润色字幕，
不需要重跑 step5，也不需要重跑 LLM。

容错：任何一批失败（网络/JSON/校验）都只影响那一批 —— 那批保持原译文并计数，绝不丢内容。
"""

import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ask_gpt import ask_gpt
from core.config_utils import load_key, load_key_or
from core import config_utils
from core import subtitle_limits
from core import subtitle_split
from core.prompts_storage import get_polish_prompt, get_polish_audit_prompt
from core.subtitle_limits import resolve_limits
import easy_util as eu
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

INPUT_FILE = 'output/log/translation_results_for_subtitles.xlsx'
OUTPUT_POLISHED_FILE = 'output/log/translation_results_polished.xlsx'

#: 每批多少行（一次调用）。20 行 ≈ 一次请求塞得下、模型也不容易在长列表里错位。
BATCH_SIZE = 20


def polish_enabled() -> bool:
    """是否启用润色（`subtitle.polish_translation`，默认关；见 config_utils.polish_translation）。"""
    return config_utils.polish_translation()


def _is_transcription_only() -> bool:
    try:
        return bool(load_key_or("transcription_only", False))
    except Exception:
        return False


def _chunks(items: Sequence, size: int) -> List[Sequence]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _log_title(base: str, extra_body: Optional[dict]) -> str:
    """按"思考开关"给缓存分区改名：开思考 `polish_subs`，关思考 `polish_subs_nothink`。

    为什么要改名：`ask_gpt` 的缓存键只看 `(model, prompt)`（分区内），`extra_body` 既不进键也不进
    日志。不改名的话，用户切换"允许模型思考"后会**命中上一次档位的缓存**，看起来"开关没反应"。
    改名让两档各存各的缓存，切换立即生效（代价是两档各花一次钱）。
    """
    return base if extra_body is None else f"{base}_nothink"


def _parse_polish_response(data, expected_ids: Sequence[int]) -> Optional[Dict[int, Tuple[str, bool]]]:
    """把模型返回解析成 `{id: (polished, changed)}`；结构不对返回 None（该批放弃润色）。"""
    if not isinstance(data, dict) or not isinstance(data.get('lines'), list):
        return None
    parsed: Dict[int, Tuple[str, bool]] = {}
    for item in data['lines']:
        if not isinstance(item, dict):
            continue
        try:
            row_id = int(item.get('id'))
        except (TypeError, ValueError):
            continue
        if row_id not in expected_ids or 'polished' not in item:
            continue
        parsed[row_id] = (str(item.get('polished', '')), bool(item.get('changed', True)))
    return parsed if len(parsed) == len(expected_ids) else None


def _audit_info_changes(pairs: Sequence[Tuple[int, str, str]], stats: Dict[str, int],
                        extra_body: Optional[dict] = None) -> set:
    """批量审校：返回"被判定为增删/篡改了信息"的 id 集合（异常时返回空集 = 全部放行）。

    `extra_body` 与润色调用共用同一个"思考开关"：用户关掉思考时，审校也一起不思考 ——
    否则会出现"我明明关了思考，怎么还在花思考 token"的困惑（2026-09-21 用户要求这个开关时
    的语义就是"控制这一步的思考"）。安全网仍在：机械护栏 + 关思考时自动收紧的覆盖率门槛。
    """
    if not pairs:
        return set()
    prompt = get_polish_audit_prompt(list(pairs))

    def valid_audit(response_data):
        if not isinstance(response_data.get('audit'), list):
            return {"status": "error", "message": "Missing required key: `audit`"}
        return {"status": "success", "message": "audit ok"}

    try:
        data = ask_gpt(prompt, response_json=True, valid_def=valid_audit,
                       log_title=_log_title('polish_audit', extra_body), extra_body=extra_body)
    except Exception as exc:  # noqa: BLE001 - 审校不可用不能变成"整批丢词"
        console.print(f"[yellow]⚠️ 润色审校不可用（{type(exc).__name__}），本批改动按原样保留[/yellow]")
        stats['audit_failed'] += 1
        return set()
    flagged = set()
    for item in data.get('audit', []):
        if not isinstance(item, dict):
            continue
        try:
            row_id = int(item.get('id'))
        except (TypeError, ValueError):
            continue
        if item.get('info_changed'):
            flagged.add(row_id)
    stats['audit_calls'] += 1
    stats['audit_flagged'] += len(flagged)
    return flagged


def polish_lines(sources: Sequence[str], translations: Sequence[str],
                 max_width: Optional[float] = None, *,
                 use_thinking: Optional[bool] = None,
                 long_lines_only: bool = False) -> Tuple[List[str], Dict[str, int]]:
    """润色整表：返回 `(新的译文列, 统计)`。任何失败都只回退到原译文，绝不丢内容。

    三个取舍旋钮（都来自配置，见 `polish_subs_main`）：
      * `max_width`：目标语显示宽度上限（超了直接回退原行）；
      * `use_thinking`：是否允许模型思考。关掉省 ~91% token，但润色更激进地压缩，
        因此自动改用更严的覆盖率门槛 `POLISH_COVERAGE_MIN_NO_THINKING`（回退更多行）；
      * `long_lines_only`：只把"有分句的长行"送去润色（`needs_polish_long_line`），
        其余行原样保留并计入 `skipped`。
    """
    sources = ["" if pd.isna(s) else str(s) for s in sources]
    current = ["" if pd.isna(t) else str(t) for t in translations]
    result = list(current)
    if use_thinking is None:
        use_thinking = config_utils.polish_thinking()
    min_coverage = (subtitle_split.POLISH_COVERAGE_MIN if use_thinking
                    else subtitle_split.POLISH_COVERAGE_MIN_NO_THINKING)
    stats = {"rows": len(current), "batches": 0, "failed_batches": 0, "changed": 0,
             "rejected": 0, "audit_calls": 0, "audit_flagged": 0, "audit_failed": 0,
             "skipped": 0, "sent": len(current), "thinking": int(bool(use_thinking))}
    reject_reasons: List[str] = []

    # 预筛：只润色"有分句的长行"时，其余行不参与（省 token 的主要手段之一）
    if long_lines_only:
        targets = [i for i, text in enumerate(current)
                   if subtitle_split.needs_polish_long_line(text)]
        stats['skipped'] = len(current) - len(targets)
        stats['sent'] = len(targets)
    else:
        targets = list(range(len(current)))

    extra_body = None if use_thinking else {"thinking": {"type": "disabled"}}

    for batch_index, index_group in enumerate(_chunks(targets, BATCH_SIZE), start=1):
        eu.check_cancel()
        rows = [(i, sources[i], current[i]) for i in index_group]
        stats['batches'] += 1
        try:
            prompt = get_polish_prompt(rows, max_width if max_width is not None else 999)

            def valid_polish(response_data, expected_ids=tuple(i for i, _, _ in rows)):
                if not isinstance(response_data.get('lines'), list):
                    return {"status": "error", "message": "Missing required key: `lines`"}
                ids = {item.get('id') for item in response_data['lines'] if isinstance(item, dict)}
                if len(ids) != len(expected_ids) or not set(expected_ids) <= ids:
                    return {"status": "error",
                            "message": (f"expected exactly one entry per id ({len(expected_ids)} lines: "
                                        f"{list(expected_ids)}), got {len(response_data['lines'])} entries. "
                                        f"Return every id once, in order, without merging or splitting lines")}
                return {"status": "success", "message": "polish ok"}

            data = ask_gpt(prompt, response_json=True, valid_def=valid_polish,
                           log_title=_log_title('polish_subs', extra_body), extra_body=extra_body)
            parsed = _parse_polish_response(data, tuple(i for i, _, _ in rows))
            if parsed is None:
                raise ValueError("响应行数与输入的 id 对不上")
        except Exception as exc:  # noqa: BLE001 - 一批失败只影响这一批
            stats['failed_batches'] += 1
            console.print(f"[yellow]⚠️ 第 {batch_index} 批润色不可用（{type(exc).__name__}），"
                          f"这 {len(index_group)} 行保持原译文[/yellow]")
            continue

        changed_pairs: List[Tuple[int, str, str]] = []
        for row_id, (polished, changed) in parsed.items():
            original = current[row_id]
            ok, reason = subtitle_split.polish_ok(original, polished, max_width=max_width,
                                                 min_coverage=min_coverage)
            if not ok:
                stats['rejected'] += 1
                if len(reject_reasons) < 5:
                    reject_reasons.append(f"#{row_id} {reason}")
                continue
            result[row_id] = polished
            # 只有"归一化后真的不同"的行才需要审校：只改标点（如去掉行尾句号）不算信息改动，
            # 送审只会白花钱。最终"改动行数"另行从 result 与原列对比得出（见函数末尾）。
            if changed or subtitle_split.normalize_text(polished) != subtitle_split.normalize_text(original):
                changed_pairs.append((row_id, original, polished))

        for row_id in _audit_info_changes(changed_pairs, stats, extra_body):
            result[row_id] = current[row_id]
            stats['reverted'] = stats.get('reverted', 0) + 1

    # "改动行数"以**最终文本**为准（用户看到的差异），而不是模型自报的 changed 标记 ——
    # 否则"只改了标点但模型说没改"的行会被漏计，统计行与肉眼所见的差异对不上。
    stats['changed'] = sum(1 for before, after in zip(current, result) if str(before) != str(after))
    if reject_reasons:
        stats['reasons'] = reject_reasons  # type: ignore[assignment]
    return result, stats


def polish_stats_line(stats: Dict[str, int]) -> str:
    """一行汇总（放在 step5.2 末尾；没开启这一步时不会打印）。"""
    text = (f"✨ 润色统计：{stats['rows']} 行（送出 {stats.get('sent', stats['rows'])} / "
            f"预筛跳过 {stats.get('skipped', 0)}）分 {stats['batches']} 批，"
            f"改动 {stats['changed']} 行 / 护栏拦下 {stats['rejected']} 行 / "
            f"审校回退 {stats.get('reverted', 0)} 行 / 失败 {stats['failed_batches']} 批"
            f"｜思考 {'开' if stats.get('thinking', 1) else '关'}")
    reasons = stats.get('reasons') or []
    if reasons:
        text += "\n   拦下原因示例：" + "；".join(reasons[:3])
    return text


def polish_subs_main():
    """step5.2 入口：开关关着就只打印一行并返回（零 LLM 调用）。"""
    if not polish_enabled():
        console.print("[cyan]✨ 字幕润色未开启（subtitle.polish_translation=false），跳过本步[/cyan]")
        return
    if _is_transcription_only():
        # 直通模式下译文就是原文，润色等于改写原文，直接跳过
        console.print("[cyan]✨ 当前是「只生成原语言字幕」模式，字幕润色跳过[/cyan]")
        return

    console.print(Panel("✨ Polish subtitles (step 5.2)", expand=False))
    df = pd.read_excel(INPUT_FILE)
    limits = resolve_limits()
    console.print(f"[cyan]📐 字幕长度档位：[/cyan]{limits.label}")
    use_thinking = config_utils.polish_thinking()
    long_lines_only = config_utils.polish_long_lines_only()
    console.print(f"[cyan]🧠 思考：{'开（更忠实，约 20 行/批 2 万 tokens）' if use_thinking else '关（省约 91% token，覆盖率门槛自动提到 0.85）'}[/cyan]")
    console.print(f"[cyan]✂️ 润色范围：{'只润色有分句的长行' if long_lines_only else '全部行'}[/cyan]")

    polished, stats = polish_lines(df['Source'].tolist(), df['Translation'].tolist(),
                                  max_width=limits.tr_limit, use_thinking=use_thinking,
                                  long_lines_only=long_lines_only)
    df_out = pd.DataFrame({'Source': df['Source'].tolist(), 'Translation': polished})
    df_out.to_excel(OUTPUT_POLISHED_FILE, index=False)

    console.print(f"[cyan]{polish_stats_line(stats)}[/cyan]")
    if stats['failed_batches']:
        console.print("[yellow]⚠️ 有批次失败：这些行保持原译文（可重跑本步，命中缓存的批次不会重复付费）[/yellow]")

    table = Table(title="✨ Polished sample（最多 5 行）")
    table.add_column("#", style="cyan")
    table.add_column("Before", style="magenta")
    table.add_column("After", style="green")
    shown = 0
    for i, (before, after) in enumerate(zip(df['Translation'].tolist(), polished)):
        if subtitle_split.normalize_text(before) == subtitle_split.normalize_text(after):
            continue
        table.add_row(str(i), str(before), str(after))
        shown += 1
        if shown >= 5:
            break
    if shown:
        console.print(table)


if __name__ == '__main__':
    try:
        import easy_util as _eu
        _eu.ensure_utf8_console()
    except Exception:
        pass
    polish_subs_main()
