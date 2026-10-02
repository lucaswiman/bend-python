"""Real buffer exports and NumPy/PyTorch storage; no array-sized conversion."""

import array
from concurrent.futures import ThreadPoolExecutor
import ctypes
import importlib.util
import mmap
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_bindings import bend_example

NUMPY = importlib.util.find_spec("numpy") is not None
TORCH = NUMPY and importlib.util.find_spec("torch") is not None

FIXTURE = """import Base
import ./bend/python.bend as Python
import ./bend/tensor.bend as Tensor

def read_out(result: Pair(Python.F32View, F32)) -> IO(Python.Object):
  (view, value) = result
  do IO<Python.Object>:
    Python.f32_release(view)
    Python.from_f32(value)

def read_index(~index: Nat -> Nat, result: Pair(Python.F32View, Nat)) -> IO(Python.Object):
  (view, count) = result
  do IO<Python.Object>:
    result : Pair(Python.F32View, F32) <- Python.f32_read(view, index(count))
    read_out(result)

def read_at(~index: Nat -> Nat, request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View <- Python.borrow_f32(value, False{})
    sized : Pair(Python.F32View, Nat) <- Python.f32_size(view)
    read_index(~index, sized)

def dimensions(values: List<Nat>) -> IO(List<Python.Object>):
  match values:
    case []:
      IO.pure(List<Python.Object>, [])
    case head <> tail:
      do IO<List<Python.Object>>:
        value : Python.Object <- Python.from_nat(head)
        rest : List<Python.Object> <- dimensions(tail)
        return value <> rest

def shape_out(result: Pair(Python.F32View, List<Nat>)) -> IO(Python.Object):
  (view, shape) = result
  do IO<Python.Object>:
    Python.f32_release(view)
    values : List<Python.Object> <- dimensions(shape)
    Python.tuple(values)

def shape(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View <- Python.borrow_f32(value, False{})
    shaped : Pair(Python.F32View, List<Nat>) <- Python.f32_shape(view)
    shape_out(shaped)

def bad_view(view: Python.F32View) -> IO(Python.Object):
  match view:
    case Python.F32View{id}:
      do IO<Python.Object>:
        result : Pair(Python.F32View, F32) <- Python.f32_read(Python.F32View{U32.inc(id)}, 0n)
        read_out(result)

def released(view: Python.F32View) -> IO(Python.Object):
  match view:
    case Python.F32View{+id}:
      do IO<Python.Object>:
        Python.f32_release(Python.F32View{id})
        result : Pair(Python.F32View, F32) <- Python.f32_read(Python.F32View{id}, 0n)
        read_out(result)

def readonly_write(view: Python.F32View) -> IO(Python.Object):
  do IO<Python.Object>:
    updated : Python.F32View <- Python.f32_write(view, 0n, 42.0)
    Python.f32_release(updated)
    Python.none()

def abort(view: Python.F32View) -> IO(Python.Object):
  Python.type_error(Python.Object, "after borrowing")

def with_view(~f: Python.F32View -> IO(Python.Object), request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View <- Python.borrow_f32(value, False{})
    f(view)

def reentrant_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, callback) = values
  do IO<Python.Object>:
    view : Python.F32View <- Python.borrow_f32(value, False{})
    ignored : Python.Object <- Python.invoke(callback, [])
    result : Pair(Python.F32View, F32) <- Python.f32_read(view, 0n)
    read_out(result)

def reentrant(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    reentrant_values(values)

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export("last", read_at(~(n => Nat.sub(n, 1n))), True{})
    Python.export("past_end", read_at(~(n => n)), True{})
    Python.export("shape", shape, True{})
    Python.export("bad_view", with_view(~bad_view), True{})
    Python.export("released", with_view(~released), True{})
    Python.export("readonly_write", with_view(~readonly_write), True{})
    Python.export("abort", with_view(~abort), True{})
    Python.export("reentrant", reentrant, True{})
    Tensor.export_numpy_f32_inplace(~(x => (x / 2.0 : F32)), "half_inplace", True{})
"""


class BufferViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bend_python import vendor

        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        path = Path(cls.directory.name)
        vendor(path / "bend")
        # A real SIGINT after a native store exercises the production batch check
        # deterministically. Only this temporary fixture contains the signal hook.
        shim = path / "bend/python.c"
        source = shim.read_text()
        hook = """
#include <signal.h>
static bool bp_test_interrupt;
__attribute__((visibility("default"))) void bp_test_interrupt_after_store(void) {
  bp_test_interrupt = true;
}
"""
        marker = "static bool bp_memory_effect(Env e, BpCall* call) {"
        if source.count(marker) != 1:
            raise AssertionError("missing native memory effect boundary")
        source = source.replace(marker, hook + "\n" + marker)
        store = "      memcpy(address, &bits, sizeof(bits));"
        if source.count(store) != 1:
            raise AssertionError("expected one shared native store")
        shim.write_text(
            source.replace(
                store,
                store
                + "\n      if (bp_test_interrupt) { bp_test_interrupt = false; raise(SIGINT); }",
            )
        )
        (path / "module.bend").write_text(FIXTURE)
        (path / "setup.py").write_text(
            "from setuptools import setup\n"
            "from bend_python import BendBuildExt, BendExtension\n"
            "setup(ext_modules=[BendExtension('tensor_fixture', 'module.bend')], "
            "cmdclass={'build_ext': BendBuildExt})\n"
        )
        result = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        extension = next(path.glob("tensor_fixture*.so"))
        spec = importlib.util.spec_from_file_location("tensor_fixture", extension)
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)
        cls.native = ctypes.PyDLL(str(extension))
        cls.native.bp_test_interrupt_after_store.argtypes = []
        cls.native.bp_test_interrupt_after_store.restype = None

    def test_standard_exporters_and_native_errors_release_storage(self):
        for value in (array.array("f", [2, 4, 8]), (ctypes.c_float * 3)(2, 4, 8)):
            with self.subTest(exporter=type(value)):
                self.assertIs(bend_example.numpy_half_inplace(value), value)
                self.assertEqual(list(value), [1, 2, 4])
                self.assertEqual(self.fixture.last(value), 4)
                self.assertEqual(self.fixture.shape(value), (3,))
        value = array.array("f", [2, 4, 8])
        for name, exception in (
            ("past_end", IndexError),
            ("bad_view", ValueError),
            ("released", ValueError),
            ("readonly_write", BufferError),
            ("abort", TypeError),
        ):
            with self.subTest(operation=name), self.assertRaises(exception):
                getattr(self.fixture, name)(value)
            value.append(16)  # No leaked buffer export may prevent resizing.
            value.pop()
            self.assertEqual(list(value), [2, 4, 8])
        self.assertEqual(bend_example.square(7), 49)

    def test_reentrant_borrow_pins_owner_and_calls_remain_independent(self):
        value = array.array("f", [7])
        other = array.array("f", [10])

        def callback():
            with self.assertRaises(BufferError):
                value.append(8)
            bend_example.numpy_half_inplace(other)

        self.assertEqual(self.fixture.reentrant(value, callback), 7)
        self.assertEqual(list(other), [5])
        value.append(8)
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = [array.array("f", [i * 2] * 1000) for i in range(8)]
            results = list(pool.map(bend_example.numpy_half_inplace, values))
        for i, (original, result) in enumerate(zip(values, results, strict=True)):
            self.assertIs(result, original)
            self.assertEqual(list(result), [i] * 1000)

    def test_large_view_reads_without_materializing_elements(self):
        # Anonymous mapping reserves virtual storage; only the last page is touched.
        count = (1 << 32) + 1
        with mmap.mmap(-1, count * 4) as storage:
            value = memoryview(storage).cast("f")
            try:
                value[-1] = 19
                self.assertEqual(self.fixture.last(value), 19)
                self.assertEqual(self.fixture.shape(value), (count,))
            finally:
                value.release()

    @unittest.skipUnless(NUMPY, "NumPy integration dependencies are optional")
    def test_numpy_layouts_aliases_and_bit_preservation(self):
        import numpy as np

        for shape in ((), (0, 3), (2, 3, 4)):
            base = np.arange(np.prod(shape, dtype=int), dtype=np.float32).reshape(shape)
            for value in (base, base.T, base[..., ::2] if shape else base):
                with self.subTest(shape=value.shape, strides=value.strides):
                    expected = value.copy() / 2
                    pointer = value.__array_interface__["data"][0]
                    self.assertIs(bend_example.numpy_half_inplace(value), value)
                    self.assertEqual(value.__array_interface__["data"][0], pointer)
                    np.testing.assert_array_equal(value, expected)
                    self.assertEqual(self.fixture.shape(value), value.shape)
        base = np.arange(12, dtype=np.float32)
        bend_example.numpy_half_inplace(base[::-2])
        np.testing.assert_array_equal(base, [0, 0.5, 2, 1.5, 4, 2.5, 6, 3.5, 8, 4.5, 10, 5.5])
        bits = np.array([0, 0x80000000, 0x7FC01234, 0x7F801234, 0x7F800000], dtype=np.uint32)
        expected = bits.copy()
        bend_example.numpy_identity_inplace(bits.view(np.float32))
        np.testing.assert_array_equal(bits, expected)

    @unittest.skipUnless(NUMPY, "NumPy integration dependencies are optional")
    def test_numpy_rejects_unsupported_storage_before_writing(self):
        import numpy as np

        readonly = np.arange(4, dtype=np.float32)
        readonly.flags.writeable = False
        overlap = np.lib.stride_tricks.as_strided(
            np.arange(3, dtype=np.float32), shape=(2, 2), strides=(4, 4), writeable=True
        )
        for value, exception in (
            (readonly, ValueError),
            (overlap, BufferError),
            (np.ones(3, dtype="float64"), BufferError),
            (np.ones(3, dtype=">f4"), BufferError),
        ):
            before = value.copy()
            with self.subTest(dtype=value.dtype, strides=value.strides):
                with self.assertRaises(exception):
                    bend_example.numpy_half_inplace(value)
                np.testing.assert_array_equal(value, before)
        # Shared cells are still readable through a read-only capability.
        self.assertEqual(self.fixture.last(overlap), 2)

    @unittest.skipUnless(TORCH, "PyTorch and NumPy integration dependencies are optional")
    def test_torch_layouts_storage_and_backward_version_tracking(self):
        import torch

        for value in (
            torch.tensor(7, dtype=torch.float32),
            torch.empty((0, 3), dtype=torch.float32),
            torch.arange(12, dtype=torch.float32).reshape(3, 4).T,
            torch.arange(12, dtype=torch.float32)[::2],
        ):
            expected = value.clone() / 2
            pointer = value.data_ptr()
            version = value._version
            self.assertIs(bend_example.torch_half_inplace(value), value)
            self.assertEqual(value.data_ptr(), pointer)
            self.assertEqual(value._version, version + 1)
            torch.testing.assert_close(value, expected, rtol=0, atol=0)
        weight = torch.tensor([3.0], requires_grad=True)
        constant = torch.tensor([4.0])
        output = (weight * constant).sum()
        bend_example.torch_half_inplace(constant)
        with self.assertRaisesRegex(RuntimeError, "modified by an inplace operation"):
            output.backward()
        value = torch.tensor([4.0], requires_grad=True)
        output = value.square().sum()
        bend_example.torch_half_inplace(value.detach())
        with self.assertRaisesRegex(RuntimeError, "modified by an inplace operation"):
            output.backward()

    @unittest.skipUnless(TORCH, "PyTorch and NumPy integration dependencies are optional")
    def test_torch_rejects_gradients_devices_and_dtypes_before_writing(self):
        import torch

        value = torch.tensor([4.0], requires_grad=True)
        for context in (torch.enable_grad(), torch.no_grad()):
            with context, self.assertRaises(TypeError):
                bend_example.torch_half_inplace(value)
        self.assertEqual(value.item(), 4)
        with torch.autograd.forward_ad.dual_level():
            dual = torch.autograd.forward_ad.make_dual(torch.tensor([4.0]), torch.tensor([1.0]))
            with self.assertRaises(TypeError):
                bend_example.torch_half_inplace(dual)
            self.assertEqual(dual.item(), 4)
            self.assertEqual(torch.autograd.forward_ad.unpack_dual(dual).tangent.item(), 1)
        for value in (torch.ones(3, dtype=torch.float64), torch.ones(3, device="meta")):
            with self.assertRaises((BufferError, TypeError)):
                bend_example.torch_half_inplace(value)

        class CopiesStorage(torch.Tensor):
            def numpy(self):
                return super().numpy().copy()

        subclass = torch.arange(3, dtype=torch.float32).as_subclass(CopiesStorage)
        before = subclass.clone()
        with self.assertRaisesRegex(TypeError, "subclasses are unsupported"):
            bend_example.torch_half_inplace(subclass)
        torch.testing.assert_close(subclass, before)
        with torch.inference_mode():
            value = torch.tensor([8.0])
            self.assertIs(bend_example.torch_half_inplace(value), value)
            self.assertEqual(value.item(), 4)

    def test_cancellation_releases_borrow_and_keeps_partial_updates(self):
        value = array.array("f", [8]) * 8192
        self.native.bp_test_interrupt_after_store()
        with self.assertRaises(KeyboardInterrupt):
            self.fixture.half_inplace(value)
        self.assertEqual(value[0], 4)
        self.assertEqual(value[-1], 8)
        value.append(16)
        self.assertEqual(self.fixture.last(value), 16)
