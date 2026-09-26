"""Build the reusable SDK: pure Python plus the canonical Bend library."""

from pathlib import Path
import sys

from setuptools import setup

sys.path.insert(0, str(Path(__file__).parent / "src"))
from bend_python.build import BuildPyWithBendLibrary


setup(cmdclass={"build_py": BuildPyWithBendLibrary})
