# VideoLingo devdocs —— 开发者接手总入口

> 本目录是给**开发者**看的工程文档（与面向用户的 `README.md`、面向用户的官网文档 `docs/` 区分开）。
> 目标：读完 `devdocs/README.md` + `devdocs/00-overview/` 即可理解全局；按需跳转到具体模块文档即可动手改代码。

- 项目：VideoLingo（视频翻译 / 本地化字幕工具），当前版本 `2.1.2`（`config.yaml`）
- 技术栈：Python 3.10 + Streamlit + WhisperX + spaCy + OpenAI 兼容 LLM API + FFmpeg
- 流水线：**单阶段字幕链路** step1 → step7（配音链路已在重构 Round 1 中整体删除）
- 文档生成基准：源码 `E:\VideoLingo\VideoLingoMove`，生成日期见各文档 front-matter 的 `last_verified`

> ⚠️ **重构 Round 1 已落地**：配音（Dubbing）功能已物理删除；密钥方案已改为 `config.example.yaml` 模板 + 本地 `config.yaml`（不入库）。
> 变更明细与**仍需人工处理的事项**（轮换密钥、清理 git 历史）见 [`05-guides/04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md)。
>
> 📌 **文档同步状态**：`README.md`、`00-overview/`、`05-guides/` 已完全同步到重构后的代码状态；
> `01-entrypoints/`、`02-pipeline/`、`03-subsystems/`、`04-interfaces/`、`06-batch-and-tools/` 中**凡涉及配音链路（step8~step12、TTS 引擎）的段落均已失效**，请以本文件和 `00-overview/` 为准，或参考 git 历史。

---

## 一、如果你是第一次接手，按这个顺序读

| 顺序 | 文档 | 你能得到什么 |
| --- | --- | --- |
| 1 | [`00-overview/01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) | 全局分层、进程模型、模块依赖图、目录职责表 |
| 2 | [`00-overview/02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) | 从视频到成片的每一步产物文件、字段、格式（最重要的一页） |
| 3 | [`00-overview/03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) | 环境搭建、启动、单步调试、断点续跑技巧 |
| 4 | [`02-pipeline/00-流水线总览.md`](02-pipeline/00-流水线总览.md) | step1~step7 的串行关系、依赖矩阵、跳过条件 |
| 5 | [`05-guides/04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md) | 重构 Round 1 做了什么、还剩什么、**你需要人工处理什么** |
| 6 | 按你要改的功能，进入 `02-pipeline/` 或 `03-subsystems/` 具体文档 | 实现细节、参数、扩展点 |

---

## 二、目录分层

```
devdocs/
├── README.md                     ← 你在这里（总入口 / 目录地图）
├── _meta/
│   └── 写作模板.md                ← 新增 devdocs 文档时必须遵守的规范
├── 00-overview/                  ← 全局视角（先读这里）
│   ├── 01-项目总览与架构.md
│   ├── 02-数据流与中间产物.md
│   ├── 03-快速上手与调试.md
│   └── 04-术语与概念表.md
├── 01-entrypoints/               ← 进程入口（谁会启动这套代码）
│   ├── 01-Streamlit主应用入口.md
│   └── 02-批量模式入口.md
├── 02-pipeline/                  ← 核心流水线，按 step 顺序
│   ├── 00-流水线总览.md
│   ├── 01-下载与音频提取.md       ← step1
│   ├── 02-语音识别ASR.md          ← step2
│   ├── 03-句子切分NLP.md          ← step3
│   ├── 04-术语总结与翻译.md       ← step4
│   ├── 05-字幕切分与时间轴.md     ← step5/step6
│   ├── 06-字幕压制与成片.md       ← step7
│   └── 07-配音音频生成.md         ← ⚠️ 已失效（step8~step11 已删除）
├── 03-subsystems/                ← 跨步骤的子系统（LLM / TTS / ASR / NLP）
│   ├── 01-LLM调用与提示词.md
│   ├── 02-TTS引擎适配层.md        ← ⚠️ 已失效（TTS 适配层已删除）
│   ├── 03-ASR引擎适配层.md
│   └── 04-NLP切分工具.md
├── 04-interfaces/                ← 对外接口与配置
│   ├── 01-配置文件与参数.md
│   ├── 02-Streamlit界面组件.md
│   └── 03-批处理任务系统.md
├── 05-guides/                    ← 动手改代码时的操作手册
│   ├── 01-如何新增一个TTS引擎.md   ← ⚠️ 已失效（无 TTS 适配层可扩展）
│   ├── 02-如何新增一个ASR引擎.md
│   ├── 03-如何新增一个流水线步骤.md
│   ├── 04-已知问题与技术债.md      ← 重构记录 + 剩余技术债
│   ├── 05-常见故障排查.md
│   ├── 06-上游3.0.4合并记录.md     ← 本次合并做了什么 / 拒绝了什么
│   ├── 07-合并后排错速查.md        ← 合并后遇到问题先查这里
│   └── 08-环境大升级方案.md        ← ⚠️ 待执行：torch 2.8 / whisperx 3.8 / uv + 项目内环境
└── 06-batch-and-tools/           ← 周边工具（批量音频提取、安装脚本等）
    ├── 01-批量音频提取工具.md
    └── 02-安装脚本与依赖.md
```

---

## 三、按任务查找（快速索引）

| 我想…… | 去看 |
| --- | --- |
| 搞清楚整个项目怎么跑起来的 | [`00-overview/01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) |
| 知道每一步产出了什么文件 | [`00-overview/02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) |
| 本地跑起来并调试 | [`00-overview/03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) |
| 改翻译质量 / 改提示词 | [`03-subsystems/01-LLM调用与提示词.md`](03-subsystems/01-LLM调用与提示词.md) |
| 增加一个 TTS 声音引擎 | [`05-guides/01-如何新增一个TTS引擎.md`](05-guides/01-如何新增一个TTS引擎.md) |
| 接入新的语音识别服务 | [`05-guides/02-如何新增一个ASR引擎.md`](05-guides/02-如何新增一个ASR引擎.md) |
| 在流程中间插入一个新步骤 | [`05-guides/03-如何新增一个流水线步骤.md`](05-guides/03-如何新增一个流水线步骤.md) |
| 改 Streamlit 页面/侧边栏 | [`04-interfaces/02-Streamlit界面组件.md`](04-interfaces/02-Streamlit界面组件.md) |
| 改批处理模式 | [`04-interfaces/03-批处理任务系统.md`](04-interfaces/03-批处理任务系统.md) |
| 加一个新的配置项 | [`04-interfaces/01-配置文件与参数.md`](04-interfaces/01-配置文件与参数.md) |
| 出 bug 了不知道从哪查 | [`05-guides/05-常见故障排查.md`](05-guides/05-常见故障排查.md) |
| 合并上游之后行为变了 / 想回退某一项 | [`05-guides/07-合并后排错速查.md`](05-guides/07-合并后排错速查.md) |
| 想知道上游合并都改了什么 | [`05-guides/06-上游3.0.4合并记录.md`](05-guides/06-上游3.0.4合并记录.md) |
| 要升级 torch/whisperx 环境（含显卡选型） | [`05-guides/08-环境大升级方案.md`](05-guides/08-环境大升级方案.md) |

