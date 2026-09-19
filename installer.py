"""VideoLingo 安装与体检脚本（torch 2.8 / whisperx 3.8 新栈）。

本文件在 `upgrade/env-torch28` 分支上完成了环境大升级方案的第四节与第七节：
  * torch 2.1.2+cu118 → **torch 2.8.0**，CUDA 轮子按显卡算力自动选择
    （cu126 / cu128 / cu129 / cpu）；
  * Python 闸门放宽到 3.10–3.13（whisperx 3.8 的 requires_python）；
  * FFmpeg 改为**大版本闸门（4–7）+ 只下合规分支**，取代原来"永远下 latest"
    （latest 已是 8.1/9.0，而 torchcodec 0.7 只支持 4–7 —— 依据是 torchcodec
    自己的安装说明 "should work with FFmpeg versions in [4, 7]"）；
    下载地址不写死，改为运行时查 GitHub Releases API（可用
    VIDELINGO_FFMPEG_URL 覆盖）；
  * 新增 L1 import 冒烟（whisperx / torchcodec / pyannote / ctranslate2 …），
    其中 `torchcodec.decoders` 是 Windows 上最容易失败的一个 —— 冒烟前会先
    调用 `runtime_libraries.setup()` 注册 FFmpeg 的 DLL 目录，否则会把
    "没接 DLL 目录"误报成"FFmpeg 版本不对"；
  * `--python` 可以把整套安装**重定向到另一个解释器**（setup_env.py 建的
    项目内 `.venv`），实现"不碰 conda、不写 C 盘"。

从上游 3.x 移植并保留的修复（相对 dev 原 install.py）：
  1. 无 GPU / macOS 分支真的装 CPU 轮子（原实现打印 CPU 提示却执行 cu118 命令）。
  2. 不用已废弃的 pynvml 探测显卡，改解析 nvidia-smi。
  3. 不再无条件改写用户全局 pip index-url（需 --auto-mirror）。
  4. 依赖安装失败即非零退出，不再吞异常报"完成"。
  5. Linux 装 fonts-noto-cjk，而不是不含中日韩字形的 fonts-noto。
  6. 有重试、有体检、有安装状态指纹。

用法：
    python installer.py                        # 只安装/体检，不自动启动
    python install.py                          # 兼容入口 = installer.py --launch
    python installer.py --check                # 只体检，退出码 1 表示有错误
    python installer.py --check --smoke        # 体检 + 逐个 import 关键包
    python installer.py --torch-backend auto   # 按显卡自动选 CUDA 轮子（默认）
    python installer.py --torch-backend cu126  # 强制指定
    python installer.py --python <解释器路径>   # 在别的解释器里安装/体检
    python installer.py --dry-run              # 只打印将要执行的安装计划
    python installer.py --force                # 忽略状态指纹强制重装
    python installer.py --auto-mirror          # 显式允许自动切换 pip 镜像
    python installer.py --launch               # 装完启动应用（--no-launch 反之）
    python installer.py --download-only        # 只用外部工具下大文件：打印 URL 后退出
    python installer.py --download-dir D       # 指定大文件目录（默认 ./_downloads）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认是 GBK，本脚本大量使用 emoji 与中文，直接打印会抛
# UnicodeEncodeError（实测 `python installer.py --check --quiet` 会崩在打印 ❌ 上）。
# 复用 dev 已有的 easy_util.ensure_utf8_console()，与上游 ee38e19 同类修复。
# 必须放在任何输出之前。
try:
    from easy_util import ensure_utf8_console
    ensure_utf8_console()
except Exception:
    pass

# ---------------------------------------------------------------- 常量
# 新栈：与 requirements.txt 保持一致。whisperx 3.8.6 要求 torch~=2.8.0 /
# torchaudio~=2.8.0 / torchvision~=0.23.0，三者必须同批同源。
TORCH_VERSION = "2.8.0"
TORCHVISION_VERSION = "0.23.0"

# 大文件（torch 三件套、FFmpeg 压缩包）统一放这个目录。
#
# 存在的意义：torch 的单个 wheel 就有 2.7–3.6 GB，网络不佳时几乎必然下载失败。
# 所以把"下载"和"安装"解耦成两步 ——
#   1. 先运行 `python installer.py --download-only`，脚本会把**完整下载 URL**
#      和**应该存放的文件名**打印出来；
#   2. 用浏览器 / 迅雷 / aria2 等外部工具下好，放进本目录；
#   3. 重新运行安装脚本，它会**优先使用已存在的本地文件**，不再联网。
# 目录名以 "_" 开头，排序时靠前、便于手动找到；已在 .gitignore 中排除。
DEFAULT_DOWNLOAD_DIR = "_downloads"
# pip 下载缓存也指到这里，便于统一清理/搬移
PIP_DL_CACHE_DIR = os.path.join(DEFAULT_DOWNLOAD_DIR, ".pip-cache")
# uv 生成的锁定文件（带平台信息，不进版本库，每次安装重新生成）
UV_LOCK_FILE = os.path.join(DEFAULT_DOWNLOAD_DIR, "requirements.lock.txt")

# Python 闸门：whisperx 3.8.6 的 requires_python 是 >=3.10,<3.14。
# 默认 3.11（与方案一致）。3.12/3.13 也完全可用：Windows 上 av 17/18 的
# win_amd64 轮子里有 cp311-abi3（稳定 ABI，3.11+ 通用），并不存在"3.12/3.13
# 装不上 av"的问题 —— 方案第三节原先的推断已被 PyPI 元数据证伪。
PYTHON_MIN, PYTHON_MAX = (3, 10), (3, 14)
PYTHON_RECOMMENDED = (3, 11)

# 每个 CUDA 后端对应的 torch/torchaudio/torchvision 安装源与中文标签
TORCH_SPECS = {
    "cu126": ("https://download.pytorch.org/whl/cu126",
              "CUDA 12.6（Pascal/Volta 及更老架构的最后可用构建）"),
    "cu128": ("https://download.pytorch.org/whl/cu128",
              "CUDA 12.8（Ampere/Ada 首选，性能最好）"),
    "cu129": ("https://download.pytorch.org/whl/cu129",
              "CUDA 12.9（Blackwell 需要新驱动）"),
    "cpu": ("https://download.pytorch.org/whl/cpu",
            "CPU 版（转录会非常慢）"),
}
# 算力 → 后端。依据 pytorch#157517：CUDA 12.8/12.9 构建已移除
# Maxwell(5.x)/Pascal(6.x)/Volta(7.0)，sm_61 等老卡必须走 cu126。
COMPUTE_CAP_RULES = (
    (7.0, "cu126"),    # < 7.0：Pascal/Maxwell/Kepler
    (12.0, "cu128"),   # 7.0 ≤ cc < 12.0：Volta/Turing/Ampere/Ada/Hopper
    (float("inf"), "cu129"),  # ≥ 12.0：Blackwell
)

# torchcodec 0.7 只支持 FFmpeg 大版本 4–7。
#
# 依据是 torchcodec 自己的安装说明（PyPI 元数据原文）：
#   "TorchCodec with CUDA should work with FFmpeg versions in [4, 7]."
#   （若需要更新版本，它建议 `conda install "ffmpeg<8"`）
# 机制：torchcodec 为每个 FFmpeg 大版本单独编一个适配 DLL，0.7 只附带
# `libtorchcodec_core4..7.dll`，**没有 core8**。装了 8.x 共享库后
# `ffmpeg -version` 正常、`import torchcodec` 可能也过，但解码时会去找
# `libtorchcodec_core8.dll` 而失败。所以 8/9 一律不可用，不做兜底。
FFMPEG_MIN_MAJOR, FFMPEG_MAX_MAJOR = 4, 7
# 下载时优先选的 BtbN 分支 —— **只列合规的 4–7**。
#
# 策略（对应"默认装新版，但环境里已有可用版本就跳过下载"）：
#   * 安装前先看项目内 ffmpeg/、再看系统 PATH：只要有一份 **大版本 4–7 且带
#     共享库** 的 ffmpeg+ffprobe，就**完全跳过下载**；
#   * 确实需要下载时按这个序列挑资产，每个分支内**优先 `-shared`**。
#
# ⚠️ 这里**不再列 8.1 / 8.0**：它们永远过不了 `ffmpeg_major_ok()`，列进来只会
# 制造"能挑到 8.x"的假象。原实现就是因为列了 8.x 且挑资产时不校验版本，
# 在 `latest` tag 没有 7.x 时挑到 8.1 —— 下载 85 MB、解压，**最后**才报
# "大版本不合规"，安装必然失败（2026-09-19 实测）。`_pick_ffmpeg_asset()` 里
# 另有一道 `_ffmpeg_branch_ok()` 过滤兜底，防止将来有人又加回不合规的分支。
FFMPEG_WIN_BRANCHES = ("7.1", "7.0")
# 兼容旧名（文档/测试引用）
FFMPEG_WIN_BRANCH = FFMPEG_WIN_BRANCHES[0]
FFMPEG_DIR_NAME = "ffmpeg"
FFMPEG_ZIP_NAME = "ffmpeg-win64.zip"
FFMPEG_API_LATEST = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
FFMPEG_API_LIST = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=30"

STATE_FILE_NAME = ".videolingo-install.json"

# 体检时要求的包（导入名 -> pip 名提示）
REQUIRED_IMPORTS = {
    "streamlit": "streamlit",
    # 侧边栏 MODEL 搜索框用；已在 requirements.txt 里。缺了只会回退成普通文本框
    # （sidebar_setting.model_input()），但仍放进体检 —— 免得静默降级成
    # 「没有搜索框、也不知道为什么」。
    "streamlit_searchbox": "streamlit-searchbox",
    "torch": "torch",
    "torchaudio": "torchaudio",
    "whisperx": "whisperx",
    "spacy": "spacy",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "cv2": "opencv-python",
    "librosa": "librosa",
    "pydub": "pydub",
    "json_repair": "json-repair",
    "ruamel.yaml": "ruamel.yaml",
}

# L1 冒烟：新栈引入的包，逐个 import。torchcodec.decoders 单独放最后一组，
# 因为它依赖 FFmpeg 共享库，是 Windows 上最容易失败的一个。
SMOKE_IMPORTS_CORE = ("whisperx", "ctranslate2", "faster_whisper", "pyannote.audio")
SMOKE_IMPORTS_OPTIONAL = ("demucs.api", "torchcodec.decoders")


# ---------------------------------------------------------------- 输出
# 输出必须能在**没有 rich** 的情况下工作。
# 实测踩过：uv 建出的 venv 里没有 pip，导致引导安装 rich 失败，接着
# info() 自己 `from rich.console import Console` 抛 ModuleNotFoundError，
# 把真正的错误（No module named pip / No module named rich）盖掉了，
# 用户只看到一条莫名其妙的 Traceback。所以这里做纯文本兜底。
try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
except ImportError:  # 引导阶段
    _RichConsole = None
    _RichPanel = None

_MARKUP_TAGS = ("[bold]", "[/bold]", "[cyan]", "[/cyan]", "[green]", "[/green]",
                "[yellow]", "[/yellow]", "[red]", "[/red]", "[bright_black]",
                "[bright_blue]", "[/bright_blue]", "[bold cyan]", "[/bold cyan]",
                "[bold green]", "[/bold green]", "[bold magenta]",
                "[/bold magenta]")


def _plain(msg):
    """把 rich 标记去掉，得到可以直接 print 的纯文本。"""
    text = str(msg)
    for tag in _MARKUP_TAGS:
        text = text.replace(tag, "")
    return text


def _console():
    return _RichConsole() if _RichConsole is not None else None


def info(msg, style=None):
    console = _console()
    if console is None:
        print(_plain(msg), flush=True)
        return
    console.print(msg, style=style)


def panel(msg, style="cyan", title=None):
    if _RichPanel is None or _RichConsole is None:
        bar = "-" * 60
        print(f"{bar}\n{_plain(msg)}\n{bar}", flush=True)
        return
    _RichConsole().print(_RichPanel.fit(msg, style=style, title=title))


# ---------------------------------------------------------------- 基础工具
def run(cmd, retries=1, check=True, env=None):
    """执行命令，失败时按 3/6/9…秒退避重试。

    dev 原实现完全没有重试：网络抖动一次就整轮安装失败。
    """
    import time as _time
    for attempt in range(retries):
        result = subprocess.run(cmd, env=env)
        if result.returncode == 0:
            return result
        if attempt < retries - 1:
            delay = min(20, 3 * (attempt + 1))
            info(f"⚠️ 命令失败（退出码 {result.returncode}），{delay}s 后重试 "
                 f"({attempt + 1}/{retries - 1})...", style="yellow")
            _time.sleep(delay)
    if check:
        raise SystemExit(f"命令失败：{' '.join(str(c) for c in cmd)}")
    return result


def uv_exe():
    """返回 uv 可执行文件路径，没有则 None（含 ~/.local/bin 兜底）。"""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "uv.exe",
                      Path.home() / ".local" / "bin" / "uv",
                      Path.home() / ".cargo" / "bin" / "uv.exe",
                      Path.home() / ".cargo" / "bin" / "uv"):
        if candidate.is_file():
            return str(candidate)
    return None


def python_has_pip():
    """当前解释器里 `import pip` 是否可用。"""
    try:
        proc = subprocess.run([sys.executable, "-c", "import pip"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def pip_command():
    """决定用哪个安装后端，返回 `(命令前缀, 类型)` 或 `(None, None)`。

    **优先 uv**：`uv pip` 是 pip 的兼容层，而且 uv 能直接装进一个**没有 pip**
    的环境（它自己解析 wheel、自己写 site-packages），所以「装上 uv 之后就没必要
    再用 pip」在这里落实。只有在没有 uv 时才轮到真正的 pip。

    顺序：
      1. 有 uv               → `[uv, pip]`
      2. 解释器自带 pip      → `[python, -m, pip]`
      3. 能 ensurepip 引导   → 引导后仍用 `[python, -m, pip]`
      4. 都没有              → `(None, None)`，由调用方给出修复指引

    需要**真正的 pip** 的只有一处：`pip download`（uv 没有这个子命令），
    见 `require_real_pip()`。
    """
    uv = uv_exe()
    if uv:
        return [uv, "pip"], "uv"

    if python_has_pip():
        return [sys.executable, "-m", "pip"], "pip"

    if _try_ensurepip():
        info("🔧 没有 uv 且该环境没有 pip，已用 ensurepip 引导安装", style="yellow")
        return [sys.executable, "-m", "pip"], "pip"

    return None, None


def require_real_pip():
    """要「真正的 pip」（`pip download` 用），没有就返回 `(None, None)`。

    不能用 `uv pip` 顶替：uv 没有 download 子命令，会直接报
    `unrecognized subcommand`（实测）。
    """
    if python_has_pip():
        return [sys.executable, "-m", "pip"], "pip"
    if _try_ensurepip():
        return [sys.executable, "-m", "pip"], "pip"
    return None, None


_ENSUREPIP_TRIED = False


def _try_ensurepip():
    """尝试用标准库 ensurepip 把 pip 装进当前解释器。只试一次。"""
    global _ENSUREPIP_TRIED
    if _ENSUREPIP_TRIED:
        return False
    _ENSUREPIP_TRIED = True
    try:
        proc = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and python_has_pip()


def explain_missing_pip():
    """两个后端都不能用时，给出可执行的修复指引。"""
    uv = uv_exe()
    panel(
        "❌ 当前环境既没有 pip，也无法自动引导。\n\n"
        "这通常是因为虚拟环境由 uv 创建且未包含 pip。任选一种修复方式：\n"
        f"  1) 删掉环境重建（推荐）：\n"
        f"       rmdir /s /q .venv\n"
        f"       uv venv .venv --python 3.11 --seed\n"
        f"  2) 直接补装 pip：\n"
        f"       {sys.executable} -m ensurepip --upgrade\n"
        + (f"  3) 用 uv 装（无需 pip）：\n"
           f"       uv pip install --python \"{sys.executable}\" pip\n" if uv else
           "  3) 安装 uv 后重试：winget install --id=astral-sh.uv\n"),
        style="red")


def uv_pip(args, retries=1, check=True, cache_dir=None):
    """直接用 uv 的 pip 兼容层装包（`uv pip ...`）。没有 uv 时返回 None。

    为什么优先走 uv：uv 是**替代 pip** 的包管理器，装上 uv 之后就没有理由再让
    每个包先经过 `python -m pip`。uv 自带这几样我们正好需要的能力：
      * `uv pip install --torch-backend cu126` —— 自动从 PyTorch 官方索引取
        带本地版本号的轮子（实测解析出 `torch==2.8.0+cu126`，与手工拼
        `--index-url https://download.pytorch.org/whl/cu126` 等价）；
      * `uv pip compile --generate-hashes` —— 生成带 sha256 的锁定文件，
        整个依赖图一次算清（实测 192 个包 / 4008 条 hash）；
      * 全局内容寻址缓存（`UV_CACHE_DIR`），同一个轮子不会下第二次。
    """
    uv = uv_exe()
    if uv is None:
        return None
    cmd = [uv, "pip", *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if cache_dir:
        env["UV_CACHE_DIR"] = str(cache_dir)
    return run(cmd, retries=retries, check=check, env=env)


def uv_lock_requirements(requirements="requirements.txt", lock_path=None,
                         python_version=None, cache_dir=None, torch_backend=None):
    """用 `uv pip compile --generate-hashes` 生成锁定文件。返回 Path 或 None。

    锁定文件把"到底会装哪 192 个包、每个的 sha256 是多少"提前固定下来。这比
    "把 requirements 交给 pip 让它自己解析"可控得多。生成的文件**带平台与
    Python 版本信息**（Windows / cp311），所以不进版本库，每次安装重新生成。

    `torch_backend` 必须传：requirements.txt 里没有 torch，但 whisperx /
    pyannote 会把它拖进来，而**默认解析拿到的是 PyPI 上的 CPU 版**
    （实测 `torch==2.8.0`，没有 `+cu126` 后缀）。带上 `--torch-backend cu126`
    才会解析出 `torch==2.8.0+cu126`。
    """
    uv = uv_exe()
    if uv is None:
        return None
    lock_path = Path(lock_path or UV_LOCK_FILE)
    version = python_version or f"{sys.version_info[0]}.{sys.version_info[1]}"
    cmd = [uv, "pip", "compile", str(requirements),
           "--python-version", version,
           "--generate-hashes",
           "--output-file", str(lock_path),
           "--quiet"]
    if torch_backend:
        cmd += ["--torch-backend", torch_backend]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if cache_dir:
        env["UV_CACHE_DIR"] = str(cache_dir)
    result = run(cmd, retries=1, check=False, env=env)
    if result.returncode != 0 or not lock_path.is_file():
        info("⚠️ uv 生成锁定文件失败，改走常规安装", style="yellow")
        return None
    count = sum(1 for line in lock_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith((" ", "#")))
    info(f"🔒 已生成锁定文件 {lock_path}（{count} 个包，带 sha256 校验）", style="green")
    return lock_path


def uv_install_locked(lock_path, cache_dir=None):
    """按锁定文件安装依赖。

    用 `uv pip install -r` 而**不是** `uv pip sync`：sync 会把锁定文件之外的
    包全部卸掉（实测它顺手卸掉了 `packaging`，用最小 lock 试的时候连 pip 和
    setuptools 都会被删），而这正是我们要保留 pip 兜底能力的地方。
    """
    return uv_pip(["install", "--python", sys.executable, "-r", str(lock_path)],
                  retries=2, check=True, cache_dir=cache_dir)


def uv_download_requirements(lock_path, target_dir, cache_dir=None):
    """把锁定文件里的轮子落成真实的 .whl 到 `target_dir`（供离线/搬机器）。

      * `uv pip install --target` **不是** `pip download` 的等价物：它写的是
        解包后的文件（`pkg/` + `*.dist-info/`），不能当 wheelhouse 用。
      * uv 也没有 `download` 子命令（实测 `unrecognized subcommand`）。
    所以：有能力时直接 `pip download`（会把 uv 已缓存的轮子从缓存取出，不重新
    联网）；没有 pip 就退回一个纯标准库的 PyPI 下载器（按 wheel 优先挑选，
    把文件写进目标目录）。torch 那三个大文件由 `--download-only` 单独负责。
    """
    try:
        target = ensure_download_dir(target_dir)
    except OSError:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

    base, kind = pip_command()
    if base is not None and kind == "pip":
        result = run([*base, "download", "-r", str(lock_path), "-d", str(target)],
                     retries=1, check=False,
                     env={**os.environ, "PIP_NO_INPUT": "1",
                          "PYTHONIOENCODING": "utf-8"})
        if result.returncode == 0:
            return result
        info("⚠️ pip download 未完全成功，改用标准库下载器补齐", style="yellow")

    return _stdlib_wheel_download(lock_path, target)


def _parse_locked_requirements(lock_path):
    """从锁定文件里取 (包名, 版本) 列表。

    ⚠️ `--generate-hashes` 的输出里，**每一行** pin 都以 `\\` 续行（后面跟着
    `--hash=sha256:...`），所以不能"看到 `\\` 就跳过"，否则一个包都读不出来
    （这是实测踩到的：192 个 pin 解析出 0 个）。正确做法是先去掉行尾的续行符，
    再判断是不是一个 `name==version` 形式的 pin。
    """
    pins = []
    for raw in Path(lock_path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if line.endswith("\\"):
            line = line[:-1].strip()
        if not line or line.startswith("-") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        name, version = name.strip(), version.strip()
        # 只接受纯 pin（排除 "name==ver ; marker" 这类带环境标记的行）
        if not name or not version or " " in name:
            continue
        pins.append((name, version.split(";")[0].strip()))
    return pins


def _stdlib_wheel_download(lock_path, target_dir):
    """纯标准库的 PyPI 下载器：按 wheel 优先，把文件写进 target_dir。

    只用于"环境里没有 pip"的兜底场景。返回最后一次请求的结果或 None。
    """
    import json as _json
    from urllib.request import Request, urlopen

    tag = current_python_tag()
    plat = platform_wheel_tag()
    downloaded, skipped = 0, 0
    for name, version in _parse_locked_requirements(lock_path):
        candidate = Path(target_dir) / f"{name.replace('-', '_')}-{version}"
        try:
            with urlopen(Request(f"https://pypi.org/pypi/{name}/{version}/json",
                                 headers={"User-Agent": "VideoLingo-installer"}),
                         timeout=60) as response:
                meta = _json.loads(response.read().decode("utf-8", "replace"))
            urls = meta.get("urls") or []
            wheels = [u for u in urls if u.get("filename", "").endswith(".whl")
                      and "py3-none-any" in u["filename"] or
                      (tag in u.get("filename", "") and plat.split("_")[0] in u.get("filename", ""))]
            if not wheels:
                skipped += 1
                continue
            chosen = wheels[0]
            dest = Path(target_dir) / chosen["filename"]
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            with urlopen(Request(chosen["url"],
                                 headers={"User-Agent": "VideoLingo-installer"}),
                         timeout=600) as response:
                dest.write_bytes(response.read())
            downloaded += 1
        except Exception:
            skipped += 1
    info(f"📥 标准库下载器：新增 {downloaded} 个文件，跳过 {skipped} 个"
         f"（torch 三件套请用 --download-only 单独下）", style="bright_black")
    return None


def uv_install_torch(backend, download_dir=None, dry_run=False):
    """用 uv 装 torch 三件套（本地已有轮子时优先用本地文件）。返回退出码。"""
    uv = uv_exe()
    if uv is None:
        return None

    local = local_wheels_for(backend, download_dir)
    targets, from_index = [], []
    for pkg, ver, _filename in wheel_names_for(backend):
        hit = local.get(pkg)
        if hit is not None:
            targets.append(str(hit))
        else:
            targets.append(f"{pkg}=={ver}")
            from_index.append(pkg)

    if dry_run:
        info(f"   [dry-run] uv pip install --torch-backend {backend} {' '.join(targets)}")
        return 0

    args = ["install", "--python", sys.executable, *targets]
    # --no-deps 只能出现**一次**：uv 会直接报
    # "the argument '--no-deps' cannot be used multiple times"（实测踩到）。
    # torch 三件套的依赖（sympy / numpy / …）已由锁定文件那一步装好。
    args.append("--no-deps")
    # --torch-backend 让 uv 自己处理 PyTorch 索引；全是本地文件时不需要联网，
    # 但带上它也无害（uv 只在真正解析索引时才用）
    args += ["--torch-backend", backend]
    if not from_index:
        # 全部来自本地文件：不需要 uv 再缓存一份几 GB 的轮子
        args.append("--no-cache-dir")
    return uv_pip(args, retries=2, check=True,
                  cache_dir=PIP_DL_CACHE_DIR).returncode


def pip(args, retries=2, check=True, cache_dir=None):
    """统一的"装包"调用：默认走 uv，没有 uv 才用真正的 pip。

    `cache_dir` 用来把下载缓存收进项目内（uv 走 `UV_CACHE_DIR`、pip 走
    `PIP_CACHE_DIR`），避免动辄几个 GB 的轮子落到 C 盘用户目录。
    """
    base, kind = pip_command()
    if base is None:
        explain_missing_pip()
        if check:
            raise SystemExit("没有可用的安装后端（既无 uv 也无 pip）")
        return subprocess.CompletedProcess(args=[], returncode=1)

    env = {**os.environ, "PIP_NO_INPUT": "1", "PYTHONIOENCODING": "utf-8"}
    if kind == "uv":
        # uv 的 pip 兼容层：不认 --disable-pip-version-check / --prefer-binary，
        # 也**不认 `--timeout`**（实测 uv 0.9.x 会直接报
        # `error: unexpected argument '--timeout' found` 并退出码 2，
        # 导致整条依赖安装失败）。超时改用 uv 自己的环境变量
        # `UV_HTTP_TIMEOUT`（秒），语义与 pip 的 --timeout 一致。
        # 需要显式告诉它目标解释器（本脚本可能是被别的解释器 re-exec 起来的）。
        cmd = [*base, "install", "--python", sys.executable, *args]
        env["UV_HTTP_TIMEOUT"] = "120"
        if cache_dir:
            env["UV_CACHE_DIR"] = str(cache_dir)
    else:
        cmd = [*base, "install",
               "--disable-pip-version-check", "--prefer-binary",
               "--retries", "5", "--timeout", "120", *args]
        if cache_dir:
            env["PIP_CACHE_DIR"] = str(cache_dir)
    return run(cmd, retries=retries, check=check, env=env)


def pip_download(requirements, target_dir, retries=2, check=False, cache_dir=None):
    """把 requirements 里的第三方依赖**下载**到目录（不安装）。

    这一步是"下载与安装分离"的关键：下载阶段只往磁盘写文件，失败了也不影响
    已经装好的部分；用户可以拿这个目录里的文件做离线安装，或者在中途断网时
    用外部工具补齐缺的那几个。

    ⚠️ 这是全脚本**唯一**必须用真正 pip 的地方：`pip download` 是 pip 独有的
    子命令，`uv pip` 没有对应实现（实测 `uv pip download` 报
    `unrecognized subcommand`）。拿不到 pip 时返回 None 并跳过预下载，
    后面照常在线安装 —— 不影响正确性，只是少了本地 wheelhouse。
    """
    base, _kind = require_real_pip()
    if base is None:
        info("ℹ️ 跳过依赖预下载（本机既无 pip 也无 uv，无法导出 wheel 文件）",
             style="bright_black")
        return None

    cmd = [*base, "download",
           "--disable-pip-version-check", "--prefer-binary",
           "--retries", "5", "--timeout", "120",
           "-r", str(requirements), "-d", str(target_dir)]
    env = {**os.environ, "PIP_NO_INPUT": "1", "PYTHONIOENCODING": "utf-8"}
    if cache_dir:
        env["PIP_CACHE_DIR"] = str(cache_dir)
    return run(cmd, retries=retries, check=check, env=env)


def pip_install_local_dir(target_dir, requirements, cache_dir=None):
    """优先用已下载到 `target_dir` 的轮子安装，缺的再走索引。"""
    # 1) 只用本地文件装（最快，且完全不联网）
    result = pip(["-r", str(requirements), "--no-index",
                  "--find-links", str(target_dir)],
                 retries=1, check=False, cache_dir=cache_dir)
    if result.returncode == 0:
        return 0
    # 2) 本地文件不全：退回正常安装（pip 会自己补缺的）
    info("ℹ️ 本地文件不足以完成安装，改走在线安装（缺的部分由 pip 补下）",
         style="yellow")
    return pip(["-r", str(requirements), "--find-links", str(target_dir)],
               retries=2, check=True, cache_dir=cache_dir).returncode


def python_ok(version_info=None):
    version = (version_info or sys.version_info)[:2]
    return PYTHON_MIN <= version < PYTHON_MAX


def _python_version_label():
    lo = f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}"
    hi = f"{PYTHON_MAX[0]}.{PYTHON_MAX[1] - 1}"
    return f"{lo}–{hi}"


def requirements_hash():
    """按 requirements.txt + torch 版本算指纹，用于跳过重复安装。"""
    digest = hashlib.sha256()
    for name in ("requirements.txt",):
        path = Path(name)
        if path.exists():
            digest.update(path.read_bytes())
    digest.update(f"torch={TORCH_VERSION}".encode())
    return digest.hexdigest()


# ---------------------------------------------------------------- 大文件下载目录
def download_dir_path(directory=None):
    return Path(directory or DEFAULT_DOWNLOAD_DIR)


def ensure_download_dir(directory=None):
    path = download_dir_path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_python_tag():
    return f"cp{sys.version_info[0]}{sys.version_info[1]}"


def platform_wheel_tag():
    """本机 wheel 平台标签（只用于拼出"应该下哪个文件"的文件名）。"""
    if os.name == "nt":
        return "win_amd64"
    if sys.platform == "darwin":
        return "macosx_11_0_arm64" if platform.machine() == "arm64" else "macosx_10_9_x86_64"
    # Linux：manylinux 的次版本号随构建而变，本地复用只要求前缀能对上
    return "manylinux"


def wheel_names_for(backend):
    """返回 [(包名, 版本号, 该后端对应的 wheel 文件名), ...]。

    文件名按 PyTorch 官方索引的命名规则拼（`{pkg}-{ver}+{backend}-{cp}-{cp}-{plat}.whl`）。
    只有**本地复用**时才会去比对文件名，所以即使将来标签变了，也只是"没命中本地文件"，
    会退回正常联网安装，不会装错东西。
    """
    tag, plat = current_python_tag(), platform_wheel_tag()
    specs = (("torch", TORCH_VERSION), ("torchaudio", TORCH_VERSION),
             ("torchvision", TORCHVISION_VERSION))
    return [(pkg, ver, f"{pkg}-{ver}+{backend}-{tag}-{tag}-{plat}.whl")
            for pkg, ver in specs]


def find_local_wheel(directory, filename):
    """在下载目录里找一个可复用的 wheel。

    命中条件（Windows/macOS 要求文件名精确匹配；Linux 的 manylinux 次版本号
    不确定，所以放宽为"包名-版本+后端"前缀匹配）。
    """
    root = download_dir_path(directory)
    if not root.is_dir():
        return None
    exact = root / filename
    if exact.is_file() and exact.stat().st_size > 0:
        return exact
    if platform_wheel_tag() == "manylinux":
        prefix = filename.split("-")[0] + "-" + filename.split("-")[1]
        for candidate in sorted(root.glob(f"{prefix}-*.whl")):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def local_wheels_for(backend, directory=None):
    """返回 {包名: 本地 wheel 路径}，只含**已经存在**的那些。"""
    found = {}
    for pkg, _ver, filename in wheel_names_for(backend):
        path = find_local_wheel(directory, filename)
        if path is not None:
            found[pkg] = path
    return found


def torch_download_plan(backend, directory=None):
    """返回 (已存在清单, 缺失清单)，每项是 (包名, 文件名, 下载 URL)。"""
    index_url, _label = TORCH_SPECS[backend]
    have, missing = [], []
    for pkg, _ver, filename in wheel_names_for(backend):
        item = (pkg, filename, f"{index_url.rstrip('/')}/{pkg}/{filename}")
        if find_local_wheel(directory, filename) is not None:
            have.append(item)
        else:
            missing.append(item)
    return have, missing


def index_wheel_info(index_url, pkg, filename):
    """从 PyTorch 索引页解析出 wheel 的**真实下载地址**（附带 sha256）。

    为什么不自己拼 URL：实测 `download.pytorch.org/whl/...` 的文件直链会返回
    **403**，索引页里的 href 指向的是 `download-r2.pytorch.org`，那个域名才能
    直接下载。给用户打印一个打不开的地址等于没帮上忙，所以这里直接读索引页。

    返回 `(url, sha256)`；解析失败返回 `(兜底 url, None)`。
    """
    import json as _json
    from urllib.request import Request, urlopen

    fallback = f"{index_url.rstrip('/')}/{pkg}/{filename}"
    try:
        request = Request(f"{index_url.rstrip('/')}/{pkg}/",
                          headers={"User-Agent": "VideoLingo-installer"})
        with urlopen(request, timeout=60) as response:
            html = response.read().decode("utf-8", "replace")
    except Exception:
        return fallback, None

    for match in re.finditer(r'href="([^"#]+)(?:#([^"]*))?"[^>]*>([^<]+)</a>', html):
        href, fragment, text = match.group(1), match.group(2) or "", match.group(3).strip()
        if text != filename and not href.endswith(filename.replace("+", "%2B")):
            continue
        sha = None
        sha_match = re.search(r"sha256=([0-9a-f]{64})", fragment)
        if sha_match:
            sha = sha_match.group(1)
        else:
            # 有些索引把 sha256 放在 data-* 属性里
            tail = html[match.start():match.start() + 1200]
            attr = re.search(r'data-core-metadata="sha256=([0-9a-f]{64})"', tail)
            if attr:
                sha = attr.group(1)
        return href, sha
    return fallback, None


def _wheel_line(item, directory, with_url=True):
    """把 (pkg, filename, url) 渲染成几行提示。"""
    pkg, filename, url = item
    lines = [f"      · {pkg}", f"        文件名：{filename}"]
    if with_url:
        lines.append(f"        下载地址：{url}")
    return lines


def report_torch_download_plan(backend, directory=None, verbose=True, resolve_urls=True):
    """打印 torch 三件套的下载 URL 与本地复用情况。返回缺失清单。"""
    root = ensure_download_dir(directory)
    have, missing = torch_download_plan(backend, directory)
    if not verbose:
        return missing

    info(f"📁 大文件目录：[bold cyan]{root}[/bold cyan]")
    for pkg, filename, _url in have:
        size = ""
        path = find_local_wheel(directory, filename)
        if path is not None:
            size = f"（{path.stat().st_size / 1024 ** 2:.0f} MB）"
        info(f"   ♻️ 已有本地文件，将直接使用：{filename} {size}", style="green")
    if missing:
        info("   ⬇️ 以下文件不存在，需要下载（单个 2.7–3.6 GB，"
             "网络不佳时建议用外部工具先下好）：", style="yellow")
        for item in missing:
            pkg, filename, url = item
            if resolve_urls:
                url, sha256 = index_wheel_info(TORCH_SPECS[backend][0], pkg, filename)
                item = (pkg, filename, url)
                for line in _wheel_line(item, root):
                    info(line, style="cyan" if "下载地址" in line else None)
                if sha256:
                    info(f"        sha256：{sha256}", style="bright_black")
            else:
                for line in _wheel_line(item, root):
                    info(line, style="cyan" if "下载地址" in line else None)
        info(f"      下载完成后放进：{root}", style="yellow")
    return missing


def print_full_download_plan(backend, directory=None):
    """`--download-only` 的全部内容：torch 三件套 + FFmpeg + 其余依赖。"""
    root = ensure_download_dir(directory)
    panel(f"📥 只下载、不安装\n\n所有文件统一放到：[bold cyan]{root}[/bold cyan]\n"
          f"下好后重新运行安装脚本，它会优先使用这些本地文件。", style="cyan")
    report_torch_download_plan(backend, directory)

    info("")
    info("📦 FFmpeg：")
    bin_dir, source, version = usable_ffmpeg()
    if bin_dir is not None:
        info(f"   ♻️ 已有可用的 FFmpeg {_fmt_version(version)}（{source}）：{bin_dir}",
             style="green")
        info("   ⏭️ 安装时会跳过 FFmpeg 下载", style="bright_black")
    else:
        cached_zip = root / FFMPEG_ZIP_NAME
        if cached_zip.is_file() and cached_zip.stat().st_size > 0:
            info(f"   ♻️ 已有压缩包：{cached_zip}"
                 f"（{cached_zip.stat().st_size / 1024 ** 2:.0f} MB）", style="green")
        else:
            url, why = resolve_ffmpeg_url()
            if url:
                info("   ⬇️ 未找到可用的 FFmpeg，需要下载：")
                info(f"        文件名：{FFMPEG_ZIP_NAME}（zip，安装脚本会自动解压到 "
                     f"./{FFMPEG_DIR_NAME}/）")
                info(f"        下载地址：{url}", style="cyan")
                info(f"        说明：{why}", style="bright_black")
                info(f"        存放位置：{cached_zip}", style="yellow")
            else:
                info(f"   ⚠️ 无法解析 FFmpeg 下载地址：{why}", style="yellow")
                _ffmpeg_manual_hint()

    info("")
    pkg_dir = root / "python"
    info(f"ℹ️ 其余第三方依赖（whisperx / pyannote / spacy 等）会由 pip 下载到："
         f"{pkg_dir}", style="bright_black")
    info(f"   安装脚本每次都会先 `pip download -r requirements.txt -d {pkg_dir}`，"
         f"再用这些文件离线安装；", style="bright_black")
    info(f"   想手工补齐时，在有网的机器上执行同一条命令，把目录整体拷过来即可。",
         style="bright_black")
    return 0


def state_path():
    return Path(sys.prefix) / STATE_FILE_NAME


def read_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(backend="unknown"):
    try:
        state_path().write_text(
            json.dumps({"requirements_hash": requirements_hash(),
                        "python": platform.python_version(),
                        "torch": TORCH_VERSION,
                        "torch_backend": backend}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------- GPU 探测
def detect_gpu_compute_cap():
    """返回本机第一块 N 卡的算力 float（如 6.1），没有 N 卡返回 None。

    只用 `nvidia-smi --query-gpu=compute_cap`，不需要先装 torch 或 pynvml
    （pynvml 已废弃）。老驱动不支持 compute_cap 字段时回退查 name 表。
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            match = re.match(r"^(\d+)\.(\d+)", text)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
    return _compute_cap_from_gpu_name(exe)


