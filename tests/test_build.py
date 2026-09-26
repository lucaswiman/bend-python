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


class BuildTests(unittest.TestCase):
    def build(self, module=None, break_proof=False, omit_thread_proof=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        for filename in ("setup.py", "pyproject.toml"):
            shutil.copy(ROOT / filename, directory)
        for name in ("bend", "examples", "src"):
            shutil.copytree(ROOT / name, directory / name,
                            ignore=shutil.ignore_patterns("*.so", "__pycache__"))
        if module is not None:
            (directory / "examples/module.bend").write_text(
                'import Base\nimport ../bend/python.bend as Python\n' + module
            )
        if break_proof:
            arithmetic = directory / "examples/arithmetic.bend"
            arithmetic.write_text(arithmetic.read_text().replace("x * x", "x + 1"))
        if omit_thread_proof:
            (directory / "bend/THREAD_PROOF.bend").unlink()
        result = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd=directory, env={**os.environ, "CC": "clang", "BEND": BEND},
            text=True, capture_output=True, timeout=60,
        )
        return directory, result

    def run_python(self, directory, source):
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", source], cwd=directory,
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

    def test_native_failure_becomes_exception_and_poison(self):
        directory, result = self.build('''
def checked_square(+x: U32) -> U32:
  U32.from_nat(Nat.mul(U32.to_nat(x), U32.to_nat(x)))

def main() -> IO(Unit):
  Python.export_u32(~(x => checked_square(x)), "checked_square", True{})
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_python(directory, '''
import bend_example as module
assert module.checked_square(12) == 144
try:
    module.checked_square(2**32 - 1)
except RuntimeError as error:
    assert "Nat" in str(error), error
else:
    raise AssertionError("native failure did not reach Python")
try:
    module.checked_square(12)
except RuntimeError as error:
    assert "previous failure" in str(error), error
else:
    raise AssertionError("corrupt runtime was reused")
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
