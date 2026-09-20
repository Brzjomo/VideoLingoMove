import streamlit as st
import os, sys, time
import easy_util as eu

eu.ensure_utf8_console()
# 关掉/刷新网页时 Windows 的 asyncio 会假报一段 ConnectionResetError（看着像崩了，
# 实际是清理已断连接时的假报错）。详见 easy_util.mute_windows_asyncio_reset_noise。
eu.mute_windows_asyncio_reset_noise()

from st_components.imports_and_utils import *
from st_components.task_runner import TaskRunner, StopTask
from core.config_utils import load_key, load_key_or
from core.step7_merge_sub_to_vid import STAGE_DONE_MARKER

# SET PATH
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in os.environ['PATH'].split(os.pathsep):
    os.environ['PATH'] += os.pathsep + current_dir
if current_dir not in sys.path:
    sys.path.append(current_dir)

SUB_VIDEO = "output/output_sub.mp4"

# 任务执行器在 session_state 里的键名
RUNNER_KEY = "_text_task_runner"


def subtitle_stage_finished():
    """字幕阶段是否已完成。

    不能只看 output_sub.mp4：当 resolution=0x0 且输入是真实视频时，我们
    **有意不生成**任何成片（省时间），此时若仍以成片存在为判据，界面会永远
    停在"开始处理字幕"按钮上。因此以 step7 写下的完成标记为准，
    成片存在只是附加条件（用于决定要不要嵌入播放器）。
    """
    return os.path.exists(STAGE_DONE_MARKER)


# ================================================================
# 任务步骤
# ================================================================
# 每一步都是无参可调用对象，由 TaskRunner 在后台线程里顺序执行。
# 长循环内部通过 eu.check_cancel() 响应暂停/停止（见 easy_util.check_cancel）。

def step_transcribe():
    step2_whisperX.transcribe()


def step_split_sentences():
    step3_1_spacy_split.split_by_spacy()
    # step3_2 内部按 llm_sentence_split 决定是否调 LLM
    step3_2_splitbymeaning.split_sentences_by_meaning()


def step_summarize():
    step4_1_summarize.get_summary()
    if load_key("pause_before_translate"):
        # 两段式流程：术语提取完成后停下来，等用户在页面上确认/编辑
        # output/log/terminology.json 或 custom_terms.xlsx 再继续。
        # 请求由 worker 线程发出（不触碰 session_state），UI 侧根据
        # paused_for_review 标记渲染确认界面。
        TaskRunner.request_review_pause()


def step_translate_and_burn():
    step4_2_translate_all.translate_all()
    step5_splitforsub.split_for_sub_main()
    step6_generate_final_timeline.align_timestamp_main()
    step7_merge_sub_to_vid.merge_subtitles_to_video()


def build_task_steps():
    """按当前配置组装步骤列表。"""
    transcription_only = load_key("transcription_only")
    steps = [
        ("转录（Whisper / 火山 ASR）", step_transcribe),
        ("断句（spaCy + LLM）", step_split_sentences),
    ]
    if transcription_only:
        # 直通模式不需要术语表，但下游会读该文件，这里保证它存在
        ensure_terminology_file()
    else:
        steps.append(("术语提取", step_summarize))
    steps.append(("翻译并压制字幕", step_translate_and_burn))
    return steps


def ensure_terminology_file():
    """确保 output/log/terminology.json 存在（直通模式与暂停点都需要）。"""
    import json
    terminology_file = "output/log/terminology.json"
    os.makedirs(os.path.dirname(terminology_file), exist_ok=True)
    if not os.path.exists(terminology_file):
        with open(terminology_file, 'w', encoding='utf-8') as f:
            json.dump({"topic": "", "terms": []}, f, ensure_ascii=False, indent=4)


def record_start_time():
    eu.start_time = time.time()


def read_time_duration():
    return eu.convert_seconds(eu.time_duration)


def reset_tokens():
    eu.prompt_tokens = 0
    eu.completion_tokens = 0
    eu.total_tokens = 0


