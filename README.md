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

### Optional: an agent skill

[`skills/bend-python`](https://github.com/lucaswiman/bend-python/tree/main/skills/bend-python)
teaches coding agents (Claude Code, Codex, Copilot, Cursor and others) to work
with bend-python and Bend 2: setup, the build, the Python interface, Bend's
checker rules, and writing laws and proofs, including agreeing on laws with
you before proving them. It needs GitHub CLI 2.90 or later:

```sh
gh skill install lucaswiman/bend-python bend-python                  # choose agent and scope
gh skill install lucaswiman/bend-python bend-python --agent codex --scope user
```

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

## NumPy arrays and PyTorch tensors

Import `./bend/tensor.bend` to borrow existing CPU float32 storage without
copying elements into Bend. NumPy uses the Python buffer protocol; PyTorch uses
`tensor.numpy()` with its default sharing behavior. Neither adapter casts,
transfers, detaches or makes an array contiguous. NumPy and PyTorch are optional
runtime dependencies; PyTorch borrowing also requires NumPy.

```bend
import Base
import ./bend/tensor.bend as Tensor

def half(x: F32) -> F32:
  (x / 2.0 : F32)

def main() -> IO(Unit):
  do IO<Unit>:
    Tensor.export_numpy_f32_inplace(~half, "numpy_half_inplace", True{})
    Tensor.export_torch_f32_inplace(~half, "torch_half_inplace", True{})
```

```python
>>> import numpy as np
>>> import fast
>>> a = np.arange(8, dtype=np.float32)
>>> fast.numpy_half_inplace(a[::2])       # mutates shared storage
array([0., 1., 2., 3.], dtype=float32)
>>> a
array([0., 1., 1., 3., 2., 5., 3., 7.], dtype=float32)
```

The exports return the exact input object. They support scalars, empty arrays,
transposes, negative strides and ordinary slices. Writable views must be
non-overlapping: a conservative stride check rejects broadcast/overlapping
storage and some unusual interleaved layouts. Read-only borrows can read shared
cells. Unsupported dtypes, byte order, devices and layouts raise Python
exceptions before mutation. Only ordinary `torch.Tensor` inputs are supported; subclasses that can customize
conversion are rejected. PyTorch tensors requiring gradients are rejected,
including under `torch.no_grad()`; forward AD tensors are also rejected. An
explicit `tensor.detach()` shares storage and is accepted. Writable PyTorch
borrows increment its version counter before modification, so backward detects
changes to saved tensors or their detached aliases. This integration does not
provide differentiation through Bend.

For custom algorithms, `Tensor.borrow_f32(object, writable)` or
`Tensor.borrow_torch_f32(object, writable)` returns an affine
`Python.F32View<writable>`. Its erased permission index lets the type checker
reject writes and maps through a read-only view:

| Bend API | Result |
|---|---|
| `Python.f32_size(writable, view)` | `(view, count)` with a `Nat` count, including sizes above 2**32 |
| `Python.f32_shape(writable, view)` | `(view, List<Nat>)`; metadata only |
| `Python.f32_read(writable, view, index)` | `(view, F32)` at a bounds-checked logical row-major index |
| `Python.f32_write(view, index, value)` | Updated `F32View<True{}>`; checked indexed write |
| `Python.f32_modify(view, index, function)` | Load, apply a captureless Bend function, store |
| `Tensor.map_inplace(~function, view)` | Map a closed template over a `F32View<True{}>` and return it |
| `Python.f32_map(view, producer)` | Consume a dependent program for the actual buffer length |
| `Python.f32_map_closed(view, function)` | Map a captureless scalar callback without step tuples |
| `Python.f32_release(writable, view)` | Release the borrow |

The `writable` arguments above are erased; they do not enter the native ABI.
Thread the returned view through each operation and release it when done.

For maps, the native driver supplies the retained buffer's actual length to a
producer of type `@count: Nat -> Python.F32MapSteps(count)`. At zero the program
is `Unit`; each successor is an affine `F32 -> Pair(F32, F32MapSteps(pred))`
that produces one updated value and the next step. A safely typed producer
cannot return too few or too many steps. Each step is consumed once, so custom
producers can use captured state. `Tensor.map_inplace` specializes its closed
template to `Python.f32_map_closed`, which validates a captureless callback once
and applies it directly without creating per-cell successor closures or tuples.
Both drivers validate the writable borrow once and use an incremental stride
cursor in logical row-major order. Singleton axes are removed from traversal
metadata; indexed operations retain their random-access decoder and checks.
The dependent driver retains its constructor and successor checks, including
validation before each cell is written.

Every borrow is also released automatically when its invocation ends, including
exceptions and cancellation. The exporter stays alive throughout the borrow;
views cannot be retained across calls. Writable borrows require exclusive access to the storage: no concurrent reads,
writes, resizing or metadata changes through Python or native aliases.
Read-only borrows allow concurrent reads, but exclude writes, resizing and
metadata changes. These rules also apply while the GIL is released. A borrow
pins a buffer export, but does not lock all aliases. Native memory effects run without
Python calls between elements and check signals between batches of at most
4096 native operations; callback work determines the time between checks;
interruption leaves completed writes in place. No array-sized temporary is
allocated. Map traversal uses O(rank) metadata, adds strides between cells, and
carries across axes without per-cell division. Random indexed access still
takes work proportional to rank. Bend's scalar callbacks can be slower than
NumPy/PyTorch vectorized kernels; benchmark your actual algorithm.

## Optional bulk BLAS

Install `bend-python[blas]` to use SciPy's public
[`cython_blas` API](https://docs.scipy.org/doc/scipy/reference/linalg.cython_blas.html)
on borrowed float32 storage. The base SDK has no runtime dependencies;
NumPy, PyTorch and SciPy are imported only when their adapters need them.
Building an extension that exports BLAS functions needs no SciPy headers,
BLAS linker flags or scientific packages. Calling those functions requires an
LP64 SciPy build (the usual wheels); ILP64 capsules are rejected before use.

```bend
import Base
import ./bend/blas.bend as Blas

def main() -> IO(Unit):
  do IO<Unit>:
    Blas.export_scale("scale", True{})
    Blas.export_dot("dot", True{})
    Blas.export_axpy("axpy", True{})
    Blas.export_matmul("matmul", True{})
```

| Python call | Result |
|---|---|
| `scale(2.0, x)` | Scales `x` in place and returns the original `x` |
| `dot(x, y)` | Returns a float32 dot product as a Python float |
| `axpy(2.0, x, y)` | Sets `y += 2*x` and returns the original `y` |
| `matmul(a, b, out)` | Sets `out = a @ b` and returns the original `out` |

All arguments are positional; coefficients must be Python floats. Vectors
have rank one and positive strides in whole float32 elements. Matrices have
rank two and Fortran layout: consecutive rows, with optional padding between
columns. For example, create `out = np.empty((m, n), dtype=np.float32,
order="F")` once and reuse it. Ordinary row-major matrices require an explicit
caller conversion. Storage must be aligned, and dimensions, increments and
leading dimensions must fit a signed 32-bit BLAS integer. Empty dimensions are
accepted; a zero inner dimension fills the output with zeros.

Inputs may be read-only. Outputs must be writable and their storage spans
must not intersect any input span, including gaps in strided views. Validation
finishes before the kernel writes. The bridge passes retained buffer pointers
directly to BLAS, without allocating or copying array elements; the BLAS
provider may use its own working memory. The usual borrow synchronization
rules apply. Kernels follow the export's GIL policy and check cancellation
after returning; a completed kernel's writes remain if cancellation raises.
The provider may use multiple CPU threads.

`export_torch_scale`, `export_torch_dot`, `export_torch_axpy` and
`export_torch_matmul` use the same CPU/autograd restrictions and version
notification as the tensor adapter. A transpose of a contiguous rank-two
tensor has the required column-major layout.

For custom Bend programs, `Python.blas_scale`, `blas_dot`, `blas_axpy` and
`blas_matmul` consume affine `F32View` arguments and return every live view,
plus the scalar for dot. Release the returned views when finished. Shape laws
prove the pure logical dimension policy; buffer metadata, capsule ABI, native
arithmetic and resource handling remain tested, trusted boundaries.

## Threads and the GIL

Every call runs in its own Bend runtime instance (heap, stack and allocator),
so threads call the same extension concurrently, and callbacks can reenter it.
The third argument to `export` sets the GIL policy: `True{}` releases the GIL
during pure Bend evaluation, `False{}` holds it. Free-threaded builds always
detach during evaluation and never enable the GIL. The bridge reattaches before
every Python operation. Each Bend evaluator uses one worker; its parallel
scheduler and GPU backends are not enabled. Invoked numerical libraries may
use their own native thread pools.

## What is proved, and what is trusted

The build refuses to compile unless every law checks:

- **Argument checks.** Typed exports accept exactly the right number of
  arguments and request a `TypeError` for every other count
  ([`bend/LAWS.bend`](https://github.com/lucaswiman/bend-python/blob/main/bend/LAWS.bend)).
- **Borrowed arrays.** A pure storage/lease model proves count preservation,
  read-after-write, preservation of other cells, bounds and permission checks,
  and release rules. The dependent map program has exactly the requested step
  count; a pure interpreter of the canonical program produces the same values
  as `List.map`. The closed-function model agrees with both. Cursor laws
  establish conservation, in-bounds access, advancement and decreasing remaining
  work; a mixed-radix axis model proves step, carry and extent conservation. These are proofs of the Bend
  program and arithmetic model, not the C traversal
  ([`bend/TENSOR_LAWS.bend`](https://github.com/lucaswiman/bend-python/blob/main/bend/TENSOR_LAWS.bend)).
- **BLAS dimensions.** Exact vector/matrix ranks, acceptance of matching shapes,
  equal vector lengths, and all three matrix-product dimension equations
  ([`bend/BLAS_LAWS.bend`](https://github.com/lucaswiman/bend-python/blob/main/bend/BLAS_LAWS.bend)).
  These pure metadata laws do not prove the BLAS implementation or C checks.
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
| Borrowed arrays retain storage identity, respect strides, clean up on failure, and notify PyTorch | C/Python integration; real NumPy, PyTorch, buffer, reentrancy and cancellation tests |
| Optional BLAS preserves layouts and checks dimensions/aliasing before writes | Pure shape laws; real SciPy/NumPy/PyTorch kernels and rejection tests; capsule ABI and arithmetic trusted |

## Limits

- Main interpreter only; subinterpreters are rejected.
- A native Bend failure (such as `Nat` overflow) raises `RuntimeError` and
  discards that runtime instance; other calls are unaffected. Native crashes and
  stack exhaustion still kill the process.
- Each active instance reserves about 10 GiB of *virtual* memory, so `ulimit -v`
  or strict overcommit can make calls fail. Up to eight idle instances are
  cached; an instance whose dynamic heap allocation high-water mark exceeded
  32 MiB is released instead. The budget excludes the sparse runtime metadata
  prefix and does not bound total resident memory.
- Bend's C runtime is private API. The build pins 2.0.28 and refuses to patch a
  runtime that has changed.

## Developing this repository

```sh
bash scripts/bootstrap.sh              # Python 3.14, uv venv, checksum-verified Bend
uv pip install --python .venv/bin/python --no-build-isolation -e .
(cd examples && CC=clang ../.venv/bin/python setup.py build_ext --inplace)
# Optional integration dependencies (CPU PyTorch wheel):
uv pip install --python .venv/bin/python numpy
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
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
