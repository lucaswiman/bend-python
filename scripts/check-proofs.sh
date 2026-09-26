#!/usr/bin/env bash
# Check the library and example proof gates with the pinned Bend compiler.
set -euo pipefail
cd "$(dirname "$0")/.."
bend=${BEND:-.tools/bend/bin/bend}
if [[ ! -x $bend ]]; then
  echo "Bend not found at $bend; run scripts/bootstrap.sh or set BEND." >&2
  exit 1
fi
for proof in bend/PROOF.bend bend/THREAD_PROOF.bend examples/PROOF.bend; do
  "$bend" "$proof" --check-only >/dev/null || { "$bend" "$proof" --check-only; exit 1; }
done