# ================================================================
# 任务控制面板
# ================================================================
@st.fragment(run_every=1)
def task_control_panel():
    """进度条 + 暂停/继续/停止按钮，每秒自动刷新。"""
    runner = TaskRunner.get(st.session_state, RUNNER_KEY)

    if runner.state == "idle":
        return

    if runner.state in ("running", "paused"):
        if runner.total_steps:
            label = runner.current_label or "准备中"
            st.progress(
                runner.progress,
                text=f"第 {runner.current_step + 1}/{runner.total_steps} 步：{label}",
            )

    if runner.state == "running":
        c1, c2, _ = st.columns([1, 1, 4])
        with c1:
            if st.button("⏸️ 暂停", key="task_pause", width="stretch"):
                runner.pause()
                st.rerun(scope="app")
        with c2:
            if st.button("⏹️ 停止", key="task_stop", width="stretch"):
                runner.stop()
                st.rerun(scope="app")

    elif runner.state == "paused":
        if runner.paused_for_review:
            st.info("已提取术语，流程暂停中。请编辑 `output/log/terminology.json` 或 "
                    "`custom_terms.xlsx`，确认后点击下方按钮继续翻译。")
            c1, c2, _ = st.columns([1, 1, 4])
            with c1:
                if st.button("✅ 术语已确认，继续", key="task_resume_review",
                             type="primary", width="stretch"):
                    runner.resume()
                    st.rerun(scope="app")
            with c2:
                if st.button("⏹️ 放弃本次任务", key="task_stop_review",
                             width="stretch"):
                    runner.stop()
                    st.rerun(scope="app")
        else:
            st.warning(f"⏸️ 已暂停：{runner.current_label}")
            c1, c2, _ = st.columns([1, 1, 4])
            with c1:
                if st.button("▶️ 继续", key="task_resume", type="primary",
                             width="stretch"):
                    runner.resume()
                    st.rerun(scope="app")
            with c2:
                if st.button("⏹️ 停止", key="task_stop2", width="stretch"):
                    runner.stop()
                    st.rerun(scope="app")

    elif runner.state == "stopped":
        st.warning("⏹️ 任务已停止。已完成的步骤结果仍保留在 output/ 中，"
                   "重新开始时会自动跳过已完成的步骤。")
        if st.button("知道了", key="task_ack_stop"):
            runner.reset()
            st.rerun(scope="app")

    elif runner.state == "error":
        st.error(f"❌ 任务出错：{runner.error_msg}")
        if st.button("知道了", key="task_ack_error"):
            runner.reset()
            st.rerun(scope="app")

    elif runner.state == "completed":
        # 交给主区域渲染完成态（成功信息、播放器、下载按钮、归档按钮）
        runner.reset()
        st.rerun(scope="app")


def text_processing_section():
    # 检查是否只进行转录
    transcription_only = load_key("transcription_only")

    if transcription_only:
        header = "音频转录和生成原语言字幕"
        steps = """
        <p style='font-size: 20px;'>
        此阶段包含以下步骤：
        <p style='font-size: 20px;'>
            1. WhisperX 逐字转录<br>
            2. 使用 NLP 进行句子分割<br>
            3. 生成原语言字幕<br>
            4. 切割和对齐长字幕<br>
            5. 生成时间轴和字幕<br>
            6. 将字幕合并到视频中
        """
    else:
        header = "翻译和生成字幕"
        steps = """
        <p style='font-size: 20px;'>
        此阶段包含以下步骤：
        <p style='font-size: 20px;'>
            1. WhisperX 逐字转录<br>
            2. 使用 NLP 和 LLM 进行句子分割<br>
            3. 总结和多步翻译<br>
            4. 切割和对齐长字幕<br>
            5. 生成时间轴和字幕<br>
            6. 将字幕合并到视频中
        """

    st.header(header)
    with st.container(border=True):
        st.markdown(steps, unsafe_allow_html=True)

        runner = TaskRunner.get(st.session_state, RUNNER_KEY)

        # 运行中/暂停中：只显示控制面板
        if runner.state != "idle":
            task_control_panel()
            return

        if not subtitle_stage_finished():
            button_text = "开始生成字幕" if transcription_only else "开始处理字幕"
            if st.button(button_text, key="text_processing_button"):
                record_start_time()
                reset_tokens()
                runner.start(build_task_steps())
                st.rerun(scope="app")
        else:
            time_duration = read_time_duration()
            success_message = f"原语言字幕生成完成！耗时：{time_duration} " if transcription_only else f"字幕翻译完成！耗时：{time_duration} "
            st.success(success_message)
            # 有真实成片才嵌入播放器；resolution=0x0 时（除纯音频输入外）不产出成片，
            # 这里静默跳过，不再额外提示。
            if os.path.exists(SUB_VIDEO) and load_key("resolution") != "0x0":
                st.video(SUB_VIDEO)
            download_subtitle_zip_button(text="下载所有字幕")

            if st.button("归档到'历史记录'", key="cleanup_in_text_processing"):
                cleanup()
                st.rerun(scope="app")
            return


