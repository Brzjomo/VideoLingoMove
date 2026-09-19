"""按内容寻址的 ASR 结果缓存，独立于会被清空的 output/ 目录。

移植自上游 043aa7a 的 core/asr_backend/transcription_cache.py，针对 dev 做了
三处必要调整：

1) 身份里加入 asr_engine 及其相关设置。
   上游只有 local/elevenlabs 两种运行时，而 dev 是 whisper/volcano；两者的
   分段命名完全相同（都是 f"{start}_{end}"），且都从 (0, duration) 起步，
   不区分引擎就会把火山的结果喂给 Whisper 流程、反之亦然。火山那十几个参数
   （标点、ITN、DDC、模型版本……）都会实质改变识别结果，也必须参与身份。

2) 不依赖 core.utils.check_cancel —— dev 没有 core/utils 包（那是上游 3.x
   重构的产物）。

3) 放在 core/all_whisper_methods/ 下，与 dev 其余 ASR 代码同处一地。

上游的实现细节被完整保留：md5 只用于收敛大文件，最终键是 sha256；原子写入；
结果结构校验；读取失败一律静默当作未命中（缓存永远不该让成功的识别失败）。
"""

import hashlib
import json
import math
import os
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

CACHE_DIR = Path(".cache/asr")
# 预处理方式、模型选项或结果解释方式变化时递增，让旧缓存自动失效
SCHEMA = 1


def _package_versions() -> dict:
    """参与身份的包版本。缺失的包记为 None（而不是报错）。"""
    packages = {}
    for name in ("whisperx", "faster-whisper", "demucs"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return packages


def cache_key(media_file: str, settings: dict) -> str:
    """算缓存键：源媒体内容 + 与 ASR 结果有关的设置。

    刻意**不**包含凭据、文件名与翻译/TTS 设置——换密钥或改文件名不该让
    缓存失效，而换模型/换引擎/改语言才应该。
    """
    digest = hashlib.md5(usedforsecurity=False)
    with open(media_file, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    identity = {
        "schema": SCHEMA,
        "media_md5": digest.hexdigest(),
        "packages": _package_versions(),
        **settings,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode()
    ).hexdigest()


def valid_result(result) -> bool:
    """结构校验：宁可当未命中重跑，也不要把坏数据灌进字幕流程。"""
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        return False
    for segment in result["segments"]:
        if not isinstance(segment, dict):
            return False
        words = segment.get("words") or []
        if not isinstance(words, list):
            return False
        if not words and not isinstance(segment.get("text"), str):
            return False
        if any(not isinstance(word, dict) or not isinstance(word.get("word"), str)
               for word in words):
            return False
        for item in [segment, *words]:
            if not isinstance(item, dict):
                return False
            for field in ("start", "end"):
                value = item.get(field)
                if value is not None and (
                    not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    return False
            if (item.get("start") is not None and item.get("end") is not None
                    and item["end"] < item["start"]):
                return False
    return True


def read_result(key: str, part: str):
    """读取缓存条目；任何异常/不合规都返回 None（当作未命中）。"""
    try:
        entry = json.loads(
            (CACHE_DIR / key / f"{part}.json").read_text(encoding="utf-8")
        )
        if (entry.get("schema") == SCHEMA
                and entry.get("key") == key
                and isinstance(entry.get("result"), dict)
                and (entry.get("language") is None
                     or isinstance(entry.get("language"), str))
                and valid_result(entry["result"])):
            return entry
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def write_result(key: str, part: str, result, language=None):
    """原子写入一条缓存。写失败只在调试时可见——缓存坏了不该毁掉成功的识别。"""
    if not valid_result(result):
        return
    if language is not None and not isinstance(language, str):
        language = None
    directory = CACHE_DIR / key
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(
                {"schema": SCHEMA, "key": key, "language": language, "result": result},
                file,
                allow_nan=False,
                ensure_ascii=False,
            )
        os.replace(temporary, directory / f"{part}.json")
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def clear_cache() -> int:
    """清空缓存，返回删除的文件数（供 UI 的"清缓存"入口使用）。"""
    removed = 0
    if not CACHE_DIR.exists():
        return removed
    for path in CACHE_DIR.rglob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
