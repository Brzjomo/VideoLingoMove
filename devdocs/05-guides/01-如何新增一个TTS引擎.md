---
title: 如何新增一个 TTS 引擎 —— ⚠️ 已失效（TTS 层已删除）
layer: 05-guides
source_files:
  - config.yaml
status: obsolete
last_verified: 2026-09-16
---

> # ⚠️ 本文已失效
>
> 重构 Round 1 已删除整个 TTS 适配层（`core/all_tts_functions/`）与配音链路（`step8~step12`）。
> **现在没有任何 TTS 引擎插槽可以扩展**，本文的 6 步清单全部无法执行。
>
> 上游 3.0.4 合并（`merge/upstream-3.0.4`）**刻意不合**整条配音链路，且其移植清单里
> 也没有恢复 TTS 的内容（见 [`06-上游3.0.4合并记录.md`](06-上游3.0.4合并记录.md) 第七节），
> 因此本文的失效结论在本次合并后依然成立——已按代码核实 `core/all_tts_functions/`
> 与 `core/step8_*`~`core/step12_*` 仍不存在。
>
> ⚠️ 正文（历史部分）里指向 `../../core/all_tts_functions/tts_main.py`、`edge_tts.py`、
> `siliconflow_fish_tts.py` 与 `core/step9_extract_refer_audio.py` 的链接**已无处可指**
> （这些文件已随配音链路删除，不再复原）。
>
> 若将来要重新引入 TTS：
> 1. 先按本文建立 `core/all_tts_functions/` 与 `tts_main(text, save_as, number, task_df)` 契约；
> 2. 但**不要**照抄原来的流水线结构——原 `step8_1/8_2/10/11` 的语速规划与时间轴算法存在多处已知缺陷，
>    历史文档（git 历史中的 `devdocs/02-pipeline/07-配音音频生成.md`）里有完整记录；
> 3. 恢复工作应从 [`04-已知问题与技术债.md`](04-已知问题与技术债.md) 的「重新加回配音链路」条目开始。

---

# 如何新增一个 TTS 引擎（历史文档）

以「新增一个假想的 `my_tts` 引擎」为例，给出**可直接照做的改动清单**。所有涉及的文件与函数名都来自现有代码的真实结构。

---

## 一、先理解适配层的契约

新引擎必须满足的唯一契约（由 `core/all_tts_functions/tts_main.py` 定义）：

```
输入：待合成文本 / 输出文件路径 / 字幕序号 / 任务 DataFrame
输出：把音频写到指定路径
```

`step10_gen_audio.process_row()` 是唯一的调用方：

```python
# core/step10_gen_audio.py:76-80（节选）
temp_file = TEMP_FILE_TEMPLATE.format(f"{number}_{line_index}")   # output/audio/tmp/<number>_<i>_temp.wav
tts_main(line, temp_file, number, tasks_df)
real_dur += get_audio_duration(temp_file)
```

因此：**写出的文件必须能被 `ffprobe`/ffmpeg 正确读取时长**，格式建议 wav（后续 `adjust_audio_speed()` 用 ffmpeg `atempo` 处理）。

---

## 二、改动清单（6 步）

### 步骤 1：新建引擎文件

路径：`core/all_tts_functions/my_tts.py`

参考最简单的现有实现 `edge_tts.py`（无需 API Key），或需要声音克隆时参考 `siliconflow_fish_tts.py`。

### 步骤 2：在 `tts_main.py` 注册分发

打开 `core/all_tts_functions/tts_main.py`，按**现有真实的分发写法**（读文件确认是 `if/elif` 链还是字典映射）加入你的分支。

> 📌 分发依据是 `load_key("tts_method")`，取值必须与 `config.yaml: tts_method` 的可选列表一致（该行的注释里列出了当前所有合法值）。

### 步骤 3：加配置段

在 `config.yaml` 的「Dubbing Settings」区域加入：

```yaml
my_tts:
  api_key: 'YOUR_KEY'
  voice: 'default'
```

