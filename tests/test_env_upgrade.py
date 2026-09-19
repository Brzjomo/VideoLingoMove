"""环境升级后新增的纯逻辑测试（不需要装 torch/whisperx 就能跑）。

覆盖三块"改了会影响安装结果、但装完才发现错"的判断：
  1. FFmpeg 版本解析与大版本闸门（torchcodec 0.7 只支持 4–7）；
  2. FFmpeg 资产挑选（必须挑到非 shared 的 7.x，且不能挑到 8/9 ——
     这正是原实现"永远下 latest"会踩的坑）；
  3. 显卡算力 → torch CUDA 后端映射（Pascal/Volta 必须回退 cu126，
     否则 sm_61 上会拿到不含该架构的 cu128/cu129 构建）。
"""

import contextlib
import io
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer  # noqa: E402


class TestParseFfmpegVersion(unittest.TestCase):
    def test_release_build(self):
        text = ("ffmpeg version 7.0.2-full_build-www.gyan.dev Copyright (c) 2000-2024 "
                "the FFmpeg developers")
        self.assertEqual(installer.parse_ffmpeg_version(text), (7, 0, 2))

    def test_version_with_n_prefix_and_git_suffix(self):
        text = "ffmpeg version n7.1.5-12-g1fdbca85aa-20260731 Copyright (c) 2000-2026"
        self.assertEqual(installer.parse_ffmpeg_version(text), (7, 1, 5))

    def test_master_build(self):
        text = "ffmpeg version N-126636-g1ef0d9d701 Copyright (c) 2000-2026"
        self.assertEqual(installer.parse_ffmpeg_version(text), (0, 0, 0))

    def test_two_component_version(self):
        self.assertEqual(installer.parse_ffmpeg_version("ffmpeg version 6.1 "), (6, 1, 0))

    def test_unparseable(self):
        self.assertIsNone(installer.parse_ffmpeg_version(""))
        self.assertIsNone(installer.parse_ffmpeg_version("not ffmpeg at all"))

    def test_none_input(self):
        self.assertIsNone(installer.parse_ffmpeg_version(None))


class TestFfmpegMajorGate(unittest.TestCase):
    def test_supported_range(self):
        for major in (4, 5, 6, 7):
            self.assertTrue(installer.ffmpeg_major_ok(major), f"{major} 应被支持")

    def test_unsupported_range(self):
        # torchcodec 0.7 不支持 8/9；0/0 表示 master 构建（解析不到版本）
        for major in (0, 3, 8, 9):
            self.assertFalse(installer.ffmpeg_major_ok(major), f"{major} 不该被接受")

    def test_none_is_not_ok(self):
        self.assertFalse(installer.ffmpeg_major_ok(None))


