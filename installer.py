"""VideoLingo 安装与体检脚本。

从上游 3.x 的 installer.py 移植结构与关键能力，但**刻意沿用 dev 当前的
固定依赖版本**（torch 2.1.2 + cu118），不引入上游那套 torch 2.8 / whisperx 3.8 /
Python 3.13 的升级 —— 环境大升级是独立的一步，需要单独验证。

相对 dev 原有 install.py 修复的实际缺陷：
  1. 无 GPU / macOS 分支**打印**"安装 CPU 版 PyTorch"却执行与 GPU 分支完全相同的
     cu118 命令（那段代码是复制粘贴来的）。现在真的按 CPU/CUDA 选择 wheel 源。
  2. check_nvidia_gpu() 依赖已废弃的 pynvml；改用 nvidia-smi 解析，零额外依赖。
  3. choose_mirror() 会**无条件改写用户全局的 pip index-url**；现在改为 --auto-mirror
     显式开启，默认不动用户环境。
  4. install_requirements() 吞掉 CalledProcessError，依赖装失败也报"完成"；
     现在失败即明确报错并以非零码退出。
  5. Linux 装的是 fonts-noto，不是 CJK 包（fonts-noto-cjk），中文字幕仍会是豆腐块。
  6. 没有重试、没有体检、没有安装状态记录。

用法：
    python install.py                # 等价于 installer.py --launch
    python installer.py --check       # 只体检，不安装（退出码 1 表示有错误）
    python installer.py --yes         # 非交互
    python installer.py --auto-mirror # 显式允许自动切换 pip 镜像
    python installer.py --no-launch   # 装完不启动
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
# dev 当前的固定版本（与 requirements.txt 保持一致）。升级这些值属于独立的
# 环境升级工作，不要在这里顺手改。
TORCH_VERSION = "2.1.2"
TORCHVISION_INDEX_SUFFIX = "cu118"
CUDA_INDEX = "https://download.pytorch.org/whl/cu118"
CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# dev 的本地 wheel 约定（用户手动下载后放在项目根目录）
LOCAL_TORCH_WHEEL = f"torch-{TORCH_VERSION}+cu118-cp310-cp310-win_amd64.whl"

# torch 2.1.2 + ctranslate2 4.4.0 的可用 Python 范围
PYTHON_MIN, PYTHON_MAX = (3, 10), (3, 13)

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


def python_ok():
    return PYTHON_MIN <= sys.version_info[:2] < PYTHON_MAX


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


def write_state():
    try:
        state_path().write_text(
            json.dumps({"requirements_hash": requirements_hash(),
                        "python": platform.python_version(),
                        "torch": TORCH_VERSION}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------- GPU / 镜像
def detect_cuda_version():
    """用 nvidia-smi 解析驱动支持的 CUDA 版本，返回 (major, minor) 或 None。

    不使用 pynvml：该包已废弃（上游改用 nvidia-ml-py），而 nvidia-smi 是
    驱动自带的，零额外依赖。
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


def install_torch(force=False):
    """安装与硬件匹配的 PyTorch。

    这是相对 dev 原脚本最实质的修复：原"无 GPU/macOS"分支打印 CPU 版提示，
    执行的却是同一套 cu118 命令。
    """
    system = platform.system()
    has_cuda = system != "Darwin" and detect_cuda_version() is not None

    local_wheel = Path(LOCAL_TORCH_WHEEL)
    if has_cuda and local_wheel.exists() and system == "Windows":
        panel(f"🎮 检测到 NVIDIA GPU，使用本地 wheel：{LOCAL_TORCH_WHEEL}", style="cyan")
        pip([f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}", str(local_wheel)],
            check=True)
        return

    if has_cuda:
        cuda = detect_cuda_version()
        panel(f"🎮 检测到 NVIDIA GPU（驱动支持 CUDA {cuda[0]}.{cuda[1]}），"
              f"安装 CUDA 版 PyTorch（{TORCHVISION_INDEX_SUFFIX}）", style="cyan")
        pip([f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}",
             "--index-url", CUDA_INDEX], check=True)
    else:
        reason = "macOS" if system == "Darwin" else "未检测到 NVIDIA GPU"
        panel(f"💻 {reason}，安装 CPU 版 PyTorch。\n"
              f"⚠️ CPU 转录会非常慢，强烈建议使用 NVIDIA GPU。", style="yellow")
        pip([f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}",
             "--index-url", CPU_INDEX], check=True)


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
def download_ffmpeg_windows(target_dir):
    import zipfile
    from urllib.request import urlretrieve

    url = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
           "ffmpeg-master-latest-win64-gpl.zip")
    zip_path = os.path.join(target_dir, "ffmpeg.zip")
    info(f"🚀 正在下载 FFmpeg：{url}")
    try:
        urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
        os.remove(zip_path)
        ffmpeg_bin = os.path.join(target_dir, "ffmpeg-master-latest-win64-gpl", "bin")
        if ffmpeg_bin not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + ffmpeg_bin
        info(f"✅ FFmpeg 已就位：{ffmpeg_bin}", style="green")
        return True
    except Exception as e:
        info(f"❌ 下载或解压 FFmpeg 失败：{e}", style="red")
        return False


