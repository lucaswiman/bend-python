"""Optional real SciPy BLAS kernels on borrowed storage, without normalization."""

import array
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import subprocess
import sys
import textwrap
import unittest

from test_bindings import MODULE_DIRECTORY, bend_example

SCIPY = importlib.util.find_spec("scipy") is not None
NUMPY = importlib.util.find_spec("numpy") is not None
TORCH = NUMPY and importlib.util.find_spec("torch") is not None
INT_MAX = (1 << 31) - 1


class OptionalDependencyTests(unittest.TestCase):
    def test_missing_scipy_is_lazy_and_maps_still_work_without_numpy(self):
        script = textwrap.dedent("""\
            import array
            import importlib.abc
            import sys

            class NoScientificPackages(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'scipy', 'numpy', 'torch'}:
                        raise ModuleNotFoundError('blocked optional dependency ' + fullname)
                    return None

            sys.meta_path.insert(0, NoScientificPackages())
            import bend_example
            assert bend_example.increment(9) == 10
            value = array.array('f', [6, -8])
            assert bend_example.numpy_half_inplace(value) is value
            assert list(value) == [3, -4]
            blocked = {'scipy', 'numpy', 'torch'}
            assert not any(name.split('.')[0] in blocked for name in sys.modules)
            a = memoryview(array.array('f', [2, 3])).cast('B').cast('f', shape=(2, 1))
            b = memoryview(array.array('f', [4])).cast('B').cast('f', shape=(1, 1))
            out = memoryview(array.array('f', [9, 10])).cast('B').cast('f', shape=(2, 1))
            calls = [
                lambda: bend_example.blas_scale(2.0, value),
                lambda: bend_example.blas_dot(value, value),
                lambda: bend_example.blas_axpy(2.0, value, array.array('f', [1, 2])),
                lambda: bend_example.blas_matmul(a, b, out),
            ]
            for call in calls:
                try:
                    call()
                except ImportError:
                    pass
                else:
                    raise AssertionError('BLAS unexpectedly ran without SciPy')
            assert list(value) == [3, -4]
            assert out.tolist() == [[9], [10]]
            assert bend_example.identity(17) == 17
            assert bend_example.numpy_half_inplace(value) is value
            assert list(value) == [1.5, -2]
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=MODULE_DIRECTORY,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(SCIPY, "SciPy BLAS is an optional dependency")
class BlasBufferTests(unittest.TestCase):
    def test_capsule_cache_retries_and_shares_validated_bindings(self):
        script = textwrap.dedent("""\
            import array
            from concurrent.futures import ThreadPoolExecutor
            import gc
            import importlib.util
            import sys
            import threading
            import types
            import weakref
            import bend_example as b
            import scipy.linalg.cython_blas as backend

            original = backend.__pyx_capi__
            lookups = []
            ready = threading.Barrier(8)
            valid = False
            class Api:
                def __getitem__(self, name):
                    lookups.append(name)
                    if name == 'sscal' and not valid:
                        return original['sdot']  # Real capsule, incompatible ABI.
                    if name == 'sdot':
                        # Resolution must permit reentry and concurrent misses.
                        nested = array.array('f', [3])
                        b.blas_scale(2.0, nested)
                        assert list(nested) == [6]
                        ready.wait(timeout=30)
                    return original[name]
            backend.__pyx_capi__ = Api()
            x = array.array('f', [2, -3])
            try:
                b.blas_scale(4.0, x)
            except ImportError:
                pass
            else:
                raise AssertionError('incompatible capsule ABI was accepted')
            assert list(x) == [2, -3]
            valid = True
            assert b.blas_scale(4.0, x) is x
            assert list(x) == [8, -12]

            def dot(seed):
                values = array.array('f', [seed, 1])
                return b.blas_dot(values, values)
            with ThreadPoolExecutor(max_workers=8) as workers:
                assert list(workers.map(dot, range(8))) == [i*i + 1 for i in range(8)]
            assert lookups.count('sscal') == 2
            assert lookups.count('sdot') == 8
            # Successfully bound operations keep their original validated ABI.
            backend.__pyx_capi__ = {}
            assert b.blas_scale(-1.0, x) is x
            assert list(x) == [-8, 12]
            assert b.blas_dot(x, x) == 208
            if importlib.util.find_spec('torch') is not None:
                import torch
                tensor = torch.tensor([3, -5], dtype=torch.float32)
                assert b.torch_blas_scale(2.0, tensor) is tensor
                assert tensor.tolist() == [6, -10]
            try:
                b.blas_axpy(2.0, x, array.array('f', [5, 7]))
            except KeyError:
                pass
            else:
                raise AssertionError('missing capsule lookup did not propagate')
            backend.__pyx_capi__ = original
            y = array.array('f', [5, 7])
            assert b.blas_axpy(2.0, x, y) is y
            assert list(y) == [-11, 31]
            assert lookups.count('sscal') == 2 and lookups.count('sdot') == 8

            # Retain the supplying module as well as its capsule after sys.modules
            # replacement; native code can remain live after its exports change.
            name = 'scipy.linalg.cython_blas'
            supplied = types.ModuleType(name)
            supplied.__pyx_capi__ = original
            reference = weakref.ref(supplied)
            sys.modules[name] = supplied
            def matrix(values):
                return memoryview(array.array('f', values)).cast('B').cast('f', shape=(1, 1))
            out = matrix([19])
            assert b.blas_matmul(matrix([3]), matrix([7]), out) is out
            assert out.tolist() == [[21]]
            sys.modules[name] = backend
            del supplied
            gc.collect()
            assert reference() is not None
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=MODULE_DIRECTORY,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stdlib_buffers_and_readonly_dot(self):
        x = array.array("f", [2, -3, 5])
        y = array.array("f", [-7, 11, 13])
        self.assertEqual(bend_example.blas_dot(memoryview(x).toreadonly(), y), 18)
        self.assertIs(bend_example.blas_scale(-2.0, x), x)
        self.assertEqual(list(x), [-4, 6, -10])
        self.assertIs(bend_example.blas_axpy(3.0, memoryview(x).toreadonly(), y), y)
        self.assertEqual(list(y), [-19, 29, -17])
        # Returning the arena's original object must not retain the buffer export.
        x.append(42)
        y.append(43)

    def test_empty_vectors_and_parallel_invocations(self):
        empty = array.array("f")
        self.assertIs(bend_example.blas_scale(2.0, empty), empty)
        self.assertEqual(bend_example.blas_dot(empty, empty), 0)
        self.assertIs(bend_example.blas_axpy(2.0, empty, empty), empty)

        def kernels(seed):
            for step in range(6):
                value = seed + step + 1
                x = array.array("f", [value, -2, 5])
                y = array.array("f", [3, 7, -4])
                self.assertEqual(bend_example.blas_dot(x, y), 3 * value - 34)
                bend_example.blas_scale(2.0, x)
                bend_example.blas_axpy(-3.0, x, y)
                self.assertEqual(list(y), [3 - 6 * value, 19, -34])

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(kernels, range(4)))


