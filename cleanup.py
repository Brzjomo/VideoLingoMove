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


# ---------------------------------------------------------------- 模型归属判定
# 旧版项目**从不设置 HF_HOME / TORCH_HOME**（已核对 da3a432 的 core/step2_whisperX.py，
# 只有 `os.environ['HF_ENDPOINT']`），所以除 Whisper 主模型外的模型都落在
# HuggingFace / torch 的**默认缓存**里，也就是 C 盘的 `%USERPROFILE%\.cache`：
#   * Whisper 识别模型 → 项目内 `_model_cache/`（load_model(download_root=MODEL_DIR)）
#   * pyannote VAD、wav2vec2 对齐 → `~/.cache/huggingface/hub`
#   * torch.hub 权重 → `~/.cache/torch/hub/checkpoints`
#
# 默认缓存是**全机共用**的，别的项目（本机的 aisummary 等）也会往里放东西。
# 所以不能整目录删，要按模型名把"确定属于 VideoLingo"的挑出来。
OWN_MODEL_PATTERNS = (
    # 中文默认识别模型（core/step2_whisperX.py: load_whisper_model_name）
    "belle-whisper",
    # whisper 全系列（large-v3 / medium / base / tiny / large-v2 …），
    # 但要排除别的项目的 distil-whisper
    "whisper-large", "whisper-medium", "whisper-small",
    "whisper-base", "whisper-tiny",
    # 对齐模型（whisperx.alignment 的默认表）
    "wav2vec2",
    # 说话人分离（智谱/火山等引擎用到时）
    "speaker-diarization", "speakerdiarization", "pyannote",
    "segmentation-3.0",
    # 新版 WhisperX 用的 VAD（silero）
    "silero-vad",
)
# 明确**不属于**本项目的关键词：命中就不动（避免删掉别人要用的模型）
# 注意 distil-whisper 必须排在 whisper 之前判断
FOREIGN_MODEL_PATTERNS = (
    "distil-whisper",      # aisummary 等在用
    "doclayout", "yolo",   # 文档版面分析（别的项目）
    "sentence-transformers", "all-minilm",
    "hy-mt", "gptq",       # 翻译类模型（别的项目）
)


def classify_model(name):
    """判断一个模型目录/条目是否属于 VideoLingo。返回 'own' / 'foreign' / 'unknown'。

    注意 torch.hub 的命名特点：**有名字的用可读名**（`wav2vec2_fairseq_base_ls960
    _asr_ls960.pth`），**微调模型则用 hash 文件名**（`955717e8-8726e21a.th`）。
    本机实测两个都属于 WhisperX 的对齐模型。所以对 `.th/.pth` 给出两种判断：
    可读名按关键词，纯 hash 名视为"本项目的"（torch.hub 在这个项目里只被
    WhisperX 用于对齐模型；若机器上还有别人用 torch.hub，会在报告里单独标出，
    用户可自行决定）。
    """
    lowered = name.lower()
    for pattern in FOREIGN_MODEL_PATTERNS:
        if pattern in lowered:
            return "foreign"
    for pattern in OWN_MODEL_PATTERNS:
        if pattern in lowered:
            return "own"
    # torch.hub 的 hash 文件名：8 位十六进制 + '-' + 8 位十六进制 + .th/.pth
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{8}\.(th|pth|pt)", lowered):
        return "own"
    return "unknown"


def hf_cache_hub_dirs():
    """列出所有可能的 HuggingFace hub 缓存目录（含环境变量与常见位置）。"""
    seen, found = set(), []

    def add(path):
        path = Path(path)
        key = str(path).lower()
        if key in seen or not path.is_dir():
            return
        seen.add(key)
        found.append(path)

    # 1) 环境变量说了算（用户可能为了省 C 盘把缓存挪到别的盘）
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                "HF_HOME"):
        value = os.environ.get(var)
        if not value:
            continue
        base = Path(value)
        if var == "HF_HOME":
            add(base / "hub")
        else:
            add(base)
        add(base / "hub")

    # 2) 默认位置（旧版没设环境变量时就是这些）
    add(HOME / ".cache" / "huggingface" / "hub")
    local = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
    add(local / "huggingface" / "hub")
    add(Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
        / "huggingface" / "hub")

    # 3) 项目内（新栈的 HF_HOME 指向这里）
    for project in project_roots():
        add(project / "_model_cache" / "hub")
        add(project / "_model_cache" / "huggingface" / "hub")

    return found


