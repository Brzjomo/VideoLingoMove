"""VideoLingo 环境与缓存清理工具（释放 C 盘空间）。

用法：
    python cleanup.py                  # 只扫描并报告占用（默认，不删任何东西）
    python cleanup.py --clean          # 清理"确定属于 VideoLingo"的缓存（安全）
    python cleanup.py --clean --models # 额外清理自动下载的模型（HuggingFace / torch hub）
    python cleanup.py --clean --all    # 再加项目内的 _downloads 与 ffmpeg
    python cleanup.py --clean --yes    # 跳过确认提示（用于脚本调用）
    python cleanup.py --clean --only pip,uv

设计原则（重要）：
  * **默认只报告，不动手**。删除是不可逆的，先看清楚再决定。
  * 分成"安全 / 需确认"两档：pip、uv 缓存是纯下载缓存，删了只是下次重下；
    而 HuggingFace / torch 缓存**可能与别的项目共用**，所以必须显式加 --models。
  * **绝不卸载/删除 anaconda/miniconda 本身**，也不碰其他 conda 环境
    （本机就有 aisummary、novelmanager 两个别人的环境）。conda 环境只能用
    `conda env remove -n <名字>` 单独处理，本脚本只在报告里给出命令。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from easy_util import ensure_utf8_console
    ensure_utf8_console()
except Exception:
    pass

HOME = Path.home()
PROJECT = Path(__file__).resolve().parent
TARGET_ENV = "videolingo"          # 旧 conda 环境名


def human(num_bytes):
    if num_bytes is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def dir_size(path):
    """递归求和目录大小；不存在返回 None。取不到的文件跳过（权限/占用）。"""
    path = Path(path)
    if not path.exists():
        return None
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


class Target:
    """一个可清理的目标。"""

    def __init__(self, key, label, path, tier, note="", purge_cmd=None):
        self.key = key
        self.label = label
        self.path = Path(path)
        self.tier = tier          # "safe" / "confirm"
        self.note = note
        # 优先用工具自带的清理命令（pip/uv 都提供了），比自己删目录更稳妥：
        # 它们只清缓存、不会误伤配置
        self.purge_cmd = purge_cmd
        self.size = dir_size(self.path)

    @property
    def exists(self):
        return self.size is not None

    def remove(self):
        """删除并返回 (成功?, 说明)。文件与目录都支持。

        有官方清理命令时优先用它；失败或没有时退回直接删目录。
        """
        if self.purge_cmd:
            try:
                proc = subprocess.run(self.purge_cmd, capture_output=True,
                                      text=True, timeout=1800)
                if proc.returncode == 0:
                    remaining = dir_size(self.path) or 0
                    if remaining < 1024 * 1024:      # <1MB 视为已清空
                        return True, f"已用 `{' '.join(self.purge_cmd[-2:])}` 清空"
                    return True, f"已清理，剩余 {human(remaining)}"
                detail = ((proc.stderr or proc.stdout or "").strip().splitlines()
                          or ["未知错误"])[-1]
                # 官方命令失败时继续走下面的直接删除
                note = f"官方清理命令失败（{detail}），改用直接删除"
            except (OSError, subprocess.SubprocessError) as exc:
                note = f"官方清理命令不可用（{exc}），改用直接删除"
        else:
            note = None

        if self.path.is_file():
            try:
                self.path.unlink()
                return True, note or "已删除（文件）"
            except Exception as exc:  # noqa: BLE001 - 要如实报告失败原因
                return False, f"未能删除（{exc}）"
        try:
            shutil.rmtree(self.path, ignore_errors=False)
            return True, note or "已删除"
        except Exception as exc:  # noqa: BLE001 - 要如实报告失败原因
            # 目录被占用时 rmtree 会中途失败，退回统计剩余并如实报告
            leftover = dir_size(self.path)
            return False, f"未能完全删除（{exc}）；仍剩余 {human(leftover)}"


# ---------------------------------------------------------------- 目标清单
def build_targets():
    local = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
    py = sys.executable
    uv = uv_exe()
    return [
        # --- 安全档：纯下载缓存，删掉只影响"下次要重新下载" ---
        Target("pip", "pip 下载缓存", local / "pip" / "Cache", "safe",
               "只缓存下载过的 wheel，可随时删",
               purge_cmd=[py, "-m", "pip", "cache", "purge"]),
        Target("uv", "uv 下载缓存", local / "uv" / "cache", "safe",
               "uv 的全局 wheel 缓存",
               purge_cmd=[uv, "cache", "clean"] if uv else None),
        Target("pip_project", "项目内 pip 缓存", PROJECT / ".pip-cache", "safe",
               "setup_env.py 指向项目内的 pip 缓存"),
        Target("uv_project", "项目内 uv 缓存", PROJECT / ".uv-cache", "safe",
               "setup_env.py 指向项目内的 uv 缓存"),

        # --- 需确认档：可能与别的项目共用 ---
        Target("hf", "HuggingFace 模型缓存", HOME / ".cache" / "huggingface", "confirm",
               "⚠️ 可能含其他项目的模型；本机另有 aisummary 在用"),
        Target("torch", "torch hub 模型缓存", HOME / ".cache" / "torch", "confirm",
               "含 WhisperX 的对齐模型（wav2vec2 等）"),
        Target("temp", "系统临时目录", local / "Temp", "confirm",
               "⚠️ 全系统共用；只建议清理里面明显过期的条目，且被占用的删不掉"),

        # --- 项目内大件 ---
        Target("downloads", "项目 _downloads（大文件目录）", PROJECT / "_downloads",
               "confirm", "torch 轮子等；若还要重装就别删，删了要重下 2.9 GB"),
        Target("ffmpeg", "项目 ffmpeg（自带 FFmpeg）", PROJECT / "ffmpeg", "confirm",
               "删了下次安装会重新下载（约 68 MB）；系统那份静态版不能替代它"),
    ]


def uv_exe():
    """找到 uv 可执行文件（pip 缓存之外顺手支持 uv cache clean）。"""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (HOME / ".local" / "bin" / "uv.exe",
                      HOME / ".local" / "bin" / "uv",
                      HOME / ".cargo" / "bin" / "uv.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def conda_exe():
    for candidate in (HOME / "anaconda3" / "Scripts" / "conda.exe",
                      HOME / "miniconda3" / "Scripts" / "conda.exe",
                      HOME / "anaconda3" / "Scripts" / "conda.bat"):
        if candidate.is_file():
            return candidate
    found = shutil.which("conda")
    return Path(found) if found else None


def conda_environments():
    """返回 {环境名: 路径}；拿不到就返回 {}。"""
    exe = conda_exe()
    if exe is None:
        return {}
    try:
        proc = subprocess.run([str(exe), "env", "list"], capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return {}
    envs = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("*", " ").split()
        if len(parts) >= 2:
            envs[parts[0]] = parts[-1]
    return envs


# ---------------------------------------------------------------- 报告
def report(targets, show_all=True):
    print("=" * 72)
    print("  VideoLingo 清理扫描（C 盘占用盘点）")
    print("=" * 72)

    # conda 情况单独说，因为最容易被误删
    print("\n【Conda】")
    exe = conda_exe()
    if exe is None:
        print("  未找到 conda（anaconda/miniconda 都不在常见位置）")
    else:
        envs = conda_environments()
        print(f"  conda 位置：{exe}")
        others = [n for n in envs if n not in ("base", TARGET_ENV)]
        if TARGET_ENV in envs:
            size = dir_size(envs[TARGET_ENV])
            print(f"  ✅ 找到旧环境 {TARGET_ENV}（{human(size)}）：{envs[TARGET_ENV]}")
            print(f"     删除命令：conda env remove -n {TARGET_ENV} -y")
        else:
            print(f"  ℹ️  没有名为 {TARGET_ENV} 的 conda 环境 —— 无需清理"
                  f"（本项目已改用 .venv）")
        if others:
            print(f"  ⚠️ 检测到其他项目环境，**本脚本不会碰它们**："
                  f"{'、'.join(others)}")
        print("  ⚠️ anaconda3 本体（base）也不在本脚本范围内："
              f"总计约 {human(dir_size(HOME / 'anaconda3'))}")

    print("\n【缓存与项目目录】")
    print(f"  {'项目':<34} {'大小':>10}  {'档位':<6} 说明")
    print("  " + "-" * 68)
    for t in targets:
        if not t.exists and not show_all:
            continue
        size = human(t.size) if t.exists else "（不存在）"
        tier = "安全" if t.tier == "safe" else "需确认"
        prev = "  ⚠️" if t.exists and t.tier == "safe" and t.key == "pip" else "    "
        print(f"{prev}{t.label:<34} {size:>10}  {tier:<6} {t.note}")

    safe_total = sum(t.size for t in targets if t.exists and t.tier == "safe")
    confirm_total = sum(t.size for t in targets if t.exists and t.tier == "confirm")
    print("\n  小计：安全档可释放 " + human(safe_total)
          + f"；需确认档另有 {human(confirm_total)}")
    print("=" * 72)


# ---------------------------------------------------------------- 清理
def confirm(prompt):
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def clean(targets, keys, assume_yes=False):
    chosen = [t for t in targets if t.key in keys]
    if not chosen:
        print("没有匹配的清理目标。")
        return 1

    print("\n将要删除：")
    total = 0
    for t in chosen:
        if not t.exists:
            print(f"  - {t.label}：不存在，跳过")
            continue
        total += t.size
        print(f"  - {t.label}  {human(t.size)}\n      {t.path}")
    if total == 0:
        print("没有可删除的内容。")
        return 0

    print(f"\n合计约 {human(total)}。**删除不可恢复**（pip/uv 缓存删了只是下次重下）。")
    if not assume_yes and not confirm("确认继续？"):
        print("已取消。")
        return 0

    print()
    failures = 0
    for t in chosen:
        if not t.exists:
            continue
        ok, message = t.remove()
        mark = "✅" if ok else "⚠️"
        print(f"  {mark} {t.label}：{message}")
        if not ok:
            failures += 1

    print(f"\n完成。释放约 {human(total)}。")
    if failures:
        print("⚠️ 有目录被占用未能完全删除（常见于 %TEMP%）。"
              "关掉相关程序后重跑，或重启后再删。")
    return 0


SAFE_KEYS = ("pip", "uv", "pip_project", "uv_project")
MODEL_KEYS = ("hf", "torch")
PROJECT_KEYS = ("downloads", "ffmpeg")


def build_parser():
    parser = argparse.ArgumentParser(
        description="VideoLingo 环境与缓存清理（默认只扫描，不删除）")
    parser.add_argument("--clean", action="store_true",
                        help="执行清理（默认只扫描报告）")
    parser.add_argument("--models", action="store_true",
                        help="同时清理 HuggingFace / torch 模型缓存（可能与别的项目共用）")
    parser.add_argument("--all", action="store_true",
                        help="再加项目内 _downloads 与 ffmpeg")
    parser.add_argument("--temp", action="store_true",
                        help="清理系统临时目录（全系统共用，谨慎）")
    parser.add_argument("--only", default=None,
                        help="只清理指定项，逗号分隔：pip,uv,hf,torch,temp,downloads,ffmpeg")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    targets = build_targets()

    report(targets)

    if not args.clean:
        print("\n提示：以上只是报告，**没有删除任何东西**。")
        print("      要清理安全档（pip / uv 缓存）：python cleanup.py --clean")
        print("      还要清理模型缓存：              python cleanup.py --clean --models")
        print("      全部（含项目 _downloads）：      python cleanup.py --clean --models --all")
        return 0

    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        known = {t.key for t in targets}
        unknown = keys - known
        if unknown:
            print(f"❌ 未知清理项：{', '.join(sorted(unknown))}")
            print(f"   可用项：{', '.join(sorted(known))}")
            return 1
    else:
        keys = set(SAFE_KEYS)
        if args.models:
            keys |= set(MODEL_KEYS)
        if args.all:
            keys |= set(PROJECT_KEYS)
        if args.temp:
            keys.add("temp")

    return clean(targets, keys, assume_yes=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