# 老驱动没有 compute_cap 字段时的名称兜底表（只覆盖常见型号）
_GPU_NAME_CAPS = (
    ("RTX 50", 12.0), ("B100", 10.0), ("B200", 10.0),
    ("RTX 40", 8.9), ("L40", 8.9), ("RTX 30", 8.6), ("A10", 8.6),
    ("A100", 8.0), ("A30", 8.0), ("RTX 20", 7.5), ("T4", 7.5),
    ("V100", 7.0), ("GTX 16", 7.5), ("GTX 10", 6.1), ("P100", 6.0),
    ("P4", 6.1), ("P40", 6.1), ("P2200", 6.1), ("P2000", 6.1),
    ("P1000", 6.1), ("P600", 6.1), ("P400", 6.1),
    ("MX150", 6.1), ("MX250", 6.1), ("MX350", 6.1),
    ("GTX 9", 5.2), ("K80", 3.7), ("K40", 3.5),
)


def _compute_cap_from_gpu_name(exe):
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    for line in lines:
        for marker, cap in _GPU_NAME_CAPS:
            if marker in line:
                return cap
    return None


def detect_torch_backend(requested="auto", gpu_cap=None):
    """决定用哪个 CUDA 后端。返回 (backend, 原因)。

    requested 为 auto 时按显卡算力选；显式指定则原样使用。`gpu_cap` 传入
    None 表示"还没探测"，由本函数自行探测（便于单测注入）。
    """
    if requested and requested != "auto":
        return requested, "命令行显式指定"
    if platform.system() == "Darwin":
        return "cpu", "macOS 无 CUDA 支持"

    cap = detect_gpu_compute_cap() if gpu_cap is None else gpu_cap
    if cap is None:
        return "cpu", "未检测到 NVIDIA GPU（无 nvidia-smi 或查询失败）"
    for threshold, backend in COMPUTE_CAP_RULES:
        if cap < threshold:
            note = {
                "cu126": "算力 < 7.0（Pascal/Volta 及更老），"
                         "cu128/cu129 构建已移除这些架构",
                "cu128": "算力 7.0–11.x，Ampere/Ada 首选 cu128",
                "cu129": "算力 ≥ 12.0（Blackwell）",
            }[backend]
            return backend, f"检测到算力 {cap}：{note}"
    return "cpu", "无法判定算力"


