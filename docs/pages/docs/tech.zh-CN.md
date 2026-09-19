**VideoLingo 视频翻译系统技术文档**

VideoLingo 是一个视频翻译 / 字幕本地化工具：自动完成视频下载、音频提取、语音识别、句子切分、术语提取与翻译、字幕时间轴生成，最后可选地把字幕压制进视频。它也提供 Web 界面用于任务管理与系统配置。

> ⚠️ **配音（TTS）链路已移除。** 早期版本支持 Fish / OpenAI / Azure / Edge / SiliconFlow / GPT-SoVITS 等配音引擎，
> 现在 `core/tts_*`、`core/step8_*`~`core/step12_*` 与全部 TTS 相关代码已删除，本项目**只产出字幕文件**。

对于开发人员，可以单步执行 `core` 下的每一个 `step*_*.py` 文件，并在 `output/` 下检查每一步的产物。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 运行时 | Python 3.10–3.13（推荐 3.11），项目内 `.venv`（由 uv 创建） |
| ASR | WhisperX 3.8 + faster-whisper（本地）或 火山引擎大模型录音识别（云端） |
| NLP | spaCy 3.8（多语言模型） |
| LLM | 任何 OpenAI 兼容接口（翻译 / 断句 / 术语总结） |
| 媒体 | FFmpeg 4–7（**共享库构建**）+ torchcodec |
| Web | Streamlit ≥1.49 |

## 核心模块

1. **视频获取与音频提取**
   - `core/step1_ytdlp.py`：用 `yt-dlp` 下载视频（支持 cookies / 代理），清理文件名；用 `ffprobe` 取时长；
     音频输入走 `output/input_manifest.json` 清单，不再包装成 `black_screen.mp4`。
   - `core/all_whisper_methods/whisperX_utils.py`：音频抽取与压缩（`convert_video_to_audio()`、
     `compress_audio()`）以及 `split_audio()` 分段。

2. **语音识别**
   - `core/step2_whisperX.py`：编排 ASR，支持两个引擎：
     - 本地 WhisperX（`transcribe_with_whisper()`，faster-whisper 后端）
     - 火山引擎大模型录音识别（`transcribe_with_volcano()`，异步 submit/query + TOS 对象存储）
     - 两者结果统一成 WhisperX 风格的 `{"segments": [...]}`；支持 Demucs 人声分离与按实测电平归一化。
   - `core/all_whisper_methods/transcription_cache.py`：**内容寻址**的转录缓存（源媒体内容 + ASR 设置 + 包版本），
     命中 `complete` 条目时连音频分离与识别一起跳过。
   - `core/all_whisper_methods/volcano_asr.py`、`tos_service.py`：火山引擎客户端与 TOS 上传/清理（单例）。
   - `core/all_whisper_methods/demucs_vl.py`：Demucs 人声/伴奏分离。

3. **文本处理与翻译**
   - `core/step3_1_spacy_split.py`：spaCy 初步分句。
   - `core/step3_2_splitbymeaning.py`：用 LLM 按句意重新切分（双候选 CoT）；
     仅转录模式下可通过 `llm_sentence_split` 开关整体关闭，改走标点机械切分。
   - `core/step4_1_summarize.py`：总结内容并提取术语表。
   - `core/step4_2_translate_all.py`：并发批量翻译，带缓存与失败回填。
   - `core/translate_once.py`：单次翻译（直译 → 反思 → 意译三步法）。
   - `core/subtitle_trim.py`、`core/estimate_duration.py`：按朗读时长估算并压缩过长的译文。

4. **字幕切分、时间轴与成片**
   - `core/step5_splitforsub.py`：按 Netflix 单行长度规范切分字幕，并做词级→句级的段内对齐。
   - `core/step6_generate_final_timeline.py`：生成 `src.srt` / `trans.srt` / `src_trans.srt` / `trans_src.srt`。
   - `core/step7_merge_sub_to_vid.py`：用 ffmpeg 压制硬字幕（可选 `h264_nvenc`）；
     `resolution: '0x0'` 时跳过压制，纯音频输入则原样复制成 `output_sub.mp4`。
   - `core/json_to_subtitle.py`、`core/onekeycleanup.py`：SRT 工具与产物归档/清理。

5. **LLM 与提示词**
   - `core/ask_gpt.py`：统一的 OpenAI 兼容调用封装（base_url 归一化、超时、重试、`output/gpt_log/` 缓存与 token 统计）。
   - `core/prompts_storage.py`：集中管理各步骤的提示模板。
   - `core/config_utils.py`：YAML 配置的线程安全读写、环境变量覆盖（`VIDEOLINGO_<KEY>`）、源语言统一解析。

6. **自然语言处理工具集**
   - `core/spacy_utils/`
     - `split_by_connector.py`：按连接词拆分。
     - `split_by_comma.py`：按逗号/冒号拆分。
     - `split_long_by_root.py`：按句子根节点拆分过长句。
     - `split_by_mark.py`：按标点拆分。
     - `load_nlp_model.py`：按语言加载与初始化 spaCy 模型。

7. **界面**
   - `st.py`：Streamlit 主应用；`build_task_steps()` 组装步骤表，`TaskRunner` 在后台线程执行，支持暂停/继续/停止与实时进度。
   - `st_components/task_runner.py`：后台任务执行器与取消检查点。
   - `st_components/download_video_section.py`：YouTube 链接下载与本地文件上传（上传幂等）。
   - `st_components/sidebar_setting.py`：侧边栏配置界面（API 预设、ASR 引擎、火山参数、TOS、分辨率等）。
   - `st_components/imports_and_utils.py`：界面通用工具（字幕打包、默认字幕选择）。

8. **批量模式**
   - `batch/utils/batch_processor.py`：按 Excel 任务表批量处理。
   - `batch/utils/video_processor.py`：下载 → 转录 → 分句 → 翻译 → 压制 → 归档的单视频流程。
   - `batch/utils/batch_paths.py`：批量路径辅助（自动创建 `batch/input`）。
   - `batch/tasks_setting-template.xlsx`：任务表模板（`Video File / Source Language / Target Language / Status`）。
   - `AudioExtract/`：独立的批量音频提取小工具。

9. **安装、体检与清理**
   - `Install.bat` → `setup_env.py` → `installer.py`：一键安装（uv 建项目内 `.venv`、按显卡算力选 torch 后端、安 FFmpeg、体检）。
   - `launch.py`：启动前预检（关键包 / ffmpeg / 端口）并把运行日志写到 `logs/`。
   - `cleanup.py`（`Cleanup.bat`）：盘点并清理旧 conda 环境与散落在各处的模型缓存。
   - `runtime_libraries.py`：把项目内 FFmpeg 的 DLL 目录与 PATH 在 import torchcodec 之前接好。
   - `requirements.txt`：依赖清单；`config.yaml`（本地、不入库）与 `config.example.yaml`（模板）为配置。
   - `tests/`：标准库 `unittest` 回归测试（9 个文件 / 174 例）。

VideoLingo 通过这些模块协同，实现从视频下载到最终生成带翻译字幕成片的自动化流程。
模块化设计让每一步都能独立运行与调试，也便于替换其中某一环（例如新增一个 ASR 引擎）。
