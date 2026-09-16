import os, sys
import streamlit as st
from core.config_utils import update_key, load_key, assign_key, _to_env_name
from st_components.imports_and_utils import ask_gpt

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

def check_api():
    """检查 API 连通性。

    必须绕过缓存（use_cache=False）：否则一旦磁盘上存在旧的
    output/gpt_log/None.json，就会命中历史记录而根本不发请求，
    从而在密钥已失效时仍然显示"有效"（见 devdocs 已知问题 P1-8）。
    """
    try:
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
        config_input("API_KEY", "api.key")
        config_input("BASE_URL", "api.base_url", help="Openai format, will add /v1/chat/completions automatically")
        
        c1, c2 = st.columns([5, 1])
        with c1:
            config_input("MODEL", "api.model", help="click to check API validity 👉")
        with c2:
            st.markdown('<div style="margin-top: 25px; margin-right: 10px;"></div>', unsafe_allow_html=True)
            if st.button("📡", key="api", help="Check API connection"):
                st.toast("API密钥有效" if check_api() else "API密钥无效",
                        icon="✅" if check_api() else "❌")
    
    with st.expander("Subtitles Settings", expanded=False):
        # ASR Engine Selection
        asr_engines = {
            "Whisper": "whisper",
            "火山引擎ASR": "volcano"
        }
        selected_asr_engine = st.selectbox(
            "ASR Engine",
            options=list(asr_engines.keys()),
            index=list(asr_engines.values()).index(load_key("asr_engine")) if load_key("asr_engine") in asr_engines.values() else 0
        )
        if asr_engines[selected_asr_engine] != load_key("asr_engine"):
            update_key("asr_engine", asr_engines[selected_asr_engine])
            st.rerun() # 建议在这里也加上 rerun，防止引擎切换时的状态不同步

        c1, c2 = st.columns(2)
        with c1:
            langs = {
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
                    # 3. 更新 Whisper 语言
                    update_key("whisper.language", new_lang_code)
                    
                    # 4. 处理火山引擎同步逻辑
                    current_asr_engine = load_key("asr_engine")
                    if current_asr_engine == "volcano":
                        current_volcano_lang = load_key("volcano_asr.language")
                        
                        lang_map = {
                            "en": "en-US", "zh": "zh-CN", "ja": "ja-JP", 
                            "ko": "ko-KR", "fr": "fr-FR", "de": "de-DE", 
                            "es": "es-MX", "pt": "pt-BR"
                        }
                        # 直接查找对应的火山语言代码
                        new_volcano_lang = lang_map.get(new_lang_code)
                        
                        if new_volcano_lang and new_volcano_lang != current_volcano_lang:
                            update_key("volcano_asr.language", new_volcano_lang)

            # --- UI 组件 ---
            # 计算当前的 index
            try:
                current_index = list(langs.values()).index(load_key("whisper.language"))
            except ValueError:
                current_index = 0

            st.selectbox(
                "Recog Lang",
                options=list(langs.keys()),
                index=current_index,
                key="_recog_lang_select",  # 必须设置 key，以便在回调中通过 session_state 访问
                on_change=on_lang_change   # 绑定回调函数
            )

        with c2:
            target_language = st.text_input("Target Lang", value=load_key("target_language"))
            if target_language != load_key("target_language"):
                update_key("target_language", target_language)

        demucs = st.toggle("Vocal separation enhance", value=load_key("demucs"), help="Recommended for videos with loud background noise, but will increase processing time")
        if demucs != load_key("demucs"):
            update_key("demucs", demucs)

        transcription_only = st.toggle("只生成原语言字幕 (跳过翻译)", value=load_key("transcription_only"), help="只生成原语言字幕，跳过翻译步骤")
        if transcription_only != load_key("transcription_only"):
            update_key("transcription_only", transcription_only)

        burn_subtitles = st.toggle("Burn-in Subtitles", value=load_key("resolution") != "0x0", help="takes longer time")
        
        resolution_options = {
            "1080p": "1920x1080",
            "360p": "640x360"
        }
        
        if burn_subtitles:
            selected_resolution = st.selectbox(
                "Video Resolution",
                options=list(resolution_options.keys()),
                index=list(resolution_options.values()).index(load_key("resolution")) if load_key("resolution") != "0x0" else 0
            )
            resolution = resolution_options[selected_resolution]
        else:
            resolution = "0x0"

        if resolution != load_key("resolution"):
            update_key("resolution", resolution)

    # Volcano Engine ASR Settings (only show when selected)
    if load_key("asr_engine") == "volcano":
        with st.expander("火山引擎ASR配置", expanded=False):

            # Required configuration
            config_input("App ID", "volcano_asr.app_id", help="火山引擎控制台获取的APP ID")
            config_input("Access Token", "volcano_asr.access_token", help="火山引擎控制台获取的Access Token")

            # Optional configuration
            config_input("Resource ID", "volcano_asr.resource_id", help="资源ID，默认: volc.bigasr.auc")

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
                "识别语言",
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
                config_input("Access Key", "tos.access_key", help="火山引擎控制台获取的Access Key，或设置环境变量TOS_ACCESS_KEY")
                config_input("Secret Key", "tos.secret_key", help="火山引擎控制台获取的Secret Key，或设置环境变量TOS_SECRET_KEY")

                # Bucket info
                config_input("Bucket名称", "tos.bucket_name", help="火山引擎TOS的Bucket名称")
                config_input("Endpoint", "tos.endpoint", help="火山引擎TOS的Endpoint地址")
                config_input("Region", "tos.region", help="火山引擎TOS的Region区域")

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