@unittest.skipUnless(SCIPY and NUMPY, "SciPy and NumPy integration dependencies are optional")
class BlasNumpyTests(unittest.TestCase):
    def test_strided_vectors_against_scipy_preserve_gaps_and_identity(self):
        import numpy as np
        from scipy.linalg import blas

        x_storage = np.array([2, 91, -3, 92, 5, 93], dtype=np.float32)
        y_storage = np.array([-7, 81, 82, 11, 83, 84, 13, 85, 86], dtype=np.float32)
        x, y = x_storage[::2], y_storage[::3]
        readonly = x.view()
        readonly.flags.writeable = False
        self.assertAlmostEqual(bend_example.blas_dot(readonly, y), blas.sdot(x.copy(), y.copy()))
        expected = blas.sscal(-2.5, x.copy())
        self.assertIs(bend_example.blas_scale(-2.5, x), x)
        np.testing.assert_array_equal(x, expected)
        expected_y = blas.saxpy(x.copy(), y.copy(), a=1.25)
        self.assertIs(bend_example.blas_axpy(1.25, x, y), y)
        np.testing.assert_array_equal(y, expected_y)
        np.testing.assert_array_equal(x_storage[1::2], [91, 92, 93])
        np.testing.assert_array_equal(y_storage[[1, 2, 4, 5, 7, 8]], [81, 82, 83, 84, 85, 86])

    def test_matmul_fortran_and_padded_columns_against_scipy(self):
        import numpy as np
        from scipy.linalg import blas

        a_storage = np.full((5, 3), 91, dtype=np.float32, order="F")
        b_storage = np.full((6, 4), 92, dtype=np.float32, order="F")
        out_storage = np.full((7, 4), 93, dtype=np.float32, order="F")
        a, b, out = a_storage[:2], b_storage[:3], out_storage[:2]
        a[:] = [[2, -3, 5], [7, 11, -13]]
        b[:] = [[17, 19, -23, 29], [-31, 37, 41, -43], [47, -53, 59, 61]]
        a.flags.writeable = b.flags.writeable = False
        expected = blas.sgemm(1, np.asfortranarray(a), np.asfortranarray(b))
        self.assertIs(bend_example.blas_matmul(a, b, out), out)
        np.testing.assert_array_equal(out, expected)
        np.testing.assert_array_equal(a_storage[2:], 91)
        np.testing.assert_array_equal(b_storage[3:], 92)
        np.testing.assert_array_equal(out_storage[2:], 93)

    def test_zero_dimension_matrix_contracts(self):
        import numpy as np

        out = np.full((2, 3), 71, dtype=np.float32, order="F")
        self.assertIs(
            bend_example.blas_matmul(
                np.empty((2, 0), dtype=np.float32), np.empty((0, 3), dtype=np.float32), out
            ),
            out,
        )
        np.testing.assert_array_equal(out, 0)
        for m, n in ((0, 3), (2, 0)):
            a = np.ones((m, 4), dtype=np.float32, order="F")
            b = np.ones((4, n), dtype=np.float32, order="F")
            out = np.empty((m, n), dtype=np.float32)
            self.assertIs(bend_example.blas_matmul(a, b, out), out)
            self.assertEqual(out.size, 0)

    def test_vector_validation_precedes_writes(self):
        import numpy as np

        unaligned = np.ndarray((3,), dtype=np.float32, buffer=bytearray(13), offset=1)
        bad_stride = np.ndarray((3,), dtype=np.float32, buffer=bytearray(20), strides=(5,))
        readonly = np.array([2, 3, 5], dtype=np.float32)
        readonly.flags.writeable = False
        invalid = (
            np.ones(3, dtype=np.float64),
            np.arange(3, dtype=np.float32)[::-1],
            np.ones((1, 3), dtype=np.float32),
            unaligned,
            bad_stride,
            readonly,
        )
        for x in invalid:
            before = x.copy()
            with self.subTest(dtype=x.dtype, shape=x.shape, strides=x.strides):
                with self.assertRaises((BufferError, TypeError, ValueError)):
                    bend_example.blas_scale(2.0, x)
                np.testing.assert_array_equal(x, before)
        y = np.array([7, 11, 13], dtype=np.float32)
        for x in (*invalid[:-1], np.ones(2, dtype=np.float32)):
            before = y.copy()
            with self.subTest(operation="axpy", shape=x.shape, strides=x.strides):
                with self.assertRaises((BufferError, TypeError, ValueError)):
                    bend_example.blas_axpy(2.0, x, y)
                np.testing.assert_array_equal(y, before)
                with self.assertRaises((BufferError, TypeError, ValueError)):
                    bend_example.blas_dot(x, y)
        storage = np.array([2, 3, 5, 7], dtype=np.float32)
        before = storage.copy()
        for x, y in ((storage, storage), (storage[:-1], storage[1:])):
            with self.subTest(overlap=(x.shape, y.shape)), self.assertRaises(BufferError):
                bend_example.blas_axpy(2.0, x, y)
            np.testing.assert_array_equal(storage, before)
        with self.assertRaises((BufferError, ValueError)):
            bend_example.blas_axpy(2.0, y, readonly)
        np.testing.assert_array_equal(readonly, [2, 3, 5])

    def test_matrix_validation_precedes_writes(self):
        import numpy as np

        a = np.array([[2, 3], [5, 7]], dtype=np.float32, order="F")
        b = np.array([[11, 13], [17, 19]], dtype=np.float32, order="F")
        out = np.full((2, 2), 23, dtype=np.float32, order="F")
        unaligned = np.ndarray((2, 2), dtype=np.float32, buffer=bytearray(17), offset=1, order="F")
        readonly = out.copy(order="F")
        readonly.flags.writeable = False
        overlapping = np.lib.stride_tricks.as_strided(a, shape=(2, 2), strides=(4, 4))
        shared = np.arange(6, dtype=np.float32).reshape((2, 3), order="F")
        cases = (
            (a.copy(order="C"), b, out),
            (a, b.copy(order="C"), out),
            (a, b, out.copy(order="C")),
            (a.astype(np.float64), b, out),
            (a, b, readonly),
            (unaligned, b, out),
            (a[::-1], b, out),
            (overlapping, b, out),
            (a[:, :1], b, out),
            (a, b, out[:, :1]),
            (a.ravel(), b, out),
            (a, b, a),
            (a, b, b),
            (shared[:, :2], b, shared[:, 1:]),
        )
        for left, right, target in cases:
            before = target.copy()
            with self.subTest(shapes=(left.shape, right.shape, target.shape), strides=left.strides):
                with self.assertRaises((BufferError, TypeError, ValueError)):
                    bend_example.blas_matmul(left, right, target)
                np.testing.assert_array_equal(target, before)

    def test_lp64_overflow_is_rejected_before_access(self):
        import numpy as np

        storage = np.array([2, 3], dtype=np.float32)
        huge_count = np.lib.stride_tricks.as_strided(storage, shape=(INT_MAX + 1,), strides=(4,))
        huge_increment = np.lib.stride_tricks.as_strided(
            storage, shape=(2,), strides=(4 * (INT_MAX + 1),)
        )
        for x in (huge_count, huge_increment):
            with self.subTest(shape=x.shape, strides=x.strides), self.assertRaises(OverflowError):
                bend_example.blas_scale(2.0, x)
            np.testing.assert_array_equal(storage, [2, 3])
        huge_lda = np.lib.stride_tricks.as_strided(
            storage, shape=(1, 2), strides=(4, 4 * (INT_MAX + 1))
        )
        b = np.ones((2, 1), dtype=np.float32, order="F")
        out = np.full((1, 1), 71, dtype=np.float32)
        with self.assertRaises(OverflowError):
            bend_example.blas_matmul(huge_lda, b, out)
        np.testing.assert_array_equal(out, 71)


