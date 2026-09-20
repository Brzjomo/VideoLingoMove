import os, sys
import streamlit as st
from core.config_utils import (update_key, load_key, load_key_or, assign_key, _to_env_name,
                               auto_length_by_language, use_llm_sentence_split)
from core import subtitle_limits

# 注意：**不要**在这里 `from st_components.imports_and_utils import ask_gpt`。
# `imports_and_utils` 反过来要 re-export 本模块的 `page_setting`（它第 26 行），
# 于是两个模块互为顶层依赖 —— 只有"先 import imports_and_utils"这一种顺序能过，
# 反过来（`import st_components.sidebar_setting` 打头，测试/新入口很容易这么写）
# 会报 `cannot import name 'page_setting' from partially initialized module`。
# `ask_gpt` 只在 check_api() 里用，改成函数内惰性导入，两条顺序都能过。

def config_input(label, key, help=None):
    """Generic config input handler

    环境变量优先：若 `<键>` 被 `VIDEOLINGO_<KEY>` 覆盖，输入框会显示环境变量的值，
    且在此处的编辑**不会生效**（下次读取仍返回环境变量）。为避免"改了没用"的困惑，
    检测到覆盖时给出明确提示（见 devdocs 已知问题 R11）。
    """
    env_name = _to_env_name(key)
    overridden = bool(os.environ.get(env_name))

    val = st.text_input(label, value=load_key(key), help=help)
    if overridden:
        st.caption(f"⚠️ 该项已被环境变量 `{env_name}` 覆盖；在此处的修改不会生效。")
    if val != load_key(key):
        if overridden:
            st.warning(f"`{key}` 当前由环境变量 `{env_name}` 决定，修改未写入。")
        else:
            update_key(key, val)
    return val

def _fetch_model_list(base_url, api_key):
    """从 `<base_url>/v1/models` 拉取可用模型 id 列表。

    手输模型名（尤其 OpenRouter 那种 `vendor/model` 形式）是最常见的配置错误，
    这里把服务端自己声明的清单取回来供搜索。
    """
    import requests
    if not base_url:
        raise ValueError("api.base_url 为空")
    url = str(base_url).rstrip('/')
    if 'v1' not in url:
        url += '/v1'
    url += '/models'
    resp = requests.get(
        url,
        headers={'Authorization': f'Bearer {api_key}'} if api_key else {},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return sorted({item['id'] for item in data.get('data', []) if item.get('id')})


def _search_models(search_term, model_list):
    """模型搜索回调。

    有匹配就只返回匹配项；完全不匹配时才把用户输入原样作为候选项 ——
    这样既能直接使用手输的模型名（有些服务不实现 /models，或在用本地模型），
    又不会让"输入了半截名字"时把半截字符串顶到候选第一位。
    """
    term = (search_term or '').strip()
    if not term:
        return list(model_list)[:50]
    lowered = term.lower()
    hits = [m for m in model_list if lowered in m.lower()]
    if hits:
        return hits[:50]
    return [term]


def sync_subtitle_lengths():
    """语言变化后按档位刷新 `subtitle.max_length` / `max_split_length`。

    只在 `subtitle.auto_length_by_language` 打开时生效（关闭则一个字节都不动）。
    覆盖是**无条件**的：想手填就关开关 —— 这是刻意设计，避免"忘了自己手改过"。
    """
    limits, changed = subtitle_limits.apply_language_profile()
    if limits.auto and changed:
        st.toast("📐 " + limits.label, icon="✅")


def model_input():
    """模型选择控件。

    优先使用 `streamlit-searchbox`（带服务端模型列表的搜索框）；未安装该依赖时
    回退到普通文本框，功能不缺失。

    `streamlit-searchbox` 已在 `requirements.txt` 里，正常装完环境就会有；这里的
    回退分支只会在「旧环境没重装」或「手工装了别的依赖集合」时命中，所以提示语
    指向重跑安装脚本，而不是让用户自己 pip install。
    """
    try:
        from streamlit_searchbox import st_searchbox
    except ImportError:
        config_input("模型", "api.model", help="点右侧 📡 可检测 API 是否可用 👉")
        st.caption("ℹ️ 未安装 `streamlit-searchbox`，这里先用普通输入框。"
                   "重跑 `python installer.py`（或 `Install.bat`）即可获得带搜索的下拉框。")
        return

    model_list = st.session_state.get('_model_list', [])
    selected = st_searchbox(
        lambda term: _search_models(term, st.session_state.get('_model_list', [])),
        label="模型",
        default=load_key("api.model"),
        default_searchterm=load_key("api.model"),
        clear_on_submit=False,
        key="api_model_searchbox",
    )
    if selected and selected != load_key("api.model"):
        update_key("api.model", selected)


def check_api():
    """检查 API 连通性。

    必须绕过缓存（use_cache=False）：否则一旦磁盘上存在旧的
    output/gpt_log/None.json，就会命中历史记录而根本不发请求，
    从而在密钥已失效时仍然显示"有效"（见 devdocs 已知问题 P1-8）。
    """
    try:
        from st_components.imports_and_utils import ask_gpt  # 惰性导入，见文件头注释

        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title=None, use_cache=False)
        return resp.get('message') == 'success'
    except Exception:
        return False

