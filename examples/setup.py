"""Build the executable example against the installed (or checkout) SDK."""

from pathlib import Path

from setuptools import setup

from bend_python import BendBuildExt, BendExtension, vendor

# An untracked copy for Bend's relative imports; always the SDK's current files.
vendor(Path(__file__).parent / "bend", force=True)

setup(
    ext_modules=[BendExtension("bend_example", "module.bend", proofs=["PROOF.bend"])],
    cmdclass={"build_ext": BendBuildExt},
)
