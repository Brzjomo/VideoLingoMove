import os, sys
import streamlit as st
from core.config_utils import update_key, load_key, assign_key
from st_components.imports_and_utils import ask_gpt

def config_input(label, key, help=None):
    """Generic config input handler"""
    val = st.text_input(label, value=load_key(key), help=help)
    if val != load_key(key):
        update_key(key, val)
    return val

def check_api():
    """检查API状态"""
    try:
        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title='None')
        return resp.get('message') == 'success'
    except Exception:
        return False

def apply_config(config_name):
    # 根据配置名称来设置API_KEY, BASE_URL, 模型等
    if config_name == "Deepseek":
        assign_key("api.key", "deepseek_api.key")
        assign_key("api.base_url", "deepseek_api.base_url")
        assign_key("api.model", "deepseek_api.model")
    elif config_name == "千问-vl-max":
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
        config_options = ["Deepseek", "千问-vl-max", "硅基流动", "Ollama"]
        selected_config = st.selectbox("选择配置", options=config_options)
        
        # 添加自定义样式的按钮
        st.markdown(
            """
            <style>
            div[data-testid="stButton"] button {
                color: white !important;
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
            # Language selection for both ASR engines
            selected_recog_lang = st.selectbox(
                "Recog Lang",
                options=list(langs.keys()),
                index=list(langs.values()).index(load_key("whisper.language"))
            )

            # Map between language codes for different ASR engines
            lang_map = {
                "en": {"whisper": "en", "volcano": "en-US"},
                "zh": {"whisper": "zh", "volcano": "zh-CN"},
                "ja": {"whisper": "ja", "volcano": "ja-JP"},
                "ko": {"whisper": "ko", "volcano": "ko-KR"},
                "fr": {"whisper": "fr", "volcano": "fr-FR"},
                "de": {"whisper": "de", "volcano": "de-DE"},
                "es": {"whisper": "es", "volcano": "es-MX"},
                "pt": {"whisper": "pt", "volcano": "pt-BR"}
            }

            # Get current values
            current_whisper_lang = load_key("whisper.language")
            current_volcano_lang = load_key("volcano_asr.language")

            # Update languages if selection changed
            if langs[selected_recog_lang] != current_whisper_lang:
                # Always update whisper.language first
                update_key("whisper.language", langs[selected_recog_lang])

                # Force refresh of current values by re-reading from config
                current_asr_engine = load_key("asr_engine")
                current_volcano_lang = load_key("volcano_asr.language")

                # Now check if we need to update volcano_asr.language
                if current_asr_engine == "volcano":
                    # Get the new volcano language code from our mapping
                    new_volcano_lang = lang_map.get(langs[selected_recog_lang], {}).get('volcano', '')

                    # Only update if it's actually different
                    if new_volcano_lang != current_volcano_lang:
                        update_key("volcano_asr.language", new_volcano_lang)

        with c2:
            target_language = st.text_input("Target Lang", value=load_key("target_language"))
            if target_language != load_key("target_language"):
                update_key("target_language", target_language)

        demucs = st.toggle("Vocal separation enhance", value=load_key("demucs"), help="Recommended for videos with loud background noise, but will increase processing time")
        if demucs != load_key("demucs"):
            update_key("demucs", demucs)
        
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

                # Bucket info (read-only display)
                st.text_input("Bucket名称", value=load_key("tos.bucket_name"), disabled=True)
                st.text_input("Endpoint", value=load_key("tos.endpoint"), disabled=True)
                st.text_input("Region", value=load_key("tos.region"), disabled=True)

                # Advanced TOS settings - using columns instead of nested expander
                st.markdown("---")
                st.markdown("**TOS高级设置**")
                auto_cleanup = st.toggle("自动清理", value=load_key("tos.auto_cleanup"),
                                       help="ASR完成后自动删除TOS上的文件")
                if auto_cleanup != load_key("tos.auto_cleanup"):
                    update_key("tos.auto_cleanup", auto_cleanup)

                if auto_cleanup:
                    retention_hours = st.number_input("文件保留时间(小时)", min_value=0, max_value=24,
                                                     value=load_key("tos.retention_time") // 3600)
                    if retention_hours * 3600 != load_key("tos.retention_time"):
                        update_key("tos.retention_time", retention_hours * 3600)

                # Test TOS connection
                if st.button("测试TOS连接", type="secondary"):
                    try:
                        from core.all_whisper_methods.tos_service import TOSService
                        tos_service = TOSService()
                        if tos_service.is_enabled():
                            st.success("✅ TOS连接成功")
                        else:
                            st.error("❌ TOS连接失败，请检查配置")
                    except Exception as e:
                        st.error(f"❌ TOS连接错误: {str(e)}")

    with st.expander("Dubbing Settings", expanded=False):
        tts_methods = ["azure_tts", "openai_tts", "fish_tts", "sf_fish_tts", "edge_tts", "gpt_sovits", "custom_tts"]
        select_tts = st.selectbox("TTS Method", options=tts_methods, index=tts_methods.index(load_key("tts_method")))
        if select_tts != load_key("tts_method"):
            update_key("tts_method", select_tts)

        # sub settings for each tts method
        if select_tts == "sf_fish_tts":
            config_input("SiliconFlow API Key", "sf_fish_tts.api_key")
            
            # Add mode selection dropdown
            mode_options = {
                "preset": "Preset",
                "custom": "Refer_stable",
                "dynamic": "Refer_dynamic"
            }
            selected_mode = st.selectbox(
                "Mode Selection",
                options=list(mode_options.keys()),
                format_func=lambda x: mode_options[x],
                index=list(mode_options.keys()).index(load_key("sf_fish_tts.mode")) if load_key("sf_fish_tts.mode") in mode_options.keys() else 0
            )
            if selected_mode != load_key("sf_fish_tts.mode"):
                update_key("sf_fish_tts.mode", selected_mode)

            if selected_mode == "preset":
                config_input("Voice", "sf_fish_tts.voice")

        elif select_tts == "openai_tts":
            config_input("302ai API", "openai_tts.api_key")
            config_input("OpenAI Voice", "openai_tts.voice")

        elif select_tts == "fish_tts":
            config_input("302ai API", "fish_tts.api_key")
            fish_tts_character = st.selectbox("Fish TTS Character", options=list(load_key("fish_tts.character_id_dict").keys()), index=list(load_key("fish_tts.character_id_dict").keys()).index(load_key("fish_tts.character")))
            if fish_tts_character != load_key("fish_tts.character"):
                update_key("fish_tts.character", fish_tts_character)

        elif select_tts == "azure_tts":
            config_input("302ai API", "azure_tts.api_key")
            config_input("Azure Voice", "azure_tts.voice")
        
        elif select_tts == "gpt_sovits":
            st.info("Please refer to Github homepage for GPT_SoVITS configuration")
            config_input("SoVITS Character", "gpt_sovits.character")
            
            refer_mode_options = {1: "Mode 1: Use provided reference audio only", 2: "Mode 2: Use first audio from video as reference", 3: "Mode 3: Use each audio from video as reference"}
            selected_refer_mode = st.selectbox(
                "Refer Mode",
                options=list(refer_mode_options.keys()),
                format_func=lambda x: refer_mode_options[x],
                index=list(refer_mode_options.keys()).index(load_key("gpt_sovits.refer_mode")),
                help="Configure reference audio mode for GPT-SoVITS"
            )
            if selected_refer_mode != load_key("gpt_sovits.refer_mode"):
                update_key("gpt_sovits.refer_mode", selected_refer_mode)
        elif select_tts == "edge_tts":
            config_input("Edge TTS Voice", "edge_tts.voice")