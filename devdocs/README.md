# VideoLingo devdocs —— 开发者接手总入口

> 本目录是给**开发者**看的工程文档（与面向用户的 `README.md`、官网文档 `docs/` 区分开）。
> 目标：读完本文件 + `00-overview/` 即可理解全局；按需跳转到具体模块文档即可动手改代码。

- 项目：VideoLingo（视频翻译 / 本地化字幕工具），配置版本 `2.1.2`
- 技术栈：Python 3.10–3.13（推荐 3.11）+ torch 2.8.0 + WhisperX 3.8.6 + Streamlit ≥1.49 + spaCy 3.8 + OpenAI 兼容 LLM API + FFmpeg 4–7（共享库构建）
- 环境：**uv 建的项目内 `.venv`**（不再用 conda；`Install.bat` 一键装，`OneKeyStart.bat` 启动）
- 流水线：**单阶段字幕链路** step1 → step7（配音链路早在重构 Round 1 中整体删除，本次清理已把相关文档一并移除）
- 文档基准：源码 `E:\VideoLingo\VideoLingoMove`，`upgrade/env-torch28` 分支；各文档 front-matter 的 `last_verified` 为准

> ⚠️ **配音（Dubbing）功能不存在**：`core/step8_*`~`core/step12_*`、`core/all_tts_functions/`、`delete_retry_dubbing.py` 均已删除，
> 配置里的 `tts_method` / `dub_volume` / `min_subtitle_duration` / `tolerance` 也已移除。本文档目录里不再保留相关文档，
> 需要历史实现请查 git 历史（`git log --diff-filter=D -- 'core/all_tts_functions/*'`）。
>
> ⚠️ **密钥方案**：`config.example.yaml` 是占位符模板（入库），`config.yaml` 是本地文件（不入库，被 `.gitignore` 忽略）。
> 历史提交里曾包含真实密钥，**仍需你轮换密钥并清理 git 历史**，见 [`05-guides/04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md)。

---

## 一、如果你是第一次接手，按这个顺序读

| 顺序 | 文档 | 你能得到什么 |
| --- | --- | --- |
| 1 | [`00-overview/01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) | 全局分层、进程模型、模块依赖图、目录职责表 |
| 2 | [`00-overview/02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) | 从视频到成片的每一步产物文件、字段、格式（最重要的一页） |
| 3 | [`00-overview/03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) | 环境搭建、启动、单步调试、断点续跑技巧 |
| 4 | [`02-pipeline/00-流水线总览.md`](02-pipeline/00-流水线总览.md) | step1~step7 的串行关系、依赖矩阵、跳过条件 |
| 5 | [`06-batch-and-tools/02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) | 新环境怎么装起来：`Install.bat` / uv / 显卡算力→torch 后端矩阵 / FFmpeg 闸门 / 清理工具 |
| 6 | [`05-guides/04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md) | 还剩什么债、**你需要人工处理什么** |
| 7 | 按你要改的功能，进入 `02-pipeline/` 或 `03-subsystems/` 具体文档 | 实现细节、参数、扩展点 |

---

## 二、目录分层

```
devdocs/
├── README.md ← 你在这里（总入口 / 目录地图）
├── _meta/
│ └── 写作模板.md ← 新增 devdocs 文档时必须遵守的规范
├── 00-overview/ ← 全局视角（先读这里）
│ ├── 01-项目总览与架构.md
│ ├── 02-数据流与中间产物.md
│ ├── 03-快速上手与调试.md
│ └── 04-术语与概念表.md
├── 01-entrypoints/ ← 进程入口（谁会启动这套代码）
│ ├── 01-Streamlit主应用入口.md
│ └── 02-批量模式入口.md
├── 02-pipeline/ ← 核心流水线，按 step 顺序
│ ├── 00-流水线总览.md
│ ├── 01-下载与音频提取.md ← step1
│ ├── 02-语音识别ASR.md ← step2
│ ├── 03-句子切分NLP.md ← step3
│ ├── 04-术语总结与翻译.md ← step4
│ ├── 05-字幕切分与时间轴.md ← step5/step6
│ └── 06-字幕压制与成片.md ← step7
├── 03-subsystems/ ← 跨步骤的子系统（LLM / ASR / NLP）
│ ├── 01-LLM调用与提示词.md
│ ├── 03-ASR引擎适配层.md
│ └── 04-NLP切分工具.md
├── 04-interfaces/ ← 对外接口与配置
│ ├── 01-配置文件与参数.md
│ ├── 02-Streamlit界面组件.md
│ └── 03-批处理任务系统.md
├── 05-guides/ ← 动手改代码时的操作手册
│ ├── 02-如何新增一个ASR引擎.md
│ ├── 03-如何新增一个流水线步骤.md
│ ├── 04-已知问题与技术债.md ← 剩余技术债 + 待人工处理项
│ ├── 05-常见故障排查.md ← 按报错字符串检索
│ └── 07-症状速查.md ← 症状 → 原因 → 确认 → 处置
└── 06-batch-and-tools/ ← 周边工具
 ├── 01-批量音频提取工具.md
 └── 02-安装脚本与依赖.md
```

> 编号不连续（`03-subsystems/` 缺 02、`05-guides/` 缺 01/06/08）是**有意的**：
> 这些位置原先是 TTS 适配层、TTS 新增指南、上游合并记录、环境升级方案，
> 已随配音链路删除或随升级落地而合并进其它文档，不再保留空壳。

---

## 三、按任务查找（快速索引）

| 我想…… | 去看 |
| --- | --- |
| 搞清楚整个项目怎么跑起来的 | [`00-overview/01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) |
| 知道每一步产出了什么文件 | [`00-overview/02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) |
| 本地跑起来并调试 | [`00-overview/03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) |
| 装环境 / 换显卡 / 装不上依赖 | [`06-batch-and-tools/02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) |
| 想清理 C 盘空间 / 删旧 conda 环境与模型缓存 | [`06-batch-and-tools/02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) 的「清理工具」一节 |
| 改翻译质量 / 改提示词 | [`03-subsystems/01-LLM调用与提示词.md`](03-subsystems/01-LLM调用与提示词.md) |
| 接入新的语音识别服务 | [`05-guides/02-如何新增一个ASR引擎.md`](05-guides/02-如何新增一个ASR引擎.md) |
| 在流程中间插入一个新步骤 | [`05-guides/03-如何新增一个流水线步骤.md`](05-guides/03-如何新增一个流水线步骤.md) |
| 改 Streamlit 页面/侧边栏 | [`04-interfaces/02-Streamlit界面组件.md`](04-interfaces/02-Streamlit界面组件.md) |
| 改批处理模式 | [`04-interfaces/03-批处理任务系统.md`](04-interfaces/03-批处理任务系统.md) |
| 加一个新的配置项 | [`04-interfaces/01-配置文件与参数.md`](04-interfaces/01-配置文件与参数.md) |
| 出 bug 了不知道从哪查 | [`05-guides/05-常见故障排查.md`](05-guides/05-常见故障排查.md) |
| 行为变了 / 想快速定位某一类症状 | [`05-guides/07-症状速查.md`](05-guides/07-症状速查.md) |
| 跑回归测试 | `python -m unittest discover -s tests -v`，见 [`06-batch-and-tools/02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) |

---

## 四、全部文档索引

下表是当前 `devdocs/` 的完整清单（`规模`为字符数近似值，用于判断深挖程度）。

### 00-overview —— 全局视角（**先读这 4 篇**）

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) | ~12K | 技术栈、目录职责、进程模型、模块依赖图、单阶段字幕链路、与上游的关系 |
| [`02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) | ~16K | **最重要**：全部产物路径 / 字段 / 消费者表、三层时间映射、目录生命周期、"想重跑删什么" |
| [`03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) | ~13K | 环境要求（新栈）、安装、启动、单步调试、重跑手法、清缓存、探针脚本 |
| [`04-术语与概念表.md`](00-overview/04-术语与概念表.md) | ~12K | 三种 chunk 的区分、LLM 术语、任务执行器术语、文件命名约定 |

