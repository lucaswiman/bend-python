#!/usr/bin/env bash
# Check the library and example proof gates with the pinned Bend compiler.
set -euo pipefail
cd "$(dirname "$0")/.."
bend=${BEND:-.tools/bend/bin/bend}
if [[ ! -x $bend ]]; then
  echo "Bend not found at $bend; run scripts/bootstrap.sh or set BEND." >&2
  exit 1
fi
if [[ -n ${PYTHON:-} ]]; then
  python=$PYTHON
elif [[ -x .venv/bin/python ]]; then
  python=.venv/bin/python
else
  python=python3
fi
PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" "$python" - "$bend" <<'PY'
from pathlib import Path
import os
import sys

from bend_python.build import _compiler, check_proof
from bend_python.proof_trust import SDK_PROOF_TRUST

os.environ["BEND"] = sys.argv[1]
compiler = _compiler()
for proof in ("bend/PROOF.bend", "bend/THREAD_PROOF.bend", "examples/PROOF.bend"):
    trust = SDK_PROOF_TRUST[Path(proof).name] if Path(proof).parent == Path("bend") else None
    check_proof(compiler, proof, trust=trust, quiet=True)
PY
