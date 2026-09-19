"""core 包初始化：在任何 core 子模块之前把运行期二进制目录接进本进程。

为什么必须放在包入口（而不是 step2_whisperX.py）
------------------------------------------------
`core/step2_whisperX.py` 的第 1 行就 `import whisperx`，而 whisperx 3.8 会
导入 torchcodec，torchcodec 在 Windows 上要求 FFmpeg 共享库已被
`os.add_dll_directory()` 注册。包入口先于其子模块执行，所以这里是唯一能
"早于 whisperx" 且不需要每个入口脚本各写一遍的挂载点。

失败不影响运行：拿不到 FFmpeg 时只留一句提示，真正需要它的 torchcodec
导入失败会由 installer.py --check 的 L1 冒烟测试给出明确诊断。
"""

from __future__ import annotations

# 先把控制台切成 UTF-8，再让任何 core 子模块有机会打印。
#
# 为什么必须在这里做（2026-09-20 实测）：`core/step2_whisperX.py` 在**模块级**就
# `rprint(f"🔧 whisperx {…}")`，而入口脚本里的 `ensure_utf8_console()` 往往排在
# import 之后 —— 例如 `batch/utils/gui.py` 的 import 在第 16–20 行、ensure 在第 29 行。
# 控制台不是 UTF-8 时（没走 `.bat` 的 `chcp 65001`：在 IDE 里直接跑、或手工
# `python -m streamlit run batch\utils\gui.py`），这一行直接抛
#
#     UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f527'
#
# 整个应用连首页都出不来。包入口先于一切子模块执行，这是唯一"绝对早于那句
# print"的挂载点；`ensure_utf8_console` 本身幂等，重复调用无害。
try:
    from easy_util import ensure_utf8_console as _ensure_utf8_console

    _ensure_utf8_console()
except Exception:  # pragma: no cover - 极端环境下不该拖垮主流程
    pass

try:
    from runtime_libraries import setup as _setup_runtime_libraries

    _setup_runtime_libraries()
except Exception as exc:  # pragma: no cover - 仅在极端环境下触发
    import sys as _sys

    print(f"[core] ⚠️ 运行期库目录接入失败（不影响非 FFmpeg 功能）：{exc}",
          file=_sys.stderr)
