---
title: 如何新增一个 ASR 引擎
layer: 05-guides
source_files:
 - core/step2_whisperX.py
 - core/all_whisper_methods/whisperX_utils.py
 - core/all_whisper_methods/volcano_asr.py
 - core/all_whisper_methods/transcription_cache.py
 - core/config_utils.py
 - config.example.yaml
status: verified
last_verified: 2026-09-16
---

# 如何新增一个 ASR 引擎

现有两个引擎：`whisper`（本地 WhisperX）与 `volcano`（火山引擎在线 ASR）。本文给出增加第三个引擎（下称 `my_asr`）的完整改动路径，并指出最容易搞错的地方：**返回结构必须伪装成 WhisperX 的形状**。

> 📌 **引擎键名是 `asr_engine`，不要改名。** 上游 3.x 把它重命名成了 `whisper.runtime`，
> 本次合并**刻意不跟**：改名会破坏所有既有 `config.yaml`（见
> 上游合并记录第七节）。新增引擎时要改的是
> `asr_engine` 的可选值集合，不是键名。

---

## 一、先理解契约

### 1.1 引擎的调用契约

`core/step2_whisperX.py` 的 `transcribe_audio` 是唯一分发点（下列为真实结构，配置不完整时会回退到 whisper）：

```python
def transcribe_audio(audio_file: str, start: float, end: float) -> Dict:
 asr_engine = load_key("asr_engine")
 ...
 if asr_engine == "volcano":
 app_id = load_key("volcano_asr.app_id")
 access_token = load_key("volcano_asr.access_token")
 if not app_id or not access_token:
 rprint("[yellow]⚠️ 火山引擎ASR配置不完整，回退到Whisper引擎[/yellow]")
 asr_engine = "whisper"

 if asr_engine == "volcano":
 return transcribe_audio_with_volcano(audio_file, start, end)
 else:
 return transcribe_audio_with_whisper(audio_file, start, end)
```

- **输入**：一个音频文件路径 + 一个时间区间 `[start, end)`（秒）。注意：是**整段音频文件 + 区间**，不是已切好的小文件；引擎要么自己切片，要么整段处理。
- **输出**：一个 `dict`，**必须**含 `segments` 列表，结构见下。

### 1.2 返回结构契约（关键）

`transcribe` 把多个区间的结果合并后交给 `process_transcription`：

```python
combined_result = {'segments': []}
for result in all_results:
 combined_result['segments'].extend(result['segments'])
df = process_transcription(combined_result)
```

而 `process_transcription` 期望：

```python
for segment in result['segments']:
 for word in segment['words']:
 word.get("word", "") # 词文本
 word['start'], word['end'] # 秒；允许缺失（有补齐逻辑）
```

⚠️ **这是最容易踩的坑**：如果你的引擎只返回句子级（无词级 `words`），`process_transcription` 里 `segment['words']` 会直接 `KeyError`。两种应对：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A（推荐） | 在引擎内部做**词级强制对齐**（forced alignment），产出 `words` 列表 | 需要对齐模型（如 whisperx 的 align 模型） |
| B | 把每个句子包装成一个"只有一个 word"的 segment，`word` 用整句文本 | 丢失词级精度 → 下游 `step6` 的句级时间戳会退化为"整句起止"，字幕时间轴粗但可用 |
| C | 若引擎本身就返回句级时间戳，也可让 `segment` 只带 `start`/`end`/`text`，然后**改写 `process_transcription`** 增加兼容分支 | 改动扩散到公共函数，风险高 |

另外，`save_language(result['language'])` 依赖返回 dict 里含 `language` 键，两个分支各自调用一次：
Whisper 分支是 `core/step2_whisperX.py`（`save_language(result['language'])`），火山分支是
`core/step2_whisperX.py`（`save_language(result.get('language'))`）。请一并提供该键。

`save_language` 本体在 `core/all_whisper_methods/whisperX_utils.py`：只在拿到
非空且不等于 `'auto'` 的字符串时才写入 `whisper.detected_language`。**不要为了"有值可写"而伪造语言**
（例如自动检测失败时兜底 `'en'`）——`core/config_utils.py` 的 `get_source_language` 会把它当成真实源语言，
让提示词与 spaCy 模型全部用错语种。

### 1.3 缓存身份契约（新增，容易漏）