class TestPickFfmpegAsset(unittest.TestCase):
    def _release(self, names):
        return {"assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                           for n in names]}

    def test_prefers_seven_shared_over_eight(self):
        """7.x 排在 8.x 前面：torchcodec 0.7 只带 core4..7，没有 core8。

        实测：装了 8.1 的 shared 包后 python 能认出 8.1，但 torchcodec 会去找
        libtorchcodec_core8.dll 而失败；换成 7.1 shared 立刻可用。
        （`FFMPEG_WIN_BRANCHES` 现已只列 7.x；即便有人把 8.x 加回候选，
        `_ffmpeg_branch_ok()` 也会把它滤掉 —— 这条用例两种情况下都成立。）
        """
        releases = [self._release([
            "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
            "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip",
        ])]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1", picked[0])

    def test_prefers_shared_within_branch(self):
        # shared 才含 torchcodec 需要的 avcodec 等动态库
        releases = [self._release([
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
            "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip",
        ])]
        self.assertEqual(installer._pick_ffmpeg_asset(releases)[0],
                         "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip")

    def test_falls_back_to_non_shared(self):
        # 7.x 的非 shared 构建同时含 exe 与 DLL，是可接受的退路
        releases = [self._release(["ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"])]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1.5", picked[0])

    def test_skips_unsupported_branches(self):
        # 9.0 不可能被选中（torchcodec 0.7 只支持 4–7）
        releases = [self._release(["ffmpeg-n9.0-latest-win64-gpl-9.0.zip"])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_never_picks_eight_x_even_when_only_eight_exists(self):
        """回归：上游 latest 只剩 8.x/9.x 时，**绝不能**挑 8.x。

        2026-09-19 实测 bug：`FFMPEG_WIN_BRANCHES` 里带着 `8.1`/`8.0` 兜底，而
        挑资产时不校验版本，于是 latest tag 里没有 7.x 时 `resolve_ffmpeg_url()`
        直接返回 `ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip` —— 下载 85 MB、
        解压，**最后**才报「大版本不合规」，安装必然失败。

        正确行为：显式列出的 8.x 分支被跳过，返回 None（让调用方继续去查
        autobuild tag，那里还有 7.1 资产）。
        """
        releases = [self._release([
            "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
            "ffmpeg-n8.0-latest-win64-gpl-shared-8.0.zip",
            "ffmpeg-master-latest-win64-gpl-shared.zip",
            "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip",
        ])]
        self.assertIsNone(
            installer._pick_ffmpeg_asset(releases),
            "8.x 不可能通过 ffmpeg_major_ok()（上限 7），选了就是白下载")

    def test_branch_gate_matches_configured_range(self):
        """`_ffmpeg_branch_ok` 与被挑中的分支必须和 FFMPEG_MIN/MAX_MAJOR 一致。"""
        self.assertTrue(installer._ffmpeg_branch_ok("7.1"))
        self.assertTrue(installer._ffmpeg_branch_ok("4.4"))
        self.assertFalse(installer._ffmpeg_branch_ok("8.1"))
        self.assertFalse(installer._ffmpeg_branch_ok("9.0"))
        self.assertFalse(installer._ffmpeg_branch_ok("3.4"))
        self.assertFalse(installer._ffmpeg_branch_ok(""))
        self.assertFalse(installer._ffmpeg_branch_ok(None))
        # 配置里的兜底分支也不该有漏网的合规值被误杀
        for b in installer.FFMPEG_WIN_BRANCHES:
            if installer._ffmpeg_branch_ok(b):
                self.assertLessEqual(int(b.split(".")[0]),
                                     installer.FFMPEG_MAX_MAJOR)

    def test_walks_older_releases(self):
        # 实测场景：latest tag 只剩 8.x/9.x，7.1 资产在更早的 autobuild tag 上
        releases = [
            self._release(["ffmpeg-n9.0-latest-win64-gpl-9.0.zip"]),
            self._release(["ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"]),
        ]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1.5", picked[0])

    def test_custom_branch_order(self):
        releases = [self._release([
            "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip",
            "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
        ])]
        picked = installer._pick_ffmpeg_asset(releases, branches=("7.1",))
        self.assertIn("n7.1", picked[0])

    def test_ignores_non_zip_and_linux_assets(self):
        releases = [self._release([
            "ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz",
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip.sha256",
        ])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_empty_input(self):
        self.assertIsNone(installer._pick_ffmpeg_asset([]))
        self.assertIsNone(installer._pick_ffmpeg_asset(None))
        self.assertIsNone(installer._pick_ffmpeg_asset([{}]))


class TestDownloadDirAndLocalWheels(unittest.TestCase):
    """下载与安装分离：大文件放 _downloads/，存在就优先用本地文件。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_wheel_names_match_index_convention(self):
        names = installer.wheel_names_for("cu126")
        self.assertEqual(len(names), 3)
        by_pkg = {pkg: fn for pkg, _ver, fn in names}
        self.assertIn(f"torch-{installer.TORCH_VERSION}+cu126-", by_pkg["torch"])
        self.assertIn(f"torchvision-{installer.TORCHVISION_VERSION}+cu126-",
                      by_pkg["torchvision"])
        for filename in by_pkg.values():
            self.assertTrue(filename.endswith(".whl"))

    def test_plan_reports_everything_missing_when_dir_empty(self):
        have, missing = installer.torch_download_plan("cu128", self.dir)
        self.assertEqual(have, [])
        self.assertEqual(len(missing), 3)
        for _pkg, filename, url in missing:
            self.assertIn(filename, url.replace("%2B", "+"))
            self.assertIn("download.pytorch.org", url)

    def test_existing_file_is_reused(self):
        for pkg, _ver, filename in installer.wheel_names_for("cu126"):
            target = os.path.join(self.dir, filename)
            with open(target, "wb") as handle:
                handle.write(b"x" * 16)
        have, missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(len(have), 3)
        self.assertEqual(missing, [])
        found = installer.local_wheels_for("cu126", self.dir)
        self.assertEqual(set(found), {"torch", "torchaudio", "torchvision"})

    def test_empty_file_is_not_reused(self):
        # 下载中断会留下 0 字节文件，绝不能当成"已下好"
        _pkg, _ver, filename = installer.wheel_names_for("cu126")[0]
        with open(os.path.join(self.dir, filename), "wb"):
            pass
        have, _missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(have, [])

    def test_wheel_for_other_backend_is_not_reused(self):
        # cu128 的轮子不能被当成 cu126 的用
        _pkg, _ver, filename = installer.wheel_names_for("cu128")[0]
        with open(os.path.join(self.dir, filename), "wb") as handle:
            handle.write(b"x" * 16)
        have, _missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(have, [])

    def test_ensure_download_dir_creates(self):
        target = os.path.join(self.dir, "nested", "_downloads")
        returned = installer.ensure_download_dir(target)
        self.assertTrue(os.path.isdir(returned))
        self.assertEqual(str(returned), target)

    def test_python_tag_matches_interpreter(self):
        self.assertEqual(installer.current_python_tag(),
                         f"cp{sys.version_info[0]}{sys.version_info[1]}")


class TestFfmpegSharedLibs(unittest.TestCase):
    """torchcodec 需要 FFmpeg **共享库**，静态构建不算可用。

    实测：系统装的是 gyan.dev 的 full_build 7.0.2（静态，bin 目录里一个 .dll
    都没有），大版本完全合规，但 `import torchcodec.decoders` 仍然失败，报
    "Could not find module '...libtorchcodec_core7.dll' (or one of its
    dependencies)"。换成 BtbN 的 7.1 **shared** 包后立即可用。
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_detects_windows_shared_dlls(self):
        (self.dir / "avcodec-61.dll").write_bytes(b"x")
        self.assertTrue(installer.has_shared_av_libs(self.dir))

    def test_detects_linux_shared_objects(self):
        (self.dir / "libavcodec.so.61").write_bytes(b"x")
        self.assertTrue(installer.has_shared_av_libs(self.dir))

    def test_static_build_has_no_shared_libs(self):
        # 静态构建：只有 exe，没有 avcodec DLL
        (self.dir / "ffmpeg.exe").write_bytes(b"x")
        (self.dir / "ffprobe.exe").write_bytes(b"x")
        self.assertFalse(installer.has_shared_av_libs(self.dir))

    def test_missing_dir(self):
        self.assertFalse(installer.has_shared_av_libs(self.dir / "nope"))

    def test_usable_ffmpeg_requires_shared_on_windows(self):
        """本机若只有静态 ffmpeg，usable_ffmpeg(require_shared=True) 必须判为不可用。"""
        bin_dir, _source, _version = installer.usable_ffmpeg(require_shared=True)
        if bin_dir is None:
            self.skipTest("本机没有可用的共享版 FFmpeg")
        self.assertTrue(installer.has_shared_av_libs(bin_dir))

    def test_usable_ffmpeg_without_shared_requirement_is_lenient(self):
        """require_shared=False 时只看大版本（Linux/macOS 的默认行为）。"""
        bin_dir, source, version = installer.usable_ffmpeg(require_shared=False)
        if bin_dir is None:
            self.skipTest("本机没有大版本合规的 FFmpeg")
        self.assertTrue(installer.ffmpeg_major_ok(version[0]))
        self.assertIn(source, ("项目内", "系统 PATH"))

    def test_absent_reason_is_a_string(self):
        self.assertIsInstance(installer.ffmpeg_absent_reason(), str)


class TestSmokeConnectsDllDirs(unittest.TestCase):
    """回归：体检的 smoke 必须先接入项目内 FFmpeg 的 DLL 目录。

    2026-09-19 实测 bug：FFmpeg 7.1.5 shared 已正确装到项目内，`import whisperx`
    / `pyannote.audio` / `demucs.api` 全过，**只有 `torchcodec.decoders` 失败**，
    报「Could not find module '...libtorchcodec_core7.dll' (or one of its
    dependencies)」—— 看起来像 FFmpeg 版本不对，实际原因是：

    Python 3.8 起扩展模块（.pyd）的依赖 DLL **不再从 PATH 解析**，只认
    `os.add_dll_directory()`。`runtime_libraries.setup()` 做的就是这件事，但它
    只在 `import core` 时被触发，而体检是**直接** import torchcodec。于是 DLL
    目录从未注册，报错把"没接 DLL"伪装成"版本不合规"。

    实测对照：不接 DLL 目录 → 必失败；接了就成功（同一份 FFmpeg、同一个 torch）。
    """

    def test_smoke_calls_runtime_libraries(self):
        """smoke_imports() 必须接入运行期库（DLL 目录）。

        直接打桩 `installer._ensure_runtime_libraries` 来观察是否被调用，
        这样不依赖真实 import 顺序，也不受本机是否装了包的影响。

        ⚠️ 必须把 stdout 收走：`smoke_imports()` 会真的 import 一遍六个模块，
        其中一个失败就会打印诊断（2026-09-19 实测：这些行混进 `Install.bat`
        的输出，让用户以为"没接入 FFmpeg 目录"）。
        """
        import unittest.mock as mock
        calls = []

        def _spy():
            calls.append(True)
            return {"platform": "test", "project_ffmpeg": None,
                    "project_ffmpeg_lib": None, "system_ffmpeg": None,
                    "dll_dirs": []}

        with mock.patch.object(installer, "_ensure_runtime_libraries",
                               side_effect=_spy):
            with contextlib.redirect_stdout(io.StringIO()):
                installer.smoke_imports(quiet=True)
        self.assertTrue(calls, "smoke_imports() 没有接入运行期库（DLL 目录）")

    def test_ensure_runtime_libraries_returns_report(self):
        """_ensure_runtime_libraries() 要返回可用的报告（失败也不能抛）。"""
        report = installer._ensure_runtime_libraries()
        self.assertIsInstance(report, dict)
        self.assertIn("dll_dirs", report)
        # 本机装了项目内 FFmpeg 时，必须报告出来并注册了目录
        if installer.project_ffmpeg_bin() is not None:
            self.assertIsNotNone(report.get("project_ffmpeg"))
            self.assertTrue(report.get("dll_dirs"))
            from pathlib import Path
            registered = [Path(d) for d in report["dll_dirs"]]
            self.assertTrue(
                any(installer.has_shared_av_libs(d) for d in registered),
                "接入的 DLL 目录里没有一份含 avcodec-*.dll 的 FFmpeg")

    def test_explain_helper_tolerates_missing_report(self):
        """诊断辅助函数不能因为在没有项目内 FFmpeg 时报错。

        同样要收走 stdout —— 它本来就是给人看的中文提示。
        """
        with contextlib.redirect_stdout(io.StringIO()):
            installer._explain_torchcodec_failure(None)
            installer._explain_torchcodec_failure({})


class TestFfmpegSkipWhenUsable(unittest.TestCase):
    """需求：环境里已有可用的 FFmpeg 就跳过下载。"""

    def setUp(self):
        # ensure_ffmpeg 会打印中文提示，测试里静音掉
        self._saved_info, self._saved_panel = installer.info, installer.panel
        installer.info = lambda *a, **k: None
        installer.panel = lambda *a, **k: None

    def tearDown(self):
        installer.info, installer.panel = self._saved_info, self._saved_panel

    def test_usable_ffmpeg_reports_source(self):
        bin_dir, source, version = installer.usable_ffmpeg()
        if bin_dir is None:
            self.skipTest("本机没有可用的 FFmpeg，跳过")
        self.assertIn(source, ("项目内", "系统 PATH"))
        self.assertTrue(installer.ffmpeg_major_ok(version[0]))

    def test_ensure_ffmpeg_skips_download_when_usable(self):
        """有可用版本时 ensure_ffmpeg 必须直接返回，不触发下载。"""
        if installer.usable_ffmpeg()[0] is None:
            self.skipTest("本机没有可用的 FFmpeg，跳过")

        calls = []

        def _boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("已有可用 FFmpeg 时不应触发下载")

        original = installer.download_ffmpeg_windows
        installer.download_ffmpeg_windows = _boom
        try:
            ok, needs_restart = installer.ensure_ffmpeg()
        finally:
            installer.download_ffmpeg_windows = original
        self.assertTrue(ok)
        self.assertFalse(needs_restart)
        self.assertEqual(calls, [])

    def test_ffmpeg_zip_name_is_stable(self):
        # 外部工具下载时用户要按这个名字存放，不能随意改
        self.assertEqual(installer.FFMPEG_ZIP_NAME, "ffmpeg-win64.zip")


class TestPipBackend(unittest.TestCase):
    """安装后端：uv 建的 venv 默认**没有 pip**，这条链子曾经直接崩掉整个安装。

    实测故障：`uv venv`（不带 --seed）→ 环境里 `import pip` 失败 →
    installer.py 引导 rich 失败 → 连报错用的 info() 也因为缺 rich 抛异常，
    真正的错误被盖掉。所以三层都要有测试：后端选择、ensurepip 兜底、纯文本输出。
    """

    def test_pip_command_prefers_uv(self):
        """有 uv 时必须选 uv —— 这就是「uv 替代 pip」的落点。"""
        if installer.uv_exe() is None:
            self.skipTest("本机没有 uv，跳过")
        base, kind = installer.pip_command()
        self.assertEqual(kind, "uv")
        self.assertEqual(base, [installer.uv_exe(), "pip"])

    def test_pip_command_falls_back_to_real_pip_without_uv(self):
        saved = installer.uv_exe
        installer.uv_exe = lambda: None
        try:
            if not installer.python_has_pip():
                self.skipTest("本机没有 pip，跳过")
            base, kind = installer.pip_command()
            self.assertEqual(kind, "pip")
            self.assertEqual(base, [sys.executable, "-m", "pip"])
        finally:
            installer.uv_exe = saved

    def test_require_real_pip_ignores_uv(self):
        """pip download 只能用真 pip：uv 没有 download 子命令。"""
        base, kind = installer.require_real_pip()
        if base is None:
            self.skipTest("本机既无 pip 也无法 ensurepip")
        self.assertEqual(kind, "pip")
        self.assertEqual(base, [sys.executable, "-m", "pip"])

    def test_python_has_pip_is_boolean(self):
        self.assertIn(installer.python_has_pip(), (True, False))

    def test_uv_exe_detection(self):
        uv = installer.uv_exe()
        self.assertTrue(uv is None or os.path.isfile(uv), f"uv 路径不存在: {uv}")

    def test_missing_pip_explains_instead_of_crashing(self):
        """两个后端都不可用时，必须给出修复指引而不是抛 ModuleNotFoundError。"""
        import contextlib
        import io
        saved_pip, saved_ensure, saved_uv = (installer.python_has_pip,
                                             installer._try_ensurepip,
                                             installer.uv_exe)
        installer.python_has_pip = lambda: False
        installer._try_ensurepip = lambda: False
        installer.uv_exe = lambda: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                base, kind = installer.pip_command()
                self.assertIsNone(base)
                self.assertIsNone(kind)
                # check=False 时应返回非零码而不是抛异常
                result = installer.pip(["nonexistent-package"], check=False)
                # pip download 路径同样要安全降级
                self.assertIsNone(installer.pip_download("requirements.txt",
                                                         self._tmp_dir()))
            self.assertNotEqual(result.returncode, 0)
        finally:
            installer.python_has_pip = saved_pip
            installer._try_ensurepip = saved_ensure
            installer.uv_exe = saved_uv

    def test_output_works_without_rich(self):
        """没有 rich 时 info()/panel() 必须退化成纯文本（这是当初盖掉真实错误的原因）。"""
        import contextlib
        import io
        saved_console, saved_panel = installer._RichConsole, installer._RichPanel
        installer._RichConsole = None
        installer._RichPanel = None
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                installer.info("[bold cyan]hello[/bold cyan]")
                installer.panel("body with [green]markup[/green]")
            text = buffer.getvalue()
            self.assertIn("hello", text)          # 标记被剥掉、正文还在
            self.assertIn("body with markup", text)
            self.assertNotIn("[bold", text)
        finally:
            installer._RichConsole, installer._RichPanel = saved_console, saved_panel

    def test_plain_strips_rich_markup(self):
        self.assertEqual(installer._plain("[bold cyan]x[/bold cyan]"),
                         installer._plain("[bold cyan]x[/bold cyan]").replace("[", ""))
        self.assertNotIn("[", installer._plain("[green]ok[/green]"))

    def test_uv_has_no_download_subcommand(self):
        """uv pip 没有 download 子命令：拿不到 pip 时必须优雅跳过预下载，而不是报错。"""
        import contextlib
        import io
        saved_pip, saved_ensure = installer.python_has_pip, installer._try_ensurepip
        installer.python_has_pip = lambda: False
        installer._try_ensurepip = lambda: False
        try:
            if installer.uv_exe() is None:
                self.skipTest("本机没有 uv，跳过")
            with contextlib.redirect_stdout(io.StringIO()):
                base, kind = installer.pip_command()
                self.assertEqual(kind, "uv")
                # 非 pip 后端下 pip_download 返回 None（跳过），不抛异常
                result = installer.pip_download("requirements.txt", self._tmp_dir())
            self.assertIsNone(result)
        finally:
            installer.python_has_pip, installer._try_ensurepip = saved_pip, saved_ensure

    def _tmp_dir(self):
        import tempfile
        return tempfile.mkdtemp()


class TestUvLockParsing(unittest.TestCase):
    """`uv pip compile --generate-hashes` 的输出解析。

    实测踩到的坑：带 hash 的锁定文件里**每一行** pin 都以 `\\` 续行，
    早期实现"看到续行符就跳过"，于是 192 个 pin 解析出 0 个。
    """

    LOCK = """\
# This file was autogenerated by uv via the following command:
#    uv pip compile requirements.txt --generate-hashes
aiohappyeyeballs==2.7.1 \\
    --hash=sha256:aaaa \\
    --hash=sha256:bbbb
whisperx==3.8.6 \\
    --hash=sha256:cccc
torch==2.8.0+cu126 \\
    --hash=sha256:dddd
numpy==2.4.6 \\
    --hash=sha256:eeee
tomli==2.0.1 ; python_version < "3.11" \\
    --hash=sha256:ffff
"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "requirements.lock.txt")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(self.LOCK)

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_all_pins_despite_continuations(self):
        pins = installer._parse_locked_requirements(self.path)
        names = [n for n, _ in pins]
        self.assertEqual(names, ["aiohappyeyeballs", "whisperx", "torch",
                                 "numpy", "tomli"])

    def test_versions_have_no_continuation_or_marker(self):
        pins = dict(installer._parse_locked_requirements(self.path))
        self.assertEqual(pins["whisperx"], "3.8.6")
        self.assertEqual(pins["torch"], "2.8.0+cu126")   # 本地版本号要保留
        for version in pins.values():
            self.assertNotIn("\\", version)
            self.assertNotIn(";", version)
            self.assertNotIn("--hash", version)

    def test_comments_and_blank_lines_ignored(self):
        pins = installer._parse_locked_requirements(self.path)
        self.assertFalse(any("autogenerated" in n for n, _ in pins))

    def test_empty_file(self):
        empty = os.path.join(self._tmp.name, "empty.txt")
        with open(empty, "w", encoding="utf-8"):
            pass
        self.assertEqual(installer._parse_locked_requirements(empty), [])


class TestUvNativePath(unittest.TestCase):
    """uv 原生安装路径：compile 生成锁定文件 → install -r 安装。

    关键取舍：用 `install -r` 而**不是** `sync` —— 实测 `sync` 会把锁定文件
    之外的包全部卸掉（最小 lock 试跑时连 pip/setuptools 都被删了）。
    """

    def test_uv_detected(self):
        self.assertTrue(installer.uv_exe() is None or
                        os.path.isfile(installer.uv_exe()))

    def test_lock_file_path_is_project_local(self):
        self.assertEqual(installer.UV_LOCK_FILE,
                         os.path.join(installer.DEFAULT_DOWNLOAD_DIR,
                                      "requirements.lock.txt"))

    def test_compile_returns_none_without_uv(self):
        saved = installer.uv_exe
        installer.uv_exe = lambda: None
        try:
            self.assertIsNone(installer.uv_lock_requirements())
        finally:
            installer.uv_exe = saved

    def test_install_locked_uses_install_not_sync(self):
        """必须走 `install -r`；sync 会卸掉 pip 等锁定文件之外的包。"""
        calls = []

        class _Result:
            returncode = 0

        def fake_uv_pip(args, **kwargs):
            calls.append(list(args))
            return _Result()

        saved = installer.uv_pip, installer.uv_exe
        installer.uv_exe = lambda: "uv"
        installer.uv_pip = fake_uv_pip
        try:
            installer.uv_install_locked("some.lock")
        finally:
            installer.uv_pip, installer.uv_exe = saved
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "install")
        self.assertIn("-r", calls[0])
        self.assertNotIn("sync", calls[0])

    def test_torch_install_passes_torch_backend(self):
        """uv 必须收到 --torch-backend，否则会装成 PyPI 的 CPU 版 torch。"""
        calls = []

        class _Result:
            returncode = 0

        def fake_uv_pip(args, **kwargs):
            calls.append(list(args))
            return _Result()

        saved = installer.uv_pip, installer.uv_exe
        installer.uv_exe = lambda: "uv"
        installer.uv_pip = fake_uv_pip
        try:
            installer.uv_install_torch("cu126", download_dir=None)
        finally:
            installer.uv_pip, installer.uv_exe = saved
        joined = " ".join(calls[0])
        self.assertIn("--torch-backend", calls[0])
        self.assertIn("cu126", calls[0])
        self.assertIn("torch==2.8.0", joined)
        self.assertIn("--no-deps", calls[0])


class TestSearchboxDependency(unittest.TestCase):
    """`streamlit-searchbox` 是侧边栏 MODEL 搜索框的依赖，已进主依赖清单。

    历史：它原本只是「装了更好」的可选包，侧边栏会提示 `pip install
    streamlit-searchbox`。既然有用就别让用户自己装 —— 现在写进 requirements.txt，
    并且纳入 installer 的体检，缺失时会被报出来而不是静默降级成文本框。
    """

    def test_declared_in_requirements(self):
        text = pathlib.Path("requirements.txt").read_text(encoding="utf-8")
        pins = [l for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        self.assertTrue(any(l.strip().startswith("streamlit-searchbox")
                            for l in pins),
                        "requirements.txt 里必须有 streamlit-searchbox")

    def test_checked_by_health_check(self):
        self.assertIn("streamlit_searchbox", installer.REQUIRED_IMPORTS)
        self.assertEqual(installer.REQUIRED_IMPORTS["streamlit_searchbox"],
                         "streamlit-searchbox")

    def test_importable_when_installed(self):
        """在装好依赖的环境里必须能 import（没装则跳过，便于纯静态检查环境）。

        ⚠️ 必须收走 stdout/stderr：`streamlit_searchbox` 会连带 import streamlit，
        后者在首次 import 时打一行
        `Thread 'MainThread': missing ScriptRunContext! ...`
        —— 2026-09-19 实测：这行混进了 `Install.bat` 的安装日志，看起来像出错了。
        """
        import importlib.util
        if importlib.util.find_spec("streamlit_searchbox") is None:
            self.skipTest("当前解释器未安装 streamlit-searchbox")
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            module = importlib.import_module("streamlit_searchbox")
        self.assertTrue(hasattr(module, "st_searchbox"))

    def test_sidebar_has_fallback(self):
        """即使依赖缺失也不能崩：sidebar_setting.model_input() 必须保留回退分支。"""
        src = pathlib.Path("st_components/sidebar_setting.py").read_text(encoding="utf-8")
        self.assertIn("from streamlit_searchbox import st_searchbox", src)
        self.assertIn("except ImportError:", src)
        self.assertIn("config_input(\"MODEL\", \"api.model\"", src)


class TestTorchBackend(unittest.TestCase):
    """算力 → 后端映射。这是本次升级最关键的一条：cu128/cu129 已移除 Pascal。"""

    def test_pascal_maps_to_cu126(self):
        for cap in (6.1, 6.0, 5.2, 3.7):  # P2200/P100/GTX9/K80
            backend, reason = installer.detect_torch_backend("auto", gpu_cap=cap)
            self.assertEqual(backend, "cu126", f"算力 {cap} 应回退 cu126")
            self.assertIn("6.1" if cap == 6.1 else str(cap), reason)

    def test_volta_and_turing_map_to_cu128(self):
        for cap in (7.0, 7.5):  # V100 / T4
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu128")

    def test_ampere_and_ada_map_to_cu128(self):
        for cap in (8.0, 8.6, 8.9):  # A100 / RTX3080 / RTX40
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu128")

    def test_blackwell_maps_to_cu129(self):
        for cap in (12.0, 12.1):
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu129")

    def test_explicit_backend_wins(self):
        for requested in ("cu126", "cu128", "cu129", "cpu"):
            backend, reason = installer.detect_torch_backend(requested, gpu_cap=8.6)
            self.assertEqual(backend, requested)
            self.assertIn("显式指定", reason)

    def test_no_gpu_falls_back_to_cpu(self):
        # gpu_cap=None 且探测不到 → cpu（这里注入 None 短路真实探测）
        backend, reason = installer.detect_torch_backend("auto", gpu_cap=None)
        self.assertIn(backend, ("cpu", "cu128", "cu126", "cu129"))
        self.assertTrue(reason)


class TestTorchSpecsCoverage(unittest.TestCase):
    def test_every_backend_has_index_and_label(self):
        for backend, (index_url, label) in installer.TORCH_SPECS.items():
            self.assertTrue(index_url.startswith("https://download.pytorch.org/whl/"))
            self.assertTrue(index_url.endswith(backend))
            self.assertTrue(label)

    def test_python_gate_matches_whisperx_requirement(self):
        # whisperx 3.8.6 的 requires_python 是 >=3.10,<3.14
        self.assertEqual(installer.PYTHON_MIN, (3, 10))
        self.assertEqual(installer.PYTHON_MAX, (3, 14))
        self.assertTrue(installer.python_ok((3, 10, 0)))
        self.assertTrue(installer.python_ok((3, 13, 9)))
        self.assertFalse(installer.python_ok((3, 9, 18)))
        self.assertFalse(installer.python_ok((3, 14, 0)))

    def test_torch_version_pins_match_whisperx(self):
        # whisperx 3.8.6 要求 torch~=2.8.0 / torchvision~=0.23.0
        self.assertEqual(installer.TORCH_VERSION, "2.8.0")
        self.assertEqual(installer.TORCHVISION_VERSION, "0.23.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
