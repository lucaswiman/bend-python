# bend-python

Write CPython extensions in [Bend 2](https://github.com/bendlang/bend), a pure,
dependently typed language that compiles to native code, and state laws about
your code that the build **proves before it compiles**. A false law is a build
error, not a failing test.

```bend
# fast.bend
import Base
import ./bend/python.bend as Python

def square(+x: U32) -> U32:
  (x * x : U32)

def main() -> IO(Unit):
  Python.export_u32(~(x => square(x)), "square", True{})
```

```bend
# LAWS.bend: the claim
import Base
import ./fast.bend as Fast

law square_wraps:
  {Fast.square(65536) == 0 : U32}
```

```bend
# PROOF.bend: its proof, checked by every build
import Base
import ./LAWS.bend as Laws

def Laws.square_wraps():
  {==}
```

```python
>>> import fast
>>> fast.square(12)
144
>>> fast.square(2**16)           # U32 arithmetic wraps, as the law says
0
>>> fast.square(-1)
OverflowError: can't convert negative value to unsigned int
```

`True{}` releases the GIL while Bend computes, so threads calling `square` run
in parallel. Installed wheels need neither Bend nor a compiler.

**Status: experimental.** Linux x86_64, CPython 3.10–3.14 including free-threaded
3.14t, Bend pinned to 2.0.28. Bend 2 is young: its strings are linked lists of
characters, so text-heavy code is slow and memory-hungry. Benchmark before
relying on it.

## Build an extension

You need Clang and the `bend-python` SDK in your build environment. The first
build downloads the pinned Bend compiler (checksum-verified, cached under
`~/.cache/bend-python`); set `BEND` to use your own, or `BEND_PYTHON_DOWNLOAD=0`
to forbid the download.

`fast.bend` imports `./bend/python.bend`. If that directory is missing, the build
copies the SDK's Bend library there; `python -m bend_python vendor bend` does
the same by hand, for editors and `bend --check-only`.

`setup.py` (setuptools is the only supported build backend):

```python
from setuptools import setup
from bend_python import BendBuildExt, BendExtension

setup(
    name="fast",
    ext_modules=[BendExtension("fast", "fast.bend", proofs=["PROOF.bend"])],
    cmdclass={"build_ext": BendBuildExt},
)
```

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=80", "bend-python==0.1.0"]
build-backend = "setuptools.build_meta"
```

Then `python -m build` or `pip install .`. The build proves the library's laws
and yours, generates C, and compiles it; a false law ends the build with
`error: Bend proof check failed: PROOF.bend` after Bend's diagnostic. Dotted
names such as `mypackage._native` work. Ship your `.bend` files in the sdist
(`include *.bend` in `MANIFEST.in`).
[`examples/`](https://github.com/lucaswiman/bend-python/tree/main/examples) is a
complete project built this way.

## The Python interface

An export receives a `Python.Call` (positional arguments and a kwargs dict) and
returns `IO(Python.Object)`. A `Python.Object` is a handle to any Python object:
passing one through Bend preserves identity, aliases, cycles and precision,
with no serialization.

```bend
def echo(request: Python.Call) -> IO(Python.Object):
  Python.unary(request)                  # exactly one positional argument

# in main:  Python.export("echo", echo, False{})
```

| Bend API | Does |
|---|---|
| `export(name, function, release_gil)` | Expose `Call -> IO(Object)`; `function` must not capture variables |
| `export_u32` / `export_f32` / `export_bool` / `export_string` / `export_binary_u32` | Typed exports from a template `~f`: argument checks and conversions included. Binary templates are curried: `~(x => y => add(x, y))` |
| `unary`, `binary` | Require exactly one/two positional arguments and no keywords |
| `to_u32` `from_u32` `to_f32` `from_f32` `to_bool` `from_bool` `to_string` `from_string` `from_nat` | Convert between Python objects and Bend values |
| `to_bytes`, `from_bytes` | Any contiguous buffer (`bytes`, `bytearray`, `memoryview`, ...) to `List<U32>` of bytes, and back to `bytes` |
| `truthy` | Python truthiness, as `bool(value)` |
| `get_item` `set_item` `getattr` `len` | The Python operations, raising Python's exceptions |
| `tuple` `list` `dict` `empty_dict` `none` | Build Python objects |
| `call(f, args, kwargs)`, `invoke(f, arguments)` | Call Python, including callbacks that reenter Bend |
| `builtins(name)`, `construct(name, arguments)` | Look up or call a builtin such as `"int"` or `"dict"` |
| `import_module(name)` | Import a module, such as `"operator"` |

Conversions are strict. `to_u32` accepts only `int` in `[0, 2**32)`, not `bool`;
`to_bool` only `bool` (use `truthy` for anything else). `to_f32` accepts only
`float` and rounds to single precision (overflowing to infinity). Strings convert
codepoint by codepoint, lone surrogates included; they are linked lists in Bend,
so prefer `to_bytes` for binary data.
`to_bytes` snapshots a direct `bytearray` while holding its lock on free-threaded
Python. For other mutable buffer exporters, including memoryviews of mutable
storage, callers must prevent concurrent writes while conversion runs.
Wrong types raise `TypeError`, out-of-range values `OverflowError`, and wrong
arity or unexpected keywords in typed exports `TypeError`. Python exceptions
raised inside a call propagate unchanged.

Handles are valid only during the call that created them. The bridge seals each
handle with a per-call key and rejects invalid decoded handles with `ValueError`.
This detects accidental fabrication or reuse from another call probabilistically;
it is not an absolute guarantee or a security boundary.

## Threads and the GIL

Every call runs in its own Bend runtime instance (heap, stack and allocator),
so threads call the same extension concurrently, and callbacks can reenter it.
The third argument to `export` sets the GIL policy: `True{}` releases the GIL
during pure Bend evaluation, `False{}` holds it. Free-threaded builds always
detach during evaluation and never enable the GIL. The bridge reattaches before
every Python operation. Each call uses one CPU core; Bend's own parallel
scheduler and GPU backends are not enabled.

## What is proved, and what is trusted

The build refuses to compile unless every law checks:

- **Argument checks.** Typed exports accept exactly the right number of
  arguments and request a `TypeError` for every other count
  ([`bend/LAWS.bend`](https://github.com/lucaswiman/bend-python/blob/main/bend/LAWS.bend)).
- **Thread protocol.** A model of the C driver proves that Python operations
  happen only while attached and never during native evaluation, that every
  call returns attached (failures included), and that free-threaded builds
  detach ([`bend/THREAD_LAWS.bend`](https://github.com/lucaswiman/bend-python/blob/main/bend/THREAD_LAWS.bend)).
- **Runtime leases.** In the same model, a call with any number of evaluation
  rounds owns one instance and changes no other, a lease cannot be returned
  twice, a native failure in any round poisons it, and only healthy, small
  instances are cached for reuse.

Each model was mutation-tested: breaking any of these rules fails a law. The
laws are about models and pure Bend code. **Nothing proves that the C bridge
implements the model**, or anything about CPython, reference counting, or the
generated runtime; those are covered by tests. The table maps each invariant to
its evidence:

| Invariant | Evidence |
|---|---|
| Python API only while attached, never during evaluation | Model proved (`native_effect_rejected`, `native_only_leaves`); C asserts attachment (and the GIL on GIL builds) at every boundary |
| Every call returns attached, including after native failures | Model proved for any slot, rounds and outcomes (`native_returns_attached`, `invocation_returns_attached`); C's `setjmp` placement trusted |
| One call per runtime instance | Model proved (`acquisition_requires_available`, `leased_invocation_rejected`, `double_return_rejected`); C's pool mutex trusted; concurrency test |
| A native failure affects only its instance | Model proved (`failure_isolated`); C unmaps poisoned instances; test |
| Failed or large instances are never reused | Model proved (`failure_poisons_lease`, `reusable_requires_*`); test for memory release |
| Wrong arity is a `TypeError` | Proved for the parsers and wrappers (`*_exact_arity`, `*_complete`, `*_rejected`); keyword rejection tested |
| Accidental forged or stale handles are detected | Sealing in C; tests. Rejection is probabilistic: 32-bit collisions remain possible. Hostile Bend code can import its own C |
| Exports cannot capture variables | Checked by C at import; test |
| Objects cross unchanged; conversions are strict | C; tests |

## Limits

- Main interpreter only; subinterpreters are rejected.
- A native Bend failure (such as `Nat` overflow) raises `RuntimeError` and
  discards that runtime instance; other calls are unaffected. Native crashes and
  stack exhaustion still kill the process.
- Each active instance reserves about 10 GiB of *virtual* memory, so `ulimit -v`
  or strict overcommit can make calls fail. Up to eight idle instances are
  cached; an instance whose heap grew past 32 MiB is released instead.
- Bend's C runtime is private API. The build pins 2.0.28 and refuses to patch a
  runtime that has changed.

## Developing this repository

```sh
bash scripts/bootstrap.sh              # Python 3.14, uv venv, checksum-verified Bend
uv pip install --python .venv/bin/python --no-build-isolation -e .
(cd examples && CC=clang ../.venv/bin/python setup.py build_ext --inplace)
.venv/bin/python -m unittest discover -s tests
uvx prek install                       # ruff, file hygiene, and proof gates on commit
```

[`docker/Dockerfile`](https://github.com/lucaswiman/bend-python/blob/main/docker/Dockerfile)
builds the pure SDK wheel once, then builds, `auditwheel`-repairs and tests the
example for CPython 3.10–3.14 and 3.14t with the image's current Clang. Select
versions with `-e PYTHON_TAGS=cp314-cp314t`.

```sh
docker build -f docker/Dockerfile -t bend-python-manylinux .
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/io:ro" -v "$PWD/wheelhouse:/wheelhouse" bend-python-manylinux
```

[CI](https://github.com/lucaswiman/bend-python/blob/main/.github/workflows/wheels.yml)
runs that recipe per version, `prek`, and `twine check` on the SDK. Record changes
under "Unreleased" in [`CHANGELOG.md`](https://github.com/lucaswiman/bend-python/blob/main/CHANGELOG.md).
To release, move them to a dated `## [X.Y.Z] - YYYY-MM-DD` section matching the
project version and publish a GitHub release tagged `vX.Y.Z`; the
[release workflow](https://github.com/lucaswiman/bend-python/blob/main/.github/workflows/release.yml)
checks both, then publishes the SDK to PyPI by trusted publishing, with
provenance attestations. [`AGENTS.md`](https://github.com/lucaswiman/bend-python/blob/main/AGENTS.md)
has notes for contributors and coding agents.

## License

[MIT](https://github.com/lucaswiman/bend-python/blob/main/LICENSE).
