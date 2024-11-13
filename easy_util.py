import threading

# 线程锁
lock = threading.Lock()

# 时间记录
start_time = 0
end_time = 0
time_duration = 0

# token记录
prompt_tokens = 0
completion_tokens = 0
total_tokens = 0

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

def record_messages():
    output = "消耗时长: " + convert_seconds(time_duration)
    output += "\n" + "消耗 prompt tokens: " + str(prompt_tokens)
    output += "\n" + "消耗 completion tokens: " + str(completion_tokens)
    output += "\n" + "共消耗tokens: " + str(get_total_tokens())
    with open("output/cost.txt", "w") as f:
        f.write(str(output))