`transcribe` 会按内容寻址缓存每个分段的结果（`core/step2_whisperX.py`，缓存键由
`_asr_cache_settings` 构造，`core/step2_whisperX.py`）。新增引擎时**必须**把"会实质改变识别结果"
的参数加进这个字典，否则换了参数仍会命中旧缓存、表现为"改了参数没效果"。

| 现状 | 位置 |
| --- | --- |
| 公共字段：`asr_engine` / `model` / `language` / `demucs` | `core/step2_whisperX.py` |
| 引擎私有字段：仅当 `asr_engine == "volcano"` 时追加 `volcano` 子字典（`resource_id`、`language`、`enable_punc`、`enable_itn`、`enable_ddc`、`enable_speaker_info`、`show_utterances`、`enable_channel_split`、`vad_segment`、`model_version`） | `core/step2_whisperX.py` |
| 缓存目录 `.cache/asr/<key>/<part>.json`，`part = f"{start:.2f}_{end:.2f}"` | `core/all_whisper_methods/transcription_cache.py`、`core/step2_whisperX.py` |

新增引擎请照火山分支的写法加一个同构的 `if settings["asr_engine"] == "my_asr":` 段。
注意 `cache_key` 还会自动带上 `whisperx` / `faster-whisper` / `demucs` 的包版本
（`transcription_cache.py`）与源媒体 md5，这些无需你手动加。

---

## 二、改动清单（5 步）

### 步骤 1：实现引擎模块

新建 `core/all_whisper_methods/my_asr.py`，导出一个类（参考 [`volcano_asr.py`](../../core/all_whisper_methods/volcano_asr.py) 的 `VolcanoASR`）或一组函数。

必须处理的边界：

| 边界 | 参考现有实现 |
| --- | --- |
| 音频格式要求 | 火山要求 16 kHz / 单声道 / 16-bit PCM WAV，为此有 `convert_video_to_audio` 里的 `raw.wav` 分支与常量 `RAW_AUDIO_WAV_FILE`（`core/all_whisper_methods/whisperX_utils.py`） |
| 文件大小限制 | Whisper 有 25 MB 限制，为此有 `split_audio` 分段（`core/all_whisper_methods/whisperX_utils.py`） |
| 鉴权失败 | 抛明确异常，不要静默返回空结果（火山分支见 `core/all_whisper_methods/volcano_asr.py` 的 `transcribe_audio`） |
| 时间戳偏移 | 若你切了片，**必须把结果的时间戳加回 `start` 偏移**。Whisper 分支见 `core/step2_whisperX.py`；火山分支见 `core/all_whisper_methods/volcano_asr.py` 的 `result_start_offset` |
| 长循环可取消 | 分段循环已经由 `transcribe` 里的 `eu.check_cancel` 覆盖（`core/step2_whisperX.py`），但**引擎内部若有自己的长循环/轮询等待**（如上传 TOS 后轮询任务状态），也应在循环里调一次 `eu.check_cancel`，否则"停止"要等整段跑完 |
| Windows 控制台 | 若新模块带 `__main__` 且会打印 emoji，照现有 18 个模块的写法在入口调 `easy_util.ensure_utf8_console`（示例见 `core/step2_whisperX.py`），否则 `python -m core.all_whisper_methods.my_asr` 会抛 `UnicodeEncodeError` |

### 步骤 2：在 `step2_whisperX.py` 加分发

```python
# 参照现有结构，新增:
def transcribe_audio_with_my_asr(audio_file: str, start: float, end: float) -> Dict:
 ...
```

并在 `transcribe_audio` 中增加分支，同时处理「配置不完整时回退 whisper」的逻辑（现有 volcano 分支的做法见 `core/step2_whisperX.py`，推荐照抄这个模式）。

### 步骤 3：准备输入音频

`transcribe` 里有两条音频准备路径（`core/step2_whisperX.py`）：

```python
if asr_engine == "volcano":
 choose_audio = ...或 RAW_AUDIO_WAV_FILE
else:
 choose_audio = ...或 RAW_AUDIO_FILE
 whisper_audio = compress_audio(choose_audio, WHISPER_FILE)
```