### 01-entrypoints —— 进程入口

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-Streamlit主应用入口.md`](01-entrypoints/01-Streamlit主应用入口.md) | ~42K | `main` 页面结构、`build_task_steps` 编排、`TaskRunner` 后台执行器、zip 打包规则、`easy_util` 全局状态 |
| [`02-批量模式入口.md`](01-entrypoints/02-批量模式入口.md) | ~38K | `StartBatch.bat` → `gui.py` → `BatchProcessor` 生命周期、目录切换、config 改写、产物归档 |

### 02-pipeline —— 核心流水线

| 文档 | 规模 | 对应步骤 |
| --- | --- | --- |
| [`00-流水线总览.md`](02-pipeline/00-流水线总览.md) | ~8K | **索引页**：step1~7 总表、依赖矩阵、跨步骤函数复用表 |
| [`01-下载与音频提取.md`](02-pipeline/01-下载与音频提取.md) | ~26K | step1 + Demucs + 音频格式转换 + `input_manifest` |
| [`02-语音识别ASR.md`](02-pipeline/02-语音识别ASR.md) | ~46K | step2：WhisperX / 火山引擎 / 分段算法 / 词级清洗 / 内容寻址缓存 |
| [`03-句子切分NLP.md`](02-pipeline/03-句子切分NLP.md) | ~24K | step3：spaCy 四步链 + LLM 语义切分 + `llm_sentence_split` 开关 |
| [`04-术语总结与翻译.md`](02-pipeline/04-术语总结与翻译.md) | ~22K | step4：术语表、三步翻译、并发与回填 |
| [`05-字幕切分与时间轴.md`](02-pipeline/05-字幕切分与时间轴.md) | ~26K | step5/step6：长度切分、词级→句级时间映射、SRT 生成 |
| [`06-字幕压制与成片.md`](02-pipeline/06-字幕压制与成片.md) | ~19K | step7：ffmpeg 硬字幕压制、`resolution: 0x0` 的三种行为、阶段完成标记 |

### 03-subsystems —— 跨步骤子系统

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-LLM调用与提示词.md`](03-subsystems/01-LLM调用与提示词.md) | ~39K | `ask_gpt` 全流程、缓存机制、token 统计、提示词函数逐条解析 |
| [`03-ASR引擎适配层.md`](03-subsystems/03-ASR引擎适配层.md) | ~17K | whisper / volcano 实现对照、`VolcanoASR` 与 `TOSService` 逐方法、两级缓存 |
| [`04-NLP切分工具.md`](03-subsystems/04-NLP切分工具.md) | ~29K | `spacy_utils/` 逐函数、真实阈值与词表、新增语言清单 |

### 04-interfaces —— 对外接口与配置

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-配置文件与参数.md`](04-interfaces/01-配置文件与参数.md) | ~30K | `config.example.yaml` 全键表、`config_utils` API 语义、环境变量覆盖 |
| [`02-Streamlit界面组件.md`](04-interfaces/02-Streamlit界面组件.md) | ~39K | `st_components/` 四个文件、侧边栏控件↔config 键大表、API 预设切换机制 |
| [`03-批处理任务系统.md`](04-interfaces/03-批处理任务系统.md) | ~42K | 四个 batch 模块逐函数、任务表列约定、状态机、共享代码边界 |

### 05-guides —— 动手手册

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`02-如何新增一个ASR引擎.md`](05-guides/02-如何新增一个ASR引擎.md) | ~8K | 返回结构契约（最大的坑）、改动清单、整段处理引擎的注意事项 |
| [`03-如何新增一个流水线步骤.md`](05-guides/03-如何新增一个流水线步骤.md) | ~7K | 设计前 4 问、5 步改动、产物契约的"替换式"改造 |
| [`04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md) | ~28K | 剩余技术债与验证盲区、**待人工处理项**、历史修复记录 |
| [`05-常见故障排查.md`](05-guides/05-常见故障排查.md) | ~21K | 按报错字符串检索的处置手册 + 诊断脚本 |
| [`07-症状速查.md`](05-guides/07-症状速查.md) | ~7K | 症状 → 原因 → 确认命令 → 处置；含三个缓存位置的区分 |

### 06-batch-and-tools —— 周边工具

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-批量音频提取工具.md`](06-batch-and-tools/01-批量音频提取工具.md) | ~16K | `AudioExtract/` 独立工具（ffmpeg 实现、串行、无跳过） |
| [`02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) | ~64K | `Install.bat` / `setup_env.py` / `installer.py` 主流程、显卡算力→torch 后端矩阵、FFmpeg 闸门、大文件下载分离、`Cleanup.bat`、依赖逐行 |

---

## 五、阅读路径推荐（按角色）

| 角色 | 建议路径 |
| --- | --- |
| **接手者（第一天）** | [`00-overview/01`](00-overview/01-项目总览与架构.md) → [`00-overview/02`](00-overview/02-数据流与中间产物.md) → [`00-overview/03`](00-overview/03-快速上手与调试.md) → [`06-batch-and-tools/02`](06-batch-and-tools/02-安装脚本与依赖.md)（把环境装起来） |
| **想提高翻译质量** | [`03-subsystems/01`](03-subsystems/01-LLM调用与提示词.md) → [`02-pipeline/04`](02-pipeline/04-术语总结与翻译.md) → [`02-pipeline/05`](02-pipeline/05-字幕切分与时间轴.md) |
| **想换 ASR** | [`02-pipeline/02`](02-pipeline/02-语音识别ASR.md) → [`05-guides/02`](05-guides/02-如何新增一个ASR引擎.md) |
| **想改 UI** | [`01-entrypoints/01`](01-entrypoints/01-Streamlit主应用入口.md) → [`04-interfaces/02`](04-interfaces/02-Streamlit界面组件.md) |
| **想改批处理** | [`01-entrypoints/02`](01-entrypoints/02-批量模式入口.md) → [`04-interfaces/03`](04-interfaces/03-批处理任务系统.md) |
| **要修 bug** | [`05-guides/05`](05-guides/05-常见故障排查.md) → [`05-guides/07`](05-guides/07-症状速查.md) → [`05-guides/04`](05-guides/04-已知问题与技术债.md) |