def detect_cuda_version():
    """用 nvidia-smi 解析驱动支持的 CUDA 版本，返回 (major, minor) 或 None。

    只作为**提示信息**使用：能不能跑取决于 torch 轮子里编译进的架构列表，
    而不是驱动报告的 CUDA 版本，所以它不再参与后端选择。
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"CUDA(?: UMD)? Version:\s*(\d+)\.(\d+)", out or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def install_torch(backend="auto", dry_run=False, download_dir=None):
    """安装与硬件匹配的 PyTorch 三件套，返回实际使用的后端。

    下载与安装分离：先在 `_downloads/` 里找同名 wheel，找到就直接用本地文件
    （不联网）；找不到才按官方索引下载。单个 torch wheel 有 2.7–3.6 GB，
    网络不佳时可以把 URL 拿出去用外部工具下好再放回来。
    """
    resolved, reason = detect_torch_backend(backend)
    index_url, label = TORCH_SPECS[resolved]

    cap = detect_gpu_compute_cap()
    cuda = detect_cuda_version()
    lines = [f"🎮 torch 后端：{resolved} —— {label}", f"   依据：{reason}"]
    if cap is not None:
        lines.append(f"   GPU 算力：{cap}")
    if cuda is not None:
        lines.append(f"   驱动支持 CUDA：{cuda[0]}.{cuda[1]}")
    if resolved != "cpu" and cuda is not None and cuda[0] * 10 + cuda[1] < int(resolved[2:]):
        lines.append(f"   ⚠️ 驱动支持的 CUDA {cuda[0]}.{cuda[1]} 低于 {resolved} 的运行时要求，"
                     f"建议更新显卡驱动，或用 --torch-backend cu126")
    if resolved == "cpu":
        lines.append("   ⚠️ CPU 转录会非常慢，强烈建议使用 NVIDIA GPU")
    panel("\n".join(lines), style="cyan" if resolved != "cpu" else "yellow")

    missing = report_torch_download_plan(resolved, download_dir)

    if dry_run:
        uv_install_torch(resolved, download_dir, dry_run=True)
        return resolved

    # 优先让 uv 自己处理 PyTorch 索引（`--torch-backend` 就是为这件事设计的，
    # 实测解析出 torch==2.8.0+cu126，与手工拼 --index-url 等价）。
    # 没有 uv 时才继续往下走 pip 回退路径。
    if uv_exe() is not None:
        uv_install_torch(resolved, download_dir)
        return resolved

    # 组装安装命令：已存在的本地文件直接用文件路径，其余交给索引
    local = local_wheels_for(resolved, download_dir)
    targets, from_index = [], []
    for pkg, ver, filename in wheel_names_for(resolved):
        local_path = local.get(pkg)
        if local_path is not None:
            # 直接用文件路径安装，pip 不会把它当成"另一个 requirement"，
            # 因此不会出现"本地装一份 + 索引再下一份"的双份下载。
            targets.append(str(local_path))
        else:
            targets.append(f"{pkg}=={ver}")
            from_index.append(pkg)

    if dry_run:
        info(f"   [dry-run] pip install {' '.join(targets)}"
             + (f" --index-url {index_url}" if from_index else ""))
        return resolved

    if not from_index:
        panel(f"✅ torch 三件套全部使用本地文件（不联网）："
              f"{len(targets)} 个文件来自 {ensure_download_dir(download_dir)}", style="green")
    elif len(from_index) < len(targets):
        info(f"♻️ 本地已有部分轮子，剩余 {', '.join(from_index)} 从官方索引下载",
             style="yellow")

    # --no-cache-dir：本函数已经从本地文件安装，不需要 pip 再存一份缓存
    # （否则一次安装会在磁盘上留两份几 GB 的轮子）
    pip([*targets, "--index-url", index_url, "--no-cache-dir"], check=True)
    return resolved


_TORCH_PROBE = (
    "import torch;"
    "print('torch', torch.__version__);"
    "print('cuda_available', torch.cuda.is_available());"
    "print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu');"
    "print('arch_list', torch.cuda.get_arch_list() if hasattr(torch.cuda, 'get_arch_list') else [])"
)


def verify_torch_gpu():
    """在**子进程**里验证 torch 能看到 GPU（本进程可能还没装/还没重启）。

    跑在子进程的原因：安装脚本自身在 torch 装好之前就 import 过它，同进程里
    看到的仍是旧模块。返回 (ok, 描述)。
    """
    try:
        proc = subprocess.run([sys.executable, "-c", _TORCH_PROBE],
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"验证 torch 失败：{e}"

    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
        return False, f"验证 torch 失败：{tail[-1] if tail else '未知错误'}"

    fields = {}
    for line in (proc.stdout or "").splitlines():
        if " " in line:
            key, _, value = line.partition(" ")
            fields[key] = value.strip()

    desc = (f"torch {fields.get('torch', '?')} | CUDA 可用："
            f"{fields.get('cuda_available', '?')} | 设备：{fields.get('device', '?')}")
    if fields.get("arch_list"):
        desc += f"\n   编译架构：{fields['arch_list']}"
    return fields.get("cuda_available") == "True", desc


def maybe_configure_mirror(enabled):
    if not enabled:
        info("ℹ️ 未改动 pip 镜像设置（需要时用 --auto-mirror 显式开启）", style="bright_black")
        return
    try:
        from core.pypi_autochoose import main as choose_mirror
        choose_mirror()
    except Exception as e:
        info(f"⚠️ 自动选择镜像失败，继续使用当前设置：{e}", style="yellow")


# ---------------------------------------------------------------- FFmpeg
def parse_ffmpeg_version(text):
    """从 `ffmpeg -version` 输出解析 (major, minor, patch)。解析失败返回 None。"""
    match = re.search(r"ffmpeg version (\S+)", text or "")
    if not match:
        # 有些构建把版本放在第一行末尾，例如 "ffmpeg version n7.1"
        match = re.search(r"version n?(\d+)\.(\d+)", text or "")
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), 0
    raw = match.group(1).lstrip("n").split("-")[0]
    parts = raw.split(".")
    numbers = []
    for part in parts[:3]:
        digits = re.match(r"^(\d+)", part)
        numbers.append(int(digits.group(1)) if digits else 0)
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def ffmpeg_version_of(exe):
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ffmpeg_version(proc.stdout or proc.stderr or "")


def ffmpeg_major_ok(major):
    return major is not None and FFMPEG_MIN_MAJOR <= major <= FFMPEG_MAX_MAJOR


def _ffmpeg_exe_name():
    return "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def _ffprobe_exe_name():
    return "ffprobe.exe" if os.name == "nt" else "ffprobe"


def _fmt_version(version):
    return ".".join(map(str, version)) if version else "版本未知"


def project_ffmpeg_bin():
    """项目内 FFmpeg 的 bin 目录（优先），没有返回 None。"""
    try:
        from runtime_libraries import find_project_ffmpeg_bin
        return find_project_ffmpeg_bin()
    except Exception:
        root = Path(FFMPEG_DIR_NAME)
        for candidate in (root / "bin", root):
            if (candidate / "ffmpeg.exe").is_file() or (candidate / "ffmpeg").is_file():
                return candidate
        return None


def has_shared_av_libs(bin_dir):
    """该 FFmpeg 目录是否带**共享库**（avcodec-*.dll / libavcodec.so）。

    为什么必须区分：`torchcodec` 通过 FFmpeg **共享库**解码，它自带
    `libtorchcodec_core4..7.dll`，但这些 DLL 依赖 `avcodec-*.dll` 等同级动态库。
    而 gyan.dev 的 `full_build` 与 BtbN 的非 shared 包都是**静态**构建 ——
    里面有 `ffmpeg.exe` 却没有那些 `.dll`。实测后果：装了系统 FFmpeg 7.0.2
    （静态）仍然 `import torchcodec.decoders` 失败，报
    "Could not find module '...libtorchcodec_core7.dll' (or one of its dependencies)"。

    所以"有大版本合规的 ffmpeg.exe"**不等于**"torchcodec 可用"，
    这里要单独看共享库在不在。
    """
    bin_dir = Path(bin_dir)
    if not bin_dir.is_dir():
        return False
    patterns = ("avcodec-*.dll", "avcodec.dll",          # Windows
                "libavcodec.so*", "libavcodec.dylib")    # Linux / macOS
    for pattern in patterns:
        try:
            if next(bin_dir.glob(pattern), None) is not None:
                return True
        except OSError:
            continue
    return False


def usable_ffmpeg(require_shared=None):
    """返回 (bin 目录, 来源, ffmpeg 版本) —— 找得到**大版本合规**的一份就返回。

    顺序与"跳过下载"的判断一致：项目内优先，其次系统 PATH。
    找不到时返回 (None, None, None)。

    `require_shared`（默认 Windows 上为 True）：是否要求带共享库。因为本项目
    真正需要 FFmpeg 的地方（torchcodec）走的是共享库，只有静态 exe 的系统
    安装不算"可用"，否则会跳过下载、然后在运行期炸掉。
    """
    if require_shared is None:
        require_shared = (os.name == "nt")
    local = project_ffmpeg_bin()
    if local is not None:
        version = ffmpeg_version_of(str(local / _ffmpeg_exe_name()))
        if ffmpeg_major_ok(version[0] if version else None) and \
                (not require_shared or has_shared_av_libs(local)):
            return local, "项目内", version

    system = shutil.which("ffmpeg")
    if system:
        version = ffmpeg_version_of(system)
        bin_dir = Path(system).resolve().parent
        if ffmpeg_major_ok(version[0] if version else None) and \
                (not require_shared or has_shared_av_libs(bin_dir)):
            return bin_dir, "系统 PATH", version

    return None, None, None


def ffmpeg_absent_reason():
    """诊断"为什么没有可用的 FFmpeg"，用于给用户一条可行动的提示。"""
    local = project_ffmpeg_bin()
    if local is not None:
        version = ffmpeg_version_of(str(local / _ffmpeg_exe_name()))
        if not ffmpeg_major_ok(version[0] if version else None):
            return f"项目内 ffmpeg 大版本不合规（{_fmt_version(version)}）"
        if not has_shared_av_libs(local):
            return "项目内 ffmpeg 是静态构建，缺少 avcodec 等共享库"
    system = shutil.which("ffmpeg")
    if system:
        version = ffmpeg_version_of(system)
        if not ffmpeg_major_ok(version[0] if version else None):
            return f"系统 ffmpeg 大版本不合规（{_fmt_version(version)}）"
        if not has_shared_av_libs(Path(system).resolve().parent):
            return "系统 ffmpeg 是静态构建（如 gyan.dev full_build），缺少共享库"
    return "未找到 ffmpeg"


def _ffmpeg_branch_ok(branch):
    """分支号（如 "7.1"）是否落在允许的大版本区间内。

    ⚠️ 这一步**必须**有：`FFMPEG_WIN_BRANCHES` 里带了 `8.1`/`8.0` 作为兜底，
    但 8.x 永远过不了 `ffmpeg_major_ok()`（上限是 7）。原实现挑资产时不看版本，
    于是 latest tag 里没有 7.x 时就会挑到 8.1 —— 下载、解压，**最后才**报
    「大版本不合规」，用户白等一次 85 MB 下载且安装必然失败（2026-09-19 实测：
    本机 `resolve_ffmpeg_url()` 正是返回 `ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip`）。
    """
    try:
        major = int(str(branch).split(".", 1)[0])
    except (TypeError, ValueError):
        return False
    return FFMPEG_MIN_MAJOR <= major <= FFMPEG_MAX_MAJOR


def _pick_ffmpeg_asset(releases, branches=None):
    """从 releases 列表里挑一个**版本合规**的 win64-gpl 资产，返回 (name, url) 或 None。

    按 `branches` 给的优先序列逐个分支找（默认 `FFMPEG_WIN_BRANCHES`，已经过
    `_ffmpeg_branch_ok` 过滤，8.x 不会被选中）；**先找带 -shared 的**
    （含 torchcodec 需要的 avcodec 等动态库），找不到再退回非 shared。
    """
    branches = branches or FFMPEG_WIN_BRANCHES
    branches = [b for b in branches if _ffmpeg_branch_ok(b)]
    assets = [a for r in (releases or []) for a in (r.get("assets") or [])]

    def find(branch, want_shared):
        prefix = f"ffmpeg-n{branch}"
        for asset in assets:
            name = asset.get("name") or ""
            if not name.startswith(prefix):
                continue
            if "win64-gpl" not in name or not name.endswith(".zip"):
                continue
            if ("-shared" in name) != want_shared:
                continue
            return name, asset.get("browser_download_url")
        return None

    for branch in branches:
        picked = find(branch, want_shared=True) or find(branch, want_shared=False)
        if picked:
            return picked
    return None


def resolve_ffmpeg_url():
    """运行时解析 Windows FFmpeg 下载地址。返回 (url, 说明) 或 (None, 原因)。

    先查 `latest` tag，再扫最近的 autobuild（7.x 资产会被 GitHub 轮换清理，
    实测 latest 下已只剩 8.x，所以必须能回退到 autobuild tag）。
    `VIDELINGO_FFMPEG_URL` 非空时直接用它，便于离线/内网部署。
    """
    override = os.environ.get("VIDELINGO_FFMPEG_URL", "").strip()
    if override:
        return override, "来自 VIDELINGO_FFMPEG_URL 环境变量"

    import json as _json
    from urllib.request import Request, urlopen

    def _get(url):
        request = Request(url, headers={"User-Agent": "VideoLingo-installer",
                                        "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=60) as response:
            return _json.loads(response.read().decode("utf-8", "replace"))

    problems = []
    try:
        picked = _pick_ffmpeg_asset([_get(FFMPEG_API_LATEST)])
        if picked:
            return picked[1], f"GitHub latest tag 下的 {picked[0]}"
        problems.append("latest tag 里没有 7.x 资产")
    except Exception as e:
        problems.append(f"查询 latest 失败：{e}")

    try:
        picked = _pick_ffmpeg_asset(_get(FFMPEG_API_LIST))
        if picked:
            return picked[1], f"最近 autobuild 里的 {picked[0]}"
        problems.append("最近 30 个 release 里都没有 7.x 资产")
    except Exception as e:
        problems.append(f"查询 release 列表失败：{e}")

    return None, "；".join(problems)


def download_ffmpeg_windows(target_dir, download_dir=None):
    """把 FFmpeg 下载/解压到项目内 ffmpeg/ 目录。

    两处相对原实现的改动：
      1. **不再下 `master-latest`**（现已是 8.1/9.0，而 torchcodec 0.7 只支持 4–7）；
         改为按 `FFMPEG_WIN_BRANCHES` 的优先序列挑"最新的合规版"；
      2. 压缩包**先落在可复用的 `_downloads/ffmpeg-win64.zip`**：网络不佳时可以
         用外部工具下好直接放进该目录，脚本会跳过联网、直接用这个文件解压
         （与 torch 轮子是同一套"下载与安装分离"的思路）。

    zip 内通常有一层 `ffmpeg-nX.Y.../` 目录，`project_ffmpeg_bin()` 会自动往下
    一层找 bin，所以解压后无需移动文件。
    """
    import zipfile
    from urllib.request import urlretrieve

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = ensure_download_dir(download_dir)
    cached_zip = cache_dir / FFMPEG_ZIP_NAME

    # 先看有没有已经下好的压缩包（外部工具下载的情况）
    if cached_zip.is_file() and cached_zip.stat().st_size > 0:
        info(f"♻️ 使用已下载的 FFmpeg 压缩包：{cached_zip}"
             f"（{cached_zip.stat().st_size / 1024 ** 2:.0f} MB）", style="green")
    else:
        if not _download_ffmpeg_zip(cached_zip):
            return None

    found = _extract_ffmpeg_zip(cached_zip, target_dir)
    if found is not None:
        return found

    # 解压出来的版本不合规：最常见的原因是 _downloads\ffmpeg-win64.zip 是**旧的
    # 8.x/9.x 包**（`latest` tag 现在只发 8.1/9.0，早先的安装脚本会把它下下来）。
    # 脚本原先到此就放弃了，但那是**可以自愈**的：删掉坏包重新按 7.x 下一份。
    # 只在"这个包不是本次刚下的"时才重试，避免把用户手工放进去的文件白白删掉
    # 又下一遍同样的东西（若 URL 又不合规，第二次会走同样判断并正常报错）。
    info("♻️ 该压缩包里的版本不合规 —— 删掉它并重新下载合规版本再试一次",
         style="yellow")
    try:
        cached_zip.unlink()
    except OSError as e:
        info(f"❌ 无法删除 {cached_zip}：{e}", style="red")
        _ffmpeg_manual_hint()
        return None

    if not _download_ffmpeg_zip(cached_zip):
        return None
    found = _extract_ffmpeg_zip(cached_zip, target_dir)
    if found is None:
        _ffmpeg_manual_hint()
    return found


def _download_ffmpeg_zip(cached_zip):
    """解析地址并下载到 `cached_zip`。成功返回 True。"""
    from urllib.request import urlretrieve

    url, why = resolve_ffmpeg_url()
    if not url:
        info(f"❌ 无法确定 FFmpeg 下载地址（{why}）", style="red")
        info("   提示：BtbN 的 `latest` tag 现在只发 8.1/9.0（8.x 起 torchcodec 0.7 "
             "不支持），7.x 只在 autobuild tag 里；脚本会自动回退去查它们。",
             style="yellow")
        return False
    info(f"🚀 正在下载 FFmpeg（{why}）")
    info(f"   文件名：{cached_zip}")
    info(f"   下载地址：{url}", style="cyan")
    try:
        urlretrieve(url, cached_zip)
    except Exception as e:
        info(f"❌ 下载 FFmpeg 失败：{e}", style="red")
        info(f"   你可以用外部工具下载上面的地址，保存为 {cached_zip}，"
             f"然后重新运行安装脚本。", style="yellow")
        return False
    return True


def _extract_ffmpeg_zip(cached_zip, target_dir):
    """解压并校验大版本。合规返回 bin 目录，否则返回 None。"""
    import zipfile

    try:
        with zipfile.ZipFile(cached_zip, "r") as zf:
            zf.extractall(target_dir)
    except Exception as e:
        info(f"❌ 解压 FFmpeg 失败：{e}", style="red")
        info(f"   若压缩包是外部工具下的，可能不完整；删掉 {cached_zip} 重试。",
             style="yellow")
        return None

    found = project_ffmpeg_bin()
    if found is None:
        info("❌ FFmpeg 解压后未找到可执行文件", style="red")
        return None
    version = ffmpeg_version_of(str(found / _ffmpeg_exe_name()))
    if not ffmpeg_major_ok(version[0] if version else None):
        info(f"⚠️ 解压得到的 FFmpeg 大版本不合规（{_fmt_version(version)}），"
             f"需要 {FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR}", style="yellow")
        return None
    info(f"✅ FFmpeg {_fmt_version(version)} 已就位：{found}", style="green")
    return found


def _ffmpeg_manual_hint():
    info(f"   手动方案：下载 FFmpeg {FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR} 的 Windows "
         f"构建（https://github.com/BtbN/FFmpeg-Builds/releases 或 "
         f"https://www.gyan.dev/ffmpeg/builds/），两种用法都行：\n"
         f"     · 把压缩包存成 {download_dir_path() / FFMPEG_ZIP_NAME}，重跑安装脚本；\n"
         f"     · 或直接解压，把 ffmpeg.exe / ffprobe.exe 放进 "
         f"./{FFMPEG_DIR_NAME}/bin/。\n"
         f"   本项目运行期会自动接入该目录，不需要改 PATH。", style="yellow")


def ensure_ffmpeg(dry_run=False, download_dir=None):
    """确保有一份**大版本合规**的 ffmpeg/ffprobe。返回 (ok, 是否需重启终端)。

    策略（对应"默认装新版，但已有可用版本就跳过下载"）：
      1. 项目内 `./ffmpeg/` 里已有合规版本 → **直接跳过下载**；
      2. 系统 PATH 上有合规版本 → **同样跳过下载**（不重复占磁盘）；
      3. 都没有才去下载（**优先 7.x 的 shared 包**，压缩包落在 `_downloads/`，
         解压到 `./ffmpeg/`）。

    ⚠️ 「合规」不只是大版本 4–7，还要**带共享库**：torchcodec 依赖 avcodec 等
    动态库，而 gyan.dev 的 full_build 是静态构建 —— 实测装了系统 FFmpeg 7.0.2
    仍然无法 import torchcodec。所以静态版会被判为「不可用」并触发下载。
    """
    # 1) / 2) 已有的可用版本
    bin_dir, source, version = usable_ffmpeg()
    if bin_dir is not None:
        info(f"✅ 已找到可用的 FFmpeg {_fmt_version(version)}"
             f"（{source}，含共享库）：{bin_dir}", style="green")
        info("   ⏭️ 跳过 FFmpeg 下载", style="bright_black")
        return True, False

    # 说清楚为什么不能用（大版本不合规 / 静态构建 / 干脆没有）
    existing = [p for p in ([str(project_ffmpeg_bin() / _ffmpeg_exe_name())]
                            if project_ffmpeg_bin() else []) +
                ([shutil.which("ffmpeg")] if shutil.which("ffmpeg") else [])]
    if existing:
        panel(f"⚠️ 现有 FFmpeg 不可用：{ffmpeg_absent_reason()}\n\n"
              f"torchcodec 0.7 需要 **大版本 "
              f"{FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR}** 且**带共享库"
              f"（avcodec-*.dll）** 的构建。\n"
              f"gyan.dev 的 full_build 与 BtbN 的非 shared 包都是静态构建："
              f"有 ffmpeg.exe 但没有那些 DLL，torchcodec 会加载失败。",
              style="yellow")

    # 3) 下载
    system = platform.system()
    if system == "Windows":
        if dry_run:
            url, why = resolve_ffmpeg_url()
            info(f"   [dry-run] 将下载 FFmpeg 到 ./{FFMPEG_DIR_NAME}/（{why}）")
            if url:
                info(f"   [dry-run] {url}")
            else:
                info("   [dry-run] ⚠️ 解析下载地址失败，真实运行时会给出手动方案",
                     style="yellow")
            return True, False
        panel(f"⬇️ 未找到可用的 FFmpeg，下载最新合规版到项目内 ./{FFMPEG_DIR_NAME}/ …",
              style="yellow")
        if download_ffmpeg_windows(FFMPEG_DIR_NAME, download_dir):
            panel("✅ FFmpeg 安装完成；本项目会在运行期自动接入该目录。", style="green")
            return True, False
        panel("❌ 自动安装失败，请按上面的手动方案放好 ffmpeg.exe / ffprobe.exe。",
              style="red")
        return False, False

    hints = {
        "Darwin": "brew install ffmpeg",
        "Linux": "sudo apt install ffmpeg   # Debian/Ubuntu\n"
                 "sudo dnf install ffmpeg   # Fedora/RHEL",
    }
    panel(f"❌ 未找到合规的 FFmpeg（需要大版本 "
          f"{FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR}）。\n\n安装方式：\n"
          f"{hints.get(system, '请用发行版包管理器安装')}", style="red")
    return False, False


def install_cjk_fonts():
    """Linux 上安装 **CJK** 字体。

    dev 原实现装的是 `fonts-noto`，它不包含中日韩字形 —— 装了中文字幕照样是
    豆腐块。这里改为各发行版真正的 CJK 包。
    """
    if platform.system() != "Linux":
        return
    if shutil.which("fc-match"):
        probe = subprocess.run(["fc-match", "Noto Sans CJK SC"],
                               capture_output=True, text=True)
        if "Noto" in (probe.stdout or ""):
            info("✅ 已存在 Noto CJK 字体", style="green")
            return

    candidates = [
        ("/etc/debian_version", ["apt-get", "install", "-y", "fonts-noto-cjk"]),
        ("/etc/redhat-release", ["dnf", "install", "-y", "google-noto-sans-cjk-fonts"]),
        ("/etc/arch-release", ["pacman", "-S", "--noconfirm", "noto-fonts-cjk"]),
    ]
    for marker, cmd in candidates:
        if os.path.exists(marker):
            full = (["sudo"] + cmd) if os.geteuid() != 0 else cmd
            info(f"🔤 安装 CJK 字体：{' '.join(cmd)}")
            run(full, retries=1, check=False)
            if shutil.which("fc-cache"):
                run(["fc-cache", "-f"], retries=1, check=False)
            return
    info("⚠️ 无法识别的 Linux 发行版，请手动安装 Noto CJK 字体", style="yellow")


# ---------------------------------------------------------------- 体检
def _ensure_runtime_libraries():
    """把项目内 FFmpeg 的 DLL 目录接进**本进程**，供 smoke 里的 torchcodec 导入用。

    为什么体检必须做这一步（2026-09-19 实测 bug）：
    torchcodec 通过 FFmpeg **共享库**解码，而 Python 3.8 起扩展模块（.pyd）的
    依赖 DLL **不再从 PATH 解析**，只认 `os.add_dll_directory()` 注册的目录。
    `runtime_libraries.setup()` 做的正是这件事，但它只在 `import core` 时被触发
    （`core/__init__.py`），而体检是**直接** `import torchcodec.decoders` ——
    于是即使 FFmpeg 7.1.5 shared 已经装得完全正确，torchcodec 仍会失败并报
    「Could not find module '...libtorchcodec_core7.dll' (or one of its dependencies)」，
    让人误以为 FFmpeg 版本不对。实测：不接 DLL 目录必失败，接了就成功。

    导入失败不致命（非 Windows、或仓库不完整时），静默跳过。
    """
    try:
        import runtime_libraries  # 模块级已调用 setup()，这里再显式调一次更明确
        return runtime_libraries.setup()
    except Exception:
        return None


def smoke_imports(quiet=False):
    """逐个 import 新栈的关键包，返回失败清单。

    torchcodec.decoders 放最后并单独报告：它依赖 FFmpeg 共享库，
    Windows 上最容易失败，失败原因通常与 DLL 目录有关 —— 所以这里**先**把
    项目内 FFmpeg 的 DLL 目录接进本进程，否则会把"没接 DLL"误报成
    "FFmpeg 版本不合规"（见 `_ensure_runtime_libraries()`）。
    """
    runtime_report = _ensure_runtime_libraries()
    if runtime_report and not quiet:
        dll_dirs = runtime_report.get("dll_dirs") or []
        info(f"   已接入 DLL 目录：{len(dll_dirs)} 个"
             f"（项目内 FFmpeg：{runtime_report.get('project_ffmpeg') or '未使用'}）",
             style="bright_black")

    failures = []
    for module in (*SMOKE_IMPORTS_CORE, *SMOKE_IMPORTS_OPTIONAL):
        try:
            __import__(module)
            if not quiet:
                info(f"   ✓ import {module}", style="green")
        except Exception as e:
            failures.append((module, e))
            if not quiet:
                info(f"   ✗ import {module}：{e}", style="red")
                # torchcodec 的失败信息很长且把"没接 DLL"和"版本不对"混在一起，
                # 这里补一句本项目的判据，避免误判。
                if module.startswith("torchcodec"):
                    _explain_torchcodec_failure(runtime_report)
    return failures


def _explain_torchcodec_failure(runtime_report=None):
    """torchcodec 导入失败时，给出本项目的可行动判据。"""
    bin_dir = (runtime_report or {}).get("project_ffmpeg")
    if bin_dir:
        av = sorted(p.name for p in Path(bin_dir).glob("avcodec-*.dll"))
        if av:
            info(f"   ℹ️ 项目内 FFmpeg 是共享库构建（{', '.join(av)}），"
                 f"且已注册 DLL 目录；若上面仍报「找不到 core7.dll 或其依赖」，"
                 f"多半是 torchcodec 与本机 torch 不匹配，而不是 FFmpeg 的问题。",
                 style="yellow")
        else:
            info(f"   ℹ️ 项目内 FFmpeg（{bin_dir}）里**没有 avcodec-*.dll**，"
                 f"说明它是静态构建，torchcodec 用不了 —— 需要 4–7 的 shared 构建。",
                 style="yellow")
    else:
        info("   ℹ️ 没有接入项目内 FFmpeg 目录；先跑 Install.bat 装一份 4–7 的 "
             "shared 构建，或把 ffmpeg.exe/ffprobe.exe 放进 ./ffmpeg/bin/。",
             style="yellow")


def health_check(quiet=False, smoke=False):
    """体检并返回错误条数。

    覆盖 dev 原先完全没有验证的部分：Python 版本、关键包能否 import、
    torch/torchaudio 是否同源同版本、ffmpeg/ffprobe 大版本、CJK 字体、配置文件。
    """
    errors, warnings = [], []

    if not python_ok():
        errors.append(f"Python 版本 {platform.python_version()} 不在支持范围 "
                      f"{_python_version_label()} 内（whisperx 3.8 的要求）")
    elif sys.version_info[:2] != PYTHON_RECOMMENDED and not quiet:
        info(f"ℹ️ 当前 Python {platform.python_version()}；本项目推荐 "
             f"{PYTHON_RECOMMENDED[0]}.{PYTHON_RECOMMENDED[1]}"
             f"（Windows 上 av 17/18 只发 cp310/cp311 轮子）", style="bright_black")

    for module, pip_name in REQUIRED_IMPORTS.items():
        try:
            __import__(module)
        except Exception as e:
            errors.append(f"无法 import {module}（pip: {pip_name}）：{e}")

    # torch / torchaudio / torchvision 必须同版本同构建来源，否则运行期会报符号错
    try:
        import torch
        import torchaudio
        if torch.__version__.split("+")[0] != torchaudio.__version__.split("+")[0]:
            errors.append(f"torch {torch.__version__} 与 torchaudio "
                          f"{torchaudio.__version__} 版本不一致")
        expected = TORCH_VERSION
        if torch.__version__.split("+")[0] != expected:
            errors.append(f"torch 版本为 {torch.__version__}，新栈要求 {expected}"
                          f"（请运行 python installer.py）")
        try:
            import torchvision
            if torchvision.__version__.split("+")[0] != TORCHVISION_VERSION:
                warnings.append(f"torchvision 版本为 {torchvision.__version__}，"
                                f"要求 {TORCHVISION_VERSION}")
        except Exception as e:
            warnings.append(f"无法 import torchvision：{e}")

        if not quiet:
            info(f"   torch {torch.__version__} | CUDA 可用：{torch.cuda.is_available()}")
            if torch.cuda.is_available():
                info(f"   GPU：{torch.cuda.get_device_name(0)}")
                cap = detect_gpu_compute_cap()
                if cap is not None and f"sm_{int(cap*10)}" not in \
                        (torch.cuda.get_arch_list() if hasattr(torch.cuda, "get_arch_list") else []):
                    warnings.append(
                        f"当前 torch 构建未包含本机算力 sm_{int(cap * 10)} "
                        f"（编译架构：{getattr(torch.cuda, 'get_arch_list', lambda: [])()}）。"
                        f"常见原因：老卡（算力 < 7.0）装了 cu128/cu129 —— "
                        f"请用 --torch-backend cu126 重装")
    except Exception:
        pass

    # FFmpeg / ffprobe：不只看有没有，还要看大版本是否被 torchcodec 支持
    local_bin = project_ffmpeg_bin()
    ffmpeg_exe = (str(local_bin / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
                  if local_bin else shutil.which("ffmpeg"))
    ffprobe_exe = (str(local_bin / ("ffprobe.exe" if os.name == "nt" else "ffprobe"))
                   if local_bin else shutil.which("ffprobe"))
    if not (ffmpeg_exe and (ffprobe_exe or shutil.which("ffprobe"))):
        errors.append("未找到 ffmpeg / ffprobe（两者都需要）")
    else:
        version = ffmpeg_version_of(ffmpeg_exe)
        if version is None:
            warnings.append(f"无法解析 FFmpeg 版本：{ffmpeg_exe}")
        elif not ffmpeg_major_ok(version[0]):
            errors.append(f"FFmpeg 大版本 {version[0]} 不受 torchcodec 0.7 支持"
                          f"（需要 {FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR}）：{ffmpeg_exe}")
        elif not quiet:
            info(f"   ffmpeg {'.'.join(map(str, version))}：{ffmpeg_exe}")

    if platform.system() == "Linux" and shutil.which("fc-match"):
        probe = subprocess.run(["fc-match", "Noto Sans CJK SC"],
                               capture_output=True, text=True)
        if "Noto" not in (probe.stdout or ""):
            warnings.append("未检测到 Noto CJK 字体，烧录中文字幕可能显示为方块")

    if not Path("config.yaml").exists():
        if Path("config.example.yaml").exists():
            warnings.append("尚无 config.yaml（首次运行会从 config.example.yaml 自动创建）")
        else:
            errors.append("缺少 config.example.yaml，无法引导配置")

    if smoke:
        failures = smoke_imports(quiet=quiet)
        for module, exc in failures:
            hint = ""
            if module.startswith("torchcodec"):
                hint = ("（FFmpeg 共享库问题：确认 FFmpeg 大版本 4–7，"
                        "或把 ffmpeg.exe/ffprobe.exe 放进 ./ffmpeg/bin/ 让本项目自动接入）")
            errors.append(f"import {module} 失败：{exc} {hint}".strip())

    # quiet 模式（供启动器的修复循环调用）只输出**问题**，不输出"通过"之类的噪音
    if not quiet:
        for w in warnings:
            info(f"⚠️ {w}", style="yellow")
    for e in errors:
        info(f"❌ {e}", style="red")
    if not quiet and not errors:
        info("✅ 体检通过", style="green")
    return len(errors)


# ---------------------------------------------------------------- 解释器重定向
def _resolve_python(spec):
    """把 --python 的取值解析成绝对路径（支持 3.11 / 路径 / 命令名）。"""
    candidate = Path(spec)
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(spec)
    if found:
        return Path(found).resolve()
    # 形如 "3.11"：交给 uv 或 py 启动器
    uv = shutil.which("uv")
    if uv and re.match(r"^\d+\.\d+(\.\d+)?$", spec):
        try:
            proc = subprocess.run([uv, "python", "find", spec],
                                  capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and proc.stdout.strip():
                return Path(proc.stdout.strip().splitlines()[-1]).resolve()
        except (OSError, subprocess.SubprocessError):
            pass
    if os.name == "nt":
        try:
            proc = subprocess.run(["py", f"-{spec}", "-c", "import sys;print(sys.executable)"],
                                  capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                return Path(proc.stdout.strip()).resolve()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def reexec_into(target: Path, argv):
    """在目标解释器里重跑本脚本（去掉 --python，避免递归）。"""
    env = {**os.environ, "VIDEOLINGO_INSTALLER_REEXEC": "1"}
    cmd = [str(target), str(Path(__file__).resolve()), *argv]
    return subprocess.run(cmd, env=env).returncode


# ---------------------------------------------------------------- 主流程
def build_parser():
    parser = argparse.ArgumentParser(description="VideoLingo 安装 / 体检")
    parser.add_argument("--check", action="store_true", help="只体检不安装")
    parser.add_argument("--quiet", action="store_true", help="体检时只输出问题")
    parser.add_argument("--smoke", action="store_true",
                        help="额外做 L1 import 冒烟（whisperx/torchcodec/pyannote…）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要执行的安装计划，不做任何改动")
    parser.add_argument("--torch-backend", default="auto",
                        choices=["auto", *TORCH_SPECS.keys()],
                        help="PyTorch 轮子来源；auto=按显卡算力自动选择（默认）")
    parser.add_argument("--python", default=None,
                        help="用指定的解释器安装/体检（如 .venv\\Scripts\\python.exe 或 3.11）")
    parser.add_argument("--download-dir", default=None,
                        help=f"大文件（torch 轮子、FFmpeg 压缩包）存放目录，"
                             f"默认 ./{DEFAULT_DOWNLOAD_DIR}")
    parser.add_argument("--download-only", action="store_true",
                        help="只打印大文件的下载地址清单后退出（用外部工具下好后重跑即可）")
    # 故意不提供 --yes：本脚本全程非交互（不提问、不确认），没有需要"跳过"的环节
    parser.add_argument("--force", action="store_true", help="忽略安装状态，强制重装")
    parser.add_argument("--auto-mirror", action="store_true",
                        help="允许自动切换 pip 镜像（默认不改动用户全局设置）")
    parser.add_argument("--launch", action="store_true", help="装完启动应用")
    parser.add_argument("--no-launch", action="store_true", help="装完不启动")
    return parser


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(args_list)

    # --python：重定向到另一个解释器执行整套流程（setup_env.py 建的项目内 .venv）
    if args.python and not os.environ.get("VIDEOLINGO_INSTALLER_REEXEC"):
        target = _resolve_python(args.python)
        if target is None:
            panel(f"❌ 找不到解释器：{args.python}\n"
                  f"（可以先跑 python setup_env.py 建项目内 .venv，"
                  f"或直接给出完整路径）", style="red")
            return 1
        if target != Path(sys.executable).resolve():
            info(f"🔁 切换到解释器：{target}", style="cyan")
            forwarded = []
            skip_next = False
            for item in args_list:
                if skip_next:
                    skip_next = False
                    continue
                if item == "--python":
                    skip_next = True
                    continue
                if item.startswith("--python="):
                    continue
                forwarded.append(item)
            return reexec_into(target, forwarded)

    if args.check:
        return 1 if health_check(quiet=args.quiet, smoke=args.smoke) else 0

    if args.download_only:
        backend, _reason = detect_torch_backend(args.torch_backend)
        return print_full_download_plan(backend, args.download_dir)

    if args.dry_run:
        backend, reason = detect_torch_backend(args.torch_backend)
        index_url, label = TORCH_SPECS[backend]
        panel(f"🧪 干跑：不做任何改动\n"
              f"Python：{sys.version.split()[0]}（{sys.executable}）\n"
              f"torch 后端：{backend} —— {label}\n依据：{reason}", style="cyan")
        install_torch(args.torch_backend, dry_run=True, download_dir=args.download_dir)
        if uv_exe() is not None:
            info(f"   [dry-run] uv pip compile requirements.txt --generate-hashes "
                 f"--torch-backend {backend} -o {UV_LOCK_FILE}")
            info(f"   [dry-run] uv pip install --python <解释器> -r {UV_LOCK_FILE}")
        else:
            info(f"   [dry-run] pip download -r requirements.txt -d "
                 f"{ensure_download_dir(args.download_dir) / 'python'}")
            info(f"   [dry-run] pip install -r requirements.txt "
                 f"--no-index --find-links {ensure_download_dir(args.download_dir) / 'python'}")
        ensure_ffmpeg(dry_run=True)
        return 0

    # 安装后端先自证：uv 建的 venv 默认没有 pip，必须在这里就发现并修好，
    # 否则后面每个 pip 调用都会各自炸一次（实测就是这样丢掉真实错误的）。
    base, kind = pip_command()
    if base is None:
        explain_missing_pip()
        return 1
    if not args.quiet:
        info(f"🧰 安装后端：{'pip' if kind == 'pip' else 'uv pip（当前环境没有 pip）'}")

    # 引导依赖：rich 用于输出，ruamel.yaml / requests 供 core 使用
    try:
        import rich  # noqa: F401
    except ImportError:
        pip(["requests", "rich", "ruamel.yaml"], retries=2, check=False,
            cache_dir=PIP_DL_CACHE_DIR)

    info(r"""
