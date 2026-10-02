# bend-python: build, API, data exchange and guarantees

## Contents

1. Project layout and build
2. The Bend-side API
3. Exchanging data with Python
4. Threads and the GIL
5. Guarantees, trust and limits
6. Troubleshooting

## 1. Project layout and build

```
myproj/
  pyproject.toml     # build-system requires setuptools>=80 and bend-python==0.1.0
  setup.py           # BendExtension + BendBuildExt
  LAWS.bend  PROOF.bend
  src/mypkg/__init__.py      # thin Python wrapper
  src/mypkg/core.bend        # entry point: main registers exports
  src/mypkg/bend/            # vendored library (untracked; add to .gitignore)
  tests/
```

```python
# setup.py
from setuptools import setup
from bend_python import BendBuildExt, BendExtension

setup(
    ext_modules=[BendExtension("mypkg._core", "src/mypkg/core.bend", proofs=["PROOF.bend"])],
    cmdclass={"build_ext": BendBuildExt},
)
```

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=80", "bend-python==0.1.0"]
build-backend = "setuptools.build_meta"

[project]
name = "mypkg"
version = "0.1.0"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
mypkg = ["*.bend"]
```

- The entry point imports `./bend/python.bend as Python`. If `bend/` is missing
  the build vendors it; `python -m bend_python vendor src/mypkg/bend` does it by
  hand (needed for `bend --check-only` and editors). Do not commit it.
- `CC=clang python setup.py build_ext --inplace` for development;
  `pip install .` / `python -m build` for distribution (ship `.bend` files in
  the sdist with `MANIFEST.in`). Installed wheels need neither Bend nor Clang.
- Setuptools is the only supported backend. The extension name must be a dotted
  identifier (`mypkg._core`).
- The compiler: `$BEND` (must report `bend 2.0.28`), else `bend` on PATH at that
  version, else a checksum-verified download cached in
  `~/.cache/bend-python/bend-2.0.28/bin/bend`. `BEND_PYTHON_DOWNLOAD=0` forbids
  the download.
- The build: checks the SDK's and your proofs, generates C, patches the runtime
  for per-call instances, compiles with Clang.

## 2. The Bend-side API

```bend
import Base
import ./bend/python.bend as Python

def double(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)   # one positional arg, no kwargs
    n : U32 <- Python.to_u32(value)
    Python.from_u32((n * 2 : U32))

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export("double", double, True{})          # True{}: release the GIL
    Python.export_u32(~(x => (x + 1 : U32)), "inc", False{})
