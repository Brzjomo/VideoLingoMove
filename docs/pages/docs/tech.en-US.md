**VideoLingo Video Translation System — Technical Documentation**

VideoLingo is a video translation / subtitle localisation tool. It automates video download, audio extraction, speech recognition, sentence segmentation, terminology extraction and translation, subtitle timeline generation, and finally burning the subtitles into the video. It also ships a web interface for task management and configuration.

> ⚠️ **The dubbing (TTS) pipeline has been removed.** Earlier versions supported Fish / OpenAI / Azure / Edge /
> SiliconFlow / GPT-SoVITS voice engines. All of that code (`core/tts_*`, `core/step8_*`…`core/step12_*`) is gone,
> and the project now **only produces subtitle files**.

For developers, each `step*_*.py` file under `core/` can be executed individually, and the artefacts of each step can be inspected under `output/`.

## Tech stack

| Layer | Choice |
| --- | --- |
| Runtime | Python 3.10–3.13 (3.11 recommended), project-local `.venv` created by uv |
| ASR | WhisperX 3.8 + faster-whisper (local), or Volcengine large-model ASR (cloud) |
| NLP | spaCy 3.8 (multilingual models) |
| LLM | any OpenAI-compatible endpoint (translation / splitting / terminology) |
| Media | FFmpeg 4–7 (**shared build**) + torchcodec |
| Web | Streamlit ≥1.49 |

## Core modules

1. **Video acquisition and audio extraction**
   - `core/step1_ytdlp.py`: downloads with `yt-dlp` (cookies / proxy supported), sanitises filenames, probes duration
     with `ffprobe`. Audio input is tracked via an `output/input_manifest.json` manifest instead of being wrapped
     into `black_screen.mp4`.
   - `core/all_whisper_methods/whisperX_utils.py`: audio extraction/compression (`convert_video_to_audio()`,
     `compress_audio()`) and `split_audio()` segmentation.

2. **Speech recognition**
   - `core/step2_whisperX.py`: ASR orchestration with two engines:
     - local WhisperX (`transcribe_with_whisper()`, faster-whisper backend)
     - Volcengine large-model ASR (`transcribe_with_volcano()`, async submit/query + TOS object storage)
     - both normalised into WhisperX-style `{"segments": [...]}`; supports Demucs vocal separation and
       loudness normalisation based on measured levels.
   - `core/all_whisper_methods/transcription_cache.py`: **content-addressed** transcription cache
     (media content + ASR settings + package versions). A `complete` hit skips separation *and* recognition.
   - `core/all_whisper_methods/volcano_asr.py`, `tos_service.py`: Volcengine client and TOS upload/cleanup (singleton).
   - `core/all_whisper_methods/demucs_vl.py`: Demucs vocal/accompaniment separation.

3. **Text processing and translation**
   - `core/step3_1_spacy_split.py`: initial spaCy sentence splitting.
   - `core/step3_2_splitbymeaning.py`: LLM-based semantic re-splitting (dual-candidate CoT); in
     transcription-only mode the whole stage can be disabled via the `llm_sentence_split` toggle, falling back to
     mechanical punctuation splitting.
   - `core/step4_1_summarize.py`: summarises the content and extracts a terminology table.
   - `core/step4_2_translate_all.py`: concurrent batch translation with caching and back-filling.
   - `core/translate_once.py`: single translation pass (literal → reflect → free, a three-step method).
   - `core/subtitle_trim.py`, `core/estimate_duration.py`: estimate reading time and compress over-long translations.

4. **Subtitle splitting, timeline and final render**
   - `core/step5_splitforsub.py`: splits subtitles to the Netflix single-line length rule and aligns
     word-level to sentence-level inside each cue.
   - `core/step6_generate_final_timeline.py`: emits `src.srt` / `trans.srt` / `src_trans.srt` / `trans_src.srt`.
   - `core/step7_merge_sub_to_vid.py`: burns hard subtitles with ffmpeg (`h264_nvenc` when available);
     `resolution: '0x0'` skips burning, and audio-only input is copied verbatim to `output_sub.mp4`.
   - `core/json_to_subtitle.py`, `core/onekeycleanup.py`: SRT helpers and artefact archiving/cleanup.

5. **LLM layer**
   - `core/ask_gpt.py`: unified OpenAI-compatible call wrapper (base_url normalisation, timeouts, retries,
     `output/gpt_log/` cache and token accounting).
   - `core/prompts_storage.py`: central prompt templates.
   - `core/config_utils.py`: thread-safe YAML config access, env-var overrides (`VIDEOLINGO_<KEY>`),
     and a single source-language resolution entry point.

6. **NLP toolkit**
   - `core/spacy_utils/`
     - `split_by_connector.py`: split on connectors.
     - `split_by_comma.py`: split on commas/colons.
     - `split_long_by_root.py`: split over-long sentences at the root node.
     - `split_by_mark.py`: split on punctuation.
     - `load_nlp_model.py`: load and initialise spaCy models per language.

7. **Web interface**
   - `st.py`: the Streamlit app; `build_task_steps()` assembles the step list and `TaskRunner` executes it on a
     background thread with pause/resume/stop and live progress.
   - `st_components/task_runner.py`: background executor and cancellation checkpoints.
   - `st_components/download_video_section.py`: YouTube download and local upload (idempotent).
   - `st_components/sidebar_setting.py`: sidebar settings (API presets, ASR engine, Volcengine params, TOS, resolution…).
   - `st_components/imports_and_utils.py`: shared UI helpers (subtitle zipping, default-subtitle selection).

8. **Batch mode**
   - `batch/utils/batch_processor.py`: batch processing driven by an Excel task table.
   - `batch/utils/video_processor.py`: the per-video flow download → transcribe → split → translate → burn → archive.
   - `batch/utils/batch_paths.py`: batch path helpers (auto-creates `batch/input`).
   - `batch/tasks_setting-template.xlsx`: task-table template (`Video File / Source Language / Target Language / Status`).
   - `AudioExtract/`: standalone batch audio-extraction tool.

9. **Install, health-check and cleanup**
   - `Install.bat` → `setup_env.py` → `installer.py`: one-click install (uv creates the project-local `.venv`,
     picks the torch CUDA backend from GPU compute capability, installs FFmpeg, runs a health check).
   - `launch.py`: pre-flight checks (key packages / ffmpeg / port) and writes run logs to `logs/`.
   - `cleanup.py` (`Cleanup.bat`): inventories and cleans old conda environments and scattered model caches.
   - `runtime_libraries.py`: wires the project-local FFmpeg DLL directory and PATH **before** torchcodec is imported.
   - `requirements.txt`: dependency pins; `config.yaml` (local, untracked) plus `config.example.yaml` (template) hold config.
   - `tests/`: standard-library `unittest` regression suite (9 files / 174 tests).

Through these modules VideoLingo automates the path from a video URL to a finished video with translated subtitles.
The modular design lets every step run and be debugged independently, and makes it easy to swap one link in the
chain — adding a new ASR engine, for example.
