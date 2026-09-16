import os, sys, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich import print as rprint
from rich.panel import Panel
from rich.console import Console

from core.ask_gpt import ask_gpt
from core.prompts_storage import get_subtitle_trim_prompt
from core.config_utils import load_key
from core.estimate_duration import init_estimator, estimate_duration

console = Console()
ESTIMATOR = None


def check_len_then_trim(text, duration):
    """若文本的预估朗读时长超过给定 duration，则调用 LLM 压缩该文本。

    预估时长会先除以 `speed_factor.max`，即允许的最快语速；只有在
    最快语速下仍然超时，才需要真正删减内容。

    Args:
        text: 待检查的文本（通常是译文）
        duration: 该条字幕可用的时长（秒）

    Returns:
        str: 原文本，或压缩后的文本
    """
    global ESTIMATOR
    if ESTIMATOR is None:
        ESTIMATOR = init_estimator()
    estimated_duration = estimate_duration(text, ESTIMATOR) / load_key("speed_factor")['max']

    console.print(f"Subtitle text: {text}, "
                  f"[bold green]Estimated reading duration: {estimated_duration:.2f} seconds[/bold green]")

    if estimated_duration > duration:
        rprint(Panel(f"Estimated reading duration {estimated_duration:.2f} seconds exceeds given duration {duration:.2f} seconds, shortening...", title="Processing", border_style="yellow"))
        original_text = text
        prompt = get_subtitle_trim_prompt(text, duration)

        def valid_trim(response):
            if 'result' not in response:
                return {'status': 'error', 'message': 'No result in response'}
            return {'status': 'success', 'message': ''}

        try:
            response = ask_gpt(prompt, response_json=True, log_title='subtitle_trim', valid_def=valid_trim)
            shortened_text = response['result']
        except Exception:
            rprint("[bold red]🚫 AI refused to answer due to sensitivity, so manually remove punctuation[/bold red]")
            shortened_text = re.sub(r'[,.!?;:，。！？；：]', ' ', text).strip()
        rprint(Panel(f"Subtitle before shortening: {original_text}\nSubtitle after shortening: {shortened_text}", title="Subtitle Shortening Result", border_style="green"))
        return shortened_text
    else:
        return text


if __name__ == '__main__':
    # 直接运行可用于测试压缩逻辑（需要 output/log 与可用的 LLM 配置）
    print(check_len_then_trim("这是一句用于测试压缩的非常长的字幕文本，理论上应该超过给定时长", 1.0))
