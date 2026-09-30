#!/usr/bin/env python3
"""Scaffold a bend-python project that builds, proves one law, and passes its test.

Usage: python3 new_project.py DIRECTORY PACKAGE

Creates DIRECTORY with a pure Bend module (logic.bend), an entry point that
exports it to Python (core.bend), LAWS.bend/PROOF.bend, a thin Python wrapper,
and a test. Then:

    cd DIRECTORY
    python -m venv .venv && .venv/bin/pip install bend-python==0.1.0 setuptools pytest
    .venv/bin/python -m bend_python vendor src/PACKAGE/bend
    CC=clang .venv/bin/python setup.py build_ext --inplace
    PYTHONPATH=src .venv/bin/python -m pytest
"""

import re
import sys
from pathlib import Path

FILES = {
    "pyproject.toml": """\
[build-system]
requires = ["setuptools>=80", "bend-python==0.1.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{package}"
version = "0.1.0"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
{package} = ["*.bend"]
""",
    "setup.py": """\
from setuptools import setup

from bend_python import BendBuildExt, BendExtension

setup(
    ext_modules=[
        BendExtension("{package}._core", "src/{package}/core.bend", proofs=["PROOF.bend"]),
    ],
    cmdclass={{"build_ext": BendBuildExt}},
)
""",
    "MANIFEST.in": """\
include src/{package}/*.bend
include LAWS.bend PROOF.bend
""",
    ".gitignore": """\
.venv/
build/
dist/
*.egg-info/
__pycache__/
*.so
# Vendored by bend-python at build time.
src/{package}/bend/
""",
    "src/{package}/logic.bend": """\
import Base

# Pure logic, importable by LAWS.bend without the Python bridge.

# The sum of the values, wrapping modulo 2^32, added to acc.
def sum(values: List<U32>, acc: U32) -> U32:
  match values:
    case []:
      acc
    case head <> tail:
      sum(tail, (acc + head : U32))
""",
    "src/{package}/core.bend": """\
import Base
import ./bend/python.bend as Python
import ./logic.bend as Logic

# checksum(data: bytes) -> int: the sum of the bytes modulo 2^32.
def checksum(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    data : List<U32> <- Python.to_bytes(value)
    Python.from_u32(Logic.sum(data, 0))

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export("checksum", checksum, True{{}})
""",
    "src/{package}/__init__.py": '''\
"""Python API. Keep this a thin wrapper: the logic lives in Bend."""

from ._core import checksum

__all__ = ["checksum"]
''',
    "LAWS.bend": """\
import Base
import ./src/{package}/logic.bend as Logic

# Checksums split over concatenation, so data can be summed in chunks.
law sum_append:
  for xs: List<U32>
  for ys: List<U32>
  for acc: U32
  {{Logic.sum(List.append(&1, U32, xs, ys), acc) == Logic.sum(ys, Logic.sum(xs, acc)) : U32}}
""",
    "PROOF.bend": """\
import Base
import ./LAWS.bend as Laws
import ./src/{package}/logic.bend as Logic

def Laws.sum_append(xs, ys, acc):
  match xs:
    case []:
      {{==}}
    case head <> tail:
      Laws.sum_append(tail, ys, (acc + head : U32))
""",
    "tests/test_{package}.py": """\
from {package} import checksum


def test_checksum():
    assert checksum(b"") == 0
    assert checksum(bytes([1, 2, 3])) == 6
    data = bytes(range(256)) * 100
    assert checksum(data) == sum(data) % 2**32
""",
}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    root, package = Path(sys.argv[1]), sys.argv[2]
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", package):
        sys.exit("PACKAGE must be a lowercase Python identifier")
    if root.exists() and any(root.iterdir()):
        sys.exit(f"{root} exists and is not empty")
    for name, template in FILES.items():
        path = root / name.format(package=package)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template.format(package=package))
    steps = __doc__.split("Then:", 1)[1].replace("DIRECTORY", str(root)).replace("PACKAGE", package)
    print(f"Created {root}. Then:{steps}")


if __name__ == "__main__":
    main()
