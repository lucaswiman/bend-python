"""Build the reusable SDK and its executable example."""

from pathlib import Path
import sys

from setuptools import setup

sys.path.insert(0, str(Path(__file__).parent / "src"))
from bend_python import BendBuildExt, BendExtension
from bend_python.build import BuildPyWithBendLibrary


setup(
    ext_modules=[BendExtension(
        "bend_example", "examples/module.bend", proofs=["examples/PROOF.bend"],
    )],
    cmdclass={"build_ext": BendBuildExt, "build_py": BuildPyWithBendLibrary},
)
