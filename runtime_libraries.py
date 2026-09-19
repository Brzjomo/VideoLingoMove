"""把项目内的二进制目录接进当前进程（Windows DLL 搜索路径 + PATH）。

为什么需要这个文件
------------------
torchcodec（whisperx 3.8 的新依赖）通过 **FFmpeg 共享库**解码音频，它在
Windows 上需要能找到 `avcodec-*.dll` 等文件。而 Python 3.8 起，
**扩展模块（.pyd）的依赖 DLL 不再从 PATH 解析**——只认
`os.add_dll_directory()` 注册的目录和 DLL 所在目录。所以：
  * 只把 `ffmpeg.exe` 放进 PATH 对 torchcodec **无效**；
  * 必须在 import torchcodec 之前调用 `os.add_dll_directory(<ffmpeg bin>)`。

上游 3.0.3 为此专门加了 runtime_libraries.py + core/__init__.py，本文件沿用
同样的思路，但多做了两件 dev 需要的事：
  1. 支持**项目内** `ffmpeg/` 目录（installer.py 现在把 FFmpeg 固定版解压到
     这里），避免依赖系统 PATH 与系统的 FFmpeg 大版本；
  2. 把项目内 ffmpeg 目录**前置**到 PATH，让 `subprocess` 调用 `ffmpeg` /
     `ffprobe` 的各处（下载、切分、压制）也用同一份、且版本受控。

被谁调用
--------
`core/__init__.py` 在导入 core 包时自动调用 `setup()`。因此
`import core.anything` 一定会先完成 DLL 注册。想让 torchcodec 单独冒烟测试时，
也可以先 `import runtime_libraries` 再 import torchcodec。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

# 项目内 FFmpeg 的候选位置：既支持"解压到 ffmpeg/ 根目录"，也支持
# "解压出 ffmpeg-7.1-full_build/bin/" 这种带一层子目录的形态。
_PROJECT_FFMPEG_DIRS = (
    _PROJECT_ROOT / "ffmpeg",
    _PROJECT_ROOT / "ffmpeg" / "bin",
)

_FFMPEG_SUBDIRS = ("bin", "lib", "")
_FFMPEG_HEADER = ("libavcodec/avcodec.h", "include/libavcodec/avcodec.h")

# 已注册的目录，避免重复 add_dll_directory（重复调用会累积句柄）
_REGISTERED_DLL_DIRS: set[str] = set()
_CONFIGURED = False


def _is_ffmpeg_dir(path: Path) -> bool:
    return (path / "ffmpeg.exe").is_file() or (path / "ffmpeg").is_file()


def _contains_ffmpeg_headers(path: Path) -> bool:
    return any((path / header).is_file() for header in _FFMPEG_HEADER)


def _iter_project_ffmpeg_candidates():
    """产出项目内 FFmpeg 的候选目录（bin 目录优先）。"""
    roots = list(_PROJECT_FFMPEG_DIRS)
    # 解压后常见一层 ffmpeg-<version>-<build>/ 子目录
    for parent in (_PROJECT_ROOT / "ffmpeg", _PROJECT_ROOT):
        if parent.is_dir():
            try:
                roots.extend(sorted(p for p in parent.iterdir() if p.is_dir()
                                    and p.name.lower().startswith("ffmpeg")))
            except OSError:
                pass
    seen = set()
    for root in roots:
        for sub in _FFMPEG_SUBDIRS:
            candidate = (root / sub) if sub else root
            key = str(candidate)
            if key not in seen and candidate.is_dir():
                seen.add(key)
                yield candidate


def find_project_ffmpeg_bin():
    """返回项目内可用的 FFmpeg bin 目录，找不到返回 None。"""
    for candidate in _iter_project_ffmpeg_candidates():
        if _is_ffmpeg_dir(candidate):
            return candidate
    return None


def find_ffmpeg_lib_dir():
    """返回含 FFmpeg **头文件**的目录（torchcodec 编译/查找时用）。"""
    for candidate in _iter_project_ffmpeg_candidates():
        if _contains_ffmpeg_headers(candidate):
            return candidate
    return None


def _add_dll_directory(path: Path):
    key = str(path)
    if key in _REGISTERED_DLL_DIRS:
        return
    if not hasattr(os, "add_dll_directory"):  # 非 Windows
        return
    try:
        os.add_dll_directory(key)
        _REGISTERED_DLL_DIRS.add(key)
    except (OSError, AttributeError):
        # 路径不存在或无权限：不影响进程启动，torchcodec 报错时另有明确提示
        pass


def _prepend_path(path: Path):
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if str(path) in parts:
        return
    os.environ["PATH"] = str(path) + (os.pathsep + current if current else "")


def _set_cuda_home_from_torch():
    """未显式设置 CUDA_HOME 时，用 torch 自带的 CUDA 目录兜底。

    ctranslate2 / faster-whisper 以及少数 torch 扩展会读 CUDA_HOME。装了
    CUDA 版 torch 却没有系统 CUDA Toolkit 的机器（本项目的目标场景）此前
    会拿到空值。
    """
    if os.environ.get("CUDA_HOME"):
        return
    try:
        from torch.utils.cpp_extension import CUDA_HOME  # noqa: N811
    except Exception:
        return
    if CUDA_HOME:
        os.environ["CUDA_HOME"] = str(CUDA_HOME)
        for sub in ("bin", "lib64", "lib"):
            candidate = Path(CUDA_HOME) / sub
            if candidate.is_dir():
                _add_dll_directory(candidate)


def setup(verbose: bool = False) -> dict:
    """把项目内二进制目录接进本进程。可重复调用（幂等）。

    返回一个描述"实际接入了什么"的字典，便于日志与体检打印。
    """
    global _CONFIGURED
    report = {
        "platform": sys.platform,
        "project_ffmpeg": None,
        "project_ffmpeg_lib": None,
        "system_ffmpeg": None,
        "dll_dirs": [],
    }

    project_bin = find_project_ffmpeg_bin()
    if project_bin is not None:
        report["project_ffmpeg"] = str(project_bin)
        _prepend_path(project_bin)  # 让 ffmpeg/ffprobe 子进程调用也走这一份
        _add_dll_directory(project_bin)

    lib_dir = find_ffmpeg_lib_dir()
    if lib_dir is not None:
        report["project_ffmpeg_lib"] = str(lib_dir)
        _add_dll_directory(lib_dir)
        os.environ.setdefault("FFMPEG_DIR", str(lib_dir))

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        report["system_ffmpeg"] = system_ffmpeg
        # 系统 FFmpeg 只在项目内没有时才需要注册（版本不受我们控制，
        # 但没有它时 torchcodec 至少还有机会导入成功）
        if project_bin is None:
            _add_dll_directory(Path(system_ffmpeg).resolve().parent)

    _set_cuda_home_from_torch()
    report["dll_dirs"] = sorted(_REGISTERED_DLL_DIRS)

    _CONFIGURED = True
    if verbose:
        print(f"[runtime] platform: {report['platform']}")
        print(f"[runtime] project ffmpeg: {report['project_ffmpeg'] or '（未使用）'}")
        print(f"[runtime] system ffmpeg: {report['system_ffmpeg'] or '（未找到）'}")
        print(f"[runtime] registered DLL dirs: {report['dll_dirs'] or '（无）'}")
    return report


def is_configured() -> bool:
    return _CONFIGURED


# 直接 `import runtime_libraries` 即完成接入 —— 与上游"导入即生效"的语义一致。
# core/__init__.py 也会显式调用 setup()，两条路径都幂等。
setup()

if __name__ == "__main__":
    setup(verbose=True)
