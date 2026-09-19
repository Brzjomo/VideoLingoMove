"""torch.load weights_only 垫片的逻辑验证（不需要真的装 torch）。

之所以能这么测：垫片本身是纯粹的 functools.wraps 包装，不依赖 torch 的任何
行为。这个测试锁住三件事：
  1. 打上之后，不传 weights_only 时默认注入 False；
  2. 调用方显式传 True 时**不被覆盖**（安全路径不受影响）；
  3. 重复打桩不会叠壳（幂等），并且保留原函数的元信息。
"""
import functools
import unittest


def make_patch(torch_module):
    """与 core/step2_whisperX.py 中 _patch_torch_load_weights_only() 同构。"""

    def _patch():
        original = torch_module.load
        if getattr(original, "_videolingo_weights_only_shim", False):
            return original

        @functools.wraps(original)
        def patched(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return original(*args, **kwargs)

        patched._videolingo_weights_only_shim = True
        torch_module.load = patched
        return original

    return _patch


class FakeTorch:
    """记录每次调用收到的 weights_only 取值。"""

    def __init__(self):
        self.calls = []

    def load(self, path, map_location=None, weights_only=None, **kwargs):
        """签名模仿 torch.load：weights_only 默认 None 表示"由 torch 决定"。"""
        self.calls.append(weights_only)
        return f"loaded:{path}"


class TestWeightsOnlyShim(unittest.TestCase):
    def setUp(self):
        self.torch = FakeTorch()
        self.patch = make_patch(self.torch)
        self.original = self.torch.load
        # 取底层函数对象：bound method 每次属性访问都会新建，身份比较永远不等。
        self.original_func = self.original.__func__

    def test_defaults_to_false(self):
        self.patch()
        self.torch.load("model.bin")
        self.assertEqual(self.torch.calls, [False])

    def test_explicit_true_is_respected(self):
        self.patch()
        self.torch.load("model.bin", weights_only=True)
        self.assertEqual(self.torch.calls, [True])

    def test_explicit_false_is_respected(self):
        self.patch()
        self.torch.load("model.bin", weights_only=False)
        self.assertEqual(self.torch.calls, [False])

    def test_positional_args_pass_through(self):
        self.patch()
        result = self.torch.load("model.bin", None)
        self.assertEqual(result, "loaded:model.bin")
        self.assertEqual(self.torch.calls, [False])

    def test_extra_kwargs_pass_through(self):
        self.patch()
        self.torch.load("model.bin", map_location="cpu")
        self.assertEqual(self.torch.calls, [False])

    def test_idempotent(self):
        self.patch()
        once = self.torch.load
        self.patch()
        self.patch()
        self.assertIs(self.torch.load, once, "重复打桩不该再包一层")
        # 打桩挂在实例属性上，拿到的是普通函数；它的 __wrapped__ 指向被抓取的
        # 那个 bound method 对象。bound method 每次属性访问都会新建，所以只能
        # 逐项比较它绑定的函数与实例，不能直接比较对象身份。
        wrapped = self.torch.load.__wrapped__
        self.assertIs(wrapped.__func__, self.original_func)
        self.assertIs(wrapped.__self__, self.torch)

    def test_marker_and_metadata(self):
        self.patch()
        self.assertTrue(getattr(self.torch.load, "_videolingo_weights_only_shim", False))
        self.assertEqual(self.torch.load.__name__, "load")


if __name__ == "__main__":
    unittest.main(verbosity=2)
