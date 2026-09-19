"""用 uv 在**项目内**建虚拟环境并装依赖（取代 conda videolingo）。

为什么要有这个脚本
------------------
dev 原先三个启动脚本都硬编码 `activate.bat videolingo`，而 conda 的环境在
`C:\\Users\\<你>\\anaconda3\\envs\\` —— 正是"不想占 C 盘"的那部分。上游 3.x 已
改成 uv + 项目内 `.venv`，本文件按同一思路实现，并额外把所有**缓存**也指到
项目内，做到"整个项目目录可搬移、不动系统盘"。

它做什么
--------
1. 建 `<项目>/.venv`（uv venv，Python 版本由 uv 自己下载，不依赖 conda）；
2. 建并把缓存变量指向项目内：HF_HOME / TORCH_HOME / UV_CACHE_DIR / PIP_CACHE_DIR；
3. 用该解释器调用 `installer.py`（自动选择 cu126/cu128/cu129/cpu 轮子）；
4. 打印项目内目录布局与后续启动方式。

用法：
    python setup_env.py                       # 默认 Python 3.11 + 安装依赖
    python setup_env.py --python 3.13         # 跟上游用 3.13
    python setup_env.py --no-install          # 只建环境
    python setup_env.py --check               # 只体检（环境已存在时）
    python setup_env.py --venv D:\\envs\\vl     # 环境建在别处（缓存仍在项目内）
    python setup_env.py --recreate            # 删掉重建
    python setup_env.py --print-env           # 只打印环境变量（供 .bat 引用）

默认值刻意与 `installer.py --torch-backend auto` 保持一致：Python 版本只影响
轮子可得性，CUDA 后端由显卡算力自动决定。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from easy_util import ensure_utf8_console
    ensure_utf8_console()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DEFAULT_PYTHON = "3.11"          # 与方案一致；3.10–3.13 都可用，见下
SUPPORTED_PYTHONS = ("3.10", "3.11", "3.12", "3.13")
UV_INSTALL_HINT = (
    "Windows:  winget install --id=astral-sh.uv\n"
    "          或 powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"\n"
    "Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh"
)

# 项目内缓存目录：与 devdocs/05-guides/08-环境大升级方案.md 第四节一致
CACHE_DIRS = {
    "HF_HOME": "_model_cache",
    "TORCH_HOME": os.path.join("_model_cache", "torch"),
    "UV_CACHE_DIR": ".uv-cache",
    "PIP_CACHE_DIR": ".pip-cache",
}
HF_MIRROR = "https://hf-mirror.com"


# ---------------------------------------------------------------- 输出
def info(msg, style=None):
    try:
        from rich import print as rprint
        rprint(msg, style=style) if style else rprint(msg)
    except Exception:
        print(msg)


def panel(msg, style="cyan"):
    try:
        from rich.panel import Panel
        from rich.console import Console
        Console().print(Panel.fit(msg, style=style))
    except Exception:
        print(msg)


# ---------------------------------------------------------------- 环境变量
def build_env(mirror=False, hf_endpoint=None, base=None):
    """构造项目内缓存环境变量字典（不修改 os.environ）。"""
    env = dict(base or os.environ)
    for var, rel in CACHE_DIRS.items():
        env[var] = str(ROOT / rel)
    if hf_endpoint:
        env["HF_ENDPOINT"] = hf_endpoint
    elif mirror:
        env["HF_ENDPOINT"] = HF_MIRROR
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def apply_env(env):
    """把环境变量写回当前进程（供随后调用的子进程继承）。"""
    os.environ.update(env)


def ensure_cache_dirs():
    created = []
    for rel in CACHE_DIRS.values():
        path = ROOT / rel
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(rel)
    return created


# ---------------------------------------------------------------- uv
def find_uv():
    exe = shutil.which("uv")
    if exe:
        return exe
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "uv.exe",
                      home / ".local" / "bin" / "uv",
                      home / ".cargo" / "bin" / "uv.exe",
                      home / ".cargo" / "bin" / "uv"):
        if candidate.is_file():
            return str(candidate)
    return None


def uv_env_supported_version():
    """返回 uv 是否可用以及它认为的 Python（失败返回 None）。"""
    uv = find_uv()
    if not uv:
        return None
    try:
        proc = subprocess.run([uv, "python", "find", DEFAULT_PYTHON],
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip().splitlines()[-1])
    return None


def create_venv_uv(venv_dir, python_version, recreate=False):
    uv = find_uv()
    cmd = [uv, "venv", str(venv_dir), "--python", python_version]
    if recreate:
        cmd.append("--clear")
    info(f"🐍 用 uv 建虚拟环境：{' '.join(cmd)}", style="cyan")
    # 缓存目录已由 ensure_cache_dirs() 建好，UV_CACHE_DIR 已写入 os.environ
    return subprocess.run(cmd, env=build_env(
        hf_endpoint=os.environ.get("HF_ENDPOINT"))).returncode


def create_venv_stdlib(venv_dir, recreate=False):
    """没有 uv 时的回退：用当前解释器的 venv 模块（无法自选 Python 版本）。"""
    if recreate and venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)
    info(f"🐍 未找到 uv，回退到当前解释器建 venv（{sys.executable}）", style="yellow")
    return subprocess.run([sys.executable, "-m", "venv", str(venv_dir)]).returncode


def venv_python(venv_dir):
    for rel in ("Scripts/python.exe", "bin/python", "Scripts/python"):
        candidate = venv_dir / rel
        if candidate.is_file():
            return candidate.resolve()
    return None


def describe_python(exe):
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import sys;print(sys.version.split()[0]);print(sys.executable)"],
            capture_output=True, text=True, timeout=60)
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        return lines[0] if lines else "未知"
    except (OSError, subprocess.SubprocessError):
        return "未知"


def version_supported(version):
    if version in SUPPORTED_PYTHONS:
        return True
    if version.startswith("3.") and version[2:].isdigit():
        minor = int(version[2:])
        return 10 <= minor <= 13
    return False


# ---------------------------------------------------------------- 主流程
def build_parser():
    parser = argparse.ArgumentParser(
        description="用 uv 在项目内建虚拟环境并安装依赖（取代 conda）")
    parser.add_argument("--python", default=DEFAULT_PYTHON,
                        help=f"Python 版本（默认 {DEFAULT_PYTHON}，支持 "
                             f"{'/'.join(SUPPORTED_PYTHONS)}）")
    parser.add_argument("--venv", default=None,
                        help="虚拟环境目录（默认 <项目>/.venv）")
    parser.add_argument("--recreate", action="store_true", help="删掉已有环境重建")
    parser.add_argument("--no-install", action="store_true", help="只建环境，不装依赖")
    parser.add_argument("--check", action="store_true", help="只体检（不建环境、不安装）")
    parser.add_argument("--smoke", action="store_true",
                        help="体检/安装后附带 L1 import 冒烟"
                             "（whisperx/torchcodec/pyannote 等逐个 import）")
    parser.add_argument("--torch-backend", default="auto",
                        help="透传给 installer.py（auto/cu126/cu128/cu129/cpu）")
    parser.add_argument("--hf-mirror", action="store_true",
                        help=f"设置 HF_ENDPOINT={HF_MIRROR}（国内加速）")
    parser.add_argument("--print-env", action="store_true",
                        help="只打印环境变量与解释器路径，供脚本引用")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    venv_dir = Path(args.venv).resolve() if args.venv else (ROOT / ".venv")

    env = build_env(mirror=args.hf_mirror)
    apply_env(env)

    if args.print_env:
        for var in (*CACHE_DIRS, "HF_ENDPOINT"):
            if var in env:
                print(f"set \"{var}={env[var]}\"")
        print(f"set \"VIDEOLINGO_PYTHON={venv_python(venv_dir) or ''}\"")
        return 0

    if args.check:
        python_exe = venv_python(venv_dir)
        if python_exe is None:
            panel(f"❌ 未找到虚拟环境：{venv_dir}\n"
                  f"请先运行：python setup_env.py", style="red")
            return 1
        cmd = [str(python_exe), "installer.py", "--check", "--python", str(python_exe)]
        if args.smoke:
            cmd.append("--smoke")
        return subprocess.run(cmd, env=env).returncode

    panel(f"🧰 建立项目内环境\n"
          f"项目根：{ROOT}\n"
          f"虚拟环境：{venv_dir}\n"
          f"目标 Python：{args.python}", style="cyan")

    if not version_supported(args.python):
        panel(f"❌ Python {args.python} 不在支持范围 "
              f"{SUPPORTED_PYTHONS[0]}–{SUPPORTED_PYTHONS[-1]} 内"
              f"（whisperx 3.8 要求 3.10–3.13）", style="red")
        return 1

    created = ensure_cache_dirs()
    if created:
        info(f"📁 已建项目内缓存目录：{', '.join(created)}", style="green")

    uv = find_uv()
    if uv is None:
        panel(f"⚠️ 未找到 uv，将回退到当前解释器自带的 venv。\n"
              f"后果：无法自选 Python 版本，也不会自动下载 Python。\n\n"
              f"安装 uv 后重跑可得到与方案一致的布局：\n{UV_INSTALL_HINT}", style="yellow")
        if venv_dir.exists() and not args.recreate:
            info(f"ℹ️ 虚拟环境已存在，跳过创建：{venv_dir}", style="bright_black")
        else:
            if create_venv_stdlib(venv_dir, recreate=args.recreate) != 0:
                panel("❌ 创建虚拟环境失败", style="red")
                return 1
    elif venv_dir.exists() and not args.recreate:
        python_exe = venv_python(venv_dir)
        info(f"ℹ️ 虚拟环境已存在：{venv_dir}"
             f"（Python {describe_python(python_exe) if python_exe else '未知'}）"
             f"；需要重建请加 --recreate", style="bright_black")
    else:
        if create_venv_uv(venv_dir, args.python, recreate=args.recreate) != 0:
            panel("❌ uv 建虚拟环境失败", style="red")
            return 1

    python_exe = venv_python(venv_dir)
    if python_exe is None:
        panel(f"❌ 建好环境后仍找不到解释器：{venv_dir}", style="red")
        return 1
    info(f"✅ 解释器：{python_exe}（Python {describe_python(python_exe)}）", style="green")

    # 让 uv 之后的调用与运行期都用同一套缓存
    env["VIRTUAL_ENV"] = str(venv_dir)
    apply_env(env)

    if args.no_install:
        info("⏭️ --no-install：跳过依赖安装", style="bright_black")
        print_next_steps(venv_dir, python_exe)
        return 0

    cmd = [str(python_exe), str(ROOT / "installer.py"),
           "--torch-backend", args.torch_backend, "--python", str(python_exe)]
    if args.smoke:
        # installer.py 装完本来就会体检；--smoke 让它额外逐个 import
        # whisperx / torchcodec / pyannote 等，正是新栈最容易出问题的地方
        cmd.append("--smoke")
    info(f"📦 调用安装器：{' '.join(cmd)}", style="cyan")
    code = subprocess.run(cmd, env=env, cwd=str(ROOT)).returncode
    if code != 0:
        panel(f"❌ 依赖安装失败（退出码 {code}）。\n"
              f"可单独重试：{python_exe} installer.py --check --smoke", style="red")
        return code

    print_next_steps(venv_dir, python_exe)
    return 0


def print_next_steps(venv_dir, python_exe):
    panel(
        "✅ 项目内环境就绪\n\n"
        f"解释器：{python_exe}\n"
        f"环境：{venv_dir}\n"
        f"缓存：{ROOT / '_model_cache'} / {ROOT / '.uv-cache'} / {ROOT / '.pip-cache'}\n\n"
        f"启动：{python_exe} launch.py\n"
        f"体检：{python_exe} installer.py --check --smoke\n\n"
        "（三个 .bat 会自动优先使用 .venv，检测不到才回退 conda）",
        style="green")


if __name__ == "__main__":
    raise SystemExit(main())