---

## 四、全部文档索引

下表是当前 `devdocs/` 的完整清单（规模为字符数近似值，用于判断深挖程度）。

### 00-overview —— 全局视角（**先读这 4 篇**）

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-项目总览与架构.md`](00-overview/01-项目总览与架构.md) | ~12K | 技术栈、目录职责、进程模型、模块依赖图、两条主链路、与上游差异 |
| [`02-数据流与中间产物.md`](00-overview/02-数据流与中间产物.md) | ~16K | **最重要**：全部产物路径 / 字段 / 消费者表、`tts_tasks.xlsx` 列演进、三层时间映射、目录生命周期 |
| [`03-快速上手与调试.md`](00-overview/03-快速上手与调试.md) | ~11K | 环境要求、安装、启动、单步调试、重跑手法、清缓存、探针脚本 |
| [`04-术语与概念表.md`](00-overview/04-术语与概念表.md) | ~10K | 三种 chunk 的区分、配音专有名词、LLM 术语、文件命名约定 |

### 01-entrypoints —— 进程入口

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-Streamlit主应用入口.md`](01-entrypoints/01-Streamlit主应用入口.md) | ~40K | `main()` 页面结构、两阶段编排、隐式状态机、zip 打包规则、`easy_util` 全局状态 |
| [`02-批量模式入口.md`](01-entrypoints/02-批量模式入口.md) | ~45K | `StartBatch.bat` → `gui.py` → `BatchProcessor` 生命周期、目录切换、config 改写、失败重试 |

