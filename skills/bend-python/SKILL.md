---
name: bend-python
description: Build CPython extensions in Bend 2 with the bend-python SDK, and state and prove laws about them with LAWS.bend/PROOF.bend. Use when writing or debugging .bend code, a BendExtension setup.py, Bend laws and proofs, or when the user mentions bend-python, Bend 2, bendlang, or proving properties of a Python extension.
license: MIT
compatibility: Linux x86_64, CPython 3.10-3.14 (and 3.14t), Clang 14+. First build needs PyPI and GitHub release downloads, or BEND set to a bend 2.0.28 executable.
metadata:
  bend-python-version: "0.1.0"
  bend-version: "2.0.28"
---

# bend-python

bend-python compiles a [Bend 2](https://github.com/bendlang/bend) program into
a CPython extension module. Bend 2 (pinned: **2.0.28**) is a pure, affine,
dependently typed language with *laws*: claims the build proves before it
compiles. A false law is a build error, not a failing test.

This is **Bend 2**, a different language from Bend 1 / HVM (`bend run`,
`fold`, `bend` blocks, `.hvm`). Never use Bend 1 syntax or documentation.

Everything you need is local; you should not need the web:

- `bend guide` prints the language guide; `bend guide effects` the FFI note.
- `bend base` prints the Base library; `bend base List` one name and its subnames.
- [references/LANGUAGE.md](references/LANGUAGE.md): Bend 2 syntax and the checker errors you will hit, with fixes.
- [references/PROOFS.md](references/PROOFS.md): writing laws and proofs, and writing code that can be proved.
- [references/PYTHON.md](references/PYTHON.md): the bend-python API, the build, data exchange, threads, and what is guaranteed.

## Setup

Below, `$SKILL` is this skill's directory (the one containing this file) and
commands run in the project directory.

```sh
uv venv .venv && uv pip install --python .venv/bin/python bend-python==0.1.0 setuptools pytest
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
walking Python objects with many FFI calls.

### 2. Agree on laws with the user before proving them

Laws are the specification. The user owns their statements; you own the
proofs. Before writing or changing any law in LAWS.bend:

1. **Explain the context in chat**: what the code does, what each law
   guarantees and what it does not, which hypotheses it needs (and why they are
   necessary), how hard the proof will be and what code changes it may force.
   Say plainly when a desirable property is false for the implementation (show
   a counterexample; see step 5) or would need machinery that does not exist
   yet (for example, verified bignum arithmetic).
2. **Show the literal law text** in a ```bend block, exactly as it would go in
   LAWS.bend, one law per proposal item:

   ```bend
   # Reversing twice gives the list back.
   law reverse_reverse:
     for xs: List<U32>
     {List.reverse(&1, U32, List.reverse(&1, U32, xs)) == xs : List<U32>}
   ```
3. **Ask** which to adopt, strengthen, weaken or drop, and wait for the answer.
   Offer a ranked recommendation, cheapest and most valuable first.
4. Changing an approved statement later (even only a quantity such as `for x`
   to `for +x`) goes back to the user with the reason.

Unproved but approved claims can live in a separate file (for example
`PENDING.bend`, imported code only, `law` statements without defs). It
type-checks against the code without gating the build: `bend PENDING.bend`
reports exactly "N TODOs found".

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
- Recursion must be structural (left-to-right argument order); otherwise use
  fuel or a `?` (unsafe) def, which the proofs cannot cover.

### 4. Prove, then mutation-check

Write PROOF.bend (`def Laws.<name>(args): ...`), check with `$BEND PROOF.bend`,
then **break the code each law covers and confirm the check fails**, restoring
it afterwards. A law that survives mutation is vacuous or misstated. Read
PROOFS.md first: the rewrite direction of `%e : P` trips everyone (the term to
eliminate must be on the **right** of `e`'s equation; flip with `Equal.sym`).

### 5. Test what is not proved

Test the extension from Python against a reference implementation, ideally
with Hypothesis. Before proposing a law about a composite operation, search for
counterexamples with the built extension (Hypothesis `find`): exact equalities
on simplified or normalized output are often false even when the mathematics
is true.

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
conversions are strict, each call runs in its own runtime instance on one core,
and a native Bend failure raises `RuntimeError` for that call only. Details and
limits: PYTHON.md.

## Common checker errors

| Message (abridged) | Fix |
|---|---|
| `a match cannot scrutinize a computed value` | Pass the value to a helper def and match its parameter |
| `a match on a parameter or field (... consumed binder ...)` | Match parameters in declaration order, in one `match a b:`; no `let` before a match on a parameter |
| `expected a filled definition ... observed <name>` | The callee is below the caller: move it up (or run `reorder_defs.py`) |
| `x (consumed more than once)` | Declare `+x` (param, pattern field `C{+x}`, or law binder `for +x`) |
| `expected : Data, observed : Type` | Use `List<&2, T>` / `+List<T>`; pairs `A & B` are Type, so define a Data record type |
| `expected a term, observed ']'` | In multi-scrutinee cases write `Nil{}` / `Con{h, t}`, not `[]` / `[x]`, after the first pattern |
| `expected a defined name, observed Rat` | Prefix imported names and constructors with the alias: `Big.Rat`, `E.Num{...}` |
| `expected a defined name, observed src/pkg/mod.f` | Inside `mod.bend` name defs `f`, not `Mod.f`; the alias is the importer's |
| `expected @_:A -> ... observed @+a:A -> ...` | Wrap a template argument: `~(a => b => f(a, b))` |
| Goal mismatch after `%e : P` | The goal must be `P` with `e`'s **right** side at `_`; flip with `Equal.sym` |
| Goal stuck on a value after `case _:` | Wildcards do not refine; split every constructor |
| `N TODOs found` | Unproved laws or `?name` holes remain |
