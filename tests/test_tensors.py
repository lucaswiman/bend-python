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

def read_out(result: Pair(Python.F32View<False{}>, F32)) -> IO(Python.Object):
  (view, value) = result
  do IO<Python.Object>:
    Python.f32_release(False{}, view)
    Python.from_f32(value)

def read_index(~index: Nat -> Nat, result: Pair(Python.F32View<False{}>, Nat)) -> IO(Python.Object):
  (view, count) = result
  do IO<Python.Object>:
    result : Pair(Python.F32View<False{}>, F32) <- Python.f32_read(False{}, view, index(count))
    read_out(result)

def read_at(~index: Nat -> Nat, request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View<False{}> <- Python.borrow_f32(value, False{})
    sized : Pair(Python.F32View<False{}>, Nat) <- Python.f32_size(False{}, view)
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

def shape_out(result: Pair(Python.F32View<False{}>, List<Nat>)) -> IO(Python.Object):
  (view, shape) = result
  do IO<Python.Object>:
    Python.f32_release(False{}, view)
    values : List<Python.Object> <- dimensions(shape)
    Python.tuple(values)

def shape(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View<False{}> <- Python.borrow_f32(value, False{})
    shaped : Pair(Python.F32View<False{}>, List<Nat>) <- Python.f32_shape(False{}, view)
    shape_out(shaped)

def bad_view(view: Python.F32View<False{}>) -> IO(Python.Object):
  match view:
    case Python.F32View{id}:
      forged = {Python.F32View{U32.inc(id)} : Python.F32View<False{}>}
      do IO<Python.Object>:
        result : Pair(Python.F32View<False{}>, F32) <- Python.f32_read(False{}, forged, 0n)
        read_out(result)

def released(view: Python.F32View<False{}>) -> IO(Python.Object):
  match view:
    case Python.F32View{+id}:
      do IO<Python.Object>:
        Python.f32_release(False{}, Python.F32View{id})
        result : Pair(Python.F32View<False{}>, F32) <-
          Python.f32_read(False{}, Python.F32View{id}, 0n)
        read_out(result)

def readonly_write(view: Python.F32View<False{}>) -> IO(Python.Object):
  match view:
    case Python.F32View{id}:
      do IO<Python.Object>:
        updated : Python.F32View<True{}> <-
          Python.f32_write({Python.F32View{id} : Python.F32View<True{}>}, 0n, 42.0)
        Python.f32_release(True{}, updated)
        Python.none()

def forged_map(offset: U32, view: Python.F32View<False{}>) -> IO(Python.Object):
  match view:
    case Python.F32View{id}:
      writable = {Python.F32View{U32.add(id, offset)} : Python.F32View<True{}>}
      do IO<Python.Object>:
        updated : Python.F32View<True{}> <-
          Python.f32_map(writable, count => Tensor.map_steps(~(x => (x / 2.0 : F32)), count))
        Python.f32_release(True{}, updated)
        Python.none()

def captured_steps(count: Nat, +offset: F32) -> Python.F32MapSteps(count):
  match count:
    case 0n:
      Unit{}
    case 1n+pred:
      value => ((value + offset : F32), captured_steps(pred, (offset + 1.0 : F32)))

def counted_steps(+count: Nat, seed: F32) -> Python.F32MapSteps(count):
  captured_steps(count, (F32.from_nat(count) + seed : F32))

def captured_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, scalar) = values
  +original = value
  do IO<Python.Object>:
    seed : F32 <- Python.to_f32(scalar)
    view : Python.F32View<True{}> <- Python.borrow_f32(original, True{})
    updated : Python.F32View<True{}> <-
      Python.f32_map(view, count => counted_steps(count, seed))
    Python.f32_release(True{}, updated)
    return original

def captured_closed_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, scalar) = values
  do IO<Python.Object>:
    offset : F32 <- Python.to_f32(scalar)
    view : Python.F32View<True{}> <- Python.borrow_f32(value, True{})
    updated : Python.F32View<True{}> <-
      Python.f32_map_closed(view, x => (x + offset : F32))
    Python.f32_release(True{}, updated)
    Python.none()

