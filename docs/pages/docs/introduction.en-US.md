# VideoLingo: Connecting the World, Frame by Frame

## 🌟 Overview ([Try VideoLingo Now!](https://videolingo.io))

VideoLingo is an all-in-one video translation, localization, and dubbing tool aimed at generating Netflix-quality subtitles. It eliminates stiff machine translations and multi-line subtitles while adding high-quality dubbing, enabling global knowledge sharing across language barriers.

Key features:
- 🎥 YouTube video download via yt-dlp

- **🎙️ Word-level subtitle recognition with WhisperX**

- **📝 NLP and GPT-based subtitle segmentation**

- **📚 GPT-generated terminology for coherent translation**

- **🔄 3-step direct translation, reflection, and adaptation for professional-level quality**

- **✅ Netflix-standard single-line subtitles only**

- **🗣️ Dubbing alignment with GPT-SoVITS and other methods**

- 🚀 One-click startup and output in Streamlit

- 📝 Detailed logging with progress resumption

Difference from similar projects: **Single-line subtitles only, superior translation quality, seamless dubbing experience**

## 🎥 Demo

<table>
<tr>
<td width="50%">

### Russian Translation
---
https://github.com/user-attachments/assets/25264b5b-6931-4d39-948c-5a1e4ce42fa7

</td>
<td width="50%">

### GPT-SoVITS Dubbing
---
https://github.com/user-attachments/assets/47d965b2-b4ab-4a0b-9d08-b49a7bf3508c

</td>
</tr>
</table>

### Language Support

**Input Language Support(more to come):**

🇺🇸 English 🤩 | 🇷🇺 Russian 😊 | 🇫🇷 French 🤩 | 🇩🇪 German 🤩 | 🇮🇹 Italian 🤩 | 🇪🇸 Spanish 🤩 | 🇯🇵 Japanese 😐 | 🇨🇳 Chinese* 😊

> *Chinese uses a separate punctuation-enhanced whisper model, for now...

**Translation supports all languages, while dubbing language depends on the chosen TTS method.**

## Installation

> **Windows users: one script is all you need.** Double-click `Install.bat` in the
> project root (or run `Install.bat` in a console). It prepares Python 3.11 and uv,
> creates a **project-local** virtual environment, picks the right torch CUDA build
> for your GPU (cu126/cu128/cu129), installs the dependencies and runs a health
> check. Nothing is written to the C: drive. Start the app with `OneKeyStart.bat`.

> **You do NOT need to install the CUDA Toolkit or cuDNN by hand.** The CUDA
> PyTorch wheels ship with the CUDA runtime they need; a recent GPU driver is
> enough. This differs from older versions (torch 2.1 + cu118).

> **FFmpeg**: version **4-7** is required. If a compatible build is already present,
> the installer skips the download entirely; otherwise it fetches a 7.x build into
> the project's `ffmpeg\` folder and wires it up at runtime, so **you never touch
> your PATH**. Please do not install "latest" via Chocolatey (that is 8.x now, which
> `torchcodec` does not support).

1. Clone the repository

```bash
git clone https://github.com/Huanshere/VideoLingo.git
cd VideoLingo
```

2. One-click install (recommended)

```bash
Install.bat
```

On a slow connection, fetch the download URLs first, grab the big files with a
download manager into `_downloads\`, then run it again:

```bash
Install.bat --download-only
Install.bat
```

Or do it by hand (this is what `Install.bat` does):

```bash
python setup_env.py --python 3.11    # project-local .venv + dependencies
python installer.py --check --smoke  # health check, imports whisperx/torchcodec/...
```

> The old conda flow still works: `conda create -n videolingo python=3.11` then
> `python installer.py`. All three launcher scripts prefer the project `.venv` and
> fall back to conda.

3. Start the application

```bash
OneKeyStart.bat
```

Or manually:

```bash
.venv\Scripts\python.exe launch.py
```

## API
The project supports OpenAI-Like API format and various dubbing interfaces:
- `claude-3-5-sonnet-20240620`, `gemini-1.5-pro-002`, `gpt-4o`, `qwen2.5-72b-instruct`, `deepseek-coder`, ... (sorted by performance)
- `azure-tts`, `openai-tts`, `siliconflow-fishtts`, `fish-tts`, `GPT-SoVITS`

For detailed installation, API configuration, and batch mode instructions, please refer to the documentation: [English](/docs/pages/docs/start.en-US.md) | [中文](/docs/pages/docs/start.zh-CN.md)

## Current Limitations

1. WhisperX transcription performance may be affected by video background noise, as it uses wav2vac model for alignment. For videos with loud background music, please enable Voice Separation Enhancement. Additionally, subtitles ending with numbers or special characters may be truncated early due to wav2vac's inability to map numeric characters (e.g., "1") to their spoken form ("one").

2. Using weaker models can lead to errors during intermediate processes due to strict JSON format requirements for responses. If this error occurs, please delete the `output` folder and retry with a different LLM, otherwise repeated execution will read the previous erroneous response causing the same error.

3. The dubbing feature may not be 100% perfect due to differences in speech rates and intonation between languages, as well as the impact of the translation step. However, this project has implemented extensive engineering processing for speech rates to ensure the best possible dubbing results.

4. **Multilingual video transcription recognition will only retain the main language**. This is because whisperX uses a specialized model for a single language when forcibly aligning word-level subtitles, and will delete unrecognized languages.

5. **Cannot dub multiple characters separately**, as whisperX's speaker distinction capability is not sufficiently reliable.

## 📄 License

This project is licensed under the Apache 2.0 License. Special thanks to the following open source projects for their contributions:

[whisperX](https://github.com/m-bain/whisperX), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [json_repair](https://github.com/mangiucugna/json_repair), [BELLE](https://github.com/LianjiaTech/BELLE)

## 📬 Contact Us

- Join our Discord: https://discord.gg/9F2G92CWPp
- Submit [Issues](https://github.com/Huanshere/VideoLingo/issues) or [Pull Requests](https://github.com/Huanshere/VideoLingo/pulls) on GitHub
- Follow me on Twitter: [@Huanshere](https://twitter.com/Huanshere)
- Email me at: team@videolingo.io

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Huanshere/VideoLingo&type=Timeline)](https://star-history.com/#Huanshere/VideoLingo&Timeline)

---

<p align="center">If you find VideoLingo helpful, please give us a ⭐️!</p>
