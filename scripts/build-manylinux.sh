#!/usr/bin/env bash
# Run inside docker/Dockerfile; mount the source read-only at /io.
set -euo pipefail
source_dir=$(realpath "${1:-/io}")
wheelhouse=${WHEELHOUSE:-/wheelhouse}
read -r -a python_tags <<< "${PYTHON_TAGS:-cp310-cp310 cp311-cp311 cp312-cp312 cp313-cp313 cp314-cp314 cp314-cp314t}"
export CC=${CC:-clang}
export BEND=${BEND:-/opt/bend/bin/bend}
export PIP_NO_CACHE_DIR=1
mkdir -p "$wheelhouse"
wheelhouse=$(realpath "$wheelhouse")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for tag in "${python_tags[@]}"; do
  python=/opt/python/$tag/bin/python
  if [[ ! -x $python ]]; then
    echo "Interpreter $tag is absent from this manylinux image." >&2
    exit 1
  fi
  stage=$work/$tag
  mkdir -p "$stage/project" "$stage/repaired"
  # Each ABI gets a clean source tree; never reuse a host-built extension.
  tar -C "$source_dir" --exclude=.git --exclude=.tools --exclude=.venv \
    --exclude=build --exclude=dist --exclude=wheelhouse --exclude='*.so' \
    --exclude='*.egg-info' --exclude=__pycache__ -cf - . \
    | tar -C "$stage/project" -xf -
  "$python" -m build --outdir "$stage/dist" "$stage/project"
  auditwheel repair --plat manylinux_2_28_x86_64 \
    --wheel-dir "$stage/repaired" "$stage/dist"/*.whl
  "$python" -m venv "$stage/venv"
  test_python=$stage/venv/bin/python
  "$test_python" -m pip install --no-index --no-deps "$stage/repaired"/*.whl
  "$test_python" -m pip install 'setuptools>=80'
  (
    cd "$stage"
    unset PYTHONPATH
    "$test_python" -c 'import pathlib, sys, sysconfig; import bend_example; assert pathlib.Path(bend_example.__file__).is_relative_to(sys.prefix); assert not sysconfig.get_config_var("Py_GIL_DISABLED") or not sys._is_gil_enabled()'
    "$test_python" -m unittest discover -s "$stage/project/tests"
  )
  cp "$stage/repaired"/*.whl "$wheelhouse/"
done