如果你的引擎需要特定格式，在这里加一个分支准备音频，并复用 `whisperX_utils` 里已有的转换函数（或新增一个同风格的）。
注意 `raw.wav` 的 16 kHz/单声道/s16 是火山侧用 ffprobe 校验的硬契约，而 `raw.mp3` 必须**直接从源媒体**编码
（`core/all_whisper_methods/whisperX_utils.py`）——不要在新增分支里把它们改成从彼此转码。

### 步骤 4：加配置

```yaml
# ASR engine selection ["whisper", "volcano", "my_asr"]
asr_engine: 'whisper'

my_asr:
 api_key: ''
 language: ''
```

注意 `asr_engine` 在模板里的位置是 `config.example.yaml`（**不是** `config.yaml`）。
若你把合法取值写进注释，**改代码时同步更新这行注释与 `st_components/sidebar_setting.py` 的
`asr_engines` 字典**（两处都要改，否则 UI 无法选中新引擎）。

### 步骤 5：接入转录缓存

照 1.3 的写法在 `_asr_cache_settings`（`core/step2_whisperX.py`）里为新引擎补一段，
把"会改变识别结果"的参数写进去。漏掉这一步的后果是：改参数后仍命中旧缓存。自测方法见「四」。

---

## 三、如果引擎是"整段处理"怎么办

有些云端 ASR 接受整个音频文件并一次性返回全文时间轴（火山就是这种）。此时：

- 可以让 `transcribe` 只跑一个区间 `(0, duration)`，即绕过 `split_audio`——但 `transcribe` 当前结构是硬编码的循环 + 合并，需要加分支；
- 更省事的做法：仍然按现有循环调用，引擎内部**忽略 `start`/`end` 参数**、每次都对整段音频请求一次。⚠️ 这会产生 N 倍重复请求（现有 git 历史里有一条 `尝试修复音频重复转录的问题`，说明这个问题真实发生过）。**请务必避免**。

> 📌 若必须整段处理，建议在 `transcribe` 里显式加分支：`if asr_engine == 'my_asr': all_results = [transcribe_audio_with_my_asr(audio, 0, duration)] else: <现有循环>`。

---

## 四、自测清单

| 测试 | 命令 / 方法 |
| --- | --- |
| 单引擎冒烟 | 删 `output/log/cleaned_chunks.xlsx`，`python -m core.step2_whisperX` |
| 产物结构 | `python -c "import pandas as pd; d=pd.read_excel('output/log/cleaned_chunks.xlsx'); print(d.columns.tolist, len(d)); print(d.head)"` |
| 词级精度 | 观察 `cleaned_chunks.xlsx` 的 `start`/`end` 是否**逐词递增**且间隔合理（若全是一个句子的起止时间重复出现，说明退化成方案 B） |
| 下游连通 | 继续跑 step3~step6，确认 `step6` 不报 `未找到与句子匹配的内容` |
| 语言回写 | 检查 `config.yaml: whisper.detected_language` 是否被正确更新 |
| 缓存身份 | 缓存条目在 `.cache/asr/<key>/<part>.json`。改一个引擎参数后重跑，控制台**不应**再打印 `♻️ 复用缓存分段 ...`；若仍复用，说明该参数没进 `_asr_cache_settings` |
| 缓存单测 | `python -m unittest tests.test_transcription_cache -v`（12 例；其中 `test_engine_is_part_of_identity` / `test_volcano_params_are_part_of_identity` 正是这条契约的回归保护） |
| 整体回归 | `python -m unittest discover -s tests -v`（**242 例**，标准库 unittest，不需 pytest；实测约 86 秒） |

清理手段：UI 侧边栏底部的「♻️ 转录缓存」折叠区有「清空转录缓存」按钮
（`st.py` 的 `cache_maintenance_section` → `transcription_cache.clear_cache`）。

---

## 十、相关文档

- [`../02-pipeline/02-语音识别ASR.md`](../02-pipeline/02-语音识别ASR.md) — `step2` 的完整流程与分段算法
- [`../03-subsystems/03-ASR引擎适配层.md`](../03-subsystems/03-ASR引擎适配层.md) — whisper / volcano 的实现对照
- [`../00-overview/02-数据流与中间产物.md`](../00-overview/02-数据流与中间产物.md) — `cleaned_chunks.xlsx` 的字段契约
- `参考文档/` 目录 — 火山引擎 ASR 与 TOS 的原始 API 文档
