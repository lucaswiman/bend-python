#!/bin/bash
# Check a Bend file and print only the first error (or the success line).
# Usage: bash first_error.sh FILE.bend [bend options...]   (uses $BEND, else `bend`)
set -o pipefail
"${BEND:-bend}" "$@" 2>&1 | awk '
  /is available: run bend update/ { next }
  /^Error/ { err = 1 }
  err && n < 16 { print; n++; next }
  /^All terms check/ { print; exit }
  !err && !/^- / { print }
'
