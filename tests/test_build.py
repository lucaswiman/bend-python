"""Exercise the real proof/compiler/runtime failure boundaries in child processes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BEND = os.environ.get("BEND", str(ROOT / ".tools/bend/bin/bend"))

# Only temporary test extensions contain this rendezvous. It stops the first
# evaluator entry until a second independent call enters, even on one CPU.
# A serializing runtime times out instead of making this a timing/speedup test.
NATIVE_RENDEZVOUS = r'''
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
'''

RENDEZVOUS_SETUP = '''
import ctypes
from concurrent.futures import ThreadPoolExecutor
import bend_example as module
probe = ctypes.CDLL(module.__file__)
probe.bp_test_arm.argtypes = []
probe.bp_test_arm.restype = None
probe.bp_test_status.argtypes = []
probe.bp_test_status.restype = ctypes.c_uint
probe.bp_test_arm()
'''


class BuildTests(unittest.TestCase):
    def build(self, module=None, break_proof=False, omit_thread_proof=False,
              native_rendezvous=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        ignored = shutil.ignore_patterns("*.so", "__pycache__", "build", "*.egg-info")
        for name in ("bend", "src"):
            shutil.copytree(ROOT / name, directory / name, ignore=ignored)
        # The example vendors the SDK library itself; never reuse a stale copy.
        shutil.copytree(ROOT / "examples", directory / "examples",
                        ignore=shutil.ignore_patterns("bend", "*.so", "__pycache__", "build"))
        if module is not None:
            (directory / "examples/module.bend").write_text(
                'import Base\nimport ./bend/python.bend as Python\n' + module
            )
        if break_proof:
            arithmetic = directory / "examples/arithmetic.bend"
            arithmetic.write_text(arithmetic.read_text().replace("x * x", "x + 1"))
        if omit_thread_proof:
            (directory / "bend/THREAD_PROOF.bend").unlink()
        if native_rendezvous:
            shim = directory / "bend/python.c"
            source = shim.read_text()
            marker = "static Term bp_apply(Env e, Term function, Term argument) {"
            self.assertEqual(source.count(marker), 1)
            source = source.replace(marker, NATIVE_RENDEZVOUS + "\n" + marker)
            evaluator = "  return corpus_eval(e.mem, term_tsk(FID_CLO_APPLY, at));"
            self.assertEqual(source.count(evaluator), 1)
            shim.write_text(source.replace(
                evaluator, "  bp_test_enter(e.mem);\n" + evaluator,
            ))
        # Build the example project against this checkout's SDK sources.
        result = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd=directory / "examples",
            env={**os.environ, "CC": "clang", "BEND": BEND, "PYTHONPATH": str(directory / "src")},
            text=True, capture_output=True, timeout=60,
        )
        return directory, result

    def run_python(self, directory, source):
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", source], cwd=directory / "examples",
            env={**os.environ, "PYTHONPATH": str(directory / "src")},
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_false_law_stops_build(self):
        directory, result = self.build(break_proof=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stdout + result.stderr)
        self.assertFalse(list(directory.rglob("*.so")))

    def test_missing_thread_proof_stops_build(self):
        directory, result = self.build(omit_thread_proof=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("THREAD_PROOF.bend", result.stdout + result.stderr)
        self.assertFalse(list(directory.rglob("*.so")))

    def test_native_evaluator_calls_overlap_with_distinct_contexts(self):
        directory, result = self.build('''
def tree(+depth: Nat, +seed: U32) -> U32:
  match depth:
    case 0n:
      seed
    case 1n+pred:
      left right = tree(pred, (seed * 2 : U32)) tree(pred, ((seed * 2 : U32) + 1 : U32))
      (left + right : U32)

def main() -> IO(Unit):
  Python.export_u32(~(seed => tree(6n, seed)), "work", True{})
''', native_rendezvous=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, RENDEZVOUS_SETUP + '''
seeds = (41, 0xfffffff9)
with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(module.work, seed) for seed in seeds]
    # Depth six has 64 leaves: seed*64, seed*64+1, ..., seed*64+63.
    expected = [(seed * 4096 + 2016) & 0xffffffff for seed in seeds]
    assert [future.result() for future in futures] == expected
status = probe.bp_test_status()
assert status == 1, f"native overlap/context rendezvous failed: {status}"
''')

    def test_native_failure_discards_only_the_failed_context(self):
        directory, result = self.build('''
def checked_square(+x: U32) -> U32:
  U32.from_nat(Nat.mul(U32.to_nat(x), U32.to_nat(x)))

def main() -> IO(Unit):
  Python.export_u32(~(x => checked_square(x)), "checked_square", True{})
''', native_rendezvous=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, RENDEZVOUS_SETUP + '''
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
''')

    def test_forged_handles_are_rejected(self):
        directory, result = self.build('''
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
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, '''
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
''')

    def test_capturing_export_fails_import(self):
        directory, result = self.build('''
def constant(value: Python.Object, request: Python.Call) -> IO(Python.Object):
  IO.pure(Python.Object, value)

def main() -> IO(Unit):
  do IO<Unit>:
    value : Python.Object <- Python.none()
    Python.export("captured", request => constant(value, request), False{})
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, '''
try:
    import bend_example
except RuntimeError as error:
    assert "captureless" in str(error), error
else:
    raise AssertionError("an export capturing a handle was accepted")
''')

    def test_duplicate_exports_fail_import(self):
        directory, result = self.build('''
def main() -> IO(Unit):
  do IO<Unit>:
    Python.export_u32(~(x => x), "duplicate", False{})
    Python.export_u32(~(x => x), "duplicate", False{})
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, '''
try:
    import bend_example
except ValueError as error:
    assert "duplicate" in str(error), error
else:
    raise AssertionError("duplicate exports accepted")
''')


if __name__ == "__main__":
    unittest.main()
