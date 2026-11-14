#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $(uname -s)-$(uname -m) != Linux-x86_64 ]]; then
  echo 'This bootstrap currently supports Linux x86_64 only.' >&2
  exit 1
fi
mkdir -p .tools
archive=.tools/bend-2.0.39-linux-x64.tar.gz
if [[ ! -f $archive ]]; then
  curl --fail --location --output "$archive" \
    https://github.com/bendlang/bend/releases/download/v2.0.39/bend-2.0.39-linux-x64.tar.gz
fi
echo "6cf462fd8f70b094a4950ebb709a9c4ffd8f1d1189d9cedf1fd63cd5d041c7ab  $archive" | sha256sum --check
tar --warning=no-unknown-keyword -xzf "$archive" -C .tools
export UV_CACHE_DIR="$PWD/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
if [[ ! -d .venv ]]; then
  uv venv --python 3.14 .venv
fi
uv pip install --python .venv/bin/python 'setuptools>=80' build
