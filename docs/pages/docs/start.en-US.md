# 🚀 Getting Started

## 📋 API Configuration

This project needs **one LLM API only** — subtitle translation, sentence splitting and terminology extraction all go through it.

> ⚠️ **The dubbing (TTS) pipeline has been removed.** Earlier versions supported Azure / OpenAI / Fish /
> SiliconFlow / Edge / GPT-SoVITS voice engines. That whole pipeline is gone, and the project now
> **only produces subtitle files** — so no TTS API key is needed any more.

### Get an API_KEY for a Large Language Model

| Recommended Model | Recommended Provider | base_url | Price | Effect |
|:-----|:---------|:---------|:-----|:---------|
| gemini-2.0-flash-exp | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | $0.3 / 1M tokens | 🥳 |
| claude-3-5-sonnet-20240620 | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | $15 / 1M tokens | 🤩 |
| deepseek-coder | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | ¥2 / 1M tokens | 😃 |
| qwen2.5-coder:32b | [Ollama](https://ollama.ai) | http://localhost:11434 | Local | 😃 |

Note: any OpenAI-compatible endpoint works, so feel free to try other models. The pipeline involves multi-step
reasoning chains and complex JSON formats, so models **smaller than 30B are not recommended**.
For a fully local setup, set `max_workers` to 1 and lower `summary_length` in `config.yaml`.

## 🛠️ Quick Start

VideoLingo runs **primarily on Windows** (the Docker / Colab / macOS / Linux deployment artefacts have been removed).

> **Note: you do NOT need to install CUDA Toolkit or cuDNN by hand.**
> CUDA-enabled PyTorch wheels bundle the CUDA runtime they need; a recent GPU driver is enough.
> The installer picks the torch CUDA flavour (cu126 / cu128 / cu129 / CPU) **automatically from your GPU's
> compute capability**.

> **Note: FFmpeg must be major version 4–7 and a shared-library build** (`torchcodec` depends on it).
> The installer first checks for a compliant version already on your system and **skips the download** if it finds
> one; otherwise it installs a copy into the project-local `ffmpeg\` directory.
> Do not use `choco install ffmpeg` (that now installs 8.x, which `torchcodec` does not support).

1. Clone the project:

   ```bash
   git clone https://github.com/Huanshere/VideoLingo.git
   cd VideoLingo
   ```

2. 🎉 One-click install (recommended):

   ```bash
   Install.bat
   ```

   It prepares Python 3.11 and uv, creates a **project-local** `.venv`, selects the torch build from your GPU's
   compute capability, installs dependencies and runs a health check. **Anaconda is not needed**, and no tool
   beyond Git is required. It is safe to run repeatedly.

   On a poor network, fetch the addresses only, download the large files with your own tool into `_downloads\`,
   then re-run:

   ```bash
   Install.bat --download-only
   Install.bat
   ```

   Or do it by hand (equivalent to what `Install.bat` does):

   ```bash
   python setup_env.py --python 3.11      # create the project-local .venv and install deps
   python installer.py --check --smoke    # health check: imports whisperx/torchcodec/pyannote one by one
   ```

   > The legacy conda flow still works: `conda create -n videolingo python=3.11` then `python installer.py`.
   > All three launchers prefer the project-local `.venv` and fall back to conda.

3. Launch the app:

   ```bash
   OneKeyStart.bat
   ```

   Manual launch: `.venv\Scripts\python.exe launch.py`

4. Set the API key in the sidebar of the page that opens and start using it~

   ![tutorial](https://github.com/user-attachments/assets/983ba58b-5ae3-4132-90f5-6d48801465dd)

5. (Optional) More settings can be edited in `config.yaml`; watch the command-line output while it runs. To use
   custom terms, add them to `custom_terms.xlsx` before processing, e.g. `Baguette | French bread | Not just any bread!`.

## 🏭 Batch Mode (beta)

Documentation: [Chinese](/batch/README.zh.md)

## 🚨 Common Errors

1. **'All array must be of the same length' or 'Key Error' during translation**:
   - Reason 1: Weaker models have poor JSON format compliance causing response parsing errors.
   - Reason 2: LLM may refuse to translate sensitive content.
   Solution: Check `response` and `msg` fields in `output/gpt_log/error.json`, delete the `output/gpt_log` folder and retry.

2. **'Retry Failed', 'SSL', 'Connection', 'Timeout'**: Usually network issues. Solution: Users in mainland China please switch network nodes and retry.

3. **local_files_only=True**: Model download failure due to network issues; verify the network can reach `huggingface.co` (the app also probes the `hf-mirror.com` mirror).

4. **`torchcodec.decoders` fails to import**: your FFmpeg is a static build. Install a shared-library build; see above.
