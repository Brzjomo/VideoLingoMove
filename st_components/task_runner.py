"""带暂停/继续/停止的后台任务执行器（Streamlit 侧）。

移植自上游 5f0ba8c 的 core/st_utils/task_runner.py，按 dev 的结构放在
st_components/ 下（dev 没有 core/st_utils 包）。

用法：
    runner = TaskRunner.get(st.session_state)
    runner.start([("转录", step2_whisperX.transcribe), ...])
    runner.pause() / runner.resume() / runner.stop()
    runner.state  # idle | running | paused | stopped | completed | error

为什么需要它：dev 原先是在 Streamlit 脚本线程里同步跑整条流程，唯一的
"中止"手段是杀掉服务进程。长视频的 Whisper 识别与多轮翻译动辄十几分钟，
这对用户是不可接受的。
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


class StopTask(Exception):
    """用户请求停止时抛出，用于从 core 里的长循环中脱身。"""


@dataclass
class TaskRunner:
    """在后台线程里顺序执行多个步骤，并支持暂停/停止。"""

    # 对外只读状态
    state: str = "idle"  # idle | running | paused | stopped | completed | error
    current_step: int = -1  # 0 起，-1 表示尚未开始
    total_steps: int = 0
    current_label: str = ""
    error_msg: str = ""
    paused_for_review: bool = False  # 由"等待人工确认"触发的暂停（区分于用户手动暂停）

    # 内部状态
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None
    _steps: List[Tuple[str, Callable]] = field(default_factory=list)

    # 类级指针，让 core 里的长循环无需持有 runner 引用即可调用
    # TaskRunner.check_cancel()。只在 start() 起的后台线程内有意义。
    _current: "Optional[TaskRunner]" = None

    def __post_init__(self):
        self._pause_event.set()  # 初始不暂停

    # ------ 取消钩子（供 core 代码调用）------

    @classmethod
    def check_cancel(cls) -> None:
        """暂停时阻塞，收到停止请求时抛 StopTask。

        任何线程都可安全调用；没有活跃 runner 时（例如直接命令行跑脚本）
        直接返回，等价于空操作。
        """
        runner = cls._current
        if runner is None:
            return
        # 暂停时在这里阻塞，这样"暂停"对内部长循环同样生效
        runner._pause_event.wait()
        if runner._stop_event.is_set():
            raise StopTask()

    # ------ 每个 session_state 一个单例 ------

    @staticmethod
    def get(session_state, key: str = "_task_runner") -> "TaskRunner":
        if key not in session_state:
            session_state[key] = TaskRunner()
        return session_state[key]

    @classmethod
    def request_review_pause(cls) -> bool:
        """由步骤内部请求"等待人工确认"的暂停。

        与 pause() 的区别只在于打上 paused_for_review 标记，让 UI 知道
        该显示"继续"确认界面而不是普通的暂停条。

        刻意由步骤自己调用（worker 线程内），而不是让步骤去写
        st.session_state —— 从非脚本线程改 session_state 不可靠。
        本方法只触碰 threading.Event 与普通属性，是安全的。
        """
        runner = cls._current
        if runner is None:
            return False
        runner.pause(for_review=True)
        return True

    # ------ 控制接口 ------

    def start(self, steps: List[Tuple[str, Callable]]):
        """在后台线程里开始执行步骤。

        Args:
            steps: [(标签, 无参可调用对象), ...]
        """
        if self.state in ("running", "paused"):
            return  # 已在运行
        if not steps:
            raise ValueError("TaskRunner.start() 需要一个非空步骤列表")

        self._steps = list(steps)
        self.total_steps = len(self._steps)
        self.current_step = -1
        self.current_label = ""
        self.error_msg = ""
        self.paused_for_review = False
        self.state = "running"
        self._pause_event.set()
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self, for_review: bool = False):
        if self.state == "running":
            self._pause_event.clear()
            self.paused_for_review = for_review
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self._pause_event.set()
            self.paused_for_review = False
            self.state = "running"

    def stop(self):
        """请求停止：任务会在下一个检查点退出。"""
        if self.state in ("running", "paused"):
            self._stop_event.set()
            self._pause_event.set()  # 若正暂停，先解除阻塞让线程能退出
            self.paused_for_review = False
            self.state = "stopped"

    def reset(self):
        """回到 idle（仅在非运行状态下允许）。"""
        if self.state not in ("running", "paused"):
            self.state = "idle"
            self.current_step = -1
            self.total_steps = 0
            self.current_label = ""
            self.error_msg = ""
            self.paused_for_review = False
            self._steps = []

    # ------ 只读属性 ------

    @property
    def is_active(self) -> bool:
        return self.state in ("running", "paused")

    @property
    def is_done(self) -> bool:
        return self.state in ("completed", "stopped", "error")

    @property
    def progress(self) -> float:
        """0.0 ~ 1.0"""
        if self.total_steps == 0:
            return 0.0
        return min((self.current_step + 1) / self.total_steps, 1.0)

    # ------ 内部实现 ------

    def _run(self):
        type(self)._current = self
        try:
            for i, (label, func) in enumerate(self._steps):
                if self._stop_event.is_set():
                    self.state = "stopped"
                    return

                self._pause_event.wait()  # 暂停时阻塞于此

                # 恢复后再次确认，避免"暂停中点了停止"被漏掉
                if self._stop_event.is_set():
                    self.state = "stopped"
                    return

                self.current_step = i
                self.current_label = label
                func()

            self.state = "completed"
        except StopTask:
            self.state = "stopped"
        except Exception as e:
            self.error_msg = f"{type(e).__name__}: {e}"
            self.state = "error"
            traceback.print_exc()
        finally:
            if type(self)._current is self:
                type(self)._current = None
