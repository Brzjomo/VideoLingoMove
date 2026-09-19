# 🚀 开始使用

## 📋 API 配置指南

本项目**只需要一个大模型 API**：字幕翻译、断句、术语总结都走它。

> ⚠️ **配音（TTS）功能已移除**：项目早期版本支持 Azure / OpenAI / Fish / SiliconFlow / Edge / GPT-SoVITS 等多种配音引擎，
> 现在整条配音链路已删除，本项目**只产出字幕文件**。因此不再需要任何 TTS 的 API key。

### 大模型的 API_KEY

| 推荐模型 | 推荐提供商 | base_url | 价格 | 效果 |
|:-----|:---------|:---------|:-----|:---------|
| gemini-2.0-flash-exp | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | $0.3 / 1M tokens | 🥳 |
| claude-3-5-sonnet-20240620 | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | $15 / 1M tokens | 🤩 |
| deepseek-coder | [302AI](https://gpt302.saaslink.net/C2oHR9) | https://api.302.ai | ¥2 / 1M tokens | 😃 |
| qwen2.5-coder:32b | [Ollama](https://ollama.ai) | http://localhost:11434 | 本地 | 😃 |

注：支持 OpenAI 格式接口，可自行尝试不同模型。但处理过程涉及多步思维链和复杂的 json 格式，**不建议使用小于 30B 的模型**。
完全本地化时请在 `config.yaml` 中把 `max_workers` 设为 1 并把 `summary_length` 调低。

## 🛠️ 快速上手

VideoLingo **主要在 Windows 上运行**（Docker / Colab / macOS / Linux 的部署产物已移除）。

> **注意：不需要手工安装 CUDA Toolkit 或 cuDNN。**
> CUDA 版 PyTorch 的 wheel 自带所需的 CUDA 运行库，只要显卡驱动足够新即可。
> torch 的 CUDA 版本（cu126 / cu128 / cu129 / CPU）由安装脚本**按显卡算力自动选择**。

> **注意：FFmpeg 需要大版本 4–7，且必须是带共享库的构建**（`torchcodec` 依赖它）。
> 安装脚本会先检查系统里已有的版本，**合规就跳过下载**；否则自动装一份到项目内 `ffmpeg\` 目录。
> 不要用 `choco install ffmpeg`（现在装到的是 8.x，`torchcodec` 不支持）。

1. 克隆项目：

   ```bash
   git clone https://github.com/Huanshere/VideoLingo.git
   cd VideoLingo
   ```

2. 🎉 一键安装（推荐）：

   ```bash
   Install.bat
   ```

   它会自动准备 Python 3.11 与 uv、在**项目内**建好 `.venv`、按显卡算力选择 torch 版本、
   安装依赖并做一次体检。**不需要 Anaconda**，也不需要 Git 之外的其他前置工具。可反复运行。

   网络不佳时，先只拿下载地址，用下载工具把大文件下好放进 `_downloads\`，再重跑：

   ```bash
   Install.bat --download-only
   Install.bat
   ```

   也可以手工来（等价于 `Install.bat` 做的事）：

   ```bash
   python setup_env.py --python 3.11      # 建项目内 .venv + 装依赖
   python installer.py --check --smoke    # 体检：逐个 import whisperx/torchcodec/pyannote
   ```

   > 旧版的 conda 流程仍然可用：`conda create -n videolingo python=3.11` 后运行 `python installer.py`。
   > 三个启动脚本都是「项目内 `.venv` 优先，conda 回退」。

3. 启动应用：

   ```bash
   OneKeyStart.bat
   ```

   手工启动：`.venv\Scripts\python.exe launch.py`

4. 在弹出网页的侧边栏中设置 API key，开始使用~

   ![tutorial](https://github.com/user-attachments/assets/983ba58b-5ae3-4132-90f5-6d48801465dd)

5. （可选）更多设置可以在 `config.yaml` 中手动修改，运行过程请注意命令行输出。如需使用自定义术语，请在处理前将术语添加到 `custom_terms.xlsx` 中，例如 `Biden | 登子 | 美国的瞌睡总统`。

## 🏭 批量模式（beta）

使用说明: [简体中文](/batch/README.zh.md)

## 🚨 常见报错

1. **翻译过程的 'All array must be of the same length' 或 'Key Error'**:
   - 原因1：弱模型遵循JSON格式能力较弱导致响应解析错误。
   - 原因2：对于敏感内容，LLM可能拒绝翻译。
   解决方案：检查 `output/gpt_log/error.json` 的 `response` 和 `msg` 字段，删掉 `output/gpt_log` 文件夹后重试。

2. **'Retry Failed', 'SSL', 'Connection', 'Timeout'**: 通常是网络问题。解决方案：中国大陆用户请切换网络节点重试。

3. **local_files_only=True**：网络问题引起的模型下载失败，需要确认网络能 ping 通 `huggingface.co`（程序也会自动探测 `hf-mirror.com` 镜像）。

4. **`torchcodec.decoders` 导入失败**：系统里的 FFmpeg 是静态构建。换成带共享库的构建即可，详见上文。

