"""cleanup.py 的删除机制单元测试（在临时目录里做，不碰真实缓存）。"""

import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 让 cleanup.dir_size() 跳过"明显很大"的目录的逐文件统计。本机 pip/uv 缓存与
#: 系统 Temp 动辄几 GB，而 `TestCleanSelection.test_unknown_only_key_is_rejected`
#: 要调 `cleanup.main()`、`TestNoSharedDataInModelsSelection` 要调 `build_targets()`
#: —— 不关掉实扫时这几个用例会把整套回归测试拖到几分钟以上（实测单个类 >60s）。
#: 该开关只影响"大小数字"（超限目录记 0），不影响"哪些目标被选中"的判定逻辑；
#: 临时目录里 KB 级的测试文件仍旧精确统计。
os.environ.setdefault("VIDEOLINGO_CLEANUP_FAST_SCAN", "1")

import cleanup  # noqa: E402


class TestHuman(unittest.TestCase):
    def test_units(self):
        self.assertEqual(cleanup.human(512), "512 B")
        self.assertEqual(cleanup.human(1024), "1.0 KB")
        self.assertEqual(cleanup.human(1024 ** 2), "1.0 MB")
        self.assertEqual(cleanup.human(5 * 1024 ** 3), "5.0 GB")

    def test_none(self):
        self.assertEqual(cleanup.human(None), "?")


class TestDirSize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_returns_none(self):
        self.assertIsNone(cleanup.dir_size(self.root / "nope"))

    def test_empty_dir_is_zero(self):
        target = self.root / "empty"
        target.mkdir()
        self.assertEqual(cleanup.dir_size(target), 0)

    def test_sums_files_recursively(self):
        (self.root / "a").mkdir()
        (self.root / "a" / "one.bin").write_bytes(b"x" * 100)
        (self.root / "a" / "b").mkdir()
        (self.root / "a" / "b" / "two.bin").write_bytes(b"x" * 250)
        self.assertEqual(cleanup.dir_size(self.root / "a"), 350)

    def test_single_file(self):
        f = self.root / "f.bin"
        f.write_bytes(b"x" * 42)
        self.assertEqual(cleanup.dir_size(f), 42)


