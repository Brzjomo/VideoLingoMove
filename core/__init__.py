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

try:
    from runtime_libraries import setup as _setup_runtime_libraries

    _setup_runtime_libraries()
except Exception as exc:  # pragma: no cover - 仅在极端环境下触发
    import sys as _sys

    print(f"[core] ⚠️ 运行期库目录接入失败（不影响非 FFmpeg 功能）：{exc}",
          file=_sys.stderr)
