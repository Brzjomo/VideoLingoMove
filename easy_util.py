import threading, os, sys

# 线程锁
lock = threading.Lock()


def ensure_utf8_console():
    """确保控制台/重定向输出使用 UTF-8。

    Windows 控制台默认是 GBK，而本项目大量使用 emoji 与中文输出，
    直接运行会在 print 时抛 UnicodeEncodeError（例如 `python core/step6_...py`）。
    在入口处调用一次即可；重复调用无害。若当前流不支持 reconfigure 则静默跳过。
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


# 时间记录
start_time = 0
end_time = 0
time_duration = 0
total_time_duration = 0

# token记录
prompt_tokens = 0
completion_tokens = 0
total_tokens = 0

# 预估单价（每百万）
price_input_uncached = 1
price_input_cached = 0.02
price_output = 2

# 命中缓存的token比例
cached_token_rate = 0.3

# 预估花费
estimated_cost = 0
estimated_total_cost = 0

# 文件名
original_name = ""

# 进度记录
current_progress = 0
processing = False

# 在文件开头添加新的变量
total_prompt_tokens = 0
total_completion_tokens = 0

# 方法
def convert_seconds(seconds):
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    if minutes >= 60:
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}小时{minutes}分{seconds}秒"
    elif minutes > 0:
        return f"{minutes}分{seconds}秒"
    else:
        return f"{seconds}秒"

def get_total_tokens():
    return prompt_tokens + completion_tokens

def get_estimated_cost():
    cost_input_uncached = prompt_tokens / 1000000 * (1 - cached_token_rate) * price_input_uncached
    cost_input_cached = prompt_tokens / 1000000 * cached_token_rate * price_input_cached
    cost_output = completion_tokens / 1000000 * price_output
    total_cost = cost_input_uncached + cost_input_cached + cost_output
    return total_cost

def get_total_estimated_cost():
    return estimated_total_cost

def get_formated_estimated_cost():
    return "{:.5f}".format(get_estimated_cost()) + "元"

def get_formated_total_estimated_cost():
    return "{:.5f}".format(get_total_estimated_cost()) + "元"

def record_messages():
    output = "消耗时长: " + convert_seconds(time_duration)
    output += "\n" + "消耗 prompt tokens: " + str(prompt_tokens)
    output += "\n" + "消耗 completion tokens: " + str(completion_tokens)
    output += "\n" + "共消耗tokens: " + str(get_total_tokens())
    output += "\n" + "预计花费: " + get_formated_estimated_cost()
    with open("output/cost.txt", "w", encoding="utf-8") as f:
        f.write(str(output))

def record_file_name(file):
    file_name = os.path.splitext(os.path.basename(file))[0]
    return file_name

# 添加进度相关的方法
def set_progress(progress: float):
    global current_progress
    current_progress = max(0.0, min(1.0, progress))

def get_progress():
    return current_progress

def set_processing(status: bool):
    global processing
    processing = status

def is_processing():
    return processing

# 添加新的方法
def add_to_total_tokens():
    """将当前视频的token添加到总计中"""
    global total_prompt_tokens, total_completion_tokens
    total_prompt_tokens += prompt_tokens
    total_completion_tokens += completion_tokens

def add_to_total_time():
    """将当前视频的处理时间添加到总计中。

    注意：若调用方已经走过 core/step6_generate_final_timeline.record_summary_info()
    （它会自行累加 total_time_duration），就**不要**再调用本函数，否则会算成 2 倍。
    见 devdocs 已知问题 P3-30。
    """
    global total_time_duration
    total_time_duration += time_duration

def add_to_total_cost():
    """把当前视频的预估花费累加到总计中。

    与 get_total_cost()（由累计 token 重算）是两套口径：
    本函数累加每次 get_estimated_cost() 的快照，适合"每视频独立计价"的场景。
    批处理统一使用 get_total_cost() 口径，因此这里只做累加以保持旧行为兼容。
    """
    global estimated_total_cost
    estimated_total_cost += get_estimated_cost()

def get_total_tokens_summary():
    """获取总token消耗统计"""
    total = total_prompt_tokens + total_completion_tokens
    return {
        'prompt': total_prompt_tokens,
        'completion': total_completion_tokens,
        'total': total
    }

def get_total_cost():
    """计算所有视频的总花费"""
    cost_input_uncached = total_prompt_tokens / 1000000 * (1 - cached_token_rate) * price_input_uncached
    cost_input_cached = total_prompt_tokens / 1000000 * cached_token_rate * price_input_cached
    cost_output = total_completion_tokens / 1000000 * price_output
    return cost_input_uncached + cost_input_cached + cost_output

def get_formatted_total_cost():
    """获取格式化的总花费字符串"""
    return "{:.5f}元".format(get_total_cost())

def get_formatted_total_tokens():
    """获取格式化的总token消耗字符串"""
    return (
        f"总 Prompt Tokens: {total_prompt_tokens:,}\n"
        f"总 Completion Tokens: {total_completion_tokens:,}\n"
        f"总 Tokens: {total_prompt_tokens + total_completion_tokens:,}"
    )

def reset_total_statistics():
    """重置所有总计统计

    包含 estimated_total_cost：此前遗漏了它，导致跨批次累加、无法清零
    （见 devdocs 已知问题 P2-7 / P3-32）。
    """
    global total_prompt_tokens, total_completion_tokens, total_time_duration, estimated_total_cost
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_time_duration = 0
    estimated_total_cost = 0

def get_safe_filename(filename: str, max_length: int = 100) -> str:
    """
    将文件名转换为安全的文件名（移除特殊字符，限制长度）

    Args:
        filename: 原始文件名（可以包含路径）
        max_length: 最大长度限制

    Returns:
        str: 安全的文件名
    """
    # 提取文件名（不含路径和扩展名）
    basename = os.path.basename(filename)
    name_without_ext = os.path.splitext(basename)[0]

    # 移除特殊字符，只保留字母、数字、空格、连字符、下划线
    # 允许中文字符（中文在Python字符串中是有效的）
    safe_name = ""
    for char in name_without_ext:
        if char.isalnum() or char in (' ', '-', '_', '.', '(', ')', '[', ']', '{', '}'):
            safe_name += char
        elif '\u4e00' <= char <= '\u9fff':  # 中文字符范围
            safe_name += char
        else:
            safe_name += '_'  # 其他特殊字符替换为下划线

    # 移除首尾空格
    safe_name = safe_name.strip()

    # 如果为空，使用默认名称
    if not safe_name:
        safe_name = "video"

    # 替换空格为下划线
    safe_name = safe_name.replace(' ', '_')

    # 限制长度
    if len(safe_name) > max_length:
        # 保留前max_length个字符，但要确保不会截断中文字符
        # 简单实现：直接截断
        safe_name = safe_name[:max_length]

    return safe_name