class TestTargetRemoval(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _target(self, name="t"):
        return cleanup.Target(name, name, self.root / "victim", "safe")

    def test_removes_directory_with_content(self):
        victim = self.root / "victim"
        (victim / "nested").mkdir(parents=True)
        (victim / "nested" / "blob.bin").write_bytes(b"x" * 2048)
        target = self._target()
        self.assertTrue(target.exists)
        self.assertEqual(target.size, 2048)

        ok, message = target.remove()
        self.assertTrue(ok, message)
        self.assertFalse(victim.exists())
        self.assertIsNone(cleanup.dir_size(victim))

    def test_missing_target_reports_not_exists(self):
        target = self._target()
        self.assertFalse(target.exists)
        self.assertIsNone(target.size)

    def test_removal_of_file_target(self):
        victim = self.root / "victim"
        victim.write_bytes(b"x" * 10)
        target = self._target()
        ok, _message = target.remove()
        self.assertTrue(ok)
        self.assertFalse(victim.exists())


class TestCleanSelection(unittest.TestCase):
    """确认默认档位选择正确：默认只清安全缓存，模型必须显式开启。

    注意：这些测试**不调用 build_targets()**，因为那会去统计真实缓存目录的
    大小（本机 pip 缓存清理前有 13 GB，量一遍要几十秒）。改成直接校验档位
    常量，测试因此是毫秒级的。
    """

    def test_default_clean_is_safe_only(self):
        keys = set(cleanup.SAFE_KEYS)
        self.assertNotIn("hf", keys)
        self.assertNotIn("torch", keys)
        self.assertNotIn("downloads", keys)
        self.assertNotIn("temp", keys)
        self.assertIn("pip", keys)
        self.assertIn("uv", keys)

    def test_model_and_project_keys_are_opt_in(self):
        self.assertEqual(tuple(cleanup.MODEL_GROUPS), ("models",))
        self.assertEqual(set(cleanup.CACHE_KEYS), {"hf", "torch"})
        self.assertEqual(set(cleanup.PROJECT_KEYS), {"downloads", "ffmpeg"})

    def test_all_keys_are_distinct(self):
        keys = (list(cleanup.SAFE_KEYS) + list(cleanup.MODEL_GROUPS)
                + list(cleanup.CACHE_KEYS) + list(cleanup.PROJECT_KEYS) + ["temp"])
        self.assertEqual(len(keys), len(set(keys)), "清理项 key 不能重复")

    def test_unknown_only_key_is_rejected(self):
        """未知项必须被拒绝，且不做任何删除。

        ⚠️ 必须把 stdout 引开：`cleanup.main()` 会**先打印整个扫描报告**再校验
        `--only`，直接调用会把「清理扫描 / 【Conda】/ 模型缓存发现」打进入
        测试输出 —— 这正是 `Install.bat` 第 4 步看起来像"自动跑了 cleanup"的
        原因（`tests/test_cleanup.py` 里唯一调用 main() 的用例）。
        """
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = cleanup.main(["--clean", "--only", "definitely-not-a-key", "--yes"])
        self.assertEqual(rc, 1)
        self.assertIn("未知清理项", buffer.getvalue())   # 确实走到了校验分支


class TestModelClassification(unittest.TestCase):
    """模型归属判定：默认 HF 缓存是全机共用的，不能整锅端。

    旧版项目从不设置 HF_HOME（已核对 da3a432 的代码，只有 HF_ENDPOINT），
    所以除项目内 _model_cache 之外，模型都落在 `%USERPROFILE%\\.cache` 下的
    默认缓存里，和别的项目混在一起。
    """

    def test_own_whisper_models(self):
        for name in ("models--Systran--faster-whisper-large-v3",
                     "models--Systran--faster-whisper-medium",
                     "models--Huan69--Belle-whisper-large-v3-zh-punct-fasterwhisper",
                     "models--openai--whisper-large-v3"):
            self.assertEqual(cleanup.classify_model(name), "own", name)

    def test_own_alignment_and_diarization(self):
        for name in ("wav2vec2_fairseq_base_ls960_asr_ls960.pth",
                     "models--pyannote--segmentation-3.0",
                     "models--pyannote--speaker-diarization-3.1",
                     "models--snakers4--silero-vad"):
            self.assertEqual(cleanup.classify_model(name), "own", name)

    def test_torch_hub_hash_filenames_are_own(self):
        """torch.hub 对微调模型用 hash 文件名（实测 955717e8-8726e21a.th）。"""
        self.assertEqual(cleanup.classify_model("955717e8-8726e21a.th"), "own")
        self.assertEqual(cleanup.classify_model("abcdef01-23456789.pth"), "own")

    def test_hash_lookalike_is_not_own(self):
        self.assertEqual(cleanup.classify_model("not-a-hash.th"), "unknown")
        self.assertEqual(cleanup.classify_model("1234.th"), "unknown")

    def test_foreign_models_are_protected(self):
        """别的项目的模型必须判为 foreign —— 删了会毁掉它们。"""
        for name in ("models--Systran--faster-distil-whisper-medium.en",
                     "models--sentence-transformers--all-MiniLM-L6-v2",
                     "models--wybxc--DocLayout-YOLO-DocStructBench-onnx",
                     "models--tencent--HY-MT1.5-1.8B-GPTQ-Int4"):
            self.assertEqual(cleanup.classify_model(name), "foreign", name)

    def test_distil_whisper_beats_whisper_rule(self):
        """distil-whisper 必须先按 foreign 判，不能被 whisper 规则抢走。"""
        self.assertEqual(
            cleanup.classify_model("models--Systran--faster-distil-whisper-large-v3"),
            "foreign")

    def test_unknown(self):
        self.assertEqual(cleanup.classify_model("some-random-thing"), "unknown")


class TestTargetSelection(unittest.TestCase):
    """--models 必须能选中逐模型挑出来的目标（key 形如 hf_own:xxx），
    但**不能**选中整个缓存目录（那里面混着别的项目的模型）。"""

    def _targets(self):
        return [
            cleanup.Target("pip", "pip", "C:/nope", "safe"),
            cleanup.Target("hf_own:a", "own a", "C:/nope", "confirm"),
            cleanup.Target("torch_ckpt:b.th", "own b", "C:/nope", "confirm"),
            cleanup.Target("hf", "整个 HF 缓存", "C:/nope", "confirm"),
            cleanup.Target("torch", "整个 torch 缓存", "C:/nope", "confirm"),
            cleanup.Target("downloads", "downloads", "C:/nope", "confirm"),
        ]

    def test_safe_only(self):
        chosen = cleanup.select_targets(self._targets(), set(cleanup.SAFE_KEYS))
        self.assertEqual([t.key for t in chosen], ["pip"])

    def test_models_group_selects_per_model_targets(self):
        keys = set(cleanup.SAFE_KEYS) | set(cleanup.MODEL_GROUPS)
        found = [t.key for t in cleanup.select_targets(self._targets(), keys)]
        self.assertIn("pip", found)
        self.assertIn("hf_own:a", found)
        self.assertIn("torch_ckpt:b.th", found)

    def test_models_group_excludes_whole_caches(self):
        """关键安全边界：--models 绝不能顺手把整个缓存目录删掉。"""
        keys = set(cleanup.SAFE_KEYS) | set(cleanup.MODEL_GROUPS)
        found = [t.key for t in cleanup.select_targets(self._targets(), keys)]
        self.assertNotIn("hf", found, "--models 不该选中整个 HF 缓存")
        self.assertNotIn("torch", found, "--models 不该选中整个 torch 缓存")
        self.assertNotIn("downloads", found)

    def test_whole_cache_requires_explicit_key(self):
        chosen = cleanup.select_targets(self._targets(), {"hf"})
        self.assertEqual([t.key for t in chosen], ["hf"])

    def test_exact_key_still_works(self):
        chosen = cleanup.select_targets(self._targets(), {"downloads"})
        self.assertEqual([t.key for t in chosen], ["downloads"])

    def test_empty_selection(self):
        self.assertEqual(cleanup.select_targets(self._targets(), {"nope"}), [])


class TestMainCleanActuallyDeletes(unittest.TestCase):
    """回归：`main() --clean` 必须真的把选中的目标交给 clean()。

    背景（2026-09-19 实测发现）：`main()` 先 `chosen = select_targets(...)`，
    却调用 `clean(chosen, set())`，而 `clean()` 内部**又**拿那个空集合筛了一遍
    —— 于是任何 `--clean` 都必然打印「没有匹配的清理目标」并返回 1：
    报告里明明列着 12 GB 可清理的模型，用户跑 `Cleanup.bat --clean --models`
    却什么都没删。

    原测试只单独测 `select_targets()`，从没测过 `select_targets → clean` 这条
    链路，所以这个 bug 一直躲过了测试。这里用 mock 锁住这条链路：
    断言 `main(['--clean', ...])` 最终传给 `clean()` 的目标**非空**。

    用不存在的临时目录做目标，`Target.remove()` 不会删到任何真实文件。

    ⚠️ 这些用例会走到真正的 `clean()`，于是会打印「将要删除 / 已删除 / 释放约
    32 B」—— 2026-09-19 实测：这些行混进了 `Install.bat` 的输出，让用户以为
    "安装完自动跑了清理脚本"。所以这里**必须把 stdout 收走**（`_quiet()`）。
    """

    @contextlib.contextmanager
    def _quiet(self):
        """把被测代码的 stdout 收进缓冲区，别喷到安装日志里。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            yield buf

    def _fake_target(self, key):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = pathlib.Path(tmp.name) / key
        p.mkdir()
        (p / "f.bin").write_bytes(b"x" * 32)
        return cleanup.Target(key, f"测试目标 {key}", p, "safe", "test")

    def _run_main(self, argv, targets):
        import unittest.mock as mock
        with mock.patch.object(cleanup, "build_targets", return_value=targets), \
             mock.patch.object(cleanup, "report", return_value=None), \
             mock.patch.object(cleanup, "warn_if_shared", return_value=None):
            return cleanup.main(argv)

    def test_clean_reaches_deletion(self):
        """--clean --only <存在的目标> 必须走到删除，而不是"没有匹配的目标"。"""
        t = self._fake_target("pip")
        with self._quiet():
            rc = self._run_main(["--clean", "--only", "pip", "--yes"], [t])
        self.assertEqual(rc, 0, "clean() 返回非 0，说明没走到删除")
        self.assertFalse(t.path.exists(),
                         "目标目录还在 —— main() 没把选中的目标交给 clean()")

    def test_clean_temp_preset_deletes_safe_targets(self):
        """不带 --only 的默认档位（--clean）也要真的删安全档。"""
        t = self._fake_target("pip")
        with self._quiet():
            rc = self._run_main(["--clean", "--yes"], [t])
        self.assertEqual(rc, 0)
        self.assertFalse(t.path.exists(), "默认档位没删安全缓存")

    def test_no_temp_excludes_temp(self):
        """--no-temp 即使和 --temp 同时给，也不该选中 temp。"""
        seen = {}

        def capture(chosen, assume_yes=False):
            seen["keys"] = [t.key for t in chosen]
            return 0

        tmp_t = self._fake_target("temp")
        pipe_t = self._fake_target("pip")
        import unittest.mock as mock
        with mock.patch.object(cleanup, "build_targets",
                               return_value=[tmp_t, pipe_t]), \
             mock.patch.object(cleanup, "report", return_value=None), \
             mock.patch.object(cleanup, "warn_if_shared", return_value=None), \
             mock.patch.object(cleanup, "clean", side_effect=capture):
            with self._quiet():
                cleanup.main(["--clean", "--temp", "--no-temp", "--yes"])
        self.assertIn("pip", seen.get("keys", []))
        self.assertNotIn("temp", seen.get("keys", []),
                         "--no-temp 没能排除系统临时目录")

    def test_no_project_excludes_project_artifacts(self):
        """--no-project 即使和 --all 同时给，也不该选中 _downloads/ffmpeg。"""
        seen = {}

        def capture(chosen, assume_yes=False):
            seen["keys"] = [t.key for t in chosen]
            return 0

        inner = tempfile.TemporaryDirectory()
        self.addCleanup(inner.cleanup)
        root = pathlib.Path(inner.name)
        dl = root / "_downloads"
        dl.mkdir()
        ff = root / "ffmpeg"
        ff.mkdir()
        d_t = cleanup.Target("downloads", "dl", dl, "confirm", "t")
        f_t = cleanup.Target("ffmpeg", "ff", ff, "confirm", "t")
        p_t = self._fake_target("pip")
        import unittest.mock as mock
        with mock.patch.object(cleanup, "build_targets",
                               return_value=[d_t, f_t, p_t]), \
             mock.patch.object(cleanup, "report", return_value=None), \
             mock.patch.object(cleanup, "warn_if_shared", return_value=None), \
             mock.patch.object(cleanup, "clean", side_effect=capture):
            with self._quiet():
                cleanup.main(["--clean", "--all", "--no-project", "--yes"])
        keys = seen.get("keys", [])
        self.assertIn("pip", keys)
        self.assertNotIn("downloads", keys, "--no-project 没能排除 _downloads")
        self.assertNotIn("ffmpeg", keys, "--no-project 没能排除 ffmpeg")


class TestNoSharedDataInModelsSelection(unittest.TestCase):
    """端到端安全断言：用真实的 build_targets()，
    `--clean --models` 选中的任何目标都不得"包含"别的项目的模型。"""

    def test_selected_targets_never_contain_foreign_models(self):
        targets = cleanup.build_targets()
        keys = set(cleanup.SAFE_KEYS) | set(cleanup.MODEL_GROUPS)
        chosen = cleanup.select_targets(targets, keys)
        for target in chosen:
            for name, path, _size, owner in cleanup.hf_models_in(target.path):
                self.assertNotEqual(
                    owner, "foreign",
                    f"{target.label} 里含别的项目的模型 {name}（{path}）")

    def test_models_never_selects_cache_roots(self):
        targets = cleanup.build_targets()
        keys = set(cleanup.SAFE_KEYS) | set(cleanup.MODEL_GROUPS)
        for target in cleanup.select_targets(targets, keys):
            self.assertNotIn(target.key, cleanup.CACHE_KEYS,
                             f"--models 不该选中 {target.key}（整个缓存）")


class TestHfModelDiscovery(unittest.TestCase):
    """`models--*` 目录的枚举与归属标注（在临时目录里做）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.hub = pathlib.Path(self._tmp.name) / "hub"
        (self.hub / "models--Systran--faster-whisper-large-v3").mkdir(parents=True)
        (self.hub / "models--Systran--faster-whisper-large-v3" / "blob").write_bytes(b"x" * 500)
        (self.hub / "models--sentence-transformers--all-MiniLM-L6-v2").mkdir()
        (self.hub / ".locks").mkdir()          # 不是模型，应被忽略

    def tearDown(self):
        self._tmp.cleanup()

    def test_lists_only_model_dirs(self):
        models = cleanup.hf_models_in(self.hub)
        names = [m[0] for m in models]
        self.assertIn("models--Systran--faster-whisper-large-v3", names)
        self.assertIn("models--sentence-transformers--all-MiniLM-L6-v2", names)
        self.assertNotIn(".locks", names)

    def test_reports_ownership_and_size(self):
        by_name = {m[0]: m for m in cleanup.hf_models_in(self.hub)}
        own = by_name["models--Systran--faster-whisper-large-v3"]
        foreign = by_name["models--sentence-transformers--all-MiniLM-L6-v2"]
        self.assertEqual(own[3], "own")
        self.assertEqual(own[2], 500)                     # 大小
        self.assertTrue(str(own[1]).endswith("faster-whisper-large-v3"))
        self.assertEqual(foreign[3], "foreign")

    def test_missing_hub_dir_is_empty(self):
        self.assertEqual(cleanup.hf_models_in(self._tmp.name + "/nope"), [])


class TestPathDiscovery(unittest.TestCase):
    """缓存目录发现要覆盖环境变量与默认位置，而不只是写死一个路径。

    注意：发现函数只返回**实际存在**的目录（不存在的没意义，大小也统计不出来），
    所以环境变量相关的用例必须在临时目录里真的建出 `hub/` 再断言。
    """

    def test_default_hf_location_is_found(self):
        hubs = [str(p).lower() for p in cleanup.hf_cache_hub_dirs()]
        self.assertTrue(any(".cache" in h and "huggingface" in h for h in hubs),
                        f"应包含默认位置 ~/.cache/huggingface/hub：{hubs}")

    def test_project_hub_is_found_when_present(self):
        """项目内 _model_cache/hub 存在时必须被发现（新栈的 HF_HOME 指向这里）。"""
        hub = cleanup.PROJECT / "_model_cache" / "hub"
        created = not hub.exists()
        if created:
            hub.mkdir(parents=True, exist_ok=True)
            self.addCleanup(lambda: shutil.rmtree(hub, ignore_errors=True))
        try:
            hubs = [str(p).lower() for p in cleanup.hf_cache_hub_dirs()]
            self.assertTrue(any("_model_cache" in h for h in hubs), hubs)
        finally:
            if created:
                shutil.rmtree(hub, ignore_errors=True)

    def test_hf_home_env_is_honoured(self):
        """用户可能把缓存挪到别的盘，环境变量必须被认。"""
        probe = self._tmpdir()
        (probe / "hub").mkdir()          # HF_HOME 下的 hub 子目录
        saved = os.environ.get("HF_HOME")
        os.environ["HF_HOME"] = str(probe)
        try:
            hubs = [str(p).lower() for p in cleanup.hf_cache_hub_dirs()]
            self.assertTrue(any("hf_home_probe" in h for h in hubs), hubs)
        finally:
            if saved is None:
                os.environ.pop("HF_HOME", None)
            else:
                os.environ["HF_HOME"] = saved

    def test_hf_hub_cache_env_is_honoured(self):
        probe = self._tmpdir()
        saved = os.environ.get("HF_HUB_CACHE")
        os.environ["HF_HUB_CACHE"] = str(probe)
        try:
            hubs = [str(p).lower() for p in cleanup.hf_cache_hub_dirs()]
            self.assertTrue(any("hf_home_probe" in h for h in hubs), hubs)
        finally:
            if saved is None:
                os.environ.pop("HF_HUB_CACHE", None)
            else:
                os.environ["HF_HUB_CACHE"] = saved

    def test_torch_home_env_is_honoured(self):
        probe = self._tmpdir("torch_home_probe")
        (probe / "hub" / "checkpoints").mkdir(parents=True)
        saved = os.environ.get("TORCH_HOME")
        os.environ["TORCH_HOME"] = str(probe)
        try:
            dirs = [str(p).lower() for p in cleanup.torch_hub_checkpoint_dirs()]
            self.assertTrue(any("torch_home_probe" in d for d in dirs), dirs)
        finally:
            if saved is None:
                os.environ.pop("TORCH_HOME", None)
            else:
                os.environ["TORCH_HOME"] = saved

    def test_torch_checkpoints_include_default(self):
        dirs = [str(p).lower() for p in cleanup.torch_hub_checkpoint_dirs()]
        self.assertTrue(any(".cache" in d and "torch" in d for d in dirs), dirs)

    def test_conda_env_paths_shape(self):
        self.assertIsInstance(cleanup.conda_env_paths(), list)

    def test_legacy_env_name_is_videolingo(self):
        """旧启动脚本硬编码的就是这个名字，不能改。"""
        self.assertEqual(cleanup.LEGACY_CONDA_ENV, "videolingo")

    def _tmpdir(self, prefix="hf_home_probe"):
        """建一个带前缀的临时目录（前缀决定断言用哪个关键词，所以必须参数化）。"""
        import uuid
        base = (pathlib.Path(tempfile.gettempdir())
                / f"{prefix}_{uuid.uuid4().hex[:8]}")
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        return base


class TestCondaDetection(unittest.TestCase):
    def test_returns_dict(self):
        envs = cleanup.conda_environments()
        self.assertIsInstance(envs, dict)

    def test_paths_are_never_conda_envs(self):
        """清理目标绝不落在"别人的 conda 环境"或"conda base"里。

        断言的是**路径**而不是标签文字：hf 目标的说明里就提到了 aisummary
        （提醒用户那里也有模型），那只是提示，不是删除目标。

        ⚠️ 关于 `conda_env` 这个目标的例外，三条不变量：
        1. 旧环境路径本身**允许**被列为目标 —— `clean()` 对它只打印
           `conda env remove -n videolingo -y`，从不直接删目录；
        2. 因此断言不能写成"目标路径不得位于 anaconda3 之下" —— 旧环境按定义
           就在 `anaconda3\\envs\\` 下，那样写会把工具自己的报告目标判成违规；
        3. 真正要守住的是：**别人的 conda 环境**（comfyui/danmu/…）与本项目的
           conda **base** 目录，不得成为任何目标的路径。

        这里只调用 build_targets() 一次。
        """
        envs = cleanup.conda_environments()
        others = {pathlib.Path(p) for name, p in envs.items()
                  if name not in ("base", cleanup.TARGET_ENV)}
        exe = cleanup.conda_exe()
        base = pathlib.Path(exe).parent.parent if exe is not None else None
        envs_dir = (base / "envs") if base is not None else None
        legacy = [pathlib.Path(p) for p in cleanup.conda_env_paths()]

        # 允许出现的目标路径：旧环境自己的目录，以及它所在的 envs/ 目录
        allowed = set()
        for p in legacy:
            allowed.add(p)
            allowed.add(p.parent)

        targets = cleanup.build_targets()
        for target in targets:
            self.assertIn(target.tier, ("safe", "confirm"))
            self.assertTrue(target.note)

            # 3) 别人的环境：任何形式的包含关系都不允许
            for guarded in others:
                self.assertFalse(
                    guarded == target.path or guarded in target.path.parents,
                    f"清理目标 {target.label} 落在别人的 conda 环境里：{target.path}",
                )
            # 3) conda base：只禁止"把 anaconda3\envs\ 或其中**别的**环境当目标"。
            #    ⚠️ 不能用 `base in target.path.parents`（要求不得位于 anaconda3 之下）：
            #    旧环境 videolingo 按定义就在 anaconda3\envs\ 下，那样写会把工具
            #    自己的报告目标判成违规 —— 这一条 2026-09-19 实测踩到过。
            if envs_dir is not None and target.path not in allowed:
                self.assertFalse(
                    envs_dir == target.path or envs_dir in target.path.parents,
                    f"清理目标 {target.label} 落在 conda 的 envs 目录里：{target.path}",
                )
                self.assertFalse(
                    base == target.path,
                    f"清理目标 {target.label} 就是 conda base 本身：{target.path}",
                )

        # 顺带一条"守卫本身没坏"的自检：**只在旧环境真的存在时才断言**。
        # 旧环境是可以被用户删掉的（本机 2026-09-19 就删了），写成无条件断言
        # 会在环境已清理的机器上误报 —— 而"没有旧环境"恰好是这套工具的目标状态。
        if envs_dir is not None and (envs_dir / cleanup.TARGET_ENV).is_dir():
            self.assertTrue(
                legacy, f"{envs_dir / cleanup.TARGET_ENV} 存在，应能探测到它")
        else:
            print(f"  [skip] 本机没有旧 conda 环境 {cleanup.TARGET_ENV}，"
                  "跳过探测自检")

    def test_conda_exe_none_is_handled(self):
        """没有 conda 时报空字典而不是抛异常。"""
        self.assertIsInstance(cleanup.conda_environments(), dict)


class TestTestsAreQuiet(unittest.TestCase):
    """单元测试不能把 cleanup 的扫描报告喷到调用方的控制台上。

    背景：`Install.bat` 装完会跑 `python -m unittest discover -s tests`。有一次
    `tests/test_cleanup.py` 里的用例直接调了 `cleanup.main([...])`，而 main()
    会**先打印整个扫描报告**，于是 `Install.bat` 的最后看起来像"自动跑了
    cleanup 脚本"。这个用例就是防止再犯。

    ⚠️ **防重入**：这两个用例要跑子进程，而子进程跑的正是本模块/本套件，
    子进程里的同一个用例又会再 fork 一层 —— 没有护栏时
    `python -m unittest discover -s tests` **永远不会结束**（每层都在等子进程）。
    因此子进程会带上 `NESTED_ENV_VAR=1`；带该变量运行时直接跳过，因为
    "是否泄漏扫描报告"已经由最外层那一次运行检查过了。
    """

    #: 扫描报告里必定出现的标题片段
    REPORT_MARKERS = ("清理扫描", "【Conda】", "模型缓存发现", "缓存与项目目录")

    #: 子进程若带此变量，说明自己就是被嵌套调用起来的，直接跳过
    NESTED_ENV_VAR = "VIDEOLINGO_CLEANUP_QUIET_NESTED"

    def _spawn(self, argv):
        import os
        import subprocess
        import sys as _sys
        env = dict(os.environ)
        env[self.NESTED_ENV_VAR] = "1"
        # 继承 PYTHONPATH：这两条用例断言子进程"整套测试退出码为 0"，而子进程
        # 用的是同一个解释器。若调用方是靠 PYTHONPATH 借来项目依赖（本项目当前
        # 没有 .venv / conda 环境时很常见），不继承就会让子进程缺依赖而误报。
        return subprocess.run(
            [_sys.executable, "-m", "unittest", *argv],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env,
        )

    def setUp(self):
        import os
        if os.environ.get(self.NESTED_ENV_VAR) == "1":
            self.skipTest("嵌套运行：静默性已由最外层那次运行检查")

    def test_cleanup_tests_print_no_report(self):
        proc = self._spawn(["tests.test_cleanup", "-v"])
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        leaked = [m for m in self.REPORT_MARKERS if m in (proc.stdout or "")]
        self.assertEqual(leaked, [],
                         f"测试输出里泄漏了扫描报告片段：{leaked}")

    def test_full_suite_prints_no_report(self):
        """整套测试（就是 Install.bat 跑的那条命令）同样不能泄漏报告。"""
        proc = self._spawn(["discover", "-s", "tests"])
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        leaked = [m for m in self.REPORT_MARKERS if m in (proc.stdout or "")]
        self.assertEqual(leaked, [],
                         f"整套测试输出泄漏了扫描报告片段：{leaked}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