def apply_config(config_name):
    # 根据配置名称来设置API_KEY, BASE_URL, 模型等
    if config_name == "Deepseek":
        assign_key("api.key", "deepseek_api.key")
        assign_key("api.base_url", "deepseek_api.base_url")
        assign_key("api.model", "deepseek_api.model")
    elif config_name == "千问":
        assign_key("api.key", "qwen_api.key")
        assign_key("api.base_url", "qwen_api.base_url")
        assign_key("api.model", "qwen_api.model")
    elif config_name == "硅基流动":
        assign_key("api.key", "siliconflow_api.key")
        assign_key("api.base_url", "siliconflow_api.base_url")
        assign_key("api.model", "siliconflow_api.model")
    elif config_name == "Ollama":
        assign_key("api.key", "ollama_api.key")
        assign_key("api.base_url", "ollama_api.base_url")
        assign_key("api.model", "ollama_api.model")
    
    # 清除之前的API状态，强制重新检查
    if 'api_status' in st.session_state:
        del st.session_state.api_status

def subtitle_length_controls():
    """字幕长度面板（2026-09-20 从主区移到侧边栏，和"字幕设置"挨着）。

    两个旋钮 + 「按语言自动设置」开关：打开（默认）时切语言即按 `core/subtitle_limits.py` 的档位
    覆盖这两个值、运行期现算（手改无效）；关闭时完全按手填值走，切换语言不改动。
    文案刻意保持一行说明（2026-09-21 用户要求与侧边栏其他项一致）。
    """
    with st.expander("✂️ 字幕长度调节", expanded=False):
        auto_length = st.toggle(
            "按语言自动设置",
            value=auto_length_by_language(),
            key="auto_length_by_language",
            help="切语言即按推荐档位覆盖下面两个值，手改无效。",
        )
        if auto_length != auto_length_by_language():
            update_key("subtitle.auto_length_by_language", bool(auto_length))
            if auto_length:
                # 打开时立刻按当前语言下发一次，避免"显示的还是手填值"
                # （不再弹 toast：用户 2026-09-21 要求这块文案全部去掉）
                subtitle_limits.apply_language_profile()
            st.rerun(scope="app")

        # 仅转录模式 + 关闭 LLM 断句时，粗切参数根本不参与（step3_2 直接用 spaCy 结果）
        split_inactive = load_key_or("transcription_only", False) and not use_llm_sentence_split()
        if split_inactive:
            st.caption("ℹ️ 仅转录 + 关闭 LLM 断句：本项不参与")

        max_split_length = st.number_input(
            "首次粗切词数上限",
            min_value=8, max_value=60,
            value=int(load_key_or("max_split_length", 20)),
            disabled=bool(auto_length) or bool(split_inactive),
            help="单位＝词（spaCy token）；自动模式按语言取。键 `max_split_length`。",
        )
        subtitle_cfg = load_key_or("subtitle", {}) or {}
        max_length = st.number_input(
            "单行最大字符数",
            min_value=10, max_value=200,
            value=int(subtitle_cfg.get("max_length", 75)),
            disabled=bool(auto_length),
            help="自动模式按语言取；手动模式源文按字符数、译文按显示宽度。键 `subtitle.max_length`。",
        )

        if st.button("保存手填值", key="save_subtitle_length", type="primary",
                     width="stretch", disabled=bool(auto_length)):
            changed = []
            if int(max_split_length) != int(load_key_or("max_split_length", 20)):
                update_key("max_split_length", int(max_split_length))
                changed.append("max_split_length")
            current_max_length = (load_key_or("subtitle", {}) or {}).get("max_length", 75)
            if int(max_length) != int(current_max_length):
                update_key("subtitle.max_length", int(max_length))
                changed.append("subtitle.max_length")
            if changed:
                st.success("已更新：" + "、".join(changed))
                st.rerun(scope="app")
            else:
                st.info("没有变化。")
        if st.button("恢复当前语言推荐值", key="reset_subtitle_length", width="stretch"):
            applied, changed = subtitle_limits.apply_language_profile(force=True)
            if changed:
                st.success("已按档位写入：" + "、".join(f"{k}={v}" for k, v in changed.items()))
                st.rerun(scope="app")
            else:
                st.info(f"已经是推荐值（{applied.label}）")


