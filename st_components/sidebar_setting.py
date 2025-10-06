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
            lang = st.selectbox(
                "Recog Lang",
                options=list(langs.keys()),
                index=list(langs.values()).index(load_key("whisper.language"))
            )
            if langs[lang] != load_key("whisper.language"):
                update_key("whisper.language", langs[lang])

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