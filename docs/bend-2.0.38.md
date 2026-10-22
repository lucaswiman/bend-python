# Bend 2.0.38 upgrade validation

This upgrades the pinned compiler from 2.0.28 to the official
[2.0.38 release](https://github.com/bendlang/bend/releases/tag/v2.0.38).
Linux x86_64 archive SHA256:
`f19d088b079c9e1a8834ecd153b7d58c11a5f8f9ab1bdb78dbc96a6bf49aaa81`.
Extracted compiler SHA256:
`98395758bc911af94c15a67bf42d35b6c3e029562c447b4f4a843e3ef6e243f0`.

The private C integration follows the new allocator declarations, FID spelling,
error path and runtime-state declarations. Initial heap size is recorded before
layout can fail; successful growth still updates the full mapping size. Exact
patch contexts reject unexpected runtime layouts.

One diagnostic classifier owns proof acceptance for builds and the shell gate.
Pure roots require strict success. SDK `PROOF.bend` permits exactly 71 printed
foreign-dependent definitions; it reports acceptance as type-checked with
declared trust and retains Bend's original failed-verdict diagnostic. The four
foreign-dependent selector laws were already present under 2.0.28. All 14 SDK
Bend source files and their law statements are unchanged. Application policies
must be explicit; neither type errors nor unfinished proofs are accepted.
Printed labels can collide, so this is not authenticated internal identity,
BendTT kernel approval, or a proof of C/CPython correspondence.

Fresh forced builds and full SDK suites passed after the cleanup fix:

| Interpreter | Result | Skips |
| --- | --- | --- |
| CPython 3.14.3, free-threaded | 84 passed | 16 optional scientific integrations |
| CPython 3.11.14, traditional GIL | 81 passed | Same 16, two free-threaded tests, one unavailable interpreter API |

Both used Clang 14 and the same pinned compiler. NumPy, SciPy and PyTorch were
absent; their integration tests remain unvalidated locally. Both proof scripts
passed their declared-trust/strict policies. Runtime tests cover identity,
handles, wide segments, concurrency, callbacks, cache reuse, failure isolation,
buffer permissions and mixed C/Bend builds. Initial-layout panic cleanup was
checked in the actual emitted C, rather than through injected allocation failure.

Real compiler mutation controls accepted the baseline under declared trust and
rejected a false selector law, an unfilled law, a new unsafe definition and a new
foreign wrapper. A real subprocess regression detects CRLF/CR normalization:
it fails against the old text-capture path and passes with raw-byte capture.
The shell gate rejects an actual 2.0.28 executable before checking proofs.
Ruff and source whitespace checks pass. Fresh integration and private-runtime
reviews found and resolved the newline and initial-cleanup defects.

[Validation receipts and original logs](validation/bend-2.0.38/FINAL_VALIDATION.json)
retain source, compiler and environment hashes. [Checker controls](validation/bend-2.0.38/PROOF_CONTROLS.json)
and [static runtime review](validation/bend-2.0.38/RUNTIME_REVIEW.json) have
separate scopes. `FILES.json` covers the selected evidence. Historical absolute
paths refer to temporary checkouts; full native test logs, generated C and native
binaries are not published here. Exact checker streams are packed in
`PROOF_CONTROL_STREAMS.json`. The checked function is unchanged since those
controls; the later runtime cleanup fix was verified by fresh native builds.
Reproduce the main workflow with an SDK development
environment, the pinned `BEND`, `CC=clang`, an examples build and unittest discovery.

This SDK validation establishes no application proof result or performance gain.
