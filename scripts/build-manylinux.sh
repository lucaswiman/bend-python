#!/usr/bin/env bash
# Run inside docker/Dockerfile; mount the source read-only at /io.
# Builds the pure SDK wheel once, then builds, repairs, and tests the example
# extension for each ABI against that installed SDK wheel.
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

# Each build gets a clean source tree; never reuse host-built or vendored files.
copy_source() {
  mkdir -p "$1"
  tar -C "$source_dir" --exclude=.git --exclude=.tools --exclude=.venv \
    --exclude=build --exclude=dist --exclude=wheelhouse --exclude='*.so' \
    --exclude='*.egg-info' --exclude=__pycache__ --exclude=./examples/bend -cf - . \
    | tar -C "$1" -xf -
}

for tag in "${python_tags[@]}"; do
  if [[ ! -x /opt/python/$tag/bin/python ]]; then
    echo "Interpreter $tag is absent from this manylinux image." >&2
    exit 1
  fi
done

copy_source "$work/sdk"
"/opt/python/${python_tags[0]}/bin/python" -m build --outdir "$work/sdk-dist" "$work/sdk"
sdk_wheels=("$work/sdk-dist"/bend_python-*-py3-none-any.whl)
if [[ ${#sdk_wheels[@]} -ne 1 || ! -f ${sdk_wheels[0]} ]]; then
  echo "Expected exactly one pure bend-python SDK wheel." >&2
  exit 1
fi
sdk_wheel=${sdk_wheels[0]}

for tag in "${python_tags[@]}"; do
  python=/opt/python/$tag/bin/python
  stage=$work/$tag
  copy_source "$stage/project"
  "$python" -m venv "$stage/venv"
  test_python=$stage/venv/bin/python
  "$test_python" -m pip install --no-index --no-deps "$sdk_wheel"
  "$test_python" -m pip install 'setuptools>=80' build
  # --no-isolation uses the installed SDK wheel; the wheel is built from an sdist.
  "$test_python" -m build --no-isolation --outdir "$stage/dist" "$stage/project/examples"
  auditwheel repair --plat manylinux_2_28_x86_64 \
    --wheel-dir "$stage/repaired" "$stage/dist"/*.whl
  "$test_python" -m pip install --no-index --no-deps "$stage/repaired"/*.whl
  (
    cd "$stage"
    unset PYTHONPATH
    "$test_python" -c 'import pathlib, sys, sysconfig; import bend_example; assert pathlib.Path(bend_example.__file__).is_relative_to(sys.prefix); assert not sysconfig.get_config_var("Py_GIL_DISABLED") or not sys._is_gil_enabled()'
    "$test_python" -m unittest discover -s "$stage/project/tests"
  )
  cp "$stage/repaired"/*.whl "$wheelhouse/"
done
cp "$sdk_wheel" "$wheelhouse/"
