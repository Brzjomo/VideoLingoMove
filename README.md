<div align="center">

<img src="/docs/logo.png" alt="VideoLingo Logo" height="140">

# Connect the World, Frame by Frame

[Website](https://videolingo.io) | [Documentation](https://docs.videolingo.io/docs/start)

[**English**](/README.md)｜[**中文**](/i18n/README.zh.md)

</div>

## 🌟 项目简介

VideoLingo 是一站式视频翻译本地化配音工具，能够一键生成 Netflix 级别的高质量字幕，告别生硬机翻，告别多行字幕，还能加上高质量的克隆配音，让全世界的知识能够跨越语言的障碍共享。

主要特点和功能：

- 🎥 使用 yt-dlp 从 Youtube 链接下载视频
- **🎙️ 使用 WhisperX 进行单词级时间轴字幕识别**
- **📝 使用 NLP 和 GPT 根据句意进行字幕分割**
- **📚 GPT 总结提取术语知识库，上下文连贯翻译**
- **🔄 三步直译、反思、意译，媲美字幕组精翻效果**
- **✅ 按照 Netflix 标准检查单行长度，绝无双行字幕**
- **🗣️ 使用 FishTTS 等方法对齐克隆配音**
- 🚀 整合包一键启动，在 streamlit 中一键出片
- 📝 详细记录每步操作日志，支持随时中断和恢复进度

与同类项目相比的优势：**绝无多行字幕，最佳的翻译质量，无缝的配音体验**

### 语言支持：

当前输入语言支持和示例：


| 输入语言 | 支持程度 | 翻译demo                                                                                  |
| -------- | -------- | ----------------------------------------------------------------------------------------- |
| 英语     | 🤩       | [英转中](https://github.com/user-attachments/assets/127373bb-c152-4b7a-8d9d-e586b2c62b4b) |
| 俄语     | 😊       | [俄转中](https://github.com/user-attachments/assets/25264b5b-6931-4d39-948c-5a1e4ce42fa7) |
| 法语     | 🤩       | [法转日](https://github.com/user-attachments/assets/3ce068c7-9854-4c72-ae77-f2484c7c6630) |
| 德语     | 🤩       | [德转中](https://github.com/user-attachments/assets/07cb9d21-069e-4725-871d-c4d9701287a3) |
| 意大利语 | 🤩       | [意转中](https://github.com/user-attachments/assets/f1f893eb-dad3-4460-aaf6-10cac999195e) |
| 西班牙语 | 🤩       | [西转中](https://github.com/user-attachments/assets/c1d28f1c-83d2-4f13-a1a1-859bd6cc3553) |
| 日语     | 😐       | [日转中](https://github.com/user-attachments/assets/856c3398-2da3-4e25-9c36-27ca2d1f68c2) |
| 中文*    | 😊       | [中转英](https://github.com/user-attachments/assets/48f746fe-96ff-47fd-bd23-59e9202b495c) |

> *中文需单独配置标点增强后的 whisper 模型，详见安装文档。但效果一般，因为 faster-whisper 加速的 whisper 失去了原有的好的断句，且识别得到的中文没有标点符号，难以断句。同样问题出现在日语上。

翻译语言支持所有语言，配音语言取决于选取的TTS。

## 改进

相对于原版在UX上有所改进：

- 支持一键切换模型API配置
- 记录并显示视频翻译的时间消耗和token消耗
- 优化打包字幕的名称
- 打包时额外包含视频转录文本，便于后续用于AI视频总结
- 支持qwen系列大模型
- 批量模式支持指定目录，而非必需拷贝至input目录
- 批量模式支持跳过已翻译的视频（存在同名srt字幕）
- 批量模式支持用户界面显示
- 支持韩语、葡萄牙语（其实抱脸上有模型，即可自行添加）
- 批量模式支持处理音频
- 支持批量提取视频的音频，便于远程部署使用
- 支持优先处理本地音频，配合设置工作时间段，实现忙时本地处理音频，闲时交互大模型进行翻译任务

## 安装

> 本节已按 **torch 2.8 / whisperx 3.8** 新栈更新。
> 与旧版最大的不同：**不再需要手工安装 CUDA Toolkit 与 cuDNN**，也不需要 conda ——
> CUDA 版 PyTorch 的 wheel 自带所需的 CUDA 运行库。
> 本项目只在 **Windows** 下运行（Docker / Colab 等产物已移除）。

### 最快路径：双击 `Install.bat`

```shell
Install.bat
```

它会自动完成下面全部步骤，可反复运行（已装好的会跳过）：

1. 准备 **Python 3.11**（3.10–3.13 也可）—— 依次尝试：复用已有 `.venv` → `py` 启动器
   → PATH 上的 python → 让 uv 下载 → `winget` 安装 → 下载官方安装包静默安装；
2. 安装 **uv**（用于在项目内建虚拟环境，不占 C 盘）；
3. 建 `<项目>\.venv`，把模型与下载缓存都指向项目内
   （`_model_cache\`、`.uv-cache\`、`_downloads\`），**整个项目可直接搬移**；
4. 安装依赖。**torch 的 CUDA 版本按显卡算力自动选择**，无需你指定：

   | 显卡 | 算力 | 选用的 torch 后端 |
   | --- | --- | --- |
   | RTX 50 系（Blackwell） | 12.x | cu129 |
   | RTX 20/30/40 系、A100、T4、V100 | 7.0–11.x | cu128 |
   | Quadro P2200、GTX 10 系等 Pascal/更老 | < 7.0 | cu126 |
   | 无 N 卡 | — | CPU 版（转录非常慢） |

   > 老卡必须走 cu126：CUDA 12.8/12.9 的 PyTorch 构建已移除 Pascal 及更老架构。

5. 体检 + 单元测试，并打印启动方式。

### 网络不佳？先下大文件，再安装

torch 的单个 wheel 有 **2.7–3.6 GB**。脚本支持"下载与安装分离"：

```shell
Install.bat --download-only     # 只打印文件名/下载地址/sha256/存放位置
# 用浏览器或下载工具把文件下好，放进项目内的 _downloads\ 目录
Install.bat                     # 再次安装：优先使用本地文件，不再联网
```

同样的机制也覆盖 FFmpeg 压缩包（`_downloads\ffmpeg-win64.zip`）与其余 pip 依赖
（`_downloads\python\`）。

### 手工步骤（等价于 Install.bat）

```shell
python setup_env.py --python 3.11      # 建项目内 .venv + 装依赖
python installer.py --check --smoke    # 体检：逐个 import whisperx/torchcodec/pyannote
```

装完启动：`OneKeyStart.bat`（会先体检再通过 `launch.py` 启动，日志写到 `logs\`）。
批量模式用 `batch\StartBatch.bat`。

三个 `.bat` 都会**优先使用项目内 `.venv`**，检测不到才回退到 conda 环境 `videolingo`
—— 旧的 conda 流程仍然可用：`conda create -n videolingo python=3.11` 后直接
`python installer.py`。

### FFmpeg

需要 **FFmpeg 4–7**（`torchcodec` 不支持 8/9）。**如果环境里已经有合规版本，安装
脚本会直接跳过下载**；没有才去取一份放进项目内 `ffmpeg\` 目录，运行期由
`runtime_libraries.py` 自动接入（不需要你改 PATH，也不需要重启终端）：

```shell
ffmpeg -version    # 确认大版本在 4–7 之间
```

## 📄 许可证

本项目采用 Apache 2.0 许可证，我们衷心感谢以下开源项目的贡献：

[whisperX](https://github.com/m-bain/whisperX), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [json_repair](https://github.com/mangiucugna/json_repair), [BELLE](https://github.com/LianjiaTech/BELLE)

## 📬 官方联系方式

- Join our Discord: https://discord.gg/9F2G92CWPp
- Submit [Issues](https://github.com/Huanshere/VideoLingo/issues) or [Pull Requests](https://github.com/Huanshere/VideoLingo/pulls) on GitHub
- Follow me on Twitter: [@Huanshere](https://twitter.com/Huanshere)
- Email me at: team@videolingo.io
