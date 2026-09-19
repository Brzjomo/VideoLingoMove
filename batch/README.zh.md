# VideoLingo Batch Mode

在使用批处理模式前，请确保你已经使用过 Streamlit 模式并正确设置了 `config.yaml` 中的参数。

> 也可以直接跑项目根目录的 `Install.bat` 把环境装好（批量模式与单次模式共用同一个环境）。
> `StartBatch.bat` 会按「项目内 `.venv` → conda 环境 `videolingo` → PATH 上的 python」的顺序找解释器。

## 使用方法

### 1. 准备视频文件

- 准备好要处理的视频文件（支持视频与音频）
- YouTube 链接可在之后填写
- 默认输入目录 `batch/input/` 若不存在会**自动创建**

### 2. 使用流程

1. 双击运行 `StartBatch.bat`，等待启动并加载界面。
2. 侧边栏设置跟普通模式类似。
3. 运行时会**自动检查 API 设置**（`batch/utils/gui.py` 的 `check_api()`）；失败时到**侧边栏**点 📡 重新检查。
4. 如果使用默认路径，会检查 batch 目录的 input 目录。如果运行后才复制视频到该目录，就刷新下。
5. 也可以指定目录，点击手动输入路径，输入后回车。会显示该目录检测到的支持的视频文件。
6. 在侧边栏确认好设置。
7. 点击“创建/更新任务配置”，会自动将视频信息输入到 `tasks_setting.xlsx` 文件。
8. 需要额外修改设置时，手动编辑 `tasks_setting.xlsx` 文件后保存。注意，此时不要再更新任务配置，会重置到默认状态。
9. 检查下方的任务详情，确认无误后，点击“开始批量处理”。
10. 等待处理完成。
11. 输出的双语字幕和转录文本将保存到输入文件夹，全部输出会存储在 `output` 目录。
12. 任务状态可在任务详情查看。

> 每次「开始批量处理」会把上一批产物**归档**到 `batch/output_archive_<时间戳>/`，而不是删掉。

### 3. 手动配置任务

编辑 `tasks_setting.xlsx` 文件（列名与顺序由 `batch/utils/batch_processor.py` 的建表语句决定）：

| 字段 | 说明 | 可选值 |
|------|------|--------|
| Video File | 视频文件名（无需 `input/` 前缀）或 YouTube 链接 | - |
| Source Language | 源语言 | 'en', 'zh', ... 或留空使用默认设置 |
| Target Language | 翻译语言 | 使用自然语言描述，或留空使用默认设置 |
| Status | 任务状态（由程序写入，一般不用手改） | 空 / `Processing...` / `Done` / `Error: ...` / `Preprocessed` / `Preprocess Failed` |

示例：

| Video File | Source Language | Target Language |
|------------|-----------------|-----------------|
| https://www.youtube.com/xxx | | German |
| Kungfu Panda.mp4 | | |

> 注意在处理视频时保持 `tasks_setting.xlsx` 关闭，否则会因占用无法写入而中断。
>
> ⚠️ 早期版本还有一列 `Dubbing`（是否配音）。**配音链路已删除**，该列也已从模板中移除，不再需要填写。

## 注意事项

### 中断处理

如果中途关闭命令行，`config.yaml` 中的语言设置可能会改变。重试前请检查设置。

### 错误处理

- 处理失败的文件会被移至 `batch/output/ERROR` 文件夹
- 错误信息记录在 `tasks_setting.xlsx` 的 `Status` 列
- 如需重试：
  1. 将 `ERROR` 下的单个视频文件夹移至根目录
  2. 重命名为 `output`
  3. 使用 Streamlit 模式重新执行

