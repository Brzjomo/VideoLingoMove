"""Streamlit API 卫生检查：不许再用已弃用的宽度参数。

背景（2026-09-20 实测）：batch 模式控制台每次渲染都打

    Please replace `use_container_width` with `width`.
    `use_container_width` will be removed after 2025-12-31.
    For `use_container_width=True`, use `width='stretch'`.
    For `use_container_width=False`, use `width='content'`.

本分支钉的是 `streamlit>=1.49.1,<2.0.0`（实测 1.64.0），所以控件宽度统一写成
`width="stretch"`（原 `use_container_width=True`）/ `width="content"`（原 `False`），
`number_input` 这类此前没有该参数的元素现在也有了。

两条断言都是**静态**的：用 `ast` 读源码 + `inspect` 查签名，不启动 Streamlit、
不需要浏览器、不写任何文件。
"""

import ast
import inspect
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit  # noqa: E402

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 不扫这些目录：环境/缓存/产物，里面全是第三方代码或生成物
SKIP_DIRS = {
    ".git", ".venv", ".python", ".uv-cache", ".pip-cache", ".cache",
    "_model_cache", "_downloads", "ffmpeg", "logs", "output", "history",
    "__pycache__", "node_modules",
}

#: 已弃用的关键字参数 → 现在该用的写法
DEPRECATED = {
    "use_container_width": "width=\"stretch\"（原 True）/ width=\"content\"（原 False）",
    "use_column_width": "width=\"stretch\"（st.image 等）",
}


def project_python_files():
    for path in sorted(PROJECT_ROOT.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


class TestStreamlitWidthApi(unittest.TestCase):
    def test_no_deprecated_width_kwargs(self):
        """`use_container_width` / `use_column_width` 一个都不许留。"""
        offenders = []
        for path in project_python_files():
            tree = parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg in DEPRECATED:
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.value.lineno} "
                        f"{node.arg}= → 改用 {DEPRECATED[node.arg]}")
        self.assertEqual(offenders, [], "仍有已弃用的宽度参数：\n" + "\n".join(offenders))

    def test_width_only_passed_to_elements_that_support_it(self):
        """传给 `width=` 的必须是真的支持 `width` 的 streamlit 元素。

        这条防的是"把 `use_container_width` 机械替换成 `width` 却换到了不支持
        该参数的元素上" —— 例如 `st.text` / `st.markdown` 没有 `width`，传了就是
        运行时 TypeError（历史上 `st.image` 就踩过一次同类问题，见
        devdocs/05-guides/04-已知问题与技术债.md 的 G4）。
        """
        problems = []
        checked = 0
        for path in project_python_files():
            tree = parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                # 只关心 `st.<元素>(...)` / `st.sidebar.<元素>(...)` 这类
                chain = []
                cursor = func
                while isinstance(cursor, ast.Attribute):
                    chain.append(cursor.attr)
                    cursor = cursor.value
                if not isinstance(cursor, ast.Name) or cursor.id != "st":
                    continue
                element = getattr(streamlit, chain[-1], None)
                width_kw = next((kw for kw in node.keywords
                                 if kw.arg == "width"), None)
                if width_kw is None or element is None:
                    continue
                checked += 1
                where = f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} st.{chain[-1]}"
                try:
                    params = inspect.signature(element).parameters
                except (TypeError, ValueError):
                    continue
                if "width" not in params:
                    problems.append(f"{where}：该元素没有 width 参数")
                    continue
                if isinstance(width_kw.value, ast.Constant) and \
                        isinstance(width_kw.value.value, str) and \
                        width_kw.value.value not in ("stretch", "content"):
                    problems.append(
                        f"{where}：width={width_kw.value.value!r} 不是 "
                        f"'stretch'/'content'")
        self.assertEqual(problems, [], "width 用法有问题：\n" + "\n".join(problems))
        self.assertGreater(checked, 0, "一个 st.<元素>(width=…) 都没扫到，"
                                       "说明这条断言已经失效（没在检查任何东西）")

    def test_no_callback_on_widget_that_disables_itself(self):
        """同一个控件不能既给 `on_click=` 又用回调会改的状态做 `disabled=`。

        2026-09-20 实测（用户报障「点开始批量处理后界面一直闪、控制台没输出」）：

            if st.button("▶️ 开始批量处理",
                         disabled=st.session_state.processing,
                         on_click=start_processing):   # start_processing 里置 processing=True
                st.session_state.processor.process_batch()   # ← 永远跑不到

        Streamlit 的 widget 回调在**脚本重跑之前**执行，所以回调先把 `processing`
        置 True → 本次渲染的按钮就是 `disabled=True` → **禁用按钮的返回值恒为 False**
        → `if st.button(...)` 的分支（唯一调用 `process_batch()` 的地方）被跳过；
        而末尾的"若 processing 就 0.5 秒后 rerun"块看到 processing 仍为 True，
        于是无限重跑：界面一直闪、进程却什么都没干、控制台一行输出都没有。

        正确写法是把状态改在**点击分支内部**（`AudioExtract/gui.py` 的
        `▶️ 开始提取` 就是这么写的）。
        """
        problems = []
        for path in project_python_files():
            tree = parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                kwargs = {kw.arg for kw in node.keywords}
                if "on_click" in kwargs and "disabled" in kwargs:
                    problems.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}："
                        f"on_click 与 disabled 同时出现在一个控件上")
        self.assertEqual(
            problems, [],
            "on_click 会在脚本重跑前执行，若它改了 disabled 依赖的状态，"
            "控件就会自己把自己禁用掉、点击分支再也进不去：\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