---

## 六、维护约定（**重要**）

1. **引用代码请用「文件 + 符号名」，不要写行号。**
 本项目曾经在 devdocs 里写了 2000+ 处 `文件:行号`，一次环境升级（`core/step2_whisperX.py` 从 549 行涨到 591 行）
 就让几乎每一处引用都失效，而文档表面仍写着 `status: verified`。
 现在的约定是：写 `core/step2_whisperX.py 的 transcribe`，不写 `core/step2_whisperX.py`。
 同理，**不要**在文件清单表里写"这个文件有多少行"，改记字符数量级。

 自查命令：

 ```powershell
 # 应无输出（只允许出现在 git 历史说明里）
 Select-String -Path devdocs\**\*.md -Pattern '\.(py|bat|yaml|toml|json|txt):\d'
 ```

2. 改代码时同步改文档：`source_files` 里列出的文件一旦行为变化，更新对应小节并刷新 `last_verified`。
3. 新增文档必须遵守 [`_meta/写作模板.md`](_meta/写作模板.md)。
4. 最容易过期的内容依次是：**中间产物路径 / config 键名 / 函数名 / 默认值**。review 时优先核对这四类。
5. front-matter 的 `status` 语义：`verified` = 已对照源码核验；`partially-obsolete` = 有已知失效段落；
 `draft` = 尚未核验。**改动代码后若没同步文档，请把状态降级，不要留着 `verified` 骗人。**

---

## 七、git 跟踪状态

| 路径 | 状态 | 说明 |
| --- | --- | --- |
| `devdocs/**/*.md` | ✅ 会被跟踪 | `.gitignore` 在 `*.md` 之后追加了 `!devdocs/` 与 `!devdocs/**/*.md` |
| `config.yaml` | ✅ 已移出跟踪并被忽略 | 配合 `.gitignore` 的 `config.yaml` 规则。⚠️ 历史中的密钥仍需轮换并清理 |
| `config.example.yaml` | ✅ 会被跟踪 | 密钥为占位符的模板，供新环境自举 |
| `batch/tasks_setting.xlsx` | ✅ 已移出跟踪并被忽略 | 运行期产物，每次「创建/更新任务配置」都从 `tasks_setting-template.xlsx` 复制生成 |
| `batch/tasks_setting-template.xlsx` | ✅ 会被跟踪 | 提交的模板，表头 4 列：`Video File / Source Language / Target Language / Status`（无 `Dubbing`） |
| `batch/utils/tos_manager.py` | ✅ 已删除 | 与 `core/all_whisper_methods/tos_service.py` 重复且未接线；能力已合并进后者 |
| `.venv/`、`.uv-cache/`、`.pip-cache/`、`_downloads/`、`ffmpeg/`、`_model_cache/` | ✅ 已忽略 | 环境大升级新增的项目内目录，保证"整个项目可搬移、不占 C 盘" |
| `Dockerfile`、`VideoLingo_colab.ipynb`、`docs/.../docker.*.md` | ✅ 已删除 | 本项目只在 Windows 下运行，不再维护容器 / Colab 产物 |

---

## 八、文档基准与已知偏差

1. **基准是「工作树」而非某个 commit**。每份文档的 `last_verified` 指做核验那天的源码状态；
 该日期之后的改动可能尚未同步，以源码为准并顺手更新。
2. 文档由多轮「读源码 → 写文档 → 交叉核验」产出。发现不一致时以源码为准。
3. **跑验证脚本请用项目环境**：`Install.bat` 会建出 `<项目>\.venv`，用它跑：
 `.venv\Scripts\python.exe <script>`。若还没装环境，旧的 conda 环境 `videolingo` 是**升级前的老栈**
 （torch 2.1.2 / streamlit 1.38 / spacy 3.7），**不能**用来跑本分支的 `st.py`；
 它只在"想跑不依赖新栈的单元测试"时还有用。
4. `config.yaml` 是本机文件，处于**调优状态**而非通用默认值，且行号会随编辑漂移 ——
 所以文档里一律引用键名（并用 `grep -n '键名' config.yaml` 定位），不引用行号。
5. **Windows 控制台编码**：直接 `python -m core.step2_whisperX` 需要 UTF-8 输出，否则打印 emoji 会崩。
 18 个带 `__main__` 的 core 模块已在入口调用 `easy_util.ensure_utf8_console`
 （`step3_1_spacy_split.py`、`step4_1_summarize.py` 例外）；手工跑脚本若仍报 `UnicodeEncodeError`，
 先设 `$env:PYTHONIOENCODING='utf-8'`。
