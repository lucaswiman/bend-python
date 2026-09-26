import array
from concurrent.futures import ThreadPoolExecutor
import gc
import importlib.util
import math
import os
from pathlib import Path
import pickle
import random
import struct
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
import unittest
import weakref

try:
    import bend_example
except ImportError:
    # A development checkout builds the example in place under examples/.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    import bend_example


U32_MAX = (1 << 32) - 1
MODULE_DIRECTORY = Path(bend_example.__file__).resolve().parent


class NativeBindingsTests(unittest.TestCase):
    def test_u32_boundaries_and_wrapping(self):
        for value in (0, 1, 2, 65535, 65536, 1 << 31, U32_MAX):
            with self.subTest(value=value):
                self.assertEqual(bend_example.identity(value), value)
                self.assertEqual(bend_example.increment(value), (value + 1) & U32_MAX)
                self.assertEqual(bend_example.square(value), value * value & U32_MAX)

    def test_bad_arguments_leave_runtime_usable(self):
        for function, expected_zero in (
            (bend_example.square, 0),
            (bend_example.increment, 1),
            (bend_example.identity, 0),
        ):
            for value in (None, True, False, 1.0, "1", b"1", [], object()):
                with (
                    self.subTest(function=function.__name__, value=value),
                    self.assertRaises(TypeError),
                ):
                    function(value)
            for value in (-1, -(1 << 100), 1 << 32, 1 << 100):
                with (
                    self.subTest(function=function.__name__, value=value),
                    self.assertRaises(OverflowError),
                ):
                    function(value)
            with self.assertRaises(TypeError):
                function()
            with self.assertRaises(TypeError):
                function(1, 2)
            with self.assertRaises(TypeError):
                function(x=1)
            self.assertEqual(function(0), expected_zero)
        self.assertEqual(bend_example.square(12), 144)

    def test_repeated_calls_match_python(self):
        rng = random.Random(20260925)
        for _ in range(10000):
            value = rng.getrandbits(32)
            self.assertEqual(bend_example.square(value), value * value & U32_MAX)
            self.assertEqual(bend_example.increment(value), (value + 1) & U32_MAX)
            self.assertEqual(bend_example.identity(value), value)

    def test_generic_echo_preserves_python_types_and_identity(self):
        class Custom:
            pass

        class Integer(int):
            pass

        generator = (x for x in range(3))
        values = (
            None,
            True,
            False,
            1 << 200,
            -(1 << 200),
            Integer(7),
            0.1,
            float("nan"),
            float("inf"),
            2 + 3j,
            "a\x00é😀\ud800",
            b"\x00\xff",
            bytearray(b"mutable"),
            [],
            (),
            {},
            {1, 2},
            frozenset({3}),
            range(5),
            slice(1, 8, 2),
            memoryview(b"buffer"),
            Ellipsis,
            NotImplemented,
            Custom(),
            object(),
            lambda: 1,
            generator,
            iter([1, 2]),
            ValueError("error object"),
            Custom,
            sys,
        )
        for value in values:
            with self.subTest(type=type(value).__name__):
                self.assertIs(bend_example.echo(value), value)
        self.assertEqual(next(generator), 0)

    def test_containers_preserve_aliases_cycles_and_keyword_arguments(self):
        cycle = []
        cycle.append(cycle)
        self.assertIs(bend_example.echo(cycle), cycle)
        for function, expected_type in (
            (bend_example.pack, tuple),
            (bend_example.pack_list, list),
        ):
            result = function(cycle, cycle, None)
            self.assertIs(type(result), expected_type)
            self.assertIs(result[0], cycle)
            self.assertIs(result[0], result[1])
            self.assertIs(result[0][0], result[0])
            self.assertIsNone(result[2])
            self.assertEqual(function(), expected_type())
        result = bend_example.kwargs(value=cycle, **{"not an identifier": cycle})
        self.assertEqual(set(result), {"value", "not an identifier"})
        self.assertIs(result["value"], cycle)
        self.assertIs(result["not an identifier"], cycle)
        with self.assertRaises(TypeError):
            bend_example.kwargs(1)

    def test_typed_adapters(self):
        self.assertEqual(bend_example.add(12, 30), 42)
        self.assertEqual(bend_example.add(U32_MAX, 2), 1)
        for args in ((), (1,), (1, 2, 3), (True, 1), (1, "2")):
            with self.assertRaises(TypeError):
                bend_example.add(*args)
        for args in ((-1, 0), (0, 1 << 32)):
            with self.assertRaises(OverflowError):
                bend_example.add(*args)
        self.assertIs(bend_example.flip(True), False)
        self.assertIs(bend_example.flip(False), True)
        for value in ("", "plain", "a\x00é😀\ud800"):
            self.assertEqual(bend_example.echo_string(value), value)
        for value in (-0.0, 1.0, 0.1, 1e30, float("inf")):
            narrowed = struct.unpack("f", struct.pack("f", value))[0]
            expected = struct.unpack("f", struct.pack("f", narrowed / 2))[0]
            self.assertEqual(bend_example.half(value), expected)
        self.assertTrue(math.isnan(bend_example.half(float("nan"))))
        # Narrowing rounds to nearest; finite doubles beyond F32 overflow to infinity.
        float_max = struct.unpack("f", struct.pack("I", 0x7F7FFFFF))[0]
        self.assertEqual(bend_example.half(3.4028235e38), float_max / 2)
        for value in (1e300, 3.4028235677973366e38, float("inf")):
            self.assertEqual(bend_example.half(value), math.inf)
            self.assertEqual(bend_example.half(-value), -math.inf)
        self.assertEqual(math.copysign(1, bend_example.half(-0.0)), -1)
        for function, value in (
            (bend_example.flip, 1),
            (bend_example.half, 1),
            (bend_example.echo_string, b"text"),
        ):
            with self.assertRaises(TypeError):
                function(value)

    def test_slow_exports_compute_same_checked_recurrence(self):
        for count in (0, 1, 2, 7):
            expected = 0
            for _ in range(32 * count):
                expected = (expected * 1664525 + 1013904223) & U32_MAX
            self.assertEqual(bend_example.slow(count), expected)
            self.assertEqual(bend_example.slow_release(count), expected)

    def test_python_operations_and_exceptions(self):
        value = object()
        mapping = {"key": value}
        self.assertIs(bend_example.getitem(mapping, "key"), value)
        self.assertIs(bend_example.setitem(mapping, "new", value), mapping)
        self.assertIs(mapping["new"], value)
        items = [None]
        self.assertIs(bend_example.setitem(items, 0, value), items)
        self.assertIs(items[0], value)
        for function, args, error in (
            (bend_example.getitem, ({}, "missing"), KeyError),
            (bend_example.getitem, ([], 0), IndexError),
            (bend_example.getitem, (None, 0), TypeError),
            (bend_example.setitem, ((), 0, None), TypeError),
            (bend_example.setitem, ([], 1, None), IndexError),
        ):
            with self.assertRaises(error):
                function(*args)
            self.assertEqual(bend_example.square(12), 144)

    def test_attribute_length_none_dict_and_invoke_effects(self):
        self.assertEqual(bend_example.attribute(3 + 4j, "imag"), 4.0)
        self.assertEqual(bend_example.attribute([], "append").__name__, "append")
        self.assertIs(bend_example.attribute(bend_example, "square"), bend_example.square)
        for value, expected in (
            ([], 0),
            ([1, 2, 3], 3),
            ("a\x00\u00e9\U0001f600", 4),
            (range(7), 7),
        ):
            self.assertEqual(bend_example.length(value), expected)
        self.assertIsNone(bend_example.make_none())
        key, value = object(), []
        result = bend_example.make_dict("a", value, key, None, "a", value)
        self.assertEqual(list(result), ["a", key])
        self.assertIs(result["a"], value)
        self.assertEqual(bend_example.make_dict(), {})
        self.assertEqual(bend_example.invoke(lambda *args: args, 1, value), (1, value))
        self.assertIs(bend_example.invoke(bend_example.echo, value), value)
        for function, args, kwargs, error in (
            (bend_example.attribute, (1, "missing"), {}, AttributeError),
            (bend_example.attribute, (1, 2), {}, TypeError),
            (bend_example.length, (object(),), {}, TypeError),
            (bend_example.length, (iter([]),), {}, TypeError),
            (bend_example.make_none, (1,), {}, TypeError),
            (bend_example.make_none, (), {"x": 1}, TypeError),
            (bend_example.make_dict, ("odd",), {}, TypeError),
            (bend_example.make_dict, ([], 1), {}, TypeError),
            (bend_example.invoke, (), {}, TypeError),
            (bend_example.invoke, (len,), {"x": 1}, TypeError),
            (bend_example.invoke, (int, "x"), {}, ValueError),
        ):
            with self.subTest(function=function.__name__, args=args), self.assertRaises(error):
                function(*args, **kwargs)
        self.assertEqual(bend_example.square(12), 144)

    def test_lengths_are_exact_beyond_u32(self):
        class Sized:
            def __init__(self, size):
                self.size = size

            def __len__(self):
                return self.size

        # Python.from_nat converts exactly; U32.from_nat would wrap 2^32 to 0.
        for size in (0, U32_MAX, U32_MAX + 1, 1 << 40, (1 << 48) - 1):
            with self.subTest(size=size):
                self.assertEqual(bend_example.length(Sized(size)), size)
        for size in (1 << 48, sys.maxsize):
            with self.assertRaises(OverflowError):
                bend_example.length(Sized(size))
        self.assertEqual(bend_example.length([1]), 1)

    def test_bytes_truthiness_imports_and_five_arguments(self):
        self.assertEqual(bend_example.reverse5(1, 2, 3, 4, 5), (5, 4, 3, 2, 1))
        items = [object() for _ in range(5)]
        self.assertTrue(
            all(a is b for a, b in zip(bend_example.reverse5(*items), reversed(items), strict=True))
        )
        for value, expected in (
            (0, False),
            (1, True),
            ([], False),
            ([0], True),
            ("", False),
            (None, False),
            (float("nan"), True),
        ):
            self.assertIs(bend_example.truth(value), expected)
        self.assertIs(bend_example.load("operator"), __import__("operator"))
        self.assertIs(bend_example.load("collections.abc"), sys.modules["collections.abc"])
        for data in (
            b"",
            b"\x00",
            b"\x01\x02\xff",
            bytes(range(256)) * 3,
            bytearray(b"abc"),
            memoryview(b"xyz"),
            array.array("H", [1, 65535]),
        ):
            raw = bytes(data)
            self.assertEqual(bend_example.checksum(data), sum(raw) & U32_MAX)
            self.assertEqual(bend_example.reversed_bytes(data), raw[::-1])
            self.assertIs(type(bend_example.reversed_bytes(data)), bytes)
        self.assertEqual(bend_example.make_bytes(), b"")
        self.assertEqual(bend_example.make_bytes(0, 255, 65), b"\x00\xffA")

        class Explodes:
            def __bool__(self):
                raise ZeroDivisionError("no truth")

        for function, args, error in (
            (bend_example.reverse5, (1, 2, 3, 4), TypeError),
            (bend_example.reverse5, (1, 2, 3, 4, 5, 6), TypeError),
            (bend_example.truth, (Explodes(),), ZeroDivisionError),
            (bend_example.load, ("no_such_module_bend",), ModuleNotFoundError),
            (bend_example.load, (1,), TypeError),
            (bend_example.checksum, ("text",), TypeError),
            (bend_example.checksum, ([1, 2],), TypeError),
            (bend_example.checksum, (memoryview(b"abcd")[::2],), BufferError),
            (bend_example.make_bytes, (256,), ValueError),
            (bend_example.make_bytes, (1, -1), OverflowError),
            (bend_example.make_bytes, (1, "x"), TypeError),
        ):
            with self.subTest(function=function.__name__, args=args), self.assertRaises(error):
                function(*args)
        self.assertEqual(bend_example.checksum(b"ok"), 218)

    def test_exports_behave_like_module_functions(self):
        for name in ("square", "echo", "make_dict"):
            with self.subTest(name=name):
                function = getattr(bend_example, name)
                self.assertEqual(function.__name__, name)
                self.assertEqual(function.__qualname__, name)
                self.assertEqual(function.__module__, "bend_example")
                self.assertEqual(repr(function), f"<bend function bend_example.{name}>")
                self.assertIs(pickle.loads(pickle.dumps(function)), function)
        self.assertEqual(bend_example.square.__doc__, "A native Bend function.")
        with self.assertRaises(TypeError):
            type(bend_example.square)()

    @unittest.skipUnless(sys.platform == "linux", "reads /proc/self/statm")
    def test_idle_runtimes_do_not_retain_large_heaps(self):
        self.run_fresh_python("""
            import bend_example
            def resident_mib():
                with open("/proc/self/statm") as statm:
                    return int(statm.read().split()[1]) * 4096 / 2**20
            text = "x" * 5_000_000
            assert bend_example.echo_string("warm") == "warm"
            baseline = resident_mib()
            for _ in range(3):
                assert bend_example.echo_string(text) == text
            # Each call touches ~100 MiB of Bend heap; only Python strings remain.
            growth = resident_mib() - baseline
            assert growth < 60, f"resident memory grew by {growth:.0f} MiB"
            assert bend_example.square(12) == 144
        """)

    def test_bend_constructs_python_builtin_objects(self):
        cases = (
            ("bool", (0,), False),
            ("int", (str(1 << 200),), 1 << 200),
            ("float", ("0.125",), 0.125),
            ("complex", (1, 2), 1 + 2j),
            ("str", (42,), "42"),
            ("bytes", ([0, 255],), b"\x00\xff"),
            ("bytearray", (b"data",), bytearray(b"data")),
            ("list", ((1, 2),), [1, 2]),
            ("tuple", ([1, 2],), (1, 2)),
            ("dict", ([("x", 1)],), {"x": 1}),
            ("set", ([1, 1],), {1}),
            ("frozenset", ([1, 1],), frozenset({1})),
            ("range", (1, 8, 2), range(1, 8, 2)),
            ("slice", (1, 8, 2), slice(1, 8, 2)),
            ("memoryview", (b"buffer",), memoryview(b"buffer")),
        )
        for name, args, expected in cases:
            with self.subTest(name=name):
                actual = bend_example.construct(name, *args)
                self.assertIs(type(actual), type(expected))
                self.assertEqual(actual, expected)
        item = object()
        self.assertIs(bend_example.construct("list", [item])[0], item)
        self.assertIs(type(bend_example.construct("object")), object)
        with self.assertRaises(AttributeError):
            bend_example.construct("not_a_builtin")
        with self.assertRaises(ValueError):
            bend_example.construct("int", "not an integer")
        self.assertEqual(bend_example.construct("int", "42"), 42)

    def test_callbacks_can_reenter_and_preserve_exceptions(self):
        self.assertEqual(bend_example.call(lambda a, *, b: a + b, 12, b=30), 42)
        self.assertEqual(bend_example.call(bend_example.square, 12), 144)
        self.assertEqual(
            bend_example.call(
                lambda x: bend_example.call(lambda y: bend_example.add(y, 1), x),
                41,
            ),
            42,
        )

        class Mapping:
            def __getitem__(self, key):
                return bend_example.square(key)

        self.assertEqual(bend_example.getitem(Mapping(), 12), 144)
        error = ValueError("callback failure")

        def fail():
            raise error

        with self.assertRaises(ValueError) as raised:
            bend_example.call(fail)
        self.assertIs(raised.exception, error)
        with self.assertRaises(TypeError):
            bend_example.call(123)
        self.assertEqual(bend_example.call(lambda: 42), 42)

    def test_call_arena_releases_owned_references(self):
        class Payload:
            pass

        value = Payload()
        reference = weakref.ref(value)
        result = bend_example.echo(value)
        del value
        self.assertIs(result, reference())
        del result
        gc.collect()
        self.assertIsNone(reference())
        result = bend_example.call(Payload)
        reference = weakref.ref(result)
        del result
        gc.collect()
        self.assertIsNone(reference())

    def test_finalizers_released_by_the_arena_can_reenter(self):
        finalized = []

        class Reenter:
            def __del__(self):
                finalized.append(bend_example.square(3) + bend_example.call(lambda: 1))

        for _ in range(100):
            bend_example.echo(Reenter())
            with self.assertRaises(TypeError):
                bend_example.square(Reenter())
            mapping = {"key": Reenter()}
            # The replaced value is finalized inside the set_item effect.
            bend_example.setitem(mapping, "key", None)
        gc.collect()
        self.assertEqual(finalized, [10] * 300)

    def test_calls_from_multiple_python_threads(self):
        def worker(seed):
            rng = random.Random(seed)
            for _ in range(1000):
                value = rng.getrandbits(32)
                self.assertEqual(bend_example.square(value), value * value & U32_MAX)
                self.assertEqual(bend_example.identity(value), value)
                payload = {"seed": seed, "value": value}
                self.assertIs(bend_example.echo(payload), payload)
                self.assertEqual(
                    bend_example.call(bend_example.increment, value), (value + 1) & U32_MAX
                )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(worker, range(8)))

    def test_concurrent_callbacks_reenter_with_independent_arenas_and_errors(self):
        self.run_fresh_python("""
            import threading
            from concurrent.futures import ThreadPoolExecutor
            import bend_example

            rendezvous = threading.Barrier(2, timeout=5)
            payloads = [object(), object()]
            errors = [ValueError("first callback"), ValueError("second callback")]

            def worker(index):
                payload = payloads[index]
                def callback(value, *, keyword):
                    assert value is payload and keyword is payload
                    rendezvous.wait()
                    assert bend_example.echo(value) is payload
                    assert bend_example.call(bend_example.square, 12) == 144
                    if index == 0:
                        raise errors[index]
                    return bend_example.call(lambda item: item, value)

                try:
                    result = bend_example.call(callback, payload, keyword=payload)
                except ValueError as error:
                    assert index == 0 and error is errors[index]
                else:
                    assert index == 1 and result is payload
                assert bend_example.echo(payload) is payload

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(worker, range(2)))
            assert bend_example.square(12) == 144
        """)

    def run_fresh_python(self, source):
        env = dict(os.environ, PYTHONPATH=str(MODULE_DIRECTORY), PATH="", BEND="/missing/bend")
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(source)],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_and_calls_need_no_compiler_or_subprocess(self):
        self.run_fresh_python("""
            import subprocess
            def forbidden(*args, **kwargs):
                raise AssertionError("native calls must not spawn a process")
            subprocess.Popen = forbidden
            import bend_example
            assert bend_example.square(12) == 144
            assert bend_example.increment(0xffffffff) == 0
            assert bend_example.identity(0xffffffff) == 0xffffffff
            value = {"cycle": []}
            value["cycle"].append(value)
            assert bend_example.echo(value) is value
            assert bend_example.call(lambda x: x, value) is value
        """)

    def test_callable_survives_deleting_module_reference(self):
        self.run_fresh_python("""
            import gc
            import sys
            import bend_example
            square = bend_example.square
            del sys.modules["bend_example"]
            del bend_example
            gc.collect()
            assert square.__name__ == "square"
            for value in range(1000):
                assert square(value) == value * value
        """)

    @unittest.skipUnless(
        sys.platform == "linux" and os.uname().machine == "x86_64",
        "sigaction layout below is specific to Linux x86_64",
    )
    def test_host_signal_handlers_are_preserved(self):
        self.run_fresh_python("""
            import ctypes as C
            import signal

            class SigAction(C.Structure):
                _fields_ = [("handler", C.c_void_p), ("mask", C.c_ulong * 16),
                            ("flags", C.c_int), ("restorer", C.c_void_p)]

            libc = C.CDLL(None, use_errno=True)
            libc.sigaction.argtypes = [C.c_int, C.POINTER(SigAction), C.POINTER(SigAction)]
            libc.sigaction.restype = C.c_int

            def handlers():
                result = []
                for sig in (signal.SIGSEGV, signal.SIGBUS, signal.SIGPIPE):
                    action = SigAction()
                    assert libc.sigaction(sig, None, C.byref(action)) == 0, C.get_errno()
                    result.append((action.handler, action.mask[0], action.flags, action.restorer))
                return result

            before = handlers()
            import bend_example
            assert bend_example.square(12) == 144
            assert handlers() == before
        """)

    @unittest.skipUnless(importlib.util.find_spec("_interpreters"), "requires _interpreters")
    def test_subinterpreter_import_fails_without_damaging_main_interpreter(self):
        self.run_fresh_python("""
            import _interpreters
            import bend_example
            interpreter = _interpreters.create()
            try:
                error = _interpreters.run_string(interpreter, "import bend_example")
                assert error is not None and error.type.__name__ == "ImportError", error
            finally:
                _interpreters.destroy(interpreter)
            assert bend_example.increment(41) == 42
        """)

    @unittest.skipUnless(
        sysconfig.get_config_var("Py_GIL_DISABLED"), "requires free-threaded CPython"
    )
    def test_import_does_not_enable_gil(self):
        self.run_fresh_python("""
            import sys
            assert not sys._is_gil_enabled()
            import bend_example
            assert not sys._is_gil_enabled()
            assert bend_example.square(12) == 144
            assert not sys._is_gil_enabled()
        """)

    def test_per_export_gil_policy(self):
        self.run_fresh_python("""
            import sys
            import threading
            import time
            import bend_example

            count = 10000
            for _ in range(10):
                start = time.monotonic()
                bend_example.slow(count)
                if time.monotonic() - start >= 0.2:
                    break
                count = min(count * 4, 0xffffffff)
            expected = bend_example.slow(count)

            def observe(function):
                stop = threading.Event()
                ready = threading.Event()
                ticks = []
                def worker():
                    ready.set()
                    while not stop.wait(0.002):
                        ticks.append(time.monotonic())
                thread = threading.Thread(target=worker)
                thread.start()
                ready.wait()
                try:
                    start = time.monotonic()
                    assert function(count) == expected
                    end = time.monotonic()
                finally:
                    stop.set()
                    thread.join()
                margin = (end - start) * 0.2
                return [t for t in ticks if start + margin < t < end - margin]

            def failure():
                held = observe(bend_example.slow)
                released = observe(bend_example.slow_release)
                if not released:
                    return "Python worker made no progress during detached Bend computation"
                if getattr(sys, "_is_gil_enabled", lambda: True)():
                    if held:
                        return "Python worker ran during GIL-retaining Bend computation"
                elif not held:
                    return "free-threaded Python worker made no progress"

            # Scheduling noise on loaded runners can starve one window; retry
            # rather than weakening what each attempt checks.
            failures = []
            for _ in range(3):
                message = failure()
                if message is None:
                    break
                failures.append(message)
            else:
                raise AssertionError(failures)
        """)


if __name__ == "__main__":
    unittest.main()