def cache_maintenance_section():
    """缓存清理入口。

    刻意独立于 text_processing_section()：即使还没导入素材（或本阶段已完成），
    用户也应该能清理缓存 —— 那正是"改了火山参数却没效果"时要做的事。
    """
    # 火山引擎二级缓存清理入口：调过火山参数后需要同时清掉
    # output/log/asr_results/，否则会命中旧结果（见 devdocs 已知问题 R5）
    asr_cache_dir = os.path.join("output", "log", "asr_results")
    if os.path.isdir(asr_cache_dir):
        cached = [f for f in os.listdir(asr_cache_dir) if f.endswith('.json')]
        if cached:
            with st.expander(f"🔧 火山 ASR 结果缓存（{len(cached)} 个文件）", expanded=False):
                st.caption(
                    "改变 `volcano_asr` 的参数（模型版本、标点、DDC 等）后，"
                    "这里的历史结果会被优先复用，导致「改了参数没效果」。"
                    "需要重跑时清理本缓存。"
                )
                if st.button("清空 ASR 结果缓存", key="clear_asr_cache_button"):
                    for f in cached:
                        try:
                            os.remove(os.path.join(asr_cache_dir, f))
                        except OSError as e:
                            st.warning(f"删除 {f} 失败: {e}")
                    st.success(f"已清理 {len(cached)} 个缓存文件")
                    st.rerun(scope="app")

    # 内容寻址的转录缓存（跨 output/ 清理存活，见
    # core/all_whisper_methods/transcription_cache.py）
    from core.all_whisper_methods import transcription_cache
    if transcription_cache.CACHE_DIR.is_dir():
        total = len(list(transcription_cache.CACHE_DIR.rglob("*.json")))
        if total:
            with st.expander(f"♻️ 转录缓存（{total} 个条目）", expanded=False):
                st.caption(
                    "按「源媒体内容 + ASR 设置」缓存识别结果。命中时会跳过"
                    "音频分离与识别，因此**不会**因为重新开始而重复计费。"
                    "换模型/换引擎/改识别语言会自动失效。"
                )
                if st.button("清空转录缓存", key="clear_transcription_cache_button"):
                    removed = transcription_cache.clear_cache()
                    st.success(f"已清理 {removed} 个缓存条目")
                    st.rerun(scope="app")


def subtitle_length_controls():
    """字幕长度调节面板。

    这两个键是最常被调的质量旋钮，此前只能手改 config.yaml。
    ⚠️ 控件宽度一律用新写法 `width="stretch"` / `width="content"`：
    `use_container_width` 自 streamlit 1.49 起弃用、2025-12-31 后移除（实测
    1.64 会往控制台打弃用警告）。本分支钉的是 `streamlit>=1.49.1`，所以
    `number_input` 也已经支持 `width=`，不必再为"1.38 不认 width"写兼容代码。
    """
    with st.expander("✂️ 字幕长度调节", expanded=False):
        st.caption("影响断行粒度与单行字数。改完立即写入 config.yaml。")

        c1, c2 = st.columns(2)
        with c1:
            max_split_length = st.number_input(
                "首次粗切词数上限 (max_split_length)",
                min_value=8, max_value=60,
                value=int(load_key_or("max_split_length", 20)),
                help="低于 18 会切得过碎影响翻译，高于 22 会让后续字幕对齐变难。默认 20。",
            )
        with c2:
            subtitle_cfg = load_key_or("subtitle", {}) or {}
            max_length = st.number_input(
                "单行最大字符数 (subtitle.max_length)",
                min_value=20, max_value=200,
                value=int(subtitle_cfg.get("max_length", 75)),
                help="每行字幕的字符上限。默认 75。",
            )

        c3, c4 = st.columns([1, 1])
        with c3:
            if st.button("保存", key="save_subtitle_length", type="primary",
                         width="stretch"):
                from core.config_utils import update_key
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
        with c4:
            if st.button("恢复默认 (20 / 75)", key="reset_subtitle_length",
                         width="stretch"):
                from core.config_utils import update_key
                update_key("max_split_length", 20)
                update_key("subtitle.max_length", 75)
                st.success("已恢复默认值")
                st.rerun(scope="app")


def main():
    st.set_page_config(page_title="VideoLingo", page_icon="assets/logo.svg")
    logo_col, _ = st.columns([1,1])
    with logo_col:
        # streamlit 1.49 起 st.image() 只认 width（int 或 "stretch"），
        # use_column_width 已被移除 —— 升级后传旧参数会直接 TypeError。
        # 注意 width 在旧版只接受 int，所以这行与新栈的 requirements.txt 绑定。
        st.image("assets/logo.png", width="stretch")
    st.markdown(button_style, unsafe_allow_html=True)
    st.markdown("<p style='font-size: 20px; color: #808080;'>你好，欢迎使用 VideoLingo。本项目目前正在建设中。如果遇到任何问题，请随时在 Github 上提问！你也可以访问我们的网站：<a href='https://videolingo.io' target='_blank'>videolingo.io</a></p>", unsafe_allow_html=True)
    # add settings
    with st.sidebar:
        page_setting()
        st.markdown(give_star_button, unsafe_allow_html=True)
    # 只有确实拿到素材才显示处理区块：否则"开始处理字幕"按钮会一直是可点的，
    # 点下去只会在 step1 抛 FileNotFoundError，用户看不出该做什么。
    if download_video_section():
        text_processing_section()
        subtitle_length_controls()
    else:
        st.info("请先在上方下载或上传一个视频/音频文件，然后再开始处理。")
    # 缓存清理入口与是否有素材无关，始终可用
    cache_maintenance_section()

if __name__ == "__main__":
    main()
