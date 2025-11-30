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

1.安装CUDA（没有N卡也要装）。

- [CUDA ToolKit 12.6](https://developer.download.nvidia.com/compute/cuda/12.6.0/local_installers/cuda_12.6.0_560.76_windows.exe)
- [CUDNN 9.3.0](https://developer.download.nvidia.com/compute/cudnn/9.3.0/local_installers/cudnn_9.3.0_windows.exe)

2.添加 `C:\Program Files\NVIDIA\CUDNN\v9.3\bin\12.6` 到系统环境变量。

3.安装[Anaconda](https://www.anaconda.com/download)。

4.创建环境：

```shell
conda create -n videolingo python=3.10.0 -y
conda activate videolingo
conda install git -y
```

5.运行安装脚本（取决于网络，可能需要重试很久）：

```shell
python install.py
```

`torch-2.1.2+cu118-cp310-cp310-win_amd64.whl`文件（根据设备架构而定）可自行下载放于项目根目录，安装脚本会优先使用本地文件。

6.完毕后，运行`OneKeyStart.bat`启动服务。或者运行`StartBatch.bat`启动批量模式。

## 📄 许可证

本项目采用 Apache 2.0 许可证，我们衷心感谢以下开源项目的贡献：

[whisperX](https://github.com/m-bain/whisperX), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [json_repair](https://github.com/mangiucugna/json_repair), [BELLE](https://github.com/LianjiaTech/BELLE)

## 📬 官方联系方式

- Join our Discord: https://discord.gg/9F2G92CWPp
- Submit [Issues](https://github.com/Huanshere/VideoLingo/issues) or [Pull Requests](https://github.com/Huanshere/VideoLingo/pulls) on GitHub
- Follow me on Twitter: [@Huanshere](https://twitter.com/Huanshere)
- Email me at: team@videolingo.io
