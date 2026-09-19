"""VideoLingo 安装与体检脚本（torch 2.8 / whisperx 3.8 新栈）。

本文件在 `upgrade/env-torch28` 分支上完成了环境大升级方案的第四节与第七节：
  * torch 2.1.2+cu118 → **torch 2.8.0**，CUDA 轮子按显卡算力自动选择
    （cu126 / cu128 / cu129 / cpu）；
  * Python 闸门放宽到 3.10–3.13（whisperx 3.8 的 requires_python）；
  * FFmpeg 改为**大版本校验 + 7.x 下载**，取代原来"永远下 latest"
    （latest 已是 8.1/9.0，而 torchcodec 0.7 只支持 4–7）；下载地址不写死，
    改为运行时查 GitHub Releases API（可用 VIDELINGO_FFMPEG_URL 覆盖）；
  * 新增 L1 import 冒烟（whisperx / torchcodec / pyannote / ctranslate2 …），
    其中 `torchcodec.decoders` 是 Windows 上最容易失败的一个；
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

# torchcodec 0.7 只支持 FFmpeg 大版本 4–7；8/9 会在导入或解码时失败。
FFMPEG_MIN_MAJOR, FFMPEG_MAX_MAJOR = 4, 7
# 项目内 FFmpeg 想要的**分支**（不再用 master/latest —— 那已经是 8.1/9.0，
# 正好落在 torchcodec 不支持的区间，等于把环境装坏）。
#
# 为什么不写死下载链接（2026-09 实测）：
#   * BtbN/FFmpeg-Builds 的长期 tag 只有 `latest`，而且**会被覆盖** —— 实测
#     `latest` 下已经只剩 8.1/9.0 的资产，7.1 的资产随 autobuild 轮换消失；
#   * `7.1` / `7.0.2` 这类 tag 从来不存在；
#   * 带 7.1 资产的 autobuild tag（如 autobuild-2026-07-31-14-10，
#     ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip）验证可下载，但会被
#     GitHub 定期清理。
# 所以改为**运行时查询 GitHub Releases API** 挑一个 7.x 资产，并允许用
# VIDELINGO_FFMPEG_URL 覆盖（离线/内网场景）。
FFMPEG_WIN_BRANCH = "7.1"
FFMPEG_DIR_NAME = "ffmpeg"
FFMPEG_API_LATEST = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
FFMPEG_API_LIST = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=30"

STATE_FILE_NAME = ".videolingo-install.json"

# 体检时要求的包（导入名 -> pip 名提示）
REQUIRED_IMPORTS = {
    "streamlit": "streamlit",
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
def _console():
    from rich.console import Console
    return Console()


def info(msg, style=None):
    _console().print(msg, style=style)


def panel(msg, style="cyan", title=None):
    from rich.panel import Panel
    _console().print(Panel.fit(msg, style=style, title=title))


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


def pip(args, retries=2, check=True):
    """统一的 pip 调用，带上对国内网络更友好的参数。"""
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--prefer-binary",
           "--retries", "5", "--timeout", "120", *args]
    env = {**os.environ, "PIP_NO_INPUT": "1", "PYTHONIOENCODING": "utf-8"}
    return run(cmd, retries=retries, check=check, env=env)


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


def install_torch(backend="auto", dry_run=False):
    """安装与硬件匹配的 PyTorch 三件套 + torchcodec，返回实际使用的后端。"""
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

    pkgs = [f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}",
            f"torchvision=={TORCHVISION_VERSION}"]
    cmd = [*pkgs, "--index-url", index_url]
    # 本地 wheel 缓存约定：不写死 cp 标签，按当前解释器推导
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    local_wheel = Path(f"torch-{TORCH_VERSION}+{resolved}-{tag}-{tag}-win_amd64.whl")
    if (platform.system() == "Windows" and resolved != "cpu" and local_wheel.exists()):
        info(f"📦 发现本地 wheel，优先使用：{local_wheel.name}", style="green")
        cmd = [str(local_wheel), f"torchaudio=={TORCH_VERSION}",
               f"torchvision=={TORCHVISION_VERSION}", "--index-url", index_url]

    if dry_run:
        info(f"   [dry-run] pip install {' '.join(cmd)}")
        return resolved

    pip(cmd, check=True)
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


def _pick_ffmpeg_asset(releases):
    """从 releases 列表里挑一个 7.x 的 win64-gpl 资产，返回 (name, url) 或 None。

    排除 `-shared`：shared 版只带 avcodec 等 DLL、不带 ffmpeg.exe；非 shared
    版同时含 exe 与 DLL，一套就够（installer 要 exe，torchcodec 要 DLL）。
    """
    prefix = f"ffmpeg-n{FFMPEG_WIN_BRANCH}"
    for release in releases or []:
        for asset in release.get("assets") or []:
            name = asset.get("name") or ""
            if not name.startswith(prefix):
                continue
            if "win64-gpl" not in name or not name.endswith(".zip"):
                continue
            if "-shared" in name:
                continue
            return name, asset.get("browser_download_url")
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


def download_ffmpeg_windows(target_dir):
    """把**大版本合规**（7.x）的 FFmpeg 下载到项目内 ffmpeg/ 目录。

    原实现下的是 `master-latest`（现已是 8.1/9.0），而 torchcodec 0.7 只支持
    FFmpeg 4–7 —— 下最新版等于把环境装坏。

    zip 内通常有一层 `ffmpeg-n7.1.x-.../` 目录，`project_ffmpeg_bin()` 会自动
    往下一层找 bin，所以解压后无需移动文件。
    """
    import zipfile
    from urllib.request import urlretrieve

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    url, why = resolve_ffmpeg_url()
    if not url:
        info(f"❌ 无法确定 FFmpeg 下载地址（{why}）", style="red")
        _ffmpeg_manual_hint()
        return None

    info(f"🚀 正在下载 FFmpeg {FFMPEG_WIN_BRANCH}（{why}）")
    info(f"   {url}", style="bright_black")
    zip_path = target_dir / "ffmpeg.zip"
    try:
        urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
        os.remove(zip_path)
    except Exception as e:
        info(f"❌ 下载或解压 FFmpeg 失败：{e}", style="red")
        _ffmpeg_manual_hint()
        return None

    found = project_ffmpeg_bin()
    if found is None:
        info("❌ FFmpeg 解压后未找到可执行文件", style="red")
        _ffmpeg_manual_hint()
        return None
    info(f"✅ FFmpeg 已就位：{found}", style="green")
    return found


def _ffmpeg_manual_hint():
    info(f"   手动方案：下载 FFmpeg {FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR} 的 Windows "
         f"构建（https://github.com/BtbN/FFmpeg-Builds/releases 或 "
         f"https://www.gyan.dev/ffmpeg/builds/），把 ffmpeg.exe / ffprobe.exe 放进 "
         f"./{FFMPEG_DIR_NAME}/bin/ 即可 —— 本项目运行期会自动接入该目录，"
         f"不需要改 PATH。", style="yellow")


def ensure_ffmpeg(dry_run=False):
    """确保有一份**大版本合规**的 ffmpeg/ffprobe。返回 (ok, 是否需重启终端)。"""
    # 1) 项目内固定版优先
    local = project_ffmpeg_bin()
    if local is not None:
        version = ffmpeg_version_of(str(local / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")))
        if version and ffmpeg_major_ok(version[0]):
            info(f"✅ 使用项目内 FFmpeg {version[0]}.{version[1]}.{version[2]}：{local}",
                 style="green")
            return True, False
        info(f"⚠️ 项目内 FFmpeg 大版本不受支持（{version}），将重新获取", style="yellow")

    # 2) 系统 PATH
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    if system_ffmpeg and system_ffprobe:
        version = ffmpeg_version_of(system_ffmpeg)
        if ffmpeg_major_ok(version[0] if version else None):
            info(f"✅ 使用系统 FFmpeg {'.'.join(map(str, version))}：{system_ffmpeg}",
                 style="green")
            return True, False
        shown = ".".join(map(str, version)) if version else "无法识别版本"
        panel(f"⚠️ 系统 FFmpeg 大版本不受支持（{shown}）。\n"
              f"torchcodec 0.7 只支持 FFmpeg {FFMPEG_MIN_MAJOR}–{FFMPEG_MAX_MAJOR}，"
              f"8/9 会在解码时失败。", style="yellow")

    # 3) 自动补齐
    system = platform.system()
    if system == "Windows":
        if dry_run:
            url, why = resolve_ffmpeg_url()
            info(f"   [dry-run] 将下载 FFmpeg {FFMPEG_WIN_BRANCH} 到 "
                 f"./{FFMPEG_DIR_NAME}/（{why}）")
            if url:
                info(f"   [dry-run] {url}")
            else:
                info("   [dry-run] ⚠️ 解析下载地址失败，真实运行时会给出手动方案",
                     style="yellow")
            return True, True
        panel(f"⬇️ 未找到合规的 FFmpeg，下载 7.x 分支到项目内 "
              f"./{FFMPEG_DIR_NAME}/ …", style="yellow")
        if download_ffmpeg_windows(FFMPEG_DIR_NAME):
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
def smoke_imports(quiet=False):
    """逐个 import 新栈的关键包，返回失败清单。

    torchcodec.decoders 放最后并单独报告：它依赖 FFmpeg 共享库，
    Windows 上最容易失败，失败原因通常与 DLL 目录有关。
    """
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
    return failures


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

    if args.dry_run:
        backend, reason = detect_torch_backend(args.torch_backend)
        index_url, label = TORCH_SPECS[backend]
        panel(f"🧪 干跑：不做任何改动\n"
              f"Python：{sys.version.split()[0]}（{sys.executable}）\n"
              f"torch 后端：{backend} —— {label}\n依据：{reason}", style="cyan")
        install_torch(args.torch_backend, dry_run=True)
        info(f"   [dry-run] pip install -r requirements.txt")
        ensure_ffmpeg(dry_run=True)
        return 0

    # 引导依赖：rich 用于输出，ruamel.yaml / requests 供 core 使用
    try:
        import rich  # noqa: F401
    except ImportError:
        pip(["requests", "rich", "ruamel.yaml"], retries=2, check=False)

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

    # FFmpeg 是硬依赖（且大版本会影响 torchcodec），先确认再装一堆东西
    ffmpeg_ok, needs_restart = ensure_ffmpeg()
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
        backend = install_torch(args.torch_backend)
        info("📦 安装 requirements.txt ...")
        pip(["-r", "requirements.txt"], retries=2, check=True)
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