__     ___     _            _     _
\ \   / (_) __| | ___  ___ | |   (_)_ __   __ _  ___
 \ \ / /| |/ _` |/ _ \/ _ \| |   | | '_ \ / _` |/ _ \
  \ V / | | (_| |  __/ (_) | |___| | | | | (_| | (_) |
   \_/  |_|\__,_|\___|\___/|_____|_|_| |_|\__, |\___/
                                          |___/
""", style="bright_blue")
    panel("🚀 开始安装 VideoLingo（torch 2.8 / whisperx 3.8 新栈）", style="bold magenta")

    if not python_ok():
        panel(f"❌ Python {platform.python_version()} 不受支持。\n"
              f"请使用 {_python_version_label()}"
              f"（推荐 {PYTHON_RECOMMENDED[0]}.{PYTHON_RECOMMENDED[1]}）。\n"
              f"最省事的方式：python setup_env.py --python "
              f"{PYTHON_RECOMMENDED[0]}.{PYTHON_RECOMMENDED[1]}", style="red")
        return 1

    # FFmpeg 是硬依赖（且大版本会影响 torchcodec）；已有可用版本时会跳过下载
    ffmpeg_ok, needs_restart = ensure_ffmpeg(download_dir=args.download_dir)
    if not ffmpeg_ok:
        return 1
    if needs_restart:
        info("ℹ️ 请重启终端后重新运行本脚本。", style="yellow")
        return 1

    maybe_configure_mirror(args.auto_mirror)

    # 状态指纹：requirements 与 torch 版本都没变就跳过，重复运行安装脚本
    # 不该每次都重装一遍几个 GB 的依赖。
    current_hash = requirements_hash()
    state = read_state()
    if not args.force and state.get("requirements_hash") == current_hash:
        panel("✅ 环境已与 requirements 指纹一致，跳过基础安装。\n"
              "（需要强制重装请加 --force）", style="green")
        backend = state.get("torch_backend", args.torch_backend)
    else:
        backend = install_torch(args.torch_backend, download_dir=args.download_dir)
        info("📦 安装 requirements.txt ...")
        uv_cache = os.path.join(args.download_dir or DEFAULT_DOWNLOAD_DIR, ".uv-cache")
        installed = False
        if uv_exe() is not None:
            # uv 原生路径：先用 --generate-hashes 把整个依赖图锁死，再按锁定文件
            # 安装。带 --torch-backend 是必须的 —— 否则 uv 会解析出 PyPI 上的
            # CPU 版 torch（实测 torch==2.8.0，没有 +cu126 后缀）。
            lock = uv_lock_requirements(cache_dir=uv_cache,
                                        torch_backend=backend)
            if lock is not None:
                uv_install_locked(lock, cache_dir=uv_cache)
                installed = True
        if not installed:
            # 没有 uv 时的 pip 回退：先下载到项目内目录，再优先用这些文件安装
            pkg_dir = ensure_download_dir(args.download_dir) / "python"
            pip_download("requirements.txt", pkg_dir, cache_dir=PIP_DL_CACHE_DIR)
            pip_install_local_dir(pkg_dir, "requirements.txt", cache_dir=PIP_DL_CACHE_DIR)
        write_state(backend)

    if platform.system() == "Linux":
        install_cjk_fonts()

    # 装完立刻在子进程里验证 GPU 可见性（本进程的 torch 可能还是旧的）
    gpu_ok, desc = verify_torch_gpu()
    if not args.quiet:
        info(f"🔎 {desc}")
    if backend != "cpu" and not gpu_ok:
        info("⚠️ torch 看不到 CUDA。常见原因：显卡驱动过旧，或后端与本机架构不匹配。"
             "可试 `python installer.py --check --smoke` 查看详情。", style="yellow")

    errors = health_check(quiet=False, smoke=True)
    if errors:
        panel(f"❌ 安装结束但体检发现 {errors} 个问题，请按上面的提示处理。", style="red")
        return 1

    panel("✅ 安装完成", style="bold green")
    info("启动应用：[bold cyan]python launch.py[/bold cyan] 或 "
         "[bold cyan]streamlit run st.py[/bold cyan]")

    if args.launch and not args.no_launch:
        return launch()
    return 0


def launch():
    launch_py = Path("launch.py")
    if launch_py.exists():
        return subprocess.run([sys.executable, str(launch_py)]).returncode
    return subprocess.run([sys.executable, "-m", "streamlit", "run", "st.py"]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
