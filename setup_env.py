"""用 uv 在**项目内**建虚拟环境并装依赖（取代 conda videolingo）。

为什么要有这个脚本
------------------
dev 原先三个启动脚本都硬编码 `activate.bat videolingo`，而 conda 的环境在
`C:\\Users\\<你>\\anaconda3\\envs\\` —— 正是"不想占 C 盘"的那部分。上游 3.x 已
改成 uv + 项目内 `.venv`，本文件按同一思路实现，并额外把所有**缓存**也指到
项目内，做到"整个项目目录可搬移、不动系统盘"。

它做什么
--------
1. 备好**项目内**的解释器 `<项目>/.python`（uv 自带下载，缺了就装一个）；
2. 用这个解释器当宿主建 `<项目>/.venv` —— 宿主在项目内，项目目录可整体搬走；
3. 建并把缓存变量指向项目内：HF_HOME / TORCH_HOME / UV_CACHE_DIR / PIP_CACHE_DIR /
   UV_PYTHON_INSTALL_DIR；
4. 用该解释器调用 `installer.py`（自动选择 cu126/cu128/cu129/cpu 轮子）；
5. 打印项目内目录布局与后续启动方式。

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
import re
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

#: 项目内 Python 的安装目录。**venv 的"宿主解释器"必须来自这里**。
#:
#: 为什么（2026-09-19 实测）：venv 只放第三方包，**标准库仍从宿主解释器读**。
#: 原先 `uv venv --python 3.11` 会让 uv 在整机范围内找一个 3.11 —— 本机它找到的是
#: `G:\Git\EOE Calendar Subscription Generator\python\python.exe`，于是
#: `<项目>\.venv\pyvenv.cfg` 写成 `home = G:\Git\...`：那个项目一删/一挪，
#: 本项目立刻不可用，std 库路径也会出现在本项目所有 traceback 里（很容易误判）。
#: 现在统一把 Python 装进项目内，宿主解释器 = `<项目>\.python\...`。
PROJECT_PYTHON_DIR = ".python"

# 项目内缓存目录：与 devdocs/05-guides/08-环境大升级方案.md 第四节一致
CACHE_DIRS = {
    "HF_HOME": "_model_cache",
    "TORCH_HOME": os.path.join("_model_cache", "torch"),
    "UV_CACHE_DIR": ".uv-cache",
    "PIP_CACHE_DIR": ".pip-cache",
    # uv 把"自己下载的 Python"也放进项目内，避免落到
    # %LOCALAPPDATA%\uv\python（又跑到 C 盘）。
    "UV_PYTHON_INSTALL_DIR": PROJECT_PYTHON_DIR,
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


def project_python_dir():
    return ROOT / PROJECT_PYTHON_DIR


def find_project_python(version=None):
    """在项目内找已装好的 Python 解释器；没有返回 None。

    形如 `<项目>/.python/cpython-3.11.x-<platform>-<arch>-<libc>/python.exe`。
    `version` 形如 "3.11"：项目内可能同时存在多个版本（先装 3.11 后改 3.12），
    此时按目录名前缀挑，挑不到再逐个问解释器自己。
    """
    base = project_python_dir()
    if not base.is_dir():
        return None
    candidates = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        for name in ("python.exe", "bin/python3", "bin/python"):
            exe = entry / name
            if exe.is_file():
                candidates.append(exe)
                break
    if not candidates:
        return None
    if version is None:
        return candidates[-1]
    for exe in candidates:
        if exe.parent.name.startswith(f"cpython-{version}."):
            return exe
    for exe in candidates:
        got = interpreter_version(exe)
        if got is not None and f"{got[0]}.{got[1]}" == version:
            return exe
    return None


def install_project_python(version=DEFAULT_PYTHON):
    """用 uv 把指定版本的 Python 装进**项目内**（`<项目>/.python`）。

    关键参数是 `--install-dir`（等价于 `UV_PYTHON_INSTALL_DIR`）—— 不指定时
    uv 会装到用户级目录（`%LOCALAPPDATA%\\uv\\python`），本函数的目的正是
    "别让解释器跑到项目外"。返回装好的解释器路径，失败返回 None。
    """
    uv = find_uv()
    if uv is None:
        return None
    info(f"🐍 项目内还没有 Python {version}，用 uv 装一个到 "
         f"{project_python_dir()} …", style="cyan")
    cmd = [uv, "python", "install", version,
           "--install-dir", str(project_python_dir())]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              env=build_env())
    except (OSError, subprocess.SubprocessError) as e:
        info(f"❌ 安装项目内 Python 失败：{e}", style="red")
        return None
    for line in (proc.stdout or "").strip().splitlines()[-3:]:
        info(f"   {line}", style="bright_black")
    if proc.returncode != 0:
        info(f"❌ uv python install 退出码 {proc.returncode}："
             f"{(proc.stderr or '')[-300:]}", style="red")
        return None
    found = find_project_python(version)
    if found is None:
        info("❌ 装完却没在项目内找到 python.exe", style="red")
    return found


def ensure_project_python(version=DEFAULT_PYTHON):
    """项目内有就用，没有就装 —— 保证存在一个<b>项目内</b>的解释器。"""
    found = find_project_python(version)
    if found is not None:
        return found
    return install_project_python(version)


def interpreter_version(exe):
    """问解释器自己要版本，返回 (3, 11, 14)；失败返回 None。"""
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        return None
    try:
        parts = [int(p) for p in lines[-1].strip().split(".")[:3]]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 3 else None


def venv_base_python(venv_dir):
    """从 `pyvenv.cfg` 读 venv 的宿主解释器目录（`home`），读不到返回 None。"""
    return venv_cfg_value(venv_dir, "home")


def venv_cfg_value(venv_dir, key):
    """读 pyvenv.cfg 里某个键的值；读不到返回 None。

    用 `utf-8-sig` 读：手工用记事本/`Set-Content` 改过这个文件时会带 BOM，
    而 `utf-8` 解码后 BOM 会变成行首的 `\\ufeff`，`str.strip()` **删不掉**它
    （它不是空白字符），于是 `home` 匹配失败 —— 实测量到的后果是
    `ensure_venv_base()` 误判成"宿主版本对不上"，把整个 `.venv` 清空重建。
    """
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.is_file():
        return None
    try:
        text = cfg.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if "=" not in stripped:
            continue
        # 精确比较键名：`startswith` 会让 versionInfo/version_info 互相误命中
        if stripped.split("=", 1)[0].strip().lower() == key.lower():
            _, _, value = stripped.partition("=")
            return Path(value.strip())
    return None


def venv_base_version(venv_dir):
    """pyvenv.cfg 里的 `version_info`，如 "3.11.9"；读不到返回 None。"""
    raw = venv_cfg_value(venv_dir, "version_info")
    return str(raw) if raw is not None else None


def version_minor(text):
    """从 "3.11.9" / "3.11" / "3.11.14 (main, ...)" 里取 "3.11"；认不出返回 None。

    为什么不用 `startswith("3.11.")`：pyvenv.cfg 里的 `version_info` 可能是
    `3.11`（标准库 venv 在某些版本上只写主次版本），那样会被误判成"版本不同"，
    进而白白清空整个环境。
    """
    match = re.match(r"\s*(\d+)\.(\d+)", str(text))
    return f"{match.group(1)}.{match.group(2)}" if match else None


def venv_base_is_project_local(venv_dir):
    """venv 的宿主解释器是否存在且位于项目内（决定要不要修/重建）。

    两个条件都不能少：只判"在项目内"时，`.python` 被删掉的 venv 会被误判为
    正常，运行时才在 `import` 阶段炸掉。
    """
    home = venv_base_python(venv_dir)
    if home is None or not home.is_dir():
        return False
    try:
        home.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def create_venv_uv(venv_dir, python_version=None, recreate=False, base_python=None):
    """用 uv 建 venv。

    `base_python` 非空时传**解释器路径**而不是版本号：这样 uv 不会去整机搜索，
    venv 的宿主就是项目内那个 Python（`pyvenv.cfg: home = <项目>/.python/...`）。
    """
    uv = find_uv()
    # --seed：让 uv 在新环境里同时装上 pip。
    # 不加这个参数时 uv 建出的 venv **没有 pip**，而 installer.py 大量依赖
    # `python -m pip`（实测会直接死在 `No module named pip` 上）。
    # installer.py 自己也有 ensurepip / uv pip 的兜底，这里只是从源头避免。
    target = str(base_python) if base_python else python_version
    cmd = [uv, "venv", str(venv_dir), "--python", target, "--seed"]
    if recreate:
        cmd.append("--clear")
    info(f"🐍 用 uv 建虚拟环境（宿主解释器：{target}）", style="cyan")
    # 缓存目录已由 ensure_cache_dirs() 建好，UV_CACHE_DIR 已写入 os.environ
    return subprocess.run(cmd, env=build_env(
        hf_endpoint=os.environ.get("HF_ENDPOINT"))).returncode


def repair_venv_host(venv_dir, base_python):
    """把 venv 的宿主解释器换成项目内的那个，**不重装任何第三方包**。

    只改 `pyvenv.cfg`，不动 `Scripts\\*.exe` —— 实测（2026-09-19）：
    `Scripts\\python.exe` 是个 274 KB 的**跳板**（不是宿主 exe 的副本），它自己
    按 `pyvenv.cfg` 的 `home` 去找真正的解释器、标准库和 `python3XX.dll`。
    早先一版把宿主 exe 直接覆盖过去，结果 `Scripts\\python.exe` 退出码
    `0xC0000135`（STATUS_DLL_NOT_FOUND，找不到 python311.dll）—— 因为裸 exe
    只会去"自己所在目录"找 DLL。改配置这条路在 uv venv 和标准库 venv 上都实测通过。

    前提是主次版本一致（3.11 → 3.11，补丁号不同没关系，ABI 相同）；
    跨主次版本（3.11 → 3.12）必须重建，`ensure_venv_base()` 负责判断。
    """
    base_dir = Path(base_python).parent
    cfg = venv_dir / "pyvenv.cfg"
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        info(f"⚠️ 读不到 {cfg}：{e}", style="yellow")
        return False

    version = interpreter_version(base_python)
    version_text = ".".join(str(p) for p in version) if version else None
    # 标准库 venv 会写 version/executable 两行，uv 只写 home/version_info；
    # 一起改掉，避免配置自相矛盾（`version` 还是旧值会让人误判）。
    replacements = {"home": str(base_dir), "executable": str(base_python)}
    if version_text:
        replacements["version"] = version_text
        replacements["version_info"] = version_text

    written, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
        if key in replacements:
            line, seen = f"{key} = {replacements[key]}", seen | {key}
        written.append(line)
    if "home" not in seen:
        written.append(f"home = {base_dir}")
    if version_text and "version_info" not in seen:
        written.append(f"version_info = {version_text}")
    try:
        cfg.write_text("\n".join(written) + "\n", encoding="utf-8")
    except OSError as e:
        info(f"⚠️ 改写 {cfg} 失败：{e}", style="yellow")
        return False

    if not venv_base_is_project_local(venv_dir):
        info(f"⚠️ 改完 {cfg}，但 home 仍不在项目内：{venv_base_python(venv_dir)}",
             style="yellow")
        return False

    info(f"🔧 venv 宿主解释器已改指项目内：{base_dir}"
         f"（{version_text or '版本未知'}）", style="green")
    return True


def ensure_venv_base(venv_dir, base_python, recreate=False):
    """保证 `venv_dir` 的宿主解释器就是 `base_python`。返回 0 表示可以用。

    三种情形：
    * 环境不存在 / 指定了 `--recreate` → 直接建；
    * 宿主已在项目内且主次版本一致 → 什么都不做；
    * 宿主在项目外、路径失效、或版本对不上 → 改 `pyvenv.cfg` 就地修（保住已装的
      包）。**跨主次版本不自动重建**：那要重下 3 GB 的 torch，只提示用户自己加
      `--recreate`。
    """
    wanted = interpreter_version(base_python)
    wanted_minor = f"{wanted[0]}.{wanted[1]}" if wanted else None
    existing = venv_python(venv_dir) if venv_dir.exists() else None

    if existing is None or recreate:
        return create_venv_uv(venv_dir, None, recreate=recreate or venv_dir.exists(),
                              base_python=base_python)

    current_minor = venv_base_version(venv_dir) or describe_python(existing)
    home = venv_base_python(venv_dir)
    same_minor = (wanted_minor is not None
                  and version_minor(current_minor) == wanted_minor)
    if venv_base_is_project_local(venv_dir) and same_minor:
        info(f"ℹ️ 虚拟环境已存在：{venv_dir}（Python {current_minor}，"
             f"宿主 {home}）", style="bright_black")
        return 0

    if same_minor:
        # 宿主在项目外 / 那个目录已经被删掉 —— 两种都能靠改 pyvenv.cfg 解决。
        # 注意这里**不判** `home.is_dir()`：旧宿主被删掉（换个项目一删就没了）
        # 正是最需要修的情形，判了就掉进"重建"，白扔已装的依赖。
        panel(f"🔧 这个环境的宿主解释器不在项目内，正在改成项目内的\n"
              f"旧宿主：{home or '（pyvenv.cfg 里没有 home，或已失效）'}\n"
              f"新宿主：{base_python}\n"
              f"（同一主次版本 {wanted_minor}，已装的依赖会保留）", style="yellow")
        return 0 if repair_venv_host(venv_dir, base_python) else 1

    if version_minor(current_minor) is None:
        panel(f"⛔ {venv_dir} 读不出 Python 版本（pyvenv.cfg 缺失或被改坏），"
              f"没法就地修。\n"
              f"确认重建：python setup_env.py "
              f"--python {wanted_minor or DEFAULT_PYTHON} --recreate", style="red")
        return 1

    panel(f"⛔ 已有的虚拟环境是 Python {current_minor}（宿主 {home}），"
          f"与目标 {wanted_minor} 主次版本不同。\n"
          f"venv 里的包是按 {current_minor} 的 ABI 装的，跨版本无法就地改。\n\n"
          f"确认要重下依赖的话，显式重建：\n"
          f"     python setup_env.py --python {wanted_minor} --recreate\n"
          f"（会清空 {venv_dir}，torch 等需要重新下载 2.7–3.6 GB）", style="red")
    return 1


def create_venv_stdlib(venv_dir, recreate=False):
    """没有 uv 时的回退：用 venv 模块建（无法自选 Python 版本）。

    优先用项目内的 Python；没有才用"当前正在跑 setup_env.py 的那个"。
    """
    if recreate and venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)
    base = find_project_python() or Path(sys.executable)
    info(f"🐍 未找到 uv，回退到 {base} 的 venv 模块建环境", style="yellow")
    return subprocess.run([str(base), "-m", "venv", str(venv_dir)]).returncode


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


def venv_has_pip(python_exe):
    """环境里能否 `import pip`。uv 不带 --seed 时建出的环境是没有的。"""
    try:
        proc = subprocess.run([str(python_exe), "-c", "import pip"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def seed_pip(python_exe):
    """用标准库 ensurepip 给环境补上 pip；失败再退回 uv pip。"""
    try:
        proc = subprocess.run([str(python_exe), "-m", "ensurepip", "--upgrade"],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode == 0 and venv_has_pip(python_exe):
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    uv = find_uv()
    if uv is None:
        return False
    info("   ensurepip 不可用，改用 uv 安装 pip ...", style="yellow")
    try:
        proc = subprocess.run([uv, "pip", "install", "--python", str(python_exe), "pip"],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and venv_has_pip(python_exe)


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
    parser.add_argument("--download-dir", default=None,
                        help="透传给 installer.py：大文件目录（默认 <项目>/_downloads）")
    parser.add_argument("--download-only", action="store_true",
                        help="只让 installer.py 打印大文件下载地址后退出（不建环境、不安装）")
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

    if args.download_only:
        # 只打印大文件下载地址：不需要环境存在，也不必先建 .venv
        cmd = [sys.executable, "installer.py", "--download-only",
               "--torch-backend", args.torch_backend]
        if args.download_dir:
            cmd += ["--download-dir", args.download_dir]
        info(f"📥 只打印下载地址：{' '.join(cmd)}", style="cyan")
        return subprocess.run(cmd, env=env, cwd=str(ROOT)).returncode

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
        panel(f"⚠️ 未找到 uv，将回退到项目内 Python 自带的 venv 模块。\n"
              f"后果：无法自动下载 Python；跨版本重建也要手工处理。\n\n"
              f"安装 uv 后重跑可得到与方案一致的布局：\n{UV_INSTALL_HINT}", style="yellow")
        if venv_dir.exists() and not args.recreate:
            info(f"ℹ️ 虚拟环境已存在，跳过创建：{venv_dir}", style="bright_black")
        else:
            if create_venv_stdlib(venv_dir, recreate=args.recreate) != 0:
                panel("❌ 创建虚拟环境失败", style="red")
                return 1
    else:
        # 先备好项目内的解释器，再拿它当宿主建/修 venv —— 顺序不能反：
        # 先建 venv 的话 uv 会去整机找同版本 Python（可能找到别的项目的）。
        base_python = ensure_project_python(args.python)
        if base_python is None:
            panel(f"❌ 无法在项目内准备好 Python {args.python}。\n\n"
                  f"可以手工装到项目内：\n"
                  f"     uv python install {args.python} "
                  f"--install-dir \"{project_python_dir()}\"\n"
                  f"（本机 uv：{uv}）", style="red")
            return 1
        info(f"🐍 项目内 Python：{base_python}"
             f"（{describe_python(base_python)}）", style="green")
        if ensure_venv_base(venv_dir, base_python, recreate=args.recreate) != 0:
            panel("❌ 准备虚拟环境失败", style="red")
            return 1

    python_exe = venv_python(venv_dir)
    if python_exe is None:
        panel(f"❌ 建好环境后仍找不到解释器：{venv_dir}", style="red")
        return 1
    info(f"✅ 解释器：{python_exe}（Python {describe_python(python_exe)}）", style="green")

    # 环境里必须有 pip。旧版 uv venv 没有 --seed，会建出无 pip 的环境，
    # 检测到就直接补一个（等价于 python -m pip install pip 的最小形态）。
    if not venv_has_pip(python_exe):
        info("🔧 环境里没有 pip，正在用 ensurepip 补装 ...", style="yellow")
        if not seed_pip(python_exe):
            panel("❌ 无法为环境补装 pip。请删掉环境后重建：\n"
                  f"     rmdir /s /q \"{venv_dir}\"\n"
                  f"     python setup_env.py --python {args.python}", style="red")
            return 1
        info("✅ pip 已就绪", style="green")

    # 让 uv 之后的调用与运行期都用同一套缓存
    env["VIRTUAL_ENV"] = str(venv_dir)
    apply_env(env)

    if args.no_install:
        info("⏭️ --no-install：跳过依赖安装", style="bright_black")
        print_next_steps(venv_dir, python_exe)
        return 0

    cmd = [str(python_exe), str(ROOT / "installer.py"),
           "--torch-backend", args.torch_backend, "--python", str(python_exe)]
    if args.download_dir:
        cmd += ["--download-dir", args.download_dir]
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
        f"宿主 Python：{venv_base_python(venv_dir) or project_python_dir()}\n"
        f"缓存：{ROOT / '_model_cache'} / {ROOT / '.uv-cache'} / {ROOT / '.pip-cache'}\n\n"
        f"启动：{python_exe} launch.py\n"
        f"体检：{python_exe} installer.py --check --smoke\n\n"
        "（三个 .bat 会自动优先使用 .venv，检测不到才回退 conda）",
        style="green")


if __name__ == "__main__":
    raise SystemExit(main())
