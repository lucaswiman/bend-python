---
name: bend-python
description: Build and debug Bend 2 CPython extensions with the bend-python SDK, including .bend code, BendExtension builds, and LAWS.bend/PROOF.bend contracts. Use for Bend 2 extension work and its laws, proofs, and performance; not Bend 1/HVM or unrelated Python extensions.
license: MIT
compatibility: Linux x86_64, CPython 3.10-3.14 (and 3.14t), Clang 14+. First build needs PyPI and GitHub release downloads, or BEND set to a bend 2.0.28 executable.
metadata:
  bend-python-version: "0.1.0"
  bend-version: "2.0.28"
---

# bend-python

bend-python compiles a [Bend 2](https://github.com/bendlang/bend) program into
a CPython extension module. Bend 2 (pinned: **2.0.28**) is a pure, affine,
dependently typed language with *laws*. Every law imported by a proof gate
needs a checked proof before the extension compiles; passing tests do not fill
unproved laws.

This is **Bend 2**, a different language from Bend 1 / HVM (`bend run`,
`fold`, `bend` blocks, `.hvm`). Never use Bend 1 syntax or documentation.

Everything you need is local; you should not need the web:

- `bend guide` prints the language guide; `bend guide effects` the FFI note.
- `bend base` prints the Base library; `bend base List` one name and its subnames.
- [references/LANGUAGE.md](references/LANGUAGE.md): Bend 2 syntax and the checker errors you will hit, with fixes.
- [references/PROOFS.md](references/PROOFS.md): writing laws and proofs, and writing code that can be proved.
- [references/PYTHON.md](references/PYTHON.md): the bend-python API, the build, data exchange, threads, and what is guaranteed.
- [references/NUMERICAL.md](references/NUMERICAL.md): read when using NumPy, PyTorch, or numerical kernels; borrowed storage, optional BLAS, layouts, and proof boundaries.

## Setup

Below, `$SKILL` is this skill's directory (the one containing this file) and
commands run in the project directory.

```sh
uv venv -p 3.14 .venv && uv pip install --python .venv/bin/python bend-python==0.1.0 setuptools pytest
# or: python3 -m venv .venv && .venv/bin/pip install bend-python==0.1.0 setuptools pytest
export BEND=$(.venv/bin/python $SKILL/scripts/find_bend.py)   # pinned compiler (downloads once)
$BEND guide                                                     # read before writing Bend
```

To start a project, scaffold one that already builds, proves a law and passes
a test, then replace its example:

```sh
python3 $SKILL/scripts/new_project.py myproj mypkg   # prints the next commands
```

The edit, check, build loop (Clang required; the build proves laws first):

```sh
.venv/bin/python -m bend_python vendor src/mypkg/bend   # beside the entry point, once
bash $SKILL/scripts/first_error.sh src/mypkg/core.bend --check-only   # fast type check
bash $SKILL/scripts/first_error.sh PROOF.bend           # laws: must end "All terms check"
CC=clang .venv/bin/python setup.py build_ext --inplace
PYTHONPATH=src .venv/bin/python -m pytest
```

## Workflow

### 1. Split the work

Put logic in Bend and keep Python a thin wrapper: every recursion or tree walk
in Python is both slow and outside what can be proved. Python converts
arguments, picks classes and raises exceptions; Bend computes. For structured
data, pass `bytes` in an encoding you define (see PYTHON.md) rather than
walking Python objects with many FFI calls. For large numerical arrays, use
borrowed storage or existing bulk kernels; see NUMERICAL.md before choosing a
representation or adding a native backend.

### 2. State the contract before implementing and proving it

Laws specify the behavior the user requested. Use existing authorization to
formalize that behavior; do not ask again for routine binder quantities or
proof helpers. If a substantive contract choice is unresolved, explain the
guarantees, hypotheses and proof limits, show the literal law text, and ask
which contract to adopt while continuing independent work. Do not silently
weaken an agreed property to make its proof easy. Show a counterexample when a
desired property is false, or identify missing machinery such as field laws.

For new features, draft laws in a separate file such as `PENDING.bend` before
implementation. Once the referenced definitions exist, check that the claims
type-check with the expected TODOs. Prove them in `PROOF.bend`, then promote
them to `LAWS.bend` and remove the pending copies. Keep unrelated pending
claims intact. Only proved laws belong in the configured build gate.

Specify observable properties independently of the runtime validator. A claim
that a result passes `valid(...)` is too weak if changing `valid` to always
return `True` also changes the law's meaning. State the reconstruction,
normalization, residual, or other promised property explicitly. See PROOFS.md
for certificates and the difference between sound success and total success.

### 3. Implement in Bend

Iterate with `$BEND file.bend --check-only` and read the first error only
(`bash $SKILL/scripts/first_error.sh file.bend`). The same few errors recur; LANGUAGE.md
lists each with its fix. Most importantly:

- `match` only on parameters or pattern-bound variables, in parameter order.
  Branch on a computed value by passing it to a helper def.
- A def may call only defs **above** it (except `@unsafe`/`name?` defs).
  `python3 $SKILL/scripts/reorder_defs.py file.bend` reorders defs to satisfy this.
- Variables are affine: mark reused ones `+x` (Data types only). A matched
  `+n` makes its pattern binders (`1n+p`, `h <> t`) reusable too.
- Recursion must be structural (left-to-right argument order). For descent
  hidden by transformations or callbacks, put input-derived `Nat` fuel first.
  Unsafe defs remain proof dependencies whose termination is unchecked.
- Evaluation is strict: pass an expensive fallback as `Unit -> Result`, not
  as an already computed argument. Check cheap syntax before dense conversion;
  collect factors or terms before canonicalizing once. See LANGUAGE.md.

### 4. Prove, then mutation-check

Write PROOF.bend (`def Laws.<name>(args): ...`), check with `$BEND PROOF.bend`,
then **mutate the behavior each law covers and confirm the proof check fails**
in an isolated copy. Check that the mutated runtime code still type-checks;
syntax or affinity failures do not validate the law. A surviving mutation may
preserve the contract or reveal a weak claim; inspect which. Read
PROOFS.md first: the rewrite direction of `%e : P` trips everyone (the term to
eliminate must be on the **right** of `e`'s equation; flip with `Equal.sym`).

### 5. Test what is not proved

Test the extension from Python against a reference implementation, ideally
with Hypothesis. Before proposing a law about a composite operation, search for
counterexamples with the built extension (Hypothesis `find`): exact equalities
on simplified or normalized output are often false even when the mathematics
is true. Include unsupported and undefined inputs, byte/degree carry boundaries,
and representative large expressions. Compare algebraic identities with an
independent semantic reference as well as checking structural contracts.

### 6. Report honestly

Say which properties are proved (and whether a proof relies on unsafe defs,
which `bend` lists), which are only tested, and what is trusted (the C bridge,
the compiler, CPython). Laws are about Bend code; they never prove the C
callers, CPython, or that the native U32 matches Bend's Word model.

## What bend-python guarantees

Proved at every build (the SDK's own laws): typed exports accept exactly the
right argument count; a model of the thread-attachment and runtime-lease
protocol. Trusted and tested: the C bridge implements that model, object
handles are sealed per call (forged handles are detected probabilistically),
conversions are strict, each call has its own runtime instance with one Bend
worker, and a native Bend failure raises `RuntimeError` for that call only. Details and
limits: PYTHON.md.
