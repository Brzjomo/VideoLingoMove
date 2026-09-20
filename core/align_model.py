"""WhisperX 对齐模型（wav2vec2）的获取：多端点回退 + 能照着做的失败提示。

为什么需要这个模块（2026-09-20 实测）
------------------------------------
日语 / 中文 / 韩语等语种的对齐模型必须**联网**从 HuggingFace 下（whisperx 的
`DEFAULT_ALIGN_MODELS_HF`，例如 ja → `jonatasgrosman/wav2vec2-large-xlsr-53-japanese`，
只下 PyTorch 权重约 1.3 GB）；只有 en/fr/de/es/it 走 torchaudio 管线
（从 download.pytorch.org 取，不经过 HF）。

这条 HF 下载路径一失败，用户会看到两句**互相矛盾、且都指向错误方向**的报错：

    Can't load feature extractor for '<repo>'. … make sure you don't have a local
    directory with the same name. Otherwise, make sure '<repo>' is the correct path
    to a directory containing a preprocessor_config.json file
    ← transformers/feature_extraction_utils.py 把 cached_file() 返回 None
      （"本地没缓存 + 网络拿不到文件元数据"）的情况吞成了"路径/同名目录"的 OSError；
      真因是它上一行的 IndexError: list index out of range（候选文件列表为空）

    ValueError: The chosen align_model "<repo>" could not be found in huggingface …
    ← whisperx/alignment.py 再包装一次；"could not be found" 听起来像模型名写错

实际是**网络问题**：模型名没错、仓库还在、也不需要改任何配置。本模块做三件事：

1. 优先用本地目录 `<model_dir>/align/<语言>/`（手动下载后放这里，之后完全离线）；
2. 否则把权重下进项目缓存：按端点顺序挨个试（用户**启动前**设的 HF_ENDPOINT →
   官方 → hf-mirror），一个端点失败就换下一个 —— 上游只试一个就放弃；
3. 全失败时抛一条带**下一步**的错误：语言、仓库、已试端点及各自原因、手动下载的
   URL、要哪些文件、放到哪个目录，以及一条项目自带的预下载命令。

⚠️ 两个实测坑（写在这里免得后人再踩）
------------------------------------
* `HF_ENDPOINT` 只在 **huggingface_hub import 时**读一次
  （`huggingface_hub/constants.py`：`ENDPOINT = os.getenv("HF_ENDPOINT", …)`，实测
  0.36.2）。所以**运行期**改 `os.environ['HF_ENDPOINT']` 是无效的 ——
  `core/step2_whisperX.py::check_hf_mirror()` 正是运行期赋值（镜像选择因此不生效，
  详见 devdocs）。要在启动前设（`OneKeyStart.bat` / shell 里 `set HF_ENDPOINT=…`）
  才有效；本模块则改用 `snapshot_download(endpoint=…)` 逐次显式传，绕开这个坑。
* **别迷信镜像**：0.36.2 + 本机代理下实测 `https://hf-mirror.com` 走 hub 下载会稳定
  失败（`FileMetadataError: Distant resource does not seem to be on huggingface.co`，
  4/4 次），而官方端点 3/3 次成功。所以这里不按 ping 选端点（ping 通 ≠ HTTPS 能下），
  而是真去试、失败就换。

为什么单独成模块
----------------
`core/step2_whisperX.py` 顶层就 import whisperx（拖 torch/CUDA），而这里的逻辑
（挑目录、挑端点、怎么提示）是纯逻辑且**可注入**，单独放着才能被
`tests/test_align_model_fallback.py` 廉价覆盖（不联网、不 import torch）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 项目内允许手动放对齐模型的目录名：`<model_dir>/align/<语言>/`
ALIGN_DIR_NAME = "align"

HF_OFFICIAL_ENDPOINT = "https://huggingface.co"
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

#: 进程**启动前**用户设的 HF_ENDPOINT（运行期再改无效，见模块说明）
_ENDPOINT_AT_IMPORT = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")

#: 手动放置时必需的文件；缺 preprocessor_config.json 就是那条 "Can't load feature
#: extractor" 的直接原因，缺权重则加载到一半失败。
REQUIRED_LOCAL_FILES = ("config.json", "preprocessor_config.json")
WEIGHT_FILES = ("pytorch_model.bin", "model.safetensors")

#: 手动下载时需要拿的文件（仓库里 flax_model.msgpack 另有 1.27 GB，PyTorch 用不上）
MANUAL_FILES = ("config.json", "preprocessor_config.json", "pytorch_model.bin",
                "vocab.json", "special_tokens_map.json")

#: 预下载时跳过这些：同仓库的 flax/tf/onnx 权重本项目用不到（省约 1.3 GB）
IGNORE_PATTERNS = ["flax_model*", "tf_model*", "*.msgpack", "*.h5", "*.ot", "*.onnx"]


class AlignModelUnavailable(RuntimeError):
    """对齐模型拿不到：本地没有、网络也没下下来。消息里带可照做的步骤。"""


# ------------------------------------------------------------------ 输出
def _default_log(message):
    """默认输出：优先 rich（项目依赖里有），否则去掉标记直接 print。"""
    try:
        from rich import print as rprint

        rprint(message)
        return
    except Exception:
        pass
    for tag in ("[cyan]", "[/cyan]", "[yellow]", "[/yellow]", "[red]", "[/red]",
                "[green]", "[/green]"):
        message = message.replace(tag, "")
    print(message)


def _one_line(text, limit=160):
    return " ".join(str(text).split())[:limit]


# ------------------------------------------------------------------ 语种 → 仓库
#: whisperx 那张表的进程内缓存。测试会直接改它（避免 import whisperx 拖 torch）。
_ALIGN_MODELS_CACHE = None


def huggingface_align_models():
    """whisperx 那张「语言 → HuggingFace 仓库」表（惰性读取 + 进程内缓存）。

    读不到（没装 whisperx）就返回空表 —— 调用方会退化成"交给 whisperx 自己处理"，
    不会因为这里拿不到表就崩。
    """
    global _ALIGN_MODELS_CACHE
    if _ALIGN_MODELS_CACHE is None:
        try:
            from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF

            _ALIGN_MODELS_CACHE = dict(DEFAULT_ALIGN_MODELS_HF)
        except Exception:
            _ALIGN_MODELS_CACHE = {}
    return dict(_ALIGN_MODELS_CACHE)


def align_model_repo(language_code):
    """语种对应的 HF 仓库名；走 torchaudio 管线的语种（en/fr/de/es/it）返回 None。"""
    code = (language_code or "").strip().lower()
    if not code:
        return None
    return huggingface_align_models().get(code)


# ------------------------------------------------------------------ 本地目录约定
def local_align_dir(model_dir, language_code):
    """手动放置对齐模型的目录：`<model_dir>/align/<语言>/`。"""
    return Path(model_dir) / ALIGN_DIR_NAME / (language_code or "").strip().lower()


def _nonempty(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def local_align_model_ready(path):
    """本地目录是否**完整**可用。

    要求 config.json / preprocessor_config.json 都在、且有一个非空权重文件 ——
    只判断目录存在会让"下到一半"的目录通过检查，之后 transformers 报的还是那句
    难以定位的 "Can't load feature extractor"（同 `step2_whisperX` 里
    `_complete_model_directory` 的思路）。
    """
    path = Path(path)
    if not all(_nonempty(path / name) for name in REQUIRED_LOCAL_FILES):
        return False
    return any(_nonempty(path / name) for name in WEIGHT_FILES)


# ------------------------------------------------------------------ 端点
def candidate_endpoints(preset=None):
    """要依次尝试的 HF 端点（去重保序）：preset/启动前设的值 → 官方 → hf-mirror。

    默认把**官方**排在镜像前面：实测官方在本机代理下是通的，而 hf-mirror 走 hub
    下载会稳定失败（见模块说明）。顺序只是"先试谁"，失败都会落到下一个。
    """
    if preset is None:
        preset = _ENDPOINT_AT_IMPORT
    ordered = [preset, HF_OFFICIAL_ENDPOINT, HF_MIRROR_ENDPOINT]
    endpoints = []
    for url in ordered:
        url = (url or "").strip().rstrip("/")
        if url and url not in endpoints:
            endpoints.append(url)
    return endpoints


# ------------------------------------------------------------------ 下载
def _snapshot_download(repo, model_dir, endpoint):
    """默认下载实现：整仓库快照（跳过 flax/tf 权重）到 `model_dir`。"""
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=repo, cache_dir=str(model_dir),
                             endpoint=endpoint, ignore_patterns=IGNORE_PATTERNS)


def _cached_locally(repo, model_dir):
    """本地缓存里是否已经有完整的一份（`local_files_only=True` 不走网络）。"""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=repo, cache_dir=str(model_dir),
                          local_files_only=True, ignore_patterns=IGNORE_PATTERNS)
        return True
    except Exception:
        return False


def ensure_align_model_cached(repo, model_dir, endpoints=None, download=None,
                              cached=None, log=None, language_code=None):
    """确保 `repo` 的权重已经在 `model_dir` 里，返回实际可用的端点（已缓存则 None）。

    逐个端点尝试，全失败抛 `AlignModelUnavailable`（消息里带手动下载的指引）。
    `language_code` 只用于把提示里的目录/命令写具体。
    """
    log = log or _default_log
    cached = cached or _cached_locally
    download = download or _snapshot_download

    if cached(repo, model_dir):
        log(f"[cyan]📁 对齐模型已在本地缓存：[/cyan]{repo}")
        return None

    errors = []
    for endpoint in (endpoints or candidate_endpoints()):
        try:
            log(f"[cyan]⬇️ 下载对齐模型：[/cyan]{repo} ← {endpoint}")
            download(repo, model_dir, endpoint)
            log(f"[green]✅ 对齐模型就绪：[/green]{repo}（{endpoint}）")
            return endpoint
        except Exception as exc:  # noqa: BLE001 - 任何失败都换下一个端点
            errors.append((endpoint, exc))
            log(f"[yellow]⚠️ {endpoint} 下载失败（{type(exc).__name__}），换下一个端点[/yellow]")

    raise AlignModelUnavailable(
        _unavailable_message(repo, model_dir, errors, language_code))


def _unavailable_message(repo, model_dir, errors, language_code=None):
    """失败提示：说清"这是网络问题"，并给出三条能照做的出路。"""
    code = (language_code or "").strip().lower() or "<语言代码>"
    target = Path(model_dir) / ALIGN_DIR_NAME / code
    if not target.is_absolute():
        # config.yaml 里 model_dir 是 './_model_cache'：提示里要给人能直接粘的绝对路径
        target = Path.cwd() / target
    lines = [
        f"❌ 对齐模型下载失败（网络问题，不是模型名写错）：{code} → {repo}",
        "",
        "  上游那两句「Can't load feature extractor … make sure you don't have a local",
        "  directory with the same name」/「could not be found in huggingface」都是**误导**：",
        "  仓库名没错，是拿不到文件元数据（transformers 把「本地没缓存 + 网络失败」",
        "  包装成了路径问题）。",
        "  已尝试的端点：",
    ]
    lines += [f"    · {endpoint} → {type(exc).__name__}: {_one_line(exc)}"
              for endpoint, exc in errors]
    lines += [
        "",
        "  三条出路（任选一条）：",
        "   1) 直接重试：这是偶发问题，重新点一次「开始处理字幕」通常就过。",
        "   2) 用项目自带命令预下载（自动换端点、可反复重跑）：",
        f"        python -m core.align_model {code}      "
        f"（在项目根目录下、用 .venv 的解释器）",
        "   3) 手动下载后放进项目（浏览器能打开 hf-mirror 就行，之后永久离线可用）：",
        f"        下载页  ：https://hf-mirror.com/{repo}/tree/main",
        f"        要的文件：{'、'.join(MANUAL_FILES)}",
        "                  （flax_model.msgpack 不用下，那是另一份 1.27 GB 的 flax 权重）",
        f"        放置目录：{target}",
        "                  （目录不存在就自己建；放好后本步直接读本地，不再联网）",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 加载
def _import_whisperx():
    import whisperx

    return whisperx


def load_align_model(language_code, device, model_dir, log=None, whisperx_module=None):
    """加载对齐模型：本地目录 → 缓存/多端点下载 → 明确的报错。

    这是 `core/step2_whisperX.py` 应该调用的入口（替代直接调
    `whisperx.load_align_model`）：上游遇到网络问题只会抛那句误导性的
    "could not be found in huggingface"，而且只试一个端点。
    """
    log = log or _default_log
    whisperx = whisperx_module or _import_whisperx()

    local = local_align_dir(model_dir, language_code)
    if local_align_model_ready(local):
        log(f"[cyan]📁 使用本地对齐模型目录：[/cyan]{local}")
        return whisperx.load_align_model(language_code=language_code, device=device,
                                        model_name=str(local), model_dir=model_dir)

    repo = align_model_repo(language_code)
    if repo is None:
        # torchaudio 管线（en/fr/de/es/it）或上游没有默认模型的语种：交给 whisperx
        return whisperx.load_align_model(language_code=language_code, device=device,
                                        model_dir=model_dir)

    ensure_align_model_cached(repo, model_dir, log=log, language_code=language_code)
    # 权重已在本地 → local_files_only 加载，网络再抖也不会卡在这一步
    try:
        return whisperx.load_align_model(language_code=language_code, device=device,
                                         model_dir=model_dir, model_cache_only=True)
    except Exception as exc:  # noqa: BLE001 - 缓存不完整/加载失败都要给指引
        raise AlignModelUnavailable(
            _unavailable_message(repo, model_dir, [], language_code) +
            f"\n\n  本地缓存已存在但加载失败：{type(exc).__name__}: {_one_line(exc)}"
        ) from exc


# ------------------------------------------------------------------ CLI
def _config_model_dir():
    try:
        from core.config_utils import load_key

        return load_key("model_dir")
    except Exception:
        return "./_model_cache"


def _main(argv=None):
    """`python -m core.align_model ja`：把某语种的对齐模型预下载到项目缓存。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m core.align_model",
        description="预下载 WhisperX 对齐模型（wav2vec2），避免转录跑到一半卡在网络上")
    parser.add_argument("language", nargs="?",
                        help="语言代码（ja / zh / ko …）；省略则列出需要联网下载的语言")
    parser.add_argument("--model-dir", default=None,
                        help="缓存目录（默认取 config.yaml 的 model_dir）")
    parser.add_argument("--endpoint", action="append", default=None,
                        help="只试这个端点（可重复；默认按内置顺序逐个试）")
    args = parser.parse_args(argv)

    if not args.language:
        repos = huggingface_align_models()
        print("需要联网下载对齐模型的语言（en/fr/de/es/it 走 torchaudio，不必预下载）：")
        for code, repo in sorted(repos.items()):
            print(f"  {code:4} {repo}")
        print("\n用法：.venv\\Scripts\\python.exe -m core.align_model ja")
        return 0

    model_dir = args.model_dir or _config_model_dir()
    repo = align_model_repo(args.language)
    if repo is None:
        print(f"{args.language} 不需要 HF 对齐模型（走 torchaudio 管线，或上游没有默认模型）。")
        return 0

    try:
        endpoint = ensure_align_model_cached(repo, model_dir, endpoints=args.endpoint,
                                             language_code=args.language)
    except AlignModelUnavailable as exc:
        print(exc)
        return 1
    print(f"✅ 已就绪：{repo} → {model_dir}（端点：{endpoint or '本地缓存已有'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
