"""Exercise the real proof/compiler/runtime failure boundaries in child processes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
BEND = os.environ.get("BEND", str(ROOT / ".tools/bend/bin/bend"))

# Only temporary test extensions contain this rendezvous. It stops the first
# evaluator entry until a second independent call enters, even on one CPU.
# A serializing runtime times out instead of making this a timing/speedup test.
NATIVE_RENDEZVOUS = r"""
#include <errno.h>
#include <time.h>

static pthread_mutex_t bp_test_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t bp_test_condition = PTHREAD_COND_INITIALIZER;
static unsigned bp_test_entered, bp_test_timeout;
static bool bp_test_armed;
static void* bp_test_contexts[2];
static pthread_t bp_test_threads[2];

__attribute__((visibility("default"))) void bp_test_arm(void) {
  pthread_mutex_lock(&bp_test_mutex);
  bp_test_entered = bp_test_timeout = 0;
  bp_test_armed = true;
  pthread_mutex_unlock(&bp_test_mutex);
}

static void bp_test_enter(void* context) {
  pthread_mutex_lock(&bp_test_mutex);
  if (bp_test_armed && bp_test_entered < 2) {
    unsigned slot = bp_test_entered++;
    bp_test_contexts[slot] = context;
    bp_test_threads[slot] = pthread_self();
    pthread_cond_broadcast(&bp_test_condition);
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += 5;
    while (bp_test_entered < 2 && !bp_test_timeout) {
      if (pthread_cond_timedwait(&bp_test_condition, &bp_test_mutex,
                                 &deadline) == ETIMEDOUT) {
        bp_test_timeout = 1;
        pthread_cond_broadcast(&bp_test_condition);
      }
    }
  }
  pthread_mutex_unlock(&bp_test_mutex);
}

