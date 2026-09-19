"""启动前预检 + 带日志启动 Streamlit。

移植上游 b256ca1 的 launch.py。价值在于：把"启动失败"从一句看不懂的
Streamlit 报错，变成一条明确的诊断（缺包 / 缺 ffmpeg / 端口被占用），
并且把这次运行的输出留一份日志便于回溯。

用法：
    python launch.py [--port 8501] [--no-log]
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认是 GBK，打印 emoji 会抛 UnicodeEncodeError
# （实测 `python launch.py` 会崩在打印 ℹ️ 上）。复用 dev 已有的
# easy_util.ensure_utf8_console()，必须放在任何输出之前。
try:
    from easy_util import ensure_utf8_console
    ensure_utf8_console()
except Exception:
    pass

LOG_DIR = Path("logs")


def log_path():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"videolingo_{stamp}.log"


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def preflight(port):
    """返回 (errors, notes)。errors 非空即不应继续启动。"""
    errors, notes = [], []

    # 1) 关键包
    for module, pip_name in (("streamlit", "streamlit"),
                             ("json_repair", "json-repair"),
                             ("torch", "torch")):
        try:
            __import__(module)
        except Exception as e:
            errors.append(f"缺少 {module}（pip install {pip_name}）：{e}")

    # 2) ffmpeg / ffprobe
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        errors.append(f"未找到 {' / '.join(missing)}（下载与音频处理必需）")

    # 3) 端口
    if port_in_use(port):
        errors.append(f"端口 {port} 已被占用：可能是上一次的 VideoLingo 还在运行。")

    # 4) 提示性信息（不阻止启动）
    try:
        import torch
        if not torch.cuda.is_available():
            notes.append("当前 torch 无法使用 CUDA，转录会走 CPU，非常慢。")
        else:
            notes.append(f"GPU：{torch.cuda.get_device_name(0)}")
    except Exception:
        pass
    try:
        import whisperx  # noqa: F401
    except Exception:
        notes.append("未安装 whisperx，Whisper 本地转录不可用。")
    if not Path("config.yaml").exists():
        notes.append("尚无 config.yaml，首次运行会从 config.example.yaml 自动创建。")

    return errors, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description="启动 VideoLingo")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-log", action="store_true", help="不写运行日志")
    args = parser.parse_args(argv)

    errors, notes = preflight(args.port)
    for note in notes:
        print(f"[launch] ℹ️  {note}")
    for err in errors:
        print(f"[launch] ❌ {err}")
    if errors:
        print("[launch] 提示：可运行 `python installer.py --check` 获取完整体检报告。")
        return 1

    cmd = [sys.executable, "-m", "streamlit", "run", "st.py",
           "--server.port", str(args.port), "--logger.level", "error"]

    if args.no_log:
        return subprocess.run(cmd).returncode

    LOG_DIR.mkdir(exist_ok=True)
    target = log_path()
    print(f"[launch] 🚀 启动中… 地址 http://127.0.0.1:{args.port}")
    print(f"[launch] 📝 运行日志：{target}")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"}
    with open(target, "w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", env=env)
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
            log.flush()
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
