#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $(uname -s)-$(uname -m) != Linux-x86_64 ]]; then
  echo 'This bootstrap currently supports Linux x86_64 only.' >&2
  exit 1
fi
mkdir -p .tools
archive=.tools/bend-2.0.40-linux-x64.tar.gz
if [[ ! -f $archive ]]; then
  curl --fail --location --output "$archive" \
    https://github.com/bendlang/bend/releases/download/v2.0.40/bend-2.0.40-linux-x64.tar.gz
fi
echo "c9ff8d67bd903fb6ad9a26b43d8a62c01021de82a44d55ce4a3dcd4732113d6b  $archive" | sha256sum --check
tar --warning=no-unknown-keyword -xzf "$archive" -C .tools
export UV_CACHE_DIR="$PWD/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
if [[ ! -d .venv ]]; then
  uv venv --python 3.14 .venv
fi
uv pip install --python .venv/bin/python 'setuptools>=80' build