def polish_controls():
    """字幕润色面板（2026-09-21）：总开关 + 两个取舍开关，默认关，文案保持一行说明。

    总开关关着时后两个置灰；思考开关只管润色那一次调用，审校始终按默认档（用户要求）。
    润色结果另存为 `output/log/translation_results_polished.xlsx`，step6 只在开关打开且行数一致时
    才用它 —— 关掉开关重跑一次即可恢复未润色字幕。
    """
    with st.expander("✨ 字幕润色", expanded=False):
        current = bool(load_key_or("subtitle.polish_translation", False))
        enabled = st.toggle(
            "翻译后润色字幕措辞",
            value=current,
            key="polish_translation",
            help="额外调用 LLM 逐行润色：只改措辞、不改信息。",
        )
        if enabled != current:
            update_key("subtitle.polish_translation", bool(enabled))
            st.rerun(scope="app")

        # 下面两个开关只在润色打开时生效（关闭时置灰，避免误以为改了会有效果）
        thinking = bool(load_key_or("subtitle.polish_thinking", True))
        allow_thinking = st.toggle(
            "允许模型思考",
            value=thinking,
            key="polish_thinking",
            disabled=not enabled,
            help="关掉节约大量 token，但更容易丢词（回退的行更多）。",
        )
        if allow_thinking != thinking:
            update_key("subtitle.polish_thinking", bool(allow_thinking))
            st.rerun(scope="app")

        long_only = bool(load_key_or("subtitle.polish_long_lines_only", False))
        only_long = st.toggle(
            "只润色有分句的长行",
            value=long_only,
            key="polish_long_lines_only",
            disabled=not enabled,
            help="短句与无分句的行原样保留。",
        )
        if only_long != long_only:
            update_key("subtitle.polish_long_lines_only", bool(only_long))
            st.rerun(scope="app")

        st.caption("ℹ️ 每 20 行一次调用；超长/丢数字/丢信息会被拦下并回退原译文。")


