import os, sys, json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threading import Lock
import json_repair
from openai import OpenAI
import time
import easy_util as eu
from requests.exceptions import RequestException
from core.config_utils import load_key

LOG_FOLDER = 'output/gpt_log'
LOCK = Lock()

# 单次请求超时（秒）。长提示词的"两段式翻译"在慢模型上可能超过 openai SDK
# 默认的 600s 之外的中间件超时；这里显式给足，避免被网关/代理提前掐断。
REQUEST_TIMEOUT = 300

# 不写日志的哨兵值。历史上这里只判断字符串 'None'，导致 log_title=None
# 写出 None.json、而字符串 'None' 反而会去读它（见 devdocs 已知问题 P1-8）。
NO_LOG_TITLES = (None, 'None')


def fix_base_url(base_url: str) -> str:
    """把配置里的 base_url 归一化成 OpenAI SDK 可用的地址。

    - 火山方舟（Volcengine Ark，base_url 里含 `ark`）：其 OpenAI 兼容端点固定在
      `/api/v3`，既不接受 `/v1` 后缀，也不能靠"缺 v1 就补 /v1"的通用规则拼出来
      （`https://ark.cn-beijing.volces.com/api/v3` 里本来就含 "v3" 不含 "v1"）。
    - 其余服务：缺 `/v1` 时补上。
    """
    if not isinstance(base_url, str):
        raise ValueError("api.base_url must be a string")
    if 'ark' in base_url:
        return "https://ark.cn-beijing.volces.com/api/v3"
    if 'v1' not in base_url:
        return base_url.strip('/') + '/v1'
    return base_url


def _is_no_log(log_title):
    return log_title in NO_LOG_TITLES


def save_log(model, prompt, response, log_title='default', message=None):
    if _is_no_log(log_title):
        return
    os.makedirs(LOG_FOLDER, exist_ok=True)
    log_data = {
        "model": model,
        "prompt": prompt,
        "response": response,
        "message": message
    }
    log_file = os.path.join(LOG_FOLDER, f"{log_title}.json")

    with LOCK:
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        else:
            logs = []
        logs.append(log_data)
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=4)


def check_ask_gpt_history(prompt, model, log_title, allow_cache=True):
    """按 (model, prompt) 命中历史记录。

    缓存键包含模型名——否则切换 LLM 供应商后会静默复用上一个模型的结果
    （见 devdocs 已知问题 P1-1）。
    """
    if _is_no_log(log_title) or not allow_cache:
        return None
    if not os.path.exists(LOG_FOLDER):
        return None
    file_path = os.path.join(LOG_FOLDER, f"{log_title}.json")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    for item in data:
        if item.get("prompt") == prompt and item.get("model") == model:
            return item.get("response")
    return None


def increase_prompt_tokens(value):
    with eu.lock:
        eu.prompt_tokens += value


def increase_completion_tokens(value):
    with eu.lock:
        eu.completion_tokens += value