def hf_models_in(hub_dir):
    """返回 hub 目录下的模型目录列表 [(名字, 路径, 大小, 归属)]。"""
    result = []
    if not Path(hub_dir).is_dir():
        return result
    try:
        entries = sorted(Path(hub_dir).iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith("models--"):
            continue
        result.append((entry.name, entry, dir_size(entry),
                       classify_model(entry.name)))
    return result


def torch_hub_checkpoint_dirs():
    """列出所有可能的 torch.hub 权重目录。"""
    seen, found = set(), []

    def add(path):
        path = Path(path)
        key = str(path).lower()
        if key in seen or not path.is_dir():
            return
        seen.add(key)
        found.append(path)

    for var in ("TORCH_HOME", "XDG_CACHE_HOME"):
        value = os.environ.get(var)
        if not value:
            continue
        base = Path(value)
        add(base / "hub" / "checkpoints" if var == "TORCH_HOME"
            else base / "torch" / "hub" / "checkpoints")
    add(HOME / ".cache" / "torch" / "hub" / "checkpoints")
    for project in project_roots():
        add(project / "_model_cache" / "torch" / "hub" / "checkpoints")
    return found


def project_roots():
    """除当前项目外，可能残留旧版运行数据的项目目录。"""
    roots, seen = [], set()

    def add(path):
        path = Path(path)
        key = str(path).lower()
        if key not in seen and path.is_dir():
            seen.add(key)
            roots.append(path)

    add(PROJECT)
    # 同盘根目录、桌面、文档、下载里叫 VideoLingo* 的目录（旧版可能放在别处）
    candidates = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads",
                  Path("C:/"), Path("D:/"), Path("E:/"), Path("F:/")]
    for base in candidates:
        if not base.is_dir():
            continue
        try:
            for entry in base.iterdir():
                if entry.is_dir() and entry.name.lower().startswith("videolingo"):
                    add(entry)
        except OSError:
            continue
    return roots


LEGACY_CONDA_ENV = "videolingo"


def conda_env_paths():
    """在所有已知 conda 安装里找**旧环境** videolingo 的路径。"""
    found = []
    for base in (HOME / "anaconda3", HOME / "miniconda3", HOME / "miniforge3",
                 HOME / "mambaforge", Path("C:/ProgramData/anaconda3"),
                 Path("C:/ProgramData/miniconda3"),
                 Path(os.environ.get("CONDA_PREFIX", "")) if
                 os.environ.get("CONDA_PREFIX") else None):
        if base is None:
            continue
        env_dir = Path(base) / "envs" / LEGACY_CONDA_ENV
        if env_dir.is_dir():
            found.append(env_dir)
    # conda 自己记录的环境列表是最可靠的来源
    for name, path in conda_environments().items():
        if name == LEGACY_CONDA_ENV and Path(path).is_dir():
            if Path(path) not in found:
                found.append(Path(path))
    return found