def page_setting():
    with st.expander("一键切换配置", expanded=False):
        config_options = ["Deepseek", "千问", "硅基流动", "Ollama"]
        selected_config = st.selectbox("选择配置", options=config_options)
        
        # 侧边栏按钮样式。
        # 注意：这里曾写成 `div[data-testid="stButton"] button { color: white !important; }`，
        # 该选择器命中**全页**所有按钮，并与 imports_and_utils.button_style 的
        # `div.stButton > button:first-child { color: #144070 }` 冲突——因为带 !important，
        # 主区域按钮文字被强制成白色，而 hover 背景是 #ffffff，会变成白字白底。
        #
        # 现在把作用域限定在侧边栏内。选择器同时写 `.stSidebar` 与
        # `[data-testid="stSidebar"]` 两种形式，不绑定具体标签名——streamlit 1.38
        # 渲染侧边栏用的是 className="stSidebar" + data-testid="stSidebar"，
        # 写死 `section[...]` 会在标签变化时静默失效（见 devdocs 已知问题 R4）。
        st.markdown(
            """
            <style>
            .stSidebar div[data-testid="stButton"] button,
            [data-testid="stSidebar"] div[data-testid="stButton"] button {
                color: white !important;
            }
            .stSidebar div[data-testid="stButton"] button:hover,
            [data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
                color: white !important;
                border-color: white !important;
                background-color: transparent !important;
            }
            </style>
            """,
            unsafe_allow_html=True
        )
        if st.button("应用配置"):
            apply_config(selected_config)

    with st.expander("LLM 配置", expanded=False):
        config_input("API 密钥 (API_KEY)", "api.key")
        config_input("接口地址 (BASE_URL)", "api.base_url", help="OpenAI 兼容格式；会自动补上 /v1/chat/completions")
        
        c1, c2 = st.columns([5, 1])
        with c1:
            model_input()
        with c2:
            st.markdown('<div style="margin-top: 25px; margin-right: 10px;"></div>', unsafe_allow_html=True)
            if st.button("📡", key="api", help="检测 API 连接是否可用"):
                # 只调用一次：check_api() 刻意绕过了缓存，双调用=两次真实请求
                is_valid = check_api()
                st.toast("API密钥有效" if is_valid else "API密钥无效",
                        icon="✅" if is_valid else "❌")

        if st.button("🔄 获取模型列表", key="fetch_model_list", width="stretch",
                     help="从 api.base_url 的 /v1/models 拉取可用模型，供上方搜索框使用"):
            try:
                with st.spinner("正在获取模型列表..."):
                    models = _fetch_model_list(load_key("api.base_url"), load_key("api.key"))
                st.session_state['_model_list'] = models
                st.toast(f"已获取 {len(models)} 个模型", icon="✅")
                st.rerun(scope="app")
            except Exception as e:
                st.toast(f"获取失败：{e}", icon="❌")
    
    with st.expander("字幕设置", expanded=False):
        # ASR 引擎选择
        asr_engines = {
            "Whisper": "whisper",
            "火山引擎ASR": "volcano"
        }
        selected_asr_engine = st.selectbox(
            "ASR 引擎",
            options=list(asr_engines.keys()),
            index=list(asr_engines.values()).index(load_key("asr_engine")) if load_key("asr_engine") in asr_engines.values() else 0
        )
        if asr_engines[selected_asr_engine] != load_key("asr_engine"):
            update_key("asr_engine", asr_engines[selected_asr_engine])
            st.rerun() # 建议在这里也加上 rerun，防止引擎切换时的状态不同步

        c1, c2 = st.columns(2)
        with c1:
            langs = {
                "🌐 自动检测": "auto",
                "🇺🇸 英语": "en",
                "🇨🇳 简体中文": "zh",
                "🇪🇸 西班牙语": "es",
                "🇷🇺 俄语": "ru",
                "🇫🇷 法语": "fr",
                "🇩🇪 德语": "de",
                "🇮🇹 意大利语": "it",
                "🇯🇵 日语": "ja",
                "🇰🇷 韩语": "ko",
                "🇵🇹 葡萄牙语": "pt"
            }
            
            # --- 定义回调函数 (Callback) ---
            def on_lang_change():
                # 1. 从 session_state 获取用户刚刚选择的值
                # 注意：这里我们使用 key="_recog_lang_select" 来获取值
                selected_label = st.session_state._recog_lang_select
                new_lang_code = langs[selected_label]
                
                # 2. 获取当前配置用于比较 (可选，也可直接覆盖)
                current_whisper_lang = load_key("whisper.language")
                
                if new_lang_code != current_whisper_lang:
                    # 3. 更新 Whisper 语言。
                    #    update_key() 会同步写入 whisper.detected_language（选 auto 时跳过），
                    #    否则残留的旧检测值会继续影响提示词与 spaCy 模型。
                    update_key("whisper.language", new_lang_code)
                    
                    # 4. 处理火山引擎同步逻辑（与 whisper.language 保持同一语义）
                    current_asr_engine = load_key("asr_engine")
                    if current_asr_engine == "volcano":
                        current_volcano_lang = load_key("volcano_asr.language")
                        
                        lang_map = {
                            # 与火山侧下拉框一致：空串表示让火山自行检测
                            "auto": "",
                            "en": "en-US", "zh": "zh-CN", "ja": "ja-JP", 
                            "ko": "ko-KR", "fr": "fr-FR", "de": "de-DE", 
                            "es": "es-MX", "pt": "pt-BR"
                        }
                        # 直接查找对应的火山语言代码
                        new_volcano_lang = lang_map.get(new_lang_code)
                        
                        if new_volcano_lang is not None and new_volcano_lang != current_volcano_lang:
                            update_key("volcano_asr.language", new_volcano_lang)

                    # 5. 按新语言刷新字幕长度档位（开关打开时无条件下发；见 sync_subtitle_lengths）
                    sync_subtitle_lengths()

            # --- UI 组件 ---
            # 计算当前的 index
            try:
                current_index = list(langs.values()).index(load_key("whisper.language"))
            except ValueError:
                current_index = 0

            st.selectbox(
                "识别语言",
                options=list(langs.keys()),
                index=current_index,
                key="_recog_lang_select",  # 必须设置 key，以便在回调中通过 session_state 访问
                on_change=on_lang_change   # 绑定回调函数
            )

        with c2:
            target_language = st.text_input("目标语言", value=load_key("target_language"))
            if target_language != load_key("target_language"):
                update_key("target_language", target_language)
                # 目标语言也决定档位（双语字幕以译文侧为准），同样按开关刷新
                sync_subtitle_lengths()

        demucs = st.toggle("人声分离增强（Demucs）", value=load_key("demucs"), help="先用 Demucs 把人声分离出来再识别：背景音乐/噪声大的视频效果更好，但会明显增加处理时间")
        if demucs != load_key("demucs"):
            update_key("demucs", demucs)

        transcription_only = st.toggle("只生成原语言字幕 (跳过翻译)", value=load_key("transcription_only"), help="只生成原语言字幕，跳过翻译步骤")
        if transcription_only != load_key("transcription_only"):
            update_key("transcription_only", transcription_only)
            # 仅转录只有一种语言，档位要按识别语言重算（见 core/subtitle_limits.py）
            sync_subtitle_lengths()

        # 断句优化开关。
        # 规则：翻译模式下**强制开启**（译文与源文长度差异大，不做按意群断句会影响
        # 双语对齐与单行长度达标），因此那时不显示开关；只有"只生成原语言字幕"时才允许关闭。
        # 判定与执行统一走 core.config_utils.use_llm_sentence_split()，避免 UI 与代码漂移。
        if transcription_only:
            llm_sentence_split = st.toggle(
                "使用 LLM 优化断句",
                value=load_key("llm_sentence_split"),
                help="开启：按意群断句，字幕更符合 Netflix 单行标准，但会消耗 LLM token；"
                     "关闭：只用 spaCy 结果 + 标点就近断开，零 LLM 调用，断行略生硬。"
            )
            if llm_sentence_split != load_key("llm_sentence_split"):
                update_key("llm_sentence_split", llm_sentence_split)
        else:
            # 翻译模式：把配置强制纠正为 true，保证后续步骤真的走 LLM 断句。
            # 此处刻意不显示任何提示文案——开关本身不出现，静默生效即可。
            if not load_key("llm_sentence_split"):
                update_key("llm_sentence_split", True)

        burn_subtitles = st.toggle("烧录字幕（压进成片）", value=load_key("resolution") != "0x0", help="开启后把字幕压进成片，需要重新编码、耗时更长；关闭则只产出字幕文件")
        
        resolution_options = {
            "1080p": "1920x1080",
            "360p": "640x360"
        }
        
        if burn_subtitles:
            selected_resolution = st.selectbox(
                "视频分辨率",
                options=list(resolution_options.keys()),
                index=list(resolution_options.values()).index(load_key("resolution")) if load_key("resolution") != "0x0" else 0
            )
            resolution = resolution_options[selected_resolution]
        else:
            resolution = "0x0"

        if resolution != load_key("resolution"):
            update_key("resolution", resolution)

    # 字幕长度面板（原来在主区，2026-09-20 移到侧边栏：它和上面的语言/字幕设置强相关 ——
    # 档位是按"识别语言/目标语言"取的，切语言就在上面那几行里发生）
    subtitle_length_controls()

    # 字幕润色开关（可选步骤，2026-09-21 用户要求"打开后才做"）
    polish_controls()

    # Volcano Engine ASR Settings (only show when selected)
    if load_key("asr_engine") == "volcano":
        with st.expander("火山引擎ASR配置", expanded=False):

            # Required configuration
            config_input("应用 ID (App ID)", "volcano_asr.app_id", help="火山引擎控制台获取的 App ID")
            config_input("访问令牌 (Access Token)", "volcano_asr.access_token", help="火山引擎控制台获取的 Access Token")

            # Optional configuration
            config_input("资源 ID (Resource ID)", "volcano_asr.resource_id", help="资源 ID，默认：volc.bigasr.auc")

            # Language selection for volcano
            volcano_langs = {
                "自动检测": "",
                "🇺🇸 英语": "en-US",
                "🇨🇳 中文": "zh-CN",
                "🇯🇵 日语": "ja-JP",
                "🇰🇷 韩语": "ko-KR",
                "🇫🇷 法语": "fr-FR",
                "🇩🇪 德语": "de-DE",
                "🇪🇸 西班牙语": "es-MX",
                "🇵🇹 葡萄牙语": "pt-BR",
                "🇮🇩 印尼语": "id-ID",
                "🇹🇭 泰语": "th-TH",
                "🇸🇦 阿拉伯语": "ar-SA"
            }
            selected_volcano_lang = st.selectbox(
                "识别语言（火山）",
                options=list(volcano_langs.keys()),
                index=list(volcano_langs.values()).index(load_key("volcano_asr.language")) if load_key("volcano_asr.language") in volcano_langs.values() else 0
            )
            if volcano_langs[selected_volcano_lang] != load_key("volcano_asr.language"):
                update_key("volcano_asr.language", volcano_langs[selected_volcano_lang])

            # Model version
            model_version = st.selectbox(
                "模型版本",
                options=["310", "400"],
                index=0 if load_key("volcano_asr.model_version") == "310" else 1
            )
            if model_version != load_key("volcano_asr.model_version"):
                update_key("volcano_asr.model_version", model_version)

            # Feature toggles
            col1, col2 = st.columns(2)
            with col1:
                enable_punc = st.toggle("自动标点", value=load_key("volcano_asr.enable_punc"))
                if enable_punc != load_key("volcano_asr.enable_punc"):
                    update_key("volcano_asr.enable_punc", enable_punc)

                enable_itn = st.toggle("数字规整", value=load_key("volcano_asr.enable_itn"))
                if enable_itn != load_key("volcano_asr.enable_itn"):
                    update_key("volcano_asr.enable_itn", enable_itn)

                enable_ddc = st.toggle("语义顺滑", value=load_key("volcano_asr.enable_ddc"))
                if enable_ddc != load_key("volcano_asr.enable_ddc"):
                    update_key("volcano_asr.enable_ddc", enable_ddc)

            with col2:
                show_utterances = st.toggle("显示分句", value=load_key("volcano_asr.show_utterances"))
                if show_utterances != load_key("volcano_asr.show_utterances"):
                    update_key("volcano_asr.show_utterances", show_utterances)

                enable_speaker_info = st.toggle("说话人分离", value=load_key("volcano_asr.enable_speaker_info"))
                if enable_speaker_info != load_key("volcano_asr.enable_speaker_info"):
                    update_key("volcano_asr.enable_speaker_info", enable_speaker_info)

                enable_channel_split = st.toggle("双声道识别", value=load_key("volcano_asr.enable_channel_split"))
                if enable_channel_split != load_key("volcano_asr.enable_channel_split"):
                    update_key("volcano_asr.enable_channel_split", enable_channel_split)

            # Advanced settings - using columns instead of nested expander
            st.markdown("---")
            st.markdown("**高级设置**")
            vad_segment = st.toggle("VAD分句", value=load_key("volcano_asr.vad_segment"),
                                   help="使用VAD分句代替语义分句，双声道识别时建议开启")
            if vad_segment != load_key("volcano_asr.vad_segment"):
                update_key("volcano_asr.vad_segment", vad_segment)

            # Test connection button
            if st.button("测试火山引擎连接", type="secondary"):
                try:
                    from core.all_whisper_methods.volcano_asr import VolcanoASR
                    asr = VolcanoASR()
                    st.success("✅ 火山引擎ASR配置有效")
                except Exception as e:
                    st.error(f"❌ 配置错误: {str(e)}")

            # TOS Configuration (for file upload)
            st.markdown("---")
            st.markdown("**TOS对象存储配置**")

            tos_enabled = st.toggle("启用TOS上传", value=load_key("tos.enabled"),
                                   help="启用后，音频文件将上传到火山引擎TOS")
            if tos_enabled != load_key("tos.enabled"):
                update_key("tos.enabled", tos_enabled)

            if tos_enabled:
                config_input("访问密钥 (Access Key)", "tos.access_key", help="火山引擎控制台获取的 Access Key，或设置环境变量 TOS_ACCESS_KEY")
                config_input("私钥 (Secret Key)", "tos.secret_key", help="火山引擎控制台获取的 Secret Key，或设置环境变量 TOS_SECRET_KEY")

                # Bucket info
                config_input("存储桶 (Bucket)", "tos.bucket_name", help="火山引擎 TOS 的 Bucket 名称")
                config_input("接入地址 (Endpoint)", "tos.endpoint", help="火山引擎 TOS 的 Endpoint 地址")
                config_input("区域 (Region)", "tos.region", help="火山引擎 TOS 的 Region 区域")

                # Advanced TOS settings - using columns instead of nested expander
                st.markdown("---")
                st.markdown("**TOS高级设置**")
                auto_cleanup = st.toggle("自动清理", value=load_key("tos.auto_cleanup"),
                                       help="启用后，ASR处理完成返回结果后会删除TOS上的音频文件")
                if auto_cleanup != load_key("tos.auto_cleanup"):
                    update_key("tos.auto_cleanup", auto_cleanup)

                # Test TOS connection
                if st.button("测试TOS连接", type="secondary"):
                    try:
                        from core.all_whisper_methods.tos_service import get_tos_service
                        tos_service = get_tos_service()
                        if tos_service.is_enabled():
                            st.success("✅ TOS连接成功")
                        else:
                            st.error("❌ TOS连接失败，请检查配置")
                    except Exception as e:
                        st.error(f"❌ TOS连接错误: {str(e)}")