def ask_gpt(prompt, response_json=True, valid_def=None, log_title='default', use_cache=True,
            bypass_cache=False, extra_body=None):
    """调用 LLM 并返回结果。

    Args:
        prompt: 提示词
        response_json: 是否要求 JSON 输出（模型需在 llm_support_json 白名单内才会传 response_format）
        valid_def: 可选的业务校验回调，接收解析后的 dict，返回
            {"status": "success"|"error", "message": str}
        log_title: 日志/缓存分区名；传 None 或 'None' 表示不写日志也不读缓存
        use_cache: 是否允许命中磁盘缓存（连通性检查等场景应传 False）
        bypass_cache: 只跳过**读取**缓存，仍会写入结果。
            调用方在"重试同一 prompt 但希望真正重新请求"时使用它——
            历史上是用 `prompt + ' ' * retry` 加空格来绕过缓存键，语义晦涩（见 devdocs R14）。
        extra_body: 直接透传给 OpenAI SDK 的额外请求体（如
            `{"thinking": {"type": "disabled"}}` 关闭推理模型的思考，
            见 core/step5_2_polish_subs.py —— 实测 20 行润色从 ~20,600 tokens 降到 1,788）。
            **会参与缓存键之外的调用参数**，调用方切换它时应确认缓存语义仍然正确。

    Returns:
        解析后的 dict（response_json=True）或原始文本（response_json=False）

    Raises:
        Exception: 3 次尝试全部失败后抛出，异常信息包含最后一次的真实失败原因。
    """
    api_set = load_key("api")
    model = api_set["model"]
    llm_support_json = load_key("llm_support_json")

    history_response = check_ask_gpt_history(
        prompt, model, log_title,
        allow_cache=use_cache and not bypass_cache
    )
    if history_response is not None and history_response is not False:
        return history_response

    if not api_set["key"]:
        raise ValueError("API_KEY is missing")

    messages = [{"role": "user", "content": prompt}]

    base_url = fix_base_url(api_set["base_url"])
    client = OpenAI(api_key=api_set["key"], base_url=base_url)
    response_format = {"type": "json_object"} if response_json and model in llm_support_json else None

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        # 失败重试时，把上一次的真实失败原因回注给模型，否则重试等于原样再问一遍
        if attempt > 0 and last_error is not None:
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "<上一次的输出不合规，已丢弃>"},
                {"role": "user", "content": f"上一次输出不合规：{last_error}\n请严格按要求重新输出，只返回合法 JSON。"},
            ]
        try:
            completion_args = {"model": model, "messages": messages, "timeout": REQUEST_TIMEOUT}
            if response_format is not None:
                completion_args["response_format"] = response_format
            if extra_body:
                # 透传给 SDK 的额外参数（例如关闭推理模型的思考）。
                completion_args["extra_body"] = extra_body
            response = client.chat.completions.create(**completion_args)
        except RequestException as e:
            last_error = f"网络请求失败: {e}"
            if attempt < max_retries - 1:
                print(f"Request error: {e}. Retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
                continue
            raise Exception(f"Still failed after {max_retries} attempts: {e}") from e
        except Exception as e:
            last_error = f"调用失败: {e}"
            if attempt < max_retries - 1:
                print(f"Unexpected error occurred: {e}\nRetrying...")
                time.sleep(2)
                continue
            raise Exception(f"Still failed after {max_retries} attempts: {e}") from e

        # token 只在真正拿到响应时统计一次（重试不再重复累加）
        try:
            increase_prompt_tokens(int(response.usage.prompt_tokens))
            increase_completion_tokens(int(response.usage.completion_tokens))
        except Exception:
            pass

        raw_content = response.choices[0].message.content

        if not response_json:
            save_log(model, prompt, raw_content, log_title=log_title)
            return raw_content

        # ① 解析（只捕获解析异常）
        try:
            response_data = json_repair.loads(raw_content)
        except Exception as e:
            last_error = f"JSON 解析失败: {e}"
            print(f"❎ json_repair parsing failed: '''{raw_content}'''")
            save_log(model, prompt, raw_content, log_title="error", message=last_error)
            if attempt == max_retries - 1:
                raise Exception(
                    f"JSON parsing still failed after {max_retries} attempts: {e}\n"
                    "Please check your network connection or API key or `output/gpt_log/error.json` to debug."
                ) from e
            continue

        # ② 业务校验（与解析分开，错误信息才不会被误写成"解析失败"）
        if valid_def:
            try:
                valid_response = valid_def(response_data)
            except Exception as e:
                valid_response = {"status": "error", "message": f"valid_def 抛异常: {e}"}
            if valid_response.get('status') != 'success':
                last_error = valid_response.get('message', '未知校验错误')
                print(f"❎ API response validation failed: {last_error}")
                if attempt == max_retries - 1:
                    save_log(model, prompt, response_data, log_title="error", message=last_error)
                    raise Exception(
                        f"API response error after {max_retries} attempts: {last_error}\n"
                        "See `output/gpt_log/error.json` for the raw response."
                    )
                continue

        save_log(model, prompt, response_data, log_title=log_title)
        return response_data

    # 理论不可达：循环内每条失败路径都会 continue 或 raise
    raise Exception(f"ask_gpt failed after {max_retries} attempts: {last_error}")


if __name__ == '__main__':
    # 注意：log_title=None 不再写出 None.json（见 devdocs 已知问题 P1-8）
    print(ask_gpt('hi there hey response in json format, just return 200.', response_json=True, log_title=None))