### 02-pipeline —— 核心流水线

| 文档 | 规模 | 对应步骤 |
| --- | --- | --- |
| [`00-流水线总览.md`](02-pipeline/00-流水线总览.md) | ~11K | **索引页**：step1~12 总表、依赖矩阵、跨步骤函数复用表 |
| [`01-下载与音频提取.md`](02-pipeline/01-下载与音频提取.md) | ~26K | step1 + Demucs + 音频格式转换 |
| [`02-语音识别ASR.md`](02-pipeline/02-语音识别ASR.md) | ~52K | step2：WhisperX / 火山引擎 / 分段算法 / 词级清洗 |
| [`03-句子切分NLP.md`](02-pipeline/03-句子切分NLP.md) | ~22K | step3：spaCy 四步链 + LLM 语义切分 |
| [`04-术语总结与翻译.md`](02-pipeline/04-术语总结与翻译.md) | ~28K | step4：术语表、三步翻译、并发与回填 |
| [`05-字幕切分与时间轴.md`](02-pipeline/05-字幕切分与时间轴.md) | ~33K | step5/step6：长度切分、词级→句级时间映射、SRT 生成 |
| [`06-字幕压制与成片.md`](02-pipeline/06-字幕压制与成片.md) | ~27K | step7/step12：ffmpeg 硬字幕压制、混音、占位视频 |
| [`07-配音音频生成.md`](02-pipeline/07-配音音频生成.md) | ~42K | step8~11：任务表、分块、参考音频、TTS、变速、拼接 |

### 03-subsystems —— 跨步骤子系统

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-LLM调用与提示词.md`](03-subsystems/01-LLM调用与提示词.md) | ~46K | `ask_gpt` 全流程、缓存机制、token 统计、8 个提示词函数逐条解析 |
| [`02-TTS引擎适配层.md`](03-subsystems/02-TTS引擎适配层.md) | ~51K | 7 个引擎统一契约与对照表、时长估算公式、新增引擎步骤 |
| [`03-ASR引擎适配层.md`](03-subsystems/03-ASR引擎适配层.md) | ~15K | whisper / volcano 实现对照、`VolcanoASR` 与 `TOSService` 逐方法、两级缓存、空 words 地雷 |
| [`04-NLP切分工具.md`](03-subsystems/04-NLP切分工具.md) | ~40K | `spacy_utils/` 逐函数、真实阈值与词表、新增语言清单 |

### 04-interfaces —— 对外接口与配置

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-配置文件与参数.md`](04-interfaces/01-配置文件与参数.md) | ~38K | `config.yaml` 全键表、`config_utils` API 语义、高级参数机制 |
| [`02-Streamlit界面组件.md`](04-interfaces/02-Streamlit界面组件.md) | ~45K | `st_components/` 三文件、49 行侧边栏控件↔config 键大表、API 预设切换机制 |
| [`03-批处理任务系统.md`](04-interfaces/03-批处理任务系统.md) | ~55K | 四个 batch 模块逐函数、任务表列约定、共享代码边界 |

