"""批量模式的路径辅助（刻意保持零依赖，便于单测）。

为什么单独一个模块：`gui.py` 在导入期就会拉起 streamlit、并通过
`st_components.imports_and_utils` → `core` → `torch` 拖进整条重依赖链。
把「默认输入目录」这类纯路径逻辑放在这里，测试可以直接导入，不需要
torch / streamlit 在场。
"""

import os

#: `batch/` 的父目录，即项目根
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认输入目录的相对位置
DEFAULT_INPUT_RELATIVE = os.path.join('batch', 'input')


def default_input_dir(root_dir=None):
    """批量模式的默认输入目录：<项目根>/batch/input。"""
    return os.path.join(root_dir or ROOT_DIR, DEFAULT_INPUT_RELATIVE)


def ensure_dir(path):
    """确保目录存在。返回 (路径, 是否新建, 错误信息或 None)。

    不抛异常：调用方是 Streamlit 界面，需要把失败原因显示给用户，
    而不是让整页崩掉。
    """
    if os.path.isdir(path):
        return path, False, None
    try:
        os.makedirs(path, exist_ok=True)
        return path, True, None
    except OSError as e:      # 权限不足 / 路径非法 / 被占用
        return path, False, str(e)


def ensure_default_input_dir(root_dir=None):
    """确保默认输入目录存在，返回 (路径, 是否新建, 错误信息或 None)。

    这个目录在 `.gitignore` 里（`batch/input/`），所以刚克隆或刚搬迁的仓库里
    根本没有它。旧实现只在下游 `video_processor.py` 里 `os.makedirs`，但界面在
    更早的地方就先判断「目录不存在」并 return 了 —— 于是默认路径必然报
    「目录不存在: <项目根>/batch/input」，永远走不到创建那一步。
    现在由界面负责把它建出来。
    """
    return ensure_dir(default_input_dir(root_dir))