def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True

    system = platform.system()
    if system == "Windows":
        panel("❌ 未找到 FFmpeg，尝试自动下载到项目目录…", style="red")
        if download_ffmpeg_windows(os.getcwd()):
            panel("✅ FFmpeg 安装完成。请**重启终端**后重新运行安装脚本。", style="green")
            return False
        panel("❌ 自动安装失败，请手动下载："
              "https://github.com/BtbN/FFmpeg-Builds/releases", style="red")
        return False

    hints = {
        "Darwin": "brew install ffmpeg",
        "Linux": "sudo apt install ffmpeg   # Debian/Ubuntu\n"
                 "sudo dnf install ffmpeg   # Fedora/RHEL",
    }
    panel(f"❌ 未找到 FFmpeg（必需）。\n\n安装方式：\n{hints.get(system, '请用发行版包管理器安装')}",
          style="red")
    return False


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
def health_check(quiet=False):
    """体检并返回错误条数。

    覆盖 dev 原先完全没有验证的部分：Python 版本、关键包能否 import、
    torch/torchaudio 是否同源同版本、ffmpeg/ffprobe、CJK 字体、配置文件。
    """
    errors, warnings = [], []

    if not python_ok():
        errors.append(f"Python 版本 {platform.python_version()} 不在支持范围 "
                      f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}–{PYTHON_MAX[0]}.{PYTHON_MAX[1] - 1} 内")

    for module, pip_name in REQUIRED_IMPORTS.items():
        try:
            __import__(module)
        except Exception as e:
            errors.append(f"无法 import {module}（pip: {pip_name}）：{e}")

    # torch / torchaudio 必须同版本同构建来源，否则运行期会报符号错
    try:
        import torch
        import torchaudio
        if torch.__version__.split("+")[0] != torchaudio.__version__.split("+")[0]:
            errors.append(f"torch {torch.__version__} 与 torchaudio "
                          f"{torchaudio.__version__} 版本不一致")
        expected = TORCH_VERSION
        if torch.__version__.split("+")[0] != expected:
            warnings.append(f"torch 版本为 {torch.__version__}，"
                            f"requirements 约定为 {expected}（环境大升级未执行属正常）")
        if not quiet:
            info(f"   torch {torch.__version__} | CUDA 可用：{torch.cuda.is_available()}")
            if torch.cuda.is_available():
                info(f"   GPU：{torch.cuda.get_device_name(0)}")
    except Exception:
        pass

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        errors.append("未找到 ffmpeg / ffprobe（两者都需要）")

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

    # quiet 模式（供启动器的修复循环调用）只输出**问题**，不输出"通过"之类的噪音
    if not quiet:
        for w in warnings:
            info(f"⚠️ {w}", style="yellow")
    for e in errors:
        info(f"❌ {e}", style="red")
    if not quiet and not errors:
        info("✅ 体检通过", style="green")
    return len(errors)


# ---------------------------------------------------------------- 主流程
def main(argv=None):
    parser = argparse.ArgumentParser(description="VideoLingo 安装 / 体检")
    parser.add_argument("--check", action="store_true", help="只体检不安装")
    parser.add_argument("--quiet", action="store_true", help="体检时只输出问题")
    parser.add_argument("--yes", action="store_true", help="非交互，跳过所有确认")
    parser.add_argument("--force", action="store_true", help="忽略安装状态，强制重装")
    parser.add_argument("--auto-mirror", action="store_true",
                        help="允许自动切换 pip 镜像（默认不改动用户全局设置）")
    parser.add_argument("--launch", action="store_true", help="装完启动应用")
    parser.add_argument("--no-launch", action="store_true", help="装完不启动")
    args = parser.parse_args(argv)

    if args.check:
        return 1 if health_check(quiet=args.quiet) else 0

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
    panel("🚀 开始安装 VideoLingo", style="bold magenta")

    if not python_ok():
        panel(f"❌ Python {platform.python_version()} 不受支持。\n"
              f"请使用 {PYTHON_MIN[0]}.{PYTHON_MIN[1]}–{PYTHON_MAX[0]}.{PYTHON_MAX[1] - 1}"
              f"（推荐 3.10，dev 的预编译 wheel 就是 cp310）。", style="red")
        return 1

    # FFmpeg 是硬依赖，先确认再装一堆东西
    if not ensure_ffmpeg():
        return 1

    maybe_configure_mirror(args.auto_mirror)

    # 状态指纹：requirements 与 torch 版本都没变就跳过，重复运行安装脚本
    # 不该每次都重装一遍几个 GB 的依赖。
    current_hash = requirements_hash()
    state = read_state()
    if not args.force and state.get("requirements_hash") == current_hash:
        panel("✅ 环境已与 requirements 指纹一致，跳过基础安装。\n"
              "（需要强制重装请加 --force）", style="green")
    else:
        install_torch(force=args.force)
        info("📦 安装 requirements.txt ...")
        pip(["-r", "requirements.txt"], retries=2, check=True)
        write_state()

    if platform.system() == "Linux":
        install_cjk_fonts()

    errors = health_check(quiet=False)
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