def captured_closed(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    captured_closed_values(values)

def forged_closed(offset: U32, view: Python.F32View<False{}>) -> IO(Python.Object):
  match view:
    case Python.F32View{id}:
      writable = {Python.F32View{U32.add(id, offset)} : Python.F32View<True{}>}
      do IO<Python.Object>:
        updated : Python.F32View<True{}> <-
          Python.f32_map_closed(writable, x => (x / 2.0 : F32))
        Python.f32_release(True{}, updated)
        Python.none()

def captured_map(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    captured_values(values)

# Unsafe equality exists only in this fixture to exercise the native ABI checks.
@unsafe def lie(-left: Nat, -right: Nat) -> {left == right : Nat}:
  lie(left, right)

def short_steps(count: Nat) -> Python.F32MapSteps(count):
  %lie(0n, count) : Python.F32MapSteps(_)
  Unit{}

def long_steps(count: Nat) -> Python.F32MapSteps(count):
  %lie(1n, count) : Python.F32MapSteps(_)
  value => (value, Unit{})

def two_steps(count: Nat) -> Python.F32MapSteps(count):
  %lie(2n, count) : Python.F32MapSteps(_)
  first => ((first + 10.0 : F32), second => ((second + 20.0 : F32), Unit{}))

def update(~operation: Python.F32View<True{}> -> IO(Python.F32View<True{}>),
  request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    +value : Python.Object <- Python.unary(request)
    view : Python.F32View<True{}> <- Python.borrow_f32(value, True{})
    updated : Python.F32View<True{}> <- operation(view)
    Python.f32_release(True{}, updated)
    return value

def abort(view: Python.F32View<False{}>) -> IO(Python.Object):
  Python.type_error(Python.Object, "after borrowing")

def with_view(~f: Python.F32View<False{}> -> IO(Python.Object),
  request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    view : Python.F32View<False{}> <- Python.borrow_f32(value, False{})
    f(view)

def reentrant_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, callback) = values
  do IO<Python.Object>:
    view : Python.F32View<False{}> <- Python.borrow_f32(value, False{})
    ignored : Python.Object <- Python.invoke(callback, [])
    result : Pair(Python.F32View<False{}>, F32) <- Python.f32_read(False{}, view, 0n)
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
    Python.export("readonly_map", with_view(~(view => forged_map(0, view))), True{})
    Python.export("bad_map", with_view(~(view => forged_map(1, view))), True{})
    Python.export("write_first", update(~(view => Python.f32_write(view, 0n, 42.0))), True{})
    Python.export("captured_map", captured_map, True{})
    Python.export("captured_closed", captured_closed, True{})
    Python.export("readonly_closed", with_view(~(view => forged_closed(0, view))), True{})
    Python.export("bad_closed", with_view(~(view => forged_closed(1, view))), True{})
    Python.export("short_map",
      update(~(view => Python.f32_map(view, count => short_steps(count)))), True{})
    Python.export("long_map",
      update(~(view => Python.f32_map(view, count => long_steps(count)))), True{})
    Python.export("two_map",
      update(~(view => Python.f32_map(view, count => two_steps(count)))), True{})
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
        path = cls.path = Path(cls.directory.name)
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
        marker = "static void bp_store("
        if source.count(marker) != 1:
            raise AssertionError("missing shared native store boundary")
        source = source.replace(marker, hook + "\n" + marker)
        store = "  memcpy(address, &bits, sizeof(bits));"
        if source.count(store) != 1:
            raise AssertionError("expected one shared native store")
        shim.write_text(
            source.replace(
                store,
                store + "\n  if (bp_test_interrupt) { bp_test_interrupt = false; raise(SIGINT); }",
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
            ("abort", TypeError),
        ):
            with self.subTest(operation=name), self.assertRaises(exception):
                getattr(self.fixture, name)(value)
            value.append(16)  # No leaked buffer export may prevent resizing.
            value.pop()
            self.assertEqual(list(value), [2, 4, 8])
        self.assertEqual(bend_example.square(7), 49)

    def test_build_rejects_readonly_writes_and_inexact_map_plans(self):
        from bend_python.build import _compiler

        prelude = (
            "import Base\nimport ./bend/python.bend as Python\n"
            "import ./bend/tensor.bend as Tensor\n"
        )
        contracts = {
            "valid": (
                "def valid(count: Nat) -> Python.F32MapSteps(count):\n"
                "  Tensor.map_steps(~(x => x), count)\n"
            ),
            "readonly_write": (
                "def bad(view: Python.F32View<False{}>) -> IO(Python.F32View<True{}>):\n"
                "  Python.f32_write(view, 0n, 1.0)\n"
            ),
            "readonly_modify": (
                "def bad(view: Python.F32View<False{}>) -> IO(Python.F32View<True{}>):\n"
                "  Python.f32_modify(view, 0n, x => x)\n"
            ),
            "readonly_map": (
                "def bad(view: Python.F32View<False{}>) -> IO(Python.F32View<True{}>):\n"
                "  Python.f32_map(view, count => Tensor.map_steps(~(x => x), count))\n"
            ),
            "short_plan": (
                "def bad(count: Nat) -> Python.F32MapSteps(count):\n"
                "  match count:\n    case 0n:\n      Unit{}\n    case 1n+pred:\n      Unit{}\n"
            ),
            "long_plan": (
                "def bad(count: Nat) -> Python.F32MapSteps(count):\n"
                "  match count:\n    case 0n:\n      value => (value, Unit{})\n"
                "    case 1n+pred:\n      value => (value, bad(pred))\n"
            ),
        }
        for name, contract in contracts.items():
            with self.subTest(contract=name):
                path = self.path / "contract.bend"
                path.write_text(prelude + contract)
                result = subprocess.run(
                    [_compiler(), str(path), "--check-only"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = result.stdout + result.stderr
                if name == "valid":
                    self.assertEqual(result.returncode, 0, output)
                else:
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn("expected :", output)
                    self.assertIn("observed :", output)

    def test_native_permissions_and_checked_indexed_write(self):
        for operation, exception, counts in (
            ("readonly_write", BufferError, (3,)),
            ("readonly_map", BufferError, (0, 3)),
            ("bad_map", ValueError, (0, 3)),
            ("readonly_closed", BufferError, (0, 3)),
            ("bad_closed", ValueError, (0, 3)),
        ):
            for count in counts:
                value = array.array("f", [2]) * count
                with self.subTest(count=count, operation=operation):
                    with self.assertRaises(exception):
                        getattr(self.fixture, operation)(value)
                    self.assertEqual(list(value), [2] * count)
                    value.append(9)
                    value.pop()
        value = array.array("f", [2, 4, 8])
        self.assertIs(self.fixture.write_first(value), value)
        self.assertEqual(list(value), [42, 4, 8])
        value.append(16)
        with self.assertRaises(IndexError):
            self.fixture.write_first(array.array("f"))

    def test_native_map_rejects_unsafe_plans_before_current_cell_write(self):
        for operation, elements, expected in (
            ("short_map", [2, 4, 8], [2, 4, 8]),
            ("long_map", [], []),
            ("two_map", [], []),
            ("two_map", [2], [2]),
            ("two_map", [2, 4, 8], [12, 4, 8]),
        ):
            with self.subTest(operation=operation, count=len(elements)):
                value = array.array("f", elements)
                with self.assertRaisesRegex(RuntimeError, "invalid.*map"):
                    getattr(self.fixture, operation)(value)
                self.assertEqual(list(value), expected)
                value.append(16)
                self.assertEqual(self.fixture.last(value), 16)
        value = array.array("f", [2, 4])
        self.assertIs(self.fixture.two_map(value), value)
        self.assertEqual(list(value), [12, 24])

    def test_dependent_map_uses_real_count_and_consumes_captured_steps_in_order(self):
        for count in (0, 1, 4095, 4096, 4097):
            with self.subTest(count=count):
                value = array.array("f", range(count))
                self.assertIs(self.fixture.captured_map(value, 7.5), value)
                self.assertEqual(list(value), [count + 7.5 + 2 * i for i in range(count)])
                value.append(16)

    def test_closed_map_rejects_captured_callbacks_before_writes_and_releases(self):
        for count in (0, 3):
            value = array.array("f", [2]) * count
            with self.assertRaisesRegex(RuntimeError, "captureless"):
                self.fixture.captured_closed(value, 7.5)
            self.assertEqual(list(value), [2] * count)
            value.append(8)
            self.assertIs(self.fixture.half_inplace(value), value)
            self.assertEqual(value[-1], 4)

    @unittest.skipUnless(NUMPY, "NumPy integration dependencies are optional")
    def test_cursor_maps_match_independent_row_major_reference(self):
        import numpy as np

        # ndindex supplies an independent logical order, including carry resets
        # and mixed negative/positive strides. Compare entire backing storage to
        # catch writes in gaps as well as duplicate or omitted cells.
        layouts = (
            lambda base: base,
            lambda base: base.T,
            lambda base: base[::-1, ::-2, ::-1],
            lambda base: base.T[::-2, ::-1, ::2],
            lambda base: base[:0, ::-1],
            lambda base: base[1:2, 2:3, 1:2].reshape(()),
            lambda base: base.transpose(1, 0, 2)[::-1].reshape(1, 7, 1, 5, 1, -1, 1, 1),
        )
        for layout, captured, depth in (
            (layout, captured, depth)
            for layout in layouts
            for captured in (False, True)
            for depth in (3, 120)
        ):
            base = np.arange(35 * depth, dtype=np.float32).reshape(5, 7, depth)
            value = layout(base)
            expected_base = base.copy()
            expected_view = layout(expected_base)
            self.assertTrue(value.size == 0 or np.shares_memory(base, value))
            for logical, index in enumerate(np.ndindex(value.shape)):
                old = expected_view[index]
                expected_view[index] = (
                    old + np.float32(value.size + 7.5 + logical)
                    if captured
                    else old / np.float32(2)
                )
            with self.subTest(shape=value.shape, strides=value.strides, captured=captured):
                pointer = value.__array_interface__["data"][0]
                result = (
                    self.fixture.captured_map(value, 7.5)
                    if captured
                    else self.fixture.half_inplace(value)
                )
                self.assertIs(result, value)
                self.assertEqual(value.__array_interface__["data"][0], pointer)
                np.testing.assert_array_equal(base, expected_base)

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
