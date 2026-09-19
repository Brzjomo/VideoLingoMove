"""向后兼容入口：真正的实现在 installer.py。

保留这个文件是因为启动器（OneKeyStart.bat / batch/StartBatch.bat /
AudioExtract/Start.bat）与文档都还在调用 `python install.py`。
行为与 `python installer.py --launch` 一致。

体检请直接用：`python installer.py --check`（或 `python install.py --check`）。
"""

import sys

from installer import main

if __name__ == "__main__":
    args = sys.argv[1:]
    # 默认装完就启动，保持 install.py 原有的行为；--check/--no-launch 除外
    if "--check" not in args and "--launch" not in args and "--no-launch" not in args:
        args.append("--launch")
    raise SystemExit(main(args))