# ---------------------------------------------------------------- 目标清单
def build_targets(extra_models=True):
    local = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
    py = sys.executable
    uv = uv_exe()
    targets = [
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
        Target("hf", "HuggingFace 模型缓存（整个）", HOME / ".cache" / "huggingface",
               "confirm",
               "⚠️ 全机共用；本机另有 aisummary 的模型在里面，默认不动"),
        Target("torch", "torch hub 模型缓存（整个）", HOME / ".cache" / "torch",
               "confirm", "含 WhisperX 的对齐模型（wav2vec2 等）"),
        Target("temp", "系统临时目录", local / "Temp", "confirm",
               "⚠️ 全系统共用；只建议清理里面明显过期的条目，且被占用的删不掉"),

        # --- 项目内大件 ---
        Target("downloads", "项目 _downloads（大文件目录）", PROJECT / "_downloads",
               "confirm", "torch 轮子等；若还要重装就别删，删了要重下 2.9 GB"),
        Target("ffmpeg", "项目 ffmpeg（自带 FFmpeg）", PROJECT / "ffmpeg", "confirm",
               "删了下次安装会重新下载（约 68 MB）；系统那份静态版不能替代它"),
    ]

    # 旧 conda 环境：只报告并给命令（不在这里直接删 —— 交给 conda 自己处理）
    for env_path in conda_env_paths():
        targets.append(Target(
            "conda_env", f"旧 conda 环境 {LEGACY_CONDA_ENV}", env_path, "confirm",
            "⚠️ 建议用 conda 自己删：conda env remove -n "
            f"{LEGACY_CONDA_ENV} -y"))
        break

    # 各缓存目录里**确定属于本项目**的模型（逐目录挑，不整锅端）
    for hub in hf_cache_hub_dirs():
        for name, path, _size, owner in hf_models_in(hub):
            if owner != "own":
                continue
            targets.append(Target(f"hf_own:{name}", f"HF 模型 {name}", path,
                                  "confirm", "判定属于 VideoLingo"))
    for ckpt_dir in torch_hub_checkpoint_dirs():
        try:
            entries = sorted(ckpt_dir.iterdir())
        except OSError:
            continue
        own = [e for e in entries
               if e.is_file() and classify_model(e.name) == "own"]
        if own:
            # hub/checkpoints 里本项目要用的就是 wav2vec2 对齐权重；逐个列更安全
            for entry in own:
                targets.append(Target(f"torch_ckpt:{entry.name}",
                                      f"torch.hub 权重 {entry.name}", entry,
                                      "confirm", "WhisperX 对齐模型"))

    # 其他项目目录里的 _model_cache（旧版项目可能还留着 Whisper 权重）
    for project in project_roots():
        cache = project / "_model_cache"
        if cache.is_dir() and dir_size(cache):
            targets.append(Target(f"project_cache:{project.name}",
                                  f"{project} 的 _model_cache", cache, "confirm",
                                  "旧项目缓存（含 Whisper 识别模型）"))
    return targets


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
def report_models():
    """列出发现的所有模型缓存，并标明归属（本项目 / 别的项目 / 未知）。

    这是"模型到底下到哪了"的答案所在。旧版项目从不设置 HF_HOME，所以除
    Whisper 识别模型（在项目内 `_model_cache/`）之外，其余都落在默认缓存
    `%USERPROFILE%\\.cache\\huggingface\\hub` 与 `...\\.cache\\torch\\hub\\checkpoints`。
    """
    print("\n【模型缓存发现】")
    hubs = hf_cache_hub_dirs()
    if not hubs:
        print("  未发现任何 HuggingFace hub 缓存目录")
    for hub in hubs:
        models = hf_models_in(hub)
        print(f"\n  📁 {hub}")
        if not models:
            print("     （无模型）")
            continue
        own = [m for m in models if m[3] == "own"]
        foreign = [m for m in models if m[3] == "foreign"]
        unknown = [m for m in models if m[3] == "unknown"]
        for name, _path, size, owner in models:
            tag = {"own": "① 本项目", "foreign": "② 别的项目",
                   "unknown": "③ 未知"}[owner]
            print(f"     [{tag}] {name}  {human(size)}")
        print(f"     → 本项目 {len(own)} 个 / 别的项目 {len(foreign)} 个 / "
              f"未知 {len(unknown)} 个")
        if not own:
            print("     ℹ️ 这里没有本项目的模型 —— 说明本机还没跑过转录，"
                  "或模型在项目内 _model_cache/")

    for ckpt in torch_hub_checkpoint_dirs():
        try:
            entries = sorted(ckpt.iterdir())
        except OSError:
            continue
        if not entries:
            continue
        print(f"\n  📁 {ckpt}")
        for entry in entries:
            if not entry.is_file():
                continue
            owner = classify_model(entry.name)
            tag = {"own": "① 本项目", "foreign": "② 别的项目",
                   "unknown": "③ 未知"}[owner]
            print(f"     [{tag}] {entry.name}  {human(dir_size(entry))}")

    # 项目内的 _model_cache（旧版的 Whisper 识别模型就在这里）
    for project in project_roots():
        cache = project / "_model_cache"
        if not cache.is_dir():
            continue
        size = dir_size(cache)
        print(f"\n  📁 {cache}  {human(size)}")
        try:
            for entry in sorted(cache.iterdir()):
                if entry.is_dir():
                    print(f"     [项目内] {entry.name}  {human(dir_size(entry))}")
        except OSError:
            pass

    print("\n  说明：① 是判定属于 VideoLingo 的，可用 --clean --models 清理；")
    print("        ② ③ 默认**不动**（可能别的项目在用）。")


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

    report_models()

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