@unittest.skipUnless(SCIPY and TORCH, "SciPy, NumPy and PyTorch are optional dependencies")
class BlasTorchTests(unittest.TestCase):
    def test_torch_strided_vectors_and_transposed_matrices(self):
        import torch

        x_storage = torch.tensor([2, 91, -3, 92, 5, 93], dtype=torch.float32)
        x = x_storage[::2]
        y = torch.tensor([-7, 11, 13], dtype=torch.float32)
        self.assertEqual(bend_example.torch_blas_dot(x, y), 18)
        pointer, version = x.data_ptr(), x._version
        self.assertIs(bend_example.torch_blas_scale(-2.0, x), x)
        self.assertEqual(x.data_ptr(), pointer)
        self.assertEqual(x._version, version + 1)
        torch.testing.assert_close(x, torch.tensor([-4, 6, -10], dtype=torch.float32))
        self.assertIs(bend_example.torch_blas_axpy(3.0, x, y), y)
        torch.testing.assert_close(y, torch.tensor([-19, 29, -17], dtype=torch.float32))
        torch.testing.assert_close(x_storage[1::2], torch.tensor([91, 92, 93], dtype=torch.float32))
        a = torch.tensor([[2, 7], [-3, 11], [5, -13]], dtype=torch.float32).T
        b = torch.tensor([[17, -31, 47], [19, 37, -53]], dtype=torch.float32).T
        out = torch.empty((2, 2), dtype=torch.float32).T
        expected = a @ b
        version = out._version
        self.assertIs(bend_example.torch_blas_matmul(a, b, out), out)
        self.assertEqual(out._version, version + 1)
        torch.testing.assert_close(out, expected, rtol=0, atol=0)

    def test_torch_rejects_gradients_and_dtypes_before_writes(self):
        import torch

        x = torch.tensor([2, 3], dtype=torch.float32)
        for bad in (
            torch.tensor([5, 7], dtype=torch.float32, requires_grad=True),
            torch.tensor([5, 7], dtype=torch.float64),
            torch.ones(2, device="meta"),
        ):
            before = x.clone()
            with self.subTest(dtype=bad.dtype, device=bad.device):
                with self.assertRaises((BufferError, TypeError)):
                    bend_example.torch_blas_axpy(2.0, bad, x)
                torch.testing.assert_close(x, before, rtol=0, atol=0)
        bad = torch.tensor([5, 7], dtype=torch.float32, requires_grad=True)
        before = bad.detach().clone()
        with torch.no_grad(), self.assertRaises(TypeError):
            bend_example.torch_blas_scale(2.0, bad)
        torch.testing.assert_close(bad.detach(), before, rtol=0, atol=0)