__attribute__((visibility("default"))) unsigned bp_test_status(void) {
  pthread_mutex_lock(&bp_test_mutex);
  unsigned result = bp_test_entered == 2;
  if (result && bp_test_contexts[0] == bp_test_contexts[1]) result |= 2;
  if (result && pthread_equal(bp_test_threads[0], bp_test_threads[1])) result |= 4;
  if (bp_test_timeout) result |= 8;
  bp_test_armed = false;
  pthread_mutex_unlock(&bp_test_mutex);
  return result;
}
"""

RENDEZVOUS_SETUP = """
import ctypes
from concurrent.futures import ThreadPoolExecutor
import bend_example as module
probe = ctypes.CDLL(module.__file__)
probe.bp_test_arm.argtypes = []
probe.bp_test_arm.restype = None
probe.bp_test_status.argtypes = []
probe.bp_test_status.restype = ctypes.c_uint
probe.bp_test_arm()
"""


class BuildTests(unittest.TestCase):
    def build(
        self,
        module=None,
        break_proof=False,
        omit_thread_proof=False,
        native_rendezvous=False,
        break_vendored_library=False,
        auto_vendor=False,
        deterministic_handles=False,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        ignored = shutil.ignore_patterns("*.so", "__pycache__", "build", "*.egg-info")
        for name in ("bend", "src"):
            shutil.copytree(ROOT / name, directory / name, ignore=ignored)
        # The example vendors the SDK library itself; never reuse a stale copy.
        shutil.copytree(
            ROOT / "examples",
            directory / "examples",
            ignore=shutil.ignore_patterns("bend", "*.so", "__pycache__", "build"),
        )
        if module is not None:
            (directory / "examples/module.bend").write_text(
                "import Base\nimport ./bend/python.bend as Python\n" + module
            )
        if break_proof:
            arithmetic = directory / "examples/arithmetic.bend"
            arithmetic.write_text(arithmetic.read_text().replace("x * x", "x + 1"))
        if omit_thread_proof:
            (directory / "bend/THREAD_PROOF.bend").unlink()
        if break_vendored_library:
            # A modified vendored copy that vendor() preserves; the SDK copy stays sound.
            shutil.copytree(directory / "bend", directory / "examples/bend")
            library = directory / "examples/bend/python.bend"
            sound = "    case [value]:\n      Some{value}\n"
            self.assertEqual(library.read_text().count(sound), 1)
            library.write_text(
                library.read_text().replace(sound, "    case [value]:\n      None{}\n")
            )
            setup = directory / "examples/setup.py"
            setup.write_text(
                setup.read_text().replace(
                    'vendor(Path(__file__).parent / "bend", force=True)',
                    "pass  # keep the modified copy",
                )
            )
        if auto_vendor:
            # Like a project that never ran `vendor`: the build must supply the library.
            setup = directory / "examples/setup.py"
            setup.write_text(
                setup.read_text().replace(
                    'vendor(Path(__file__).parent / "bend", force=True)',
                    "pass  # rely on the build",
                )
            )
        if deterministic_handles:
            # Fix only this fixture's entropy; key derivation and validation stay real.
            shim = directory / "bend/python.c"
            source = shim.read_text()
            entropy = "getrandom(&bp_secret, sizeof(bp_secret), 0)"
            self.assertEqual(source.count(entropy), 1)
            shim.write_text(source.replace(entropy, "(bp_secret = 0, sizeof(bp_secret))"))
        if native_rendezvous:
            shim = directory / "bend/python.c"
            source = shim.read_text()
            marker = "static Term bp_apply(Env e, Term function, Term argument) {"
            self.assertEqual(source.count(marker), 1)
            source = source.replace(marker, NATIVE_RENDEZVOUS + "\n" + marker)
            evaluator = "  return corpus_eval(e.mem, term_tsk(FID_CLO_APPLY, at));"
            self.assertEqual(source.count(evaluator), 1)
            shim.write_text(
                source.replace(
                    evaluator,
                    "  bp_test_enter(e.mem);\n" + evaluator,
                )
            )
        # Build the example project against this checkout's SDK sources.
        result = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd=directory / "examples",
            env={**os.environ, "CC": "clang", "BEND": BEND, "PYTHONPATH": str(directory / "src")},
            text=True,
            capture_output=True,
            timeout=60,
        )
        return directory, result

    def run_python(self, directory, source):
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", source],
            cwd=directory / "examples",
            env={**os.environ, "PYTHONPATH": str(directory / "src")},
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assertCleanBuildError(self, result, message):
        # Bend's diagnostic is printed, then setuptools reports "error: ..."
        # instead of a CalledProcessError traceback.
        output = result.stdout + result.stderr
        self.assertIn(f"error: {message}", output)
        self.assertNotIn("CalledProcessError", output)
        self.assertNotIn("Traceback", output)

    def test_false_law_stops_build(self):
        directory, result = self.build(break_proof=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stdout + result.stderr)
        self.assertCleanBuildError(
            result, "Bend proof check failed: PROOF.bend (see Bend output above)"
        )
        self.assertFalse(list(directory.rglob("*.so")))

    def test_missing_thread_proof_stops_build(self):
        directory, result = self.build(omit_thread_proof=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("THREAD_PROOF.bend", result.stdout + result.stderr)
        self.assertCleanBuildError(result, "Bend proof file not found: ")
        self.assertFalse(list(directory.rglob("*.so")))

    def test_modified_vendored_library_is_the_one_proved(self):
        directory, result = self.build(break_vendored_library=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("singleton_roundtrip", result.stdout + result.stderr)
        self.assertCleanBuildError(
            result, f"Bend proof check failed: {directory / 'examples/bend/PROOF.bend'}"
        )
        self.assertFalse(list(directory.rglob("*.so")))

    def test_ill_typed_program_stops_build(self):
        directory, result = self.build("""
