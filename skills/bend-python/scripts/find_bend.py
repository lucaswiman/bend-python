#!/usr/bin/env python3
"""Print the path of the pinned Bend compiler that bend-python builds with.

Run it with the Python environment that has bend-python installed. It honours
$BEND, then a matching `bend` on PATH, then downloads the checksum-verified
release into ~/.cache/bend-python (unless BEND_PYTHON_DOWNLOAD=0).
"""

import sys

try:
    from bend_python.build import _compiler
except ImportError:
    sys.exit("bend-python is not installed in this Python: pip install bend-python")

try:
    print(_compiler())
except Exception as error:  # the SDK raises setuptools errors with a clear message
    sys.exit(f"find_bend: {error}")
