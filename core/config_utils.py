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


def load_key_or(key: str, default: Any = None) -> Any:
    """读取配置项，键不存在时返回 default 而不抛 KeyError。

    用于"模板新增的键在已存在的旧 config.yaml 里还没有"的场景：
    `_read_config()` 只会在文件**缺失**时从 config.example.yaml 引导，
    已经存在的旧文件不会自动补键。
    """
    try:
        return load_key(key)
    except KeyError:
        return default


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
            # 手动改识别语言时，原子地把 detected_language 一起对齐。
            # 否则切换语言后残留的旧检测值会继续影响提示词与 spaCy 模型
            # （见 devdocs 已知问题：侧边栏「识别语言」切换后提示词仍用旧语言）。
            # 选择 "auto" 时不覆盖：此时应交给下一次转录写入真实检测结果。
            if key == "whisper.language" and new_value != "auto" and "detected_language" in current:
                current["detected_language"] = new_value
            with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
                yaml.dump(data, file)
            return True
        else:
            raise KeyError(f"Key '{keys[-1]}' not found in configuration")


def get_source_language() -> str:
    """解析"实际生效的源语言"，作为全流程唯一判定点。

    规则：
      - `whisper.language` 是明确的语言代码时，一律以它为准；
      - 只有它是 `auto`（或空）时，才回退到 ASR 写入的 `whisper.detected_language`。

    此前 `prompts_storage.py` 无条件读 `detected_language`，而侧边栏切换识别
    语言时只写 `whisper.language`，于是出现"切成 en 之后提示词仍声称源语言是
    zh、spaCy 也仍加载中文模型"的错配。
    """
    language = load_key_or("whisper.language")
    if not isinstance(language, str) or not language.strip() or language.strip() == "auto":
        language = load_key_or("whisper.detected_language")
    if not isinstance(language, str) or not language.strip() or language.strip() == "auto":
        raise ValueError(
            "源语言未知：请先在侧边栏选择识别语言，或先运行一次转录以自动检测。"
        )
    return language.strip()


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


def use_llm_sentence_split() -> bool:
    """是否使用 LLM 优化断句（step3_2 按句意切分、step5 切超长行）。

    规则（单一判定点，避免各处判断漂移）：
      - 正常翻译模式：**强制使用** LLM 断句。译文长度与源文差异大，
        不做按意群的断句会直接影响双语对齐与单行长度达标。
      - 仅转录模式（`transcription_only = true`）：允许用 `llm_sentence_split`
        关闭它，此时只出原语言字幕，用 spaCy 结果 + 标点就近断开即可，可省下全部断句 token。

    也就是说 `llm_sentence_split: false` 只在只生成原语言字幕时生效。
    """
    if not load_key("transcription_only"):
        return True
    try:
        return bool(load_key("llm_sentence_split"))
    except KeyError:
        return True


def llm_split_disabled_reason() -> str:
    """给 UI 用的一句话说明：为什么当前不能关闭 LLM 断句。"""
    if not load_key("transcription_only"):
        return ("当前为翻译模式，断句优化**强制开启**（译文与源文长度差异大，"
                "不做按意群断句会影响双语对齐与单行长度）。"
                "如需关闭，请先打开「只生成原语言字幕 (跳过翻译)」。")
    return ""


def auto_length_by_language() -> bool:
    """字幕长度是否**按语言自动取档**（单一判定点，UI 与 step3_2/step5 共用）。

    打开（默认）：切语言即按档位覆盖 `subtitle.max_length` / `max_split_length`，
    且**运行期按当前语言现算** —— config 里那两个值只是"当前语言的落盘副本"，
    手改无效（要手填请先关掉这个开关）。
    关闭：完全按手填值走（单一 `max_length`：源文按字符数、译文按宽度 × multiplier），
    切换语言不改动。

    档位表与取档规则见 `core/subtitle_limits.py`。
    """
    try:
        return bool(load_key("subtitle.auto_length_by_language"))
    except KeyError:
        return True


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
