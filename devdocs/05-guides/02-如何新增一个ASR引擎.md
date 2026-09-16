---
title: 如何新增一个 ASR 引擎
layer: 05-guides
source_files:
  - core/step2_whisperX.py
  - core/all_whisper_methods/whisperX_utils.py
  - core/all_whisper_methods/volcano_asr.py
  - config.yaml
status: verified
last_verified: 2026-02-06
---

# 如何新增一个 ASR 引擎

现有两个引擎：`whisper`（本地 WhisperX）与 `volcano`（火山引擎在线 ASR）。本文给出增加第三个引擎（下称 `my_asr`）的完整改动路径，并指出最容易搞错的地方：**返回结构必须伪装成 WhisperX 的形状**。

---

## 一、先理解契约

### 1.1 引擎的调用契约

`core/step2_whisperX.py` 里的分发点：

```python
def transcribe_audio(audio_file: str, start: float, end: float) -> Dict:
    asr_engine = load_key("asr_engine")
    ...
    if asr_engine == "volcano":
        return transcribe_audio_with_volcano(audio_file, start, end)
    else:
        return transcribe_audio_with_whisper(audio_file, start, end)
```

- **输入**：一个音频文件路径 + 一个时间区间 `[start, end)`（秒）。注意：是**整段音频文件 + 区间**，不是已切好的小文件；引擎要么自己切片，要么整段处理。
- **输出**：一个 `dict`，**必须**含 `segments` 列表，结构见下。

### 1.2 返回结构契约（关键）

`transcribe()` 把多个区间的结果合并后交给 `process_transcription()`：

```python
combined_result = {'segments': []}
for result in all_results:
    combined_result['segments'].extend(result['segments'])
df = process_transcription(combined_result)
```

而 `process_transcription()` 期望：

```python
for segment in result['segments']:
    for word in segment['words']:
        word.get("word", "")      # 词文本
        word['start'], word['end']  # 秒；允许缺失（有补齐逻辑）
```

⚠️ **这是最容易踩的坑**：如果你的引擎只返回句子级（无词级 `words`），`process_transcription()` 里 `segment['words']` 会直接 `KeyError`。两种应对：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A（推荐） | 在引擎内部做**词级强制对齐**（forced alignment），产出 `words` 列表 | 需要对齐模型（如 whisperx 的 align 模型） |
| B | 把每个句子包装成一个"只有一个 word"的 segment，`word` 用整句文本 | 丢失词级精度 → 下游 `step6` 的句级时间戳会退化为"整句起止"，字幕时间轴粗但可用 |
| C | 若引擎本身就返回句级时间戳，也可让 `segment` 只带 `start`/`end`/`text`，然后**改写 `process_transcription()`** 增加兼容分支 | 改动扩散到公共函数，风险高 |

另外，**中文场景**下 `save_language(result['language'])` 依赖返回 dict 里含 `language` 键（`core/step2_whisperX.py:181`），请一并提供。

---

## 二、改动清单（5 步）

### 步骤 1：实现引擎模块

新建 `core/all_whisper_methods/my_asr.py`，导出一个类（参考 [`volcano_asr.py`](../../core/all_whisper_methods/volcano_asr.py) 的 `VolcanoASR`）或一组函数。

必须处理的边界：

| 边界 | 参考现有实现 |
| --- | --- |
| 音频格式要求 | 火山要求 16 kHz / 单声道 / 16-bit PCM WAV，为此专门有 `convert_to_volcano_wav()` 与 `RAW_AUDIO_WAV_FILE`（`whisperX_utils.py:27-49`） |
| 文件大小限制 | Whisper 有 25 MB 限制，为此有 `split_audio()` 分段（`whisperX_utils.py:131`） |
| 鉴权失败 | 抛明确异常，不要静默返回空结果 |
| 时间戳偏移 | 若你切了片，**必须把结果的时间戳加回 `start` 偏移**（Whisper 分支的做法见 `core/step2_whisperX.py:194-202`） |