```

| API | Does |
|---|---|
| `export(name, f, release_gil)` | Expose `f : Python.Call -> IO(Python.Object)`; `f` must be a top-level def (no captured variables) |
| `export_u32` `export_f32` `export_bool` `export_string` | Typed unary exports from a template `~f : T -> T`, with argument checks |
| `export_binary_u32(~f, name, gil)` | Curried template: `~(x => y => add(x, y))` |
| `unary(request)`, `binary(request)` | Exactly one / two positional arguments, no keywords (`TypeError` otherwise) |
| `PyCall{args, kwargs}` | Match a request directly: `case Python.PyCall{a <> rest, kw}:`; `require_no_kwargs(kw)` |
| `to_u32` `from_u32` `to_f32` `from_f32` `to_bool` `from_bool` `to_string` `from_string` `from_nat` | Strict conversions (`to_u32`: int in `[0, 2**32)`, not bool; `to_bool`: bool only) |
| `to_bytes(o) -> IO(List<U32>)`, `from_bytes(List<U32>)` | Any contiguous buffer to bytes and back (values must be < 256) |
| `truthy(o)` | `bool(o)` |
| `get_item` `set_item` `getattr(o, "name")` `len(o) -> IO(Nat)` | Python operations; Python exceptions propagate |
| `tuple(list)` `list(list)` `dict(pairs)` `empty_dict()` `none()` | Build Python objects |
| `call(f, args_tuple, kwargs)`, `invoke(f, [args])` | Call Python; `invoke` uses positional vectorcall without intermediate Python containers |
| `identical(left, right)` | Python object identity, without calling equality or truthiness |
| `builtins("int")`, `construct("int", [args])`, `import_module("math")` | Look up builtins and modules |
| `type_error(T, "message")` | Raise `TypeError` (the only exception constructor; for others, call Python) |
| `arity`, `argument`, `singleton`, `pair` | Pure helpers on argument lists |

Python sees each export as a module-level function of the extension
(`mypkg._core.double(21) == 42`).

## 3. Exchanging data with Python

For NumPy/PyTorch storage, custom F32 maps, or bulk numerical kernels, read
[NUMERICAL.md](NUMERICAL.md). Its borrow and layout contracts avoid copying
enormous arrays into the ordinary bytes/list interchange path below.

- `Python.Object` handles preserve identity and precision but each operation is
  an FFI call, and handles are valid only during the call that produced them:
  never store one for a later call.
- For structured values define a byte encoding and pass `bytes` both ways:
  `to_bytes` in, `from_bytes(...)` out. Python then only holds bytes and never
  recurses. A **postfix encoding with a stack-machine decoder** is structurally
  recursive and makes `decode(encode(x)) == x` provable:
  - `[16, v]` pushes a value, `17` pushes a mark, tags pop what they need
    (for example "collect values down to the mark").
  - Put the node's class tag last so Python can read `enc[-1]` without
    decoding.
- Canonical encodings make Python equality and hashing a bytes comparison;
  prove the round trip so that "equal bytes" means "equal value".
- `to_bytes` returns affine `List<U32>`; copy it into `List<&2, U32>` before
  reusing, and copy back before `from_bytes`.
- Big integers: pass `(negative: bool, abs(n).to_bytes(length, "little"))`;
  return them by calling Python's `int.from_bytes` from Bend
  (`Python.getattr(Python.builtins("int"), "from_bytes")`, then `invoke`).
- 64-bit floats: Bend has only F32. Walk the structure in Bend and call
  `operator.add`, `cmath.sin` and so on through `Python.invoke`.
- Errors: keep unsupported input, undefined values, failed certificates, and
  successful results distinct in Bend, then map them to useful Python errors.
  A thin wrapper can decode separate sentinels (for example `False` versus
  `None`) using `is`; do not conflate either with a successful zero or empty
  encoding. `Python.type_error` handles argument errors directly.
- After changing Bend code, rebuild the extension before Python tests or
  benchmarks. Start a fresh Python process: an already imported native module
  still uses its loaded code. A proof check alone does not update the binary.

## 4. Threads and the GIL

- Every call runs in its own runtime instance; threads may call the same
  extension concurrently and callbacks may reenter it.
- `release_gil = True{}` detaches during pure Bend evaluation; the bridge
  reattaches for every Python operation. Free-threaded 3.14t always detaches.
- Each Bend evaluator uses one worker; Bend's parallel scheduler and GPU are
  not used. Invoked numerical libraries can use their own native thread pools.

## 5. Guarantees, trust and limits

Proved by every build (the SDK's laws in `bend/LAWS.bend`,
`bend/THREAD_LAWS.bend`):

- Typed exports and `unary`/`binary` accept exactly the right argument count and
  request `TypeError` otherwise.
- In a model of the C driver: Python operations happen only while attached,
  never during native evaluation; every call returns attached, failures
  included; a call owns one runtime instance; a native failure poisons only its
  instance; only healthy small instances are reused.

Trusted (tested, not proved): that the C bridge implements the model; CPython
and reference counting; the generated runtime; handle sealing (a per-call key;
forged or stale handles are detected probabilistically and raise
`ValueError`); strict conversions; export capture checks.

Limits:

- Linux x86_64, main interpreter only (no subinterpreters).
- A native Bend failure (such as `Nat` overflow or a runtime error) raises
  `RuntimeError` and discards that instance. Native crashes and stack
  exhaustion kill the process.
- Each active instance reserves about 10 GiB of virtual memory; `ulimit -v` or
  strict overcommit can make calls fail. Up to eight idle instances are cached.
- Bend's runtime is private API; the SDK pins 2.0.28.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `error: Bend proof check failed: PROOF.bend` | Run `bend PROOF.bend` for the diagnostic |
| `Cannot find bend 2.0.28` | Allow the download or set `BEND` |
| Build cannot find Clang | Set `CC=clang` (Clang 14+) |
| `ValueError` about a handle | A handle escaped its call; never cache `Python.Object` |
| `TypeError` from a typed export | Wrong argument count, or keywords given |
| `OverflowError` from `to_u32` | Negative or >= 2**32 |
| `ValueError` from `from_bytes` | A value >= 256 in the list |
| Wrong results only after optimizing | Test against a Python reference with Hypothesis |