### 05-guides —— 动手手册

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-如何新增一个TTS引擎.md`](05-guides/01-如何新增一个TTS引擎.md) | ~6K | 6 步改动清单 + 自测三件事 |
| [`02-如何新增一个ASR引擎.md`](05-guides/02-如何新增一个ASR引擎.md) | ~7K | 返回结构契约（最大的坑）、5 步清单、整段处理引擎的注意事项 |
| [`03-如何新增一个流水线步骤.md`](05-guides/03-如何新增一个流水线步骤.md) | ~6K | 设计前 4 问、5 步改动、产物契约的"替换式"改造 |
| [`04-已知问题与技术债.md`](05-guides/04-已知问题与技术债.md) | ~30K | **P0~P3 分级缺陷清单**，含复现条件与建议修法 |
| [`05-常见故障排查.md`](05-guides/05-常见故障排查.md) | ~19K | 按报错字符串检索的处置手册 + 诊断脚本 |
| [`06-上游3.0.4合并记录.md`](05-guides/06-上游3.0.4合并记录.md) | ~14K | 从上游 v3.0.4 移植了什么、修掉哪些真实缺陷、刻意拒绝了什么（含验证边界） |
| [`07-合并后排错速查.md`](05-guides/07-合并后排错速查.md) | ~9K | 症状 → 原因 → 确认命令 → 处置；含三个缓存位置的区分与回退方法 |
| [`08-环境大升级方案.md`](05-guides/08-环境大升级方案.md) | ~17K | ⚠️ **待执行方案**：torch 2.8 / whisperx 3.8 升级、显卡架构矩阵（cu126/cu128/cu129）、uv + 项目内环境、验证清单与回滚 |

### 06-batch-and-tools —— 周边工具

| 文档 | 规模 | 内容 |
| --- | --- | --- |
| [`01-批量音频提取工具.md`](06-batch-and-tools/01-批量音频提取工具.md) | ~23K | `AudioExtract/` 独立工具（ffmpeg 实现、串行、无跳过） |
| [`02-安装脚本与依赖.md`](06-batch-and-tools/02-安装脚本与依赖.md) | ~44K | `install.py` 8 阶段、依赖逐行、Docker 差异、16 项核对清单 |

> 规模列为字符数近似值（K = 千字符），用于判断深挖程度，不作为精确指标。
> 全部 30 份文档当前状态：`status: verified`（已对照源码核验），相对链接 300 条全部可解析。

---

## 五、阅读路径推荐（按角色）

| 角色 | 建议路径 |
| --- | --- |
| **接手者（第一天）** | [`00-overview/01`](00-overview/01-项目总览与架构.md) → [`00-overview/02`](00-overview/02-数据流与中间产物.md) → [`00-overview/03`](00-overview/03-快速上手与调试.md) → [`05-guides/04`](05-guides/04-已知问题与技术债.md)（重构记录 + 待人工处理项） |
| **想提高翻译质量** | [`03-subsystems/01`](03-subsystems/01-LLM调用与提示词.md) → [`02-pipeline/04`](02-pipeline/04-术语总结与翻译.md) → [`02-pipeline/05`](02-pipeline/05-字幕切分与时间轴.md) |
| **想换 ASR** | [`02-pipeline/02`](02-pipeline/02-语音识别ASR.md) → [`05-guides/02`](05-guides/02-如何新增一个ASR引擎.md) |
| **想改 UI** | [`01-entrypoints/01`](01-entrypoints/01-Streamlit主应用入口.md) → [`04-interfaces/02`](04-interfaces/02-Streamlit界面组件.md) |
| **想改批处理** | [`01-entrypoints/02`](01-entrypoints/02-批量模式入口.md) → [`04-interfaces/03`](04-interfaces/03-批处理任务系统.md) |
| **要修 bug** | [`05-guides/05`](05-guides/05-常见故障排查.md) → [`05-guides/04`](05-guides/04-已知问题与技术债.md) |

---

## 六、维护约定

1. 改代码时同步改文档：`source_files` 里列出的文件一旦行为变化，应更新对应文档的对应小节，并刷新 `last_verified`。
2. 新增文档必须遵守 [`_meta/写作模板.md`](_meta/写作模板.md)。
3. 中间产物路径、config 键名、函数名是文档里最容易过期的内容，review 时优先核对这三类。
4. 每份文档的 front-matter 里 `status: verified` 表示「已对照源码逐行核验」，`draft` 表示「待核验」。接手时优先信任 `verified`。
5. **重构后未同步的文档**：`01-entrypoints/`、`02-pipeline/`、`03-subsystems/`、`04-interfaces/`、`06-batch-and-tools/` 中涉及配音链路的部分已过期。更新时应删除或标注这些段落，而不要试图"修正"已被删除的代码描述。

---

## 七、git 跟踪状态（已在 Round 1 修复）

| 路径 | 状态 | 说明 |
| --- | --- | --- |
| `devdocs/**/*.md` | ✅ 现在**会**被跟踪 | `.gitignore` 在 `*.md` 之后追加了 `!devdocs/` 与 `!devdocs/**/*.md` |
| `config.yaml` | ✅ 已移出跟踪并被忽略 | `git rm --cached config.yaml`，配合 `.gitignore: config.yaml`。⚠️ 历史中的密钥仍需你轮换并清理 |
| `config.example.yaml` | ✅ 会被跟踪 | 密钥为占位符的模板，供新环境自举 |
| `batch/tasks_setting.xlsx` | ✅ 已移出跟踪并被忽略 | 运行期产物，每次「创建/更新任务配置」都从 `tasks_setting-template.xlsx` 复制生成 |
| `batch/tasks_setting-template.xlsx` | ✅ 会被跟踪 | 提交的模板（已移除 `Dubbing` 列） |
| `batch/utils/tos_manager.py` | ✅ 已删除（Round 2） | 与 `core/all_whisper_methods/tos_service.py` 重复且未接线；能力已合并进后者 |

验证命令：

```powershell
git check-ignore -v config.yaml batch/tasks_setting.xlsx   # 应命中 .gitignore
git check-ignore -v devdocs/README.md                      # 应输出 !devdocs/... 例外规则
git status --short                                          # devdocs/ 应显示为未跟踪（??）或已暂存
```

---

## 八、文档基准与已知偏差

1. **基准是「工作树」而非 git HEAD**。本目录所有文档的 `last_verified` 日期当天的工作树状态为准。Round 1 重构后工作树已大幅变化，**任何 commit 早于本次重构的默认值描述都可能过期**。
2. **审阅方式**：文档由多轮「读源码 → 写文档 → 交叉核验」产出，所有行号引用格式为 `文件路径:行号`，可直接跳转核对。发现不一致时以源码为准，并请顺手更新该文档。
3. **运行验证需用 conda 环境**：开发机上 PATH 里的 `python`（3.10）**没有安装 pandas** 等项目依赖，而 conda 环境 `videolingo` 里有。跑验证脚本请用：
   `& "$env:USERPROFILE\anaconda3\envs\videolingo\python.exe" <script>`
4. `config.yaml` 当前处于**调优状态**而非通用默认值：`transcription_only: true`、`resolution: '0x0'`、`demucs: false`。新接手者直接跑会看到"没有翻译、没有真实视频"。UI 已对 `resolution: 0x0` 加了显式警告。
5. **Windows 控制台编码**：直接 `python core/stepX.py` 需要 UTF-8 输出，否则打印 emoji 会崩。入口已调用 `easy_util.ensure_utf8_console()`；手工跑脚本时若仍报 `UnicodeEncodeError`，先设 `$env:PYTHONIOENCODING='utf-8'`。