⚠️ 若引擎需要 Key，**默认值用占位符字符串**（现有代码里用的是 `'YOUR_302_API_KEY'`、`'密钥'`），并在报错时给出明确提示。

### 步骤 4：（可选）在 UI 暴露选项

改 [`st_components/sidebar_setting.py`](../../st_components/sidebar_setting.py)：

- 找到 `tts_method` 对应的 `st.selectbox`，把 `my_tts` 加进 `options`；
- 若引擎有可调参数，按同文件中其它引擎的写法加控件，并用 `update_key` / `assign_key` 写回 `config.yaml`。

> 若不想改 UI，也可手改 `config.yaml: tts_method: 'my_tts'` 直接生效。

### 步骤 5：处理并发与限流

`core/step10_gen_audio.py:102` 的逻辑是：

```python
max_workers = load_key("max_workers") if load_key("tts_method") != "gpt_sovits" else 1
```

- 若你的引擎是**本地串行服务**（如 GPT-SoVITS 的本地 HTTP 服务），必须在此条件里加白名单，否则 1000 并发会把服务打挂；
- 若是**云端 API**，建议在引擎内部自己做重试与限流，而不是靠外层并发数（外层默认 1000 太高）。

### 步骤 6：（可选）需要参考音频时

如果引擎做声音克隆（fish / gpt_sovits 模式），它需要 `output/audio/refers/<number>.wav`。这个文件由 `step9_extract_refer_audio.py` 准备好，**无需你自己切**；引擎内按 `number` 参数拼路径读取即可。

---

## 三、必须自测的三件事

| 测试 | 方法 | 通过标准 |
| --- | --- | --- |
| 单条合成 | 写个最小脚本直接调用你的函数，输出到 `output/audio/tmp/test.wav` | 文件能播放、时长合理 |
| 端到端 | 删 `output/audio/tts_tasks.xlsx`、`output/audio/segs/`、`output/audio/tmp/`，重跑 step8~11 | 生成 `output/dub.mp3` 且能听清 |
| 时长对齐 | 跑完后 `output/dub.mp3` 的总时长应接近源视频时长（±3 秒内合理） | 见下节检查脚本 |

```python
# 时长对齐检查
from core.all_whisper_methods.whisperX_utils import get_audio_duration
print('原音频:', get_audio_duration('output/audio/raw.mp3'))
print('配音轨:', get_audio_duration('output/dub.mp3'))
```

---

## 四、常见坑

| 坑 | 说明 |
| --- | --- |
| 输出采样率不一致 | `step11` 会把所有分段统一处理为 16000 Hz 单声道后再拼（`process_audio_segment()` 用 ffmpeg 重采样），但**变速前**的时长统计依赖真实文件，若你的文件头信息不可靠会导致 `real_dur` 出错 |
| 输出文件头缺失时长 | 某些引擎返回的流式音频没有正确的 duration header，会让 `get_audio_duration()` 解析失败（它靠解析 ffmpeg stderr 的 `Duration:` 行） |
| 音色参数没接上 | 声音克隆类引擎的 `refer_mode` / `voice_id` 语义各不同，务必读现有实现，不要凭猜测写 |
| 忘记在 `imports_and_utils.py` 注册 | **不需要**！TTS 引擎不经过 `st_components/imports_and_utils.py`，它由 `tts_main` 内部导入 |

---

## 十、相关文档

- [`../03-subsystems/02-TTS引擎适配层.md`](../03-subsystems/02-TTS引擎适配层.md) — 现有 7 个引擎的对照表与统一契约
- [`../02-pipeline/07-配音音频生成.md`](../02-pipeline/07-配音音频生成.md) — 调用方 `step10` 的完整逻辑
- [`../04-interfaces/02-Streamlit界面组件.md`](../04-interfaces/02-Streamlit界面组件.md) — 侧边栏控件改法
- [`../04-interfaces/01-配置文件与参数.md`](../04-interfaces/01-配置文件与参数.md) — 加配置项的规范