def select_targets(targets, keys):
    """按 key 选目标。支持三类匹配：

      * 精确 key（`pip`、`uv`、`downloads`、`hf`、`torch` …）
      * 组 key **`models`**：只选逐模型挑出来的 `hf_own:*` / `torch_ckpt:*`
        —— 即**判定属于本项目**的那几个模型；
      * 注意 `models` **不会**选整个缓存目录（`hf` / `torch`），因为那里面
        混着别的项目的模型。要删整个缓存必须显式写 `--only hf,torch`。
        这个区分是整个脚本最关键的安全边界，有测试守着。

    定义在 clean() 之前，纯粹为了阅读顺序（模块级函数定义顺序其实无关）。
    """
    chosen = []
    for target in targets:
        if target.key in keys:
            chosen.append(target)
            continue
        if "models" in keys and target.key.split(":", 1)[0] in (
                "hf_own", "torch_ckpt"):
            chosen.append(target)
    return chosen


def clean(targets, keys, assume_yes=False):
    chosen = select_targets(targets, keys)
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
# "本项目的模型"= 逐模型挑出来的那些（hf_own:* / torch_ckpt:*），
# 见 select_targets() 的组 key 处理。**不含**整个缓存目录。
MODEL_GROUPS = ("models",)
# 整个缓存目录：里面混着别的项目的模型，必须由用户点名才动
CACHE_KEYS = ("hf", "torch")
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
                        help="只清理指定项，逗号分隔："
                             "pip,uv,pip_project,uv_project,models,hf,torch,temp,"
                             "downloads,ffmpeg,conda_env")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    targets = build_targets()

    report(targets)

    if not args.clean:
        print("\n提示：以上只是报告，**没有删除任何东西**。")
        print("      清理安全档（pip / uv 缓存）：  python cleanup.py --clean")
        print("      再清本项目的模型（只删判定属于本项目的）：")
        print("                                      python cleanup.py --clean --models")
        print("      再加项目内 _downloads 与 ffmpeg：python cleanup.py --clean --models --all")
        print("      整个 HF/torch 缓存（⚠️ 会牵连别的项目，需点名）：")
        print("                                      python cleanup.py --clean --only hf,torch")
        return 0

    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        known = {t.key for t in targets} | {"models"} | set(CACHE_KEYS)
        unknown = keys - known
        if unknown:
            print(f"❌ 未知清理项：{', '.join(sorted(unknown))}")
            print(f"   可用项：{', '.join(sorted(known))}")
            return 1
    else:
        keys = set(SAFE_KEYS)
        if args.models:
            # 只加"本项目的模型"这一组；**不加** hf/torch 整个缓存
            keys |= set(MODEL_GROUPS)
        if args.all:
            keys |= set(PROJECT_KEYS)
        if args.temp:
            keys.add("temp")

    chosen = select_targets(targets, keys)
    warn_if_shared(chosen)
    return clean(chosen, set(), assume_yes=args.yes)


def warn_if_shared(chosen):
    """如果选中的目标里含别的项目的模型，明确警告（不阻止，但要说清后果）。"""
    risky = []
    for target in chosen:
        if target.key in CACHE_KEYS:
            foreign = [m[0] for m in hf_models_in(target.path)
                       if m[3] == "foreign"] if target.key == "hf" else []
            risky.append((target, foreign))
    if not risky:
        return
    print("\n⚠️ 注意：以下目标包含**别的项目**可能正在使用的模型：")
    for target, foreign in risky:
        print(f"   - {target.label}（{target.path}）")
        for name in foreign:
            print(f"       会一并删除：{name}")
    print("   若只想删本项目的模型，请改用：--clean --models")


if __name__ == "__main__":
    raise SystemExit(main())
