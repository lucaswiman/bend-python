"""Build native Python extensions whose application code is written in Bend."""

from .library import get_include, library_path, vendor

BEND_VERSION = "2.0.28"

__all__ = [
    "BEND_VERSION",
    "BendBuildExt",
    "BendExtension",
    "get_include",
    "library_path",
    "vendor",
]


def __getattr__(name):
    # Installing a binary wheel or vendoring its Bend sources needs no build
    # dependencies. Only extension authors need setuptools in their build env.
    if name in {"BendBuildExt", "BendExtension"}:
        from . import build

        return getattr(build, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