### 步骤 2：在 `step2_whisperX.py` 加分发

```python
# 参照现有结构，新增:
def transcribe_audio_with_my_asr(audio_file: str, start: float, end: float) -> Dict:
    ...
```

并在 `transcribe_audio()` 中增加分支，同时处理「配置不完整时回退 whisper」的逻辑（现有 volcano 分支的做法见 `core/step2_whisperX.py:258-271`，推荐照抄这个模式）。

### 步骤 3：准备输入音频

`transcribe()` 里有两条音频准备路径（`core/step2_whisperX.py:346-364`）：

```python
if asr_engine == "volcano":
    choose_audio = ...或 RAW_AUDIO_WAV_FILE
else:
    choose_audio = ...或 RAW_AUDIO_FILE
    whisper_audio = compress_audio(choose_audio, WHISPER_FILE)
```

如果你的引擎需要特定格式，在这里加一个分支准备音频，并复用 `whisperX_utils` 里已有的转换函数（或新增一个同风格的）。

### 步骤 4：加配置

```yaml
# ASR engine selection ["whisper", "volcano", "my_asr"]
asr_engine: 'whisper'

my_asr:
  api_key: ''
  language: ''
```

注意 `config.yaml:36` 的注释里列出了合法取值，**改代码时同步更新这行注释**。

### 步骤 5：（可选）UI 暴露

在 [`st_components/sidebar_setting.py`](../../st_components/sidebar_setting.py) 里找到 `asr_engine` 的 `selectbox`，加入新选项；若引擎有可调参数，按同文件既有写法加控件。

---

## 三、如果引擎是"整段处理"怎么办

有些云端 ASR 接受整个音频文件并一次性返回全文时间轴（火山就是这种）。此时：

- 可以让 `transcribe()` 只跑一个区间 `(0, duration)`，即绕过 `split_audio()`——但 `transcribe()` 当前结构是硬编码的循环 + 合并，需要加分支；
- 更省事的做法：仍然按现有循环调用，引擎内部**忽略 `start`/`end` 参数**、每次都对整段音频请求一次。⚠️ 这会产生 N 倍重复请求（现有 git 历史里有一条 `尝试修复音频重复转录的问题`，说明这个问题真实发生过）。**请务必避免**。

> 📌 若必须整段处理，建议在 `transcribe()` 里显式加分支：`if asr_engine == 'my_asr': all_results = [transcribe_audio_with_my_asr(audio, 0, duration)] else: <现有循环>`。

---

## 四、自测清单

| 测试 | 命令 / 方法 |
| --- | --- |
| 单引擎冒烟 | 删 `output/log/cleaned_chunks.xlsx`，`python -m core.step2_whisperX` |
| 产物结构 | `python -c "import pandas as pd; d=pd.read_excel('output/log/cleaned_chunks.xlsx'); print(d.columns.tolist(), len(d)); print(d.head())"` |
| 词级精度 | 观察 `cleaned_chunks.xlsx` 的 `start`/`end` 是否**逐词递增**且间隔合理（若全是一个句子的起止时间重复出现，说明退化成方案 B） |
| 下游连通 | 继续跑 step3~step6，确认 `step6` 不报 `未找到与句子匹配的内容` |
| 语言回写 | 检查 `config.yaml: whisper.detected_language` 是否被正确更新 |

---

## 十、相关文档

- [`../02-pipeline/02-语音识别ASR.md`](../02-pipeline/02-语音识别ASR.md) — `step2` 的完整流程与分段算法
- [`../03-subsystems/03-ASR引擎适配层.md`](../03-subsystems/03-ASR引擎适配层.md) — whisper / volcano 的实现对照
- [`../00-overview/02-数据流与中间产物.md`](../00-overview/02-数据流与中间产物.md) — `cleaned_chunks.xlsx` 的字段契约
- `参考文档/` 目录 — 火山引擎 ASR 与 TOS 的原始 API 文档