def main() -> IO(Unit):
  Python.export_u32(~(x => True{}), "wrong", False{})
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertCleanBuildError(
            result, "Bend compilation failed: module.bend (see Bend output above)"
        )
        self.assertFalse(list(directory.rglob("*.so")))

    def test_native_evaluator_calls_overlap_with_distinct_contexts(self):
        directory, result = self.build(
            """
def tree(+depth: Nat, +seed: U32) -> U32:
  match depth:
    case 0n:
      seed
    case 1n+pred:
      left right = tree(pred, (seed * 2 : U32)) tree(pred, ((seed * 2 : U32) + 1 : U32))
      (left + right : U32)

def main() -> IO(Unit):
  Python.export_u32(~(seed => tree(6n, seed)), "work", True{})
""",
            native_rendezvous=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(
            directory,
            RENDEZVOUS_SETUP
            + """
seeds = (41, 0xfffffff9)
with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(module.work, seed) for seed in seeds]
    # Depth six has 64 leaves: seed*64, seed*64+1, ..., seed*64+63.
    expected = [(seed * 4096 + 2016) & 0xffffffff for seed in seeds]
    assert [future.result() for future in futures] == expected
status = probe.bp_test_status()
assert status == 1, f"native overlap/context rendezvous failed: {status}"
""",
        )

    def test_native_failure_discards_only_the_failed_context(self):
        directory, result = self.build(
            """
def checked_square(+x: U32) -> U32:
  U32.from_nat(Nat.mul(U32.to_nat(x), U32.to_nat(x)))

def main() -> IO(Unit):
  Python.export_u32(~(x => checked_square(x)), "checked_square", True{})
""",
            native_rendezvous=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(
            directory,
            RENDEZVOUS_SETUP
            + """
def fail():
    try:
        module.checked_square(2**32 - 1)
    except RuntimeError as error:
        assert "Nat" in str(error), error
    else:
        raise AssertionError("native failure did not reach Python")

with ThreadPoolExecutor(max_workers=2) as executor:
    failed = executor.submit(fail)
    healthy = executor.submit(module.checked_square, 12)
    failed.result()
    assert healthy.result() == 144
assert probe.bp_test_status() == 1
for value in (0, 1, 12, 65535):
    assert module.checked_square(value) == value * value
""",
        )

    def test_forged_handles_are_rejected(self):
        # With this fixed seed, both forgeries decode outside the arena. Random
        # keys can decode a forgery to a valid index, so rejection is not guaranteed.
        directory, result = self.build(
            """
def forge_constant(request: Python.Call) -> IO(Python.Object):
  IO.pure(Python.Object, Python.PyObject{0})

def forge_next(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{Python.PyObject{id} <> _, _}:
      IO.pure(Python.Object, Python.PyObject{(id + 1 : U32)})
    case _:
      Python.type_error(Python.Object, "expected an argument")

def copy(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{Python.PyObject{id} <> _, _}:
      IO.pure(Python.Object, Python.PyObject{id})
    case _:
      Python.type_error(Python.Object, "expected an argument")

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export("forge_constant", forge_constant, False{})
    Python.export("forge_next", forge_next, False{})
    Python.export("copy", copy, False{})
""",
            deterministic_handles=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(
            directory,
            """
import bend_example as module
values = [object() for _ in range(64)]
for _ in range(200):
    for function in (module.forge_constant, module.forge_next):
        try:
            function(*values)
        except ValueError as error:
            assert "invalid Python object handle" in str(error), error
        else:
            raise AssertionError(f"{function.__name__} accepted a forged handle")
# Rebuilding a handle from its own sealed value is not forging.
assert module.copy(values[0], values[1]) is values[0]
""",
        )

    def test_capturing_export_fails_import(self):
        directory, result = self.build("""
def constant(value: Python.Object, request: Python.Call) -> IO(Python.Object):
  IO.pure(Python.Object, value)

def main() -> IO(Unit):
  do IO<Unit>:
    value : Python.Object <- Python.none()
    Python.export("captured", request => constant(value, request), False{})
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(
            directory,
            """
try:
    import bend_example
except RuntimeError as error:
    assert "captureless" in str(error), error
else:
    raise AssertionError("an export capturing a handle was accepted")
""",
        )

    def test_missing_library_is_vendored_and_its_proofs_run_quietly(self):
        directory, result = self.build(auto_vendor=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("vendoring the Bend library", output)
        self.assertTrue((directory / "examples/bend/python.bend").is_file())
        # The library's own proof output (e.g. its foreign-code notes) stays quiet.
        self.assertNotIn("LAWS.singleton_accepted", output)
        self.run_python(directory, "import bend_example\nassert bend_example.square(12) == 144\n")

    def test_duplicate_exports_fail_import(self):
        directory, result = self.build("""
def main() -> IO(Unit):
  do IO<Unit>:
    Python.export_u32(~(x => x), "duplicate", False{})
    Python.export_u32(~(x => x), "duplicate", False{})
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(
            directory,
            """
try:
    import bend_example
except ValueError as error:
    assert "duplicate" in str(error), error
else:
    raise AssertionError("duplicate exports accepted")
""",
        )


class CompilerDownloadTests(unittest.TestCase):
    """The pinned compiler is fetched, verified, and cached without network access here."""

    def setUp(self):
        from bend_python import build

        self.build_module = build
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        release = self.root / "release"
        (release / "bend/bin").mkdir(parents=True)
        executable = release / "bend/bin/bend"
        executable.write_text("#!/bin/sh\necho 'bend 2.0.28'\n")
        executable.chmod(0o755)
        self.archive = self.root / "bend.tar.gz"
        subprocess.run(["tar", "-czf", str(self.archive), "-C", str(release), "bend"], check=True)
        import hashlib

        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.cache = self.root / "cache"
        patcher = unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BEND_PYTHON_DOWNLOAD", None)

    def release(self, digest):
        return unittest.mock.patch.object(
            self.build_module, "BEND_RELEASE", (self.archive.as_uri(), digest)
        )

    def test_download_verifies_extracts_and_caches(self):
        with self.release(self.digest):
            executable = self.build_module._cached_compiler()
        self.assertEqual(executable, self.cache / "bend-python/bend-2.0.28/bin/bend")
        self.assertEqual(self.build_module._bend_version(str(executable)), "bend 2.0.28")
        self.archive.unlink()  # A second lookup must not download again.
        with self.release(self.digest):
            self.assertEqual(self.build_module._cached_compiler(), executable)

    def test_checksum_mismatch_installs_nothing(self):
        from setuptools.errors import ExecError

        with self.release("0" * 64), self.assertRaisesRegex(ExecError, "checksum mismatch"):
            self.build_module._cached_compiler()
        self.assertEqual(list((self.cache / "bend-python").iterdir()), [])

    def test_download_can_be_disabled(self):
        with (
            self.release(self.digest),
            unittest.mock.patch.dict(os.environ, {"BEND_PYTHON_DOWNLOAD": "0"}),
        ):
            self.assertIsNone(self.build_module._cached_compiler())
        self.assertFalse((self.cache / "bend-python/bend-2.0.28").exists())

    def test_explicit_bend_must_match_the_pinned_version(self):
        from setuptools.errors import ExecError

        wrong = self.root / "wrong-bend"
        wrong.write_text("#!/bin/sh\necho 'bend 2.0.27'\n")
        wrong.chmod(0o755)
        with (
            unittest.mock.patch.dict(os.environ, {"BEND": str(wrong)}),
            self.assertRaisesRegex(ExecError, "2.0.27"),
        ):
            self.build_module._compiler()


class ParallelBuildTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc") and shutil.which("clang"), "requires gcc and clang")
    def test_mixed_extensions_keep_their_compilers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "module.bend").write_text(
                "import Base\nimport ./bend/python.bend as Python\n"
                'def main() -> IO(Unit):\n  Python.export_u32(~(x => x), "identity", False{})\n'
            )
            (directory / "native.c").write_text(
                "#include <Python.h>\n"
                'static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "native", 0, -1};\n'
                "PyMODINIT_FUNC PyInit_native(void) { return PyModule_Create(&module); }\n"
            )
            (directory / "setup.py").write_text("""
import threading
from setuptools import setup, Extension
from bend_python import BendBuildExt, BendExtension
entered, native_done = threading.Event(), threading.Event()
class MixedBuild(BendBuildExt):
    def build_extensions(self):
        for name in ("compiler_so", "linker_so"):
            self.compiler.set_executable(name, ["gcc", *getattr(self.compiler, name)[1:]])
        # Newer setuptools runs commands through call; older ones through spawn.
        method = "call" if hasattr(self.compiler, "call") else "spawn"
        run = getattr(self.compiler, method)
        def rendezvous(command, **kwargs):
            if "-c" in command and any(str(arg).endswith("/module.c") for arg in command):
                entered.set()
                assert native_done.wait(300), "native build timed out"
            return run(command, **kwargs)
        setattr(self.compiler, method, rendezvous)
        super().build_extensions()
    def build_extension(self, extension):
        if extension.name != "native":
            return super().build_extension(extension)
        assert entered.wait(300), "Bend build timed out"
        try:
            return super().build_extension(extension)
        finally:
            native_done.set()
setup(name="mixed", ext_modules=[BendExtension("module", "module.bend"),
      Extension("native", ["native.c"], extra_compile_args=["-fno-tree-loop-distribute-patterns"])],
      cmdclass={"build_ext": MixedBuild})
""")
            environment = {key: value for key, value in os.environ.items() if key != "CC"}
            environment.update(BEND=BEND, PYTHONPATH=str(ROOT / "src"))
            result = subprocess.run(
                [sys.executable, "setup.py", "build_ext", "--inplace", "--parallel=2"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=600,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run(
                [sys.executable, "-c", "import native, module; assert module.identity(42) == 42"],
                cwd=directory,
                check=True,
                timeout=10,
            )

    def test_parallel_discovery_waits_for_complete_vendor_copy(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        from bend_python.build import _bridge_libraries

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "module.bend"
            source.write_text("import ./bend/python.bend as Python\n")
            copied, release = threading.Event(), threading.Event()
            copyfile = shutil.copyfile

            # Pause after python.bend is copied but before python.c is.
            def pause_copy(source, destination, **kwargs):
                result = copyfile(source, destination, **kwargs)
                if Path(source).name == "python.bend":
                    copied.set()
                    self.assertTrue(release.wait(5))
                return result

            with unittest.mock.patch("shutil.copyfile", pause_copy), ThreadPoolExecutor(2) as pool:
                first = pool.submit(_bridge_libraries, source)
                try:
                    self.assertTrue(copied.wait(5))
                    second = pool.submit(_bridge_libraries, source)
                    # Unserialized, it would already have returned without the library.
                    with self.assertRaises(TimeoutError):
                        second.result(timeout=0.5)
                finally:
                    release.set()
                for future in (first, second):
                    self.assertEqual(future.result(), [directory / "bend"])


class PinnedCompilerTests(unittest.TestCase):
    def test_every_bend_download_uses_the_same_release_and_checksum(self):
        sys.path.insert(0, str(ROOT / "src"))
        self.addCleanup(sys.path.remove, str(ROOT / "src"))
        from bend_python.build import BEND_RELEASE

        url, digest = BEND_RELEASE
        for path in ("scripts/bootstrap.sh", "docker/Dockerfile"):
            text = (ROOT / path).read_text()
            with self.subTest(path=path):
                self.assertIn(url, text)
                self.assertIn(digest, text)


class VendorTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "src"))
        self.addCleanup(sys.path.remove, str(ROOT / "src"))
        from bend_python import library_path, vendor

        self.library, self.vendor = library_path(), vendor
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_vendor_copies_and_accepts_identical_files(self):
        target = self.directory / "bend"
        self.assertEqual(self.vendor(target), target.resolve())
        self.assertEqual(
            (target / "python.c").read_bytes(), (self.library / "python.c").read_bytes()
        )
        (target / "unrelated.bend").write_text("kept")
        self.vendor(target)
        self.assertEqual((target / "unrelated.bend").read_text(), "kept")

    def test_vendor_never_writes_through_symlinks(self):
        target = self.directory / "bend"
        target.mkdir()
        outside = self.directory / "outside.c"
        shared = self.directory / "shared.c"
        shared.write_text("shared")
        (target / "python.c").symlink_to(outside)  # dangling
        (target / "python.bend").symlink_to(shared)
        with self.assertRaises(FileExistsError) as raised:
            self.vendor(target)
        self.assertIn("python.c", str(raised.exception))
        self.assertIn("python.bend", str(raised.exception))
        self.assertFalse(outside.exists())
        self.vendor(target, force=True)
        self.assertFalse(outside.exists())
        self.assertEqual(shared.read_text(), "shared")
        for name in ("python.c", "python.bend"):
            self.assertFalse((target / name).is_symlink())
            self.assertEqual((target / name).read_bytes(), (self.library / name).read_bytes())

    def test_vendor_accepts_symlinks_to_the_library(self):
        target = self.directory / "bend"
        target.mkdir()
        (target / "python.c").symlink_to(self.library / "python.c")
        self.vendor(target)
        self.assertTrue((target / "python.c").is_symlink())
        linked = self.directory / "linked"
        linked.symlink_to(self.library, target_is_directory=True)
        self.assertEqual(self.vendor(linked, force=True), self.library.resolve())

    def test_vendor_refuses_modified_files_and_directories(self):
        target = self.directory / "bend"
        target.mkdir()
        (target / "python.bend").write_text("modified")
        (target / "python.c").mkdir()
        with self.assertRaises(FileExistsError):
            self.vendor(target)
        self.assertEqual((target / "python.bend").read_text(), "modified")
        with self.assertRaises(IsADirectoryError):
            self.vendor(target, force=True)


if __name__ == "__main__":
    unittest.main()
