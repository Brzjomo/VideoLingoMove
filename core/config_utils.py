from ruamel.yaml import YAML
from typing import Any
import os, sys, re, shutil
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 配置文件位置。可用环境变量 VIDEOLINGO_CONFIG 指向别处（便于测试或多环境切换）。
CONFIG_PATH = os.environ.get('VIDEOLINGO_CONFIG', 'config.yaml')
CONFIG_EXAMPLE_PATH = 'config.example.yaml'
config_lock = threading.Lock()

yaml = YAML()
yaml.preserve_quotes = True

# 环境变量前缀。`api.key` -> VIDEOLINGO_API_KEY
ENV_PREFIX = 'VIDEOLINGO_'
# 尚未替换的占位符（模板里的值），命中即视为"未配置"
PLACEHOLDER_MARKERS = ('YOUR_', '密钥', 'your_', '<', '>')


def _to_env_name(key: str) -> str:
    """把配置键路径转成环境变量名：`api.key` -> `VIDEOLINGO_API_KEY`"""
    return ENV_PREFIX + re.sub(r'[^0-9A-Za-z]+', '_', key).upper()


def _expand_env(value: Any) -> Any:
    """展开字符串中的 ${VAR} 引用（未定义时替换为空串）。"""
    if not isinstance(value, str) or '${' not in value:
        return value
    return re.sub(r'\$\{([^}]+)\}', lambda m: os.environ.get(m.group(1).strip(), ''), value)


def _read_config() -> Any:
    """读取并解析 config.yaml；文件缺失时从 config.example.yaml 引导创建。

    读取时会按需创建配置文件（首次运行友好），且**不**写入任何内容。
    """
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(CONFIG_EXAMPLE_PATH):
            shutil.copy2(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
            # 注意：这里只用 ASCII 输出。Windows 控制台默认 GBK，
            # 打印 emoji 会抛 UnicodeEncodeError（见 devdocs 已知问题）。
            print(f"[config] {CONFIG_PATH} not found; created it from {CONFIG_EXAMPLE_PATH}. "
                  f"Fill in your API keys, or provide them via the {ENV_PREFIX}API_KEY "
                  f"environment variable.")
        else:
            raise FileNotFoundError(
                f"找不到配置文件 {CONFIG_PATH}，也找不到模板 {CONFIG_EXAMPLE_PATH}。"
                "请从版本库获取 config.example.yaml。"
            )
    with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
        return yaml.load(file)


def _coerce_env(raw: str) -> Any:
    """环境变量一律是字符串，这里做最小化类型还原。

    只处理明确的布尔与数字（整数/浮点），其余原样返回字符串。
    这样 `VIDEOLINGO_MAX_WORKERS=8` 得到的是 int 8 而不是 "8"。
    """
    text = raw.strip()
    lowered = text.lower()
    if lowered == 'true':
        return True
    if lowered == 'false':
        return False
    if re.fullmatch(r'[+-]?\d+', text):
        return int(text)
    if re.fullmatch(r'[+-]?\d*\.\d+([eE][+-]?\d+)?', text):
        return float(text)
    return raw


def load_key(key: str) -> Any:
    """读取配置项。

    优先级（高 → 低）：
      1. 环境变量 `VIDEOLINGO_<KEY>`（点号转下划线、大写），例如 `api.key` → `VIDEOLINGO_API_KEY`
      2. `config.yaml` 中的值（其中的 `${VAR}` 会被展开）
      3. 报错（键不存在）

    这样密钥可以完全不落盘：只要设置环境变量即可覆盖文件里的占位符。
    注意：环境变量是字符串，布尔与数字会被 `_coerce_env()` 还原类型。
    """
    env_name = _to_env_name(key)
    if env_name in os.environ and os.environ[env_name] != '':
        return _coerce_env(os.environ[env_name])

    with config_lock:
        data = _read_config()

    keys = key.split('.')
    value = data
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            raise KeyError(f"Key '{k}' not found in configuration")
    return _expand_env(value)


def update_key(key: str, new_value: Any) -> bool:
    with config_lock:
        data = _read_config()

        keys = key.split('.')
        current = data
        for k in keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False

        if isinstance(current, dict) and keys[-1] in current:
            current[keys[-1]] = new_value
            with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
                yaml.dump(data, file)
            return True
        else:
            raise KeyError(f"Key '{keys[-1]}' not found in configuration")


# basic utils
def get_joiner(language):
    if language in load_key('language_split_with_space'):
        return " "
    elif language in load_key('language_split_without_space'):
        return ""
    else:
        raise ValueError(f"Unsupported language code: {language}")


def is_placeholder(value: Any) -> bool:
    """判断某个配置值是否仍是模板占位符（用于 UI 提示"未配置"）。"""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if stripped == '':
        return True
    return any(marker in stripped for marker in PLACEHOLDER_MARKERS)


if __name__ == "__main__":
    print(load_key('language_split_with_space'))


def assign_key(target_key: str, source_key: str) -> bool:
    with config_lock:
        data = _read_config()

        source_keys = source_key.split('.')
        target_keys = target_key.split('.')
        source_value = data
        for k in source_keys:
            if isinstance(source_value, dict) and k in source_value:
                source_value = source_value[k]
            else:
                raise KeyError(f"Key '{k}' not found in configuration")

        current = data
        for k in target_keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False

        if isinstance(current, dict) and target_keys[-1] in current:
            current[target_keys[-1]] = source_value
            with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
                yaml.dump(data, file)
            return True
        else:
            raise KeyError(f"Key '{target_keys[-1]}' not found in configuration")
