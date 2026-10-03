"""Build benchmark exports against the installed checkout SDK."""

from pathlib import Path

from setuptools import setup

from bend_python import BendBuildExt, BendExtension, vendor

vendor(Path(__file__).parent / "bend", force=True)

setup(
    name="bend-python-benchmark",
    version="0.0.0",
    ext_modules=[BendExtension("bend_benchmark", "module.bend")],
    cmdclass={"build_ext": BendBuildExt},
)
