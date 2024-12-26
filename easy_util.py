import threading, os

# 线程锁
lock = threading.Lock()

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
price_input_cached = 0.1
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
    cost_inpur_cached = prompt_tokens / 1000000 * cached_token_rate * price_input_cached
    cost_output = completion_tokens / 1000000 * price_output
    total_cost = cost_input_uncached + cost_inpur_cached + cost_output
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
