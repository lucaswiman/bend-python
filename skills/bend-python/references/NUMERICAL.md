# Numerical libraries and borrowed storage

Use this reference when Bend works with NumPy arrays, CPU PyTorch tensors, or
NumPy/SciPy native kernels. The tensor and BLAS APIs require an SDK containing
`tensor.bend` and `blas.bend`; install the matching SDK from this checkout or a
release that includes them, then refresh the project's untracked vendored
library. An older installed wheel does not gain new APIs from a skill update.
For a clean downstream build before a matching release is published, use the
installed checkout SDK with `pip install --no-build-isolation .` (and install
its build dependencies), or point the build-system requirement at that source.
Otherwise build isolation can install the older pinned PyPI SDK and auto-vendor
sources without these APIs, even if the development environment has new ones.

## Choose the execution path

| Work | Path | Relevant cost or constraint |
|---|---|---|
| Existing NumPy/SciPy/PyTorch operation | Look up its callable and use `Python.invoke` / `Python.call` | Objects retain identity and dtype; the called operation decides allocation and copying |
| Custom elementwise F32 transformation | `Tensor.export_numpy_f32_inplace` / `export_torch_f32_inplace` | Shares storage and supports general strides; Bend's per-element driver can be slower than a vectorized kernel |
| Scale, dot, axpy, matrix multiplication | `Blas.export_*` / `export_torch_*` | One bulk native call on retained float32 pointers, subject to BLAS layouts and LP64 limits |
| Custom indexing or traversal | Borrow an affine `Python.F32View<writable>` | Keep and return its lease through effects; release it within the invocation |

For enormous arrays, avoid `tolist`, `bytes`, `to_bytes`, or a Bend list of
elements as an interchange format. Those paths materialize data. Borrowed
metadata is small; the array remains in its original storage. An explicit
caller conversion can be appropriate when an algorithm requires another
layout, but describe and measure that allocation rather than hiding it in an
adapter. Zero-copy borrowing does not imply that an arbitrary downstream
library operation uses no working memory.

Keep algorithm composition, shape policy, exports, and proved invariants in
Bend. C handles storage, runtime/CPython attachment, and native ABI calls;
Python handles the build and the public package boundary.

## Borrowing and custom maps

```bend
import Base
import ./bend/tensor.bend as Tensor

def half(x: F32) -> F32:
  (x / 2.0 : F32)

def main() -> IO(Unit):
  do IO<Unit>:
    Tensor.export_numpy_f32_inplace(~half, "half_inplace", True{})
    Tensor.export_torch_f32_inplace(~half, "torch_half_inplace", True{})
```

`half_inplace(array)` returns the same Python object after updating shared
storage. The generic buffer path also accepts compatible `array.array('f')`
and memoryviews without importing NumPy. Scalars, empty arrays, transposes,
ordinary slices, and negative strides are supported. There is no implicit
cast, device transfer, detach, or contiguity conversion.

`Tensor.borrow_f32(object, writable)` and `borrow_torch_f32` return
`Python.F32View<writable>`. The erased Bool permission prevents ordinary Bend
code from passing a read-only view to a write operation. Views are affine;
do not duplicate them, cache their IDs, or retain them across exported calls.
Read/shape/size effects return their view with the result, and writes/maps
return an updated view. Consume returned pairs in helper definitions, since a
`do` binding cannot destructure an affine pair. Call
`Python.f32_release(writable, view)` when finished; invocation cleanup also
releases outstanding exports after exceptions.

`Tensor.map_inplace(~function, view)` maps a closed template and returns the
writable view. For captured state, `Python.f32_map` consumes an affine producer
`@count: Nat -> Python.F32MapSteps(count)`. Its count comes from retained
storage, never a caller-supplied length. A zero-length program is `Unit`; each
successor is a closure consuming one F32 and returning its replacement and the
next step. This permits constant live traversal state. Indexed `f32_modify`
still requires a captureless callback; use the dependent map API for captured
per-map state.

PyTorch inputs must be ordinary CPU float32 tensors. The adapter uses the
default sharing behavior of `tensor.numpy()`, requires NumPy, and rejects
subclasses, tensors requiring gradients (also under `torch.no_grad()`), and
forward-AD tensors. An explicit caller `tensor.detach()` shares storage and
is accepted. Writable borrowing increments PyTorch's version counter before
granting access, so saved tensors and detached aliases observe mutation.
This does not implement differentiation through Bend.

Borrowing retains the exporter but does not lock its aliases. Writable access
requires exclusive access to storage; read-only access permits concurrent
reads. In either case, exclude conflicting writes, resizing, and metadata
changes through Python or native aliases, including while detached. The
writable layout check conservatively rejects overlapping cells.

## Calling an existing numerical kernel from Bend

The ordinary object interface is useful beyond the specialized F32 APIs.
For example, the third positional argument of `numpy.multiply` is its output:

```bend
import Base
import ./bend/python.bend as Python

def twice(+array: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    numpy : Python.Object <- Python.import_module("numpy")
    multiply : Python.Object <- Python.getattr(numpy, "multiply")
    factor : Python.Object <- Python.from_f32(2.0)
    Python.invoke(multiply, [array, factor, array])
```

This passes the existing object as input and output; it does not transfer
elements into Bend. `Python.call` also accepts a tuple and keyword dictionary
for kernels needing `out=` or other options. Check the called library's
documented dtype, alias, stride, device, and allocation behavior. NumPy/SciPy
wrappers may normalize inputs or allocate results; passing a Python object
alone does not prove a zero-copy operation. The called library governs its
own native threads and GIL release.

## Optional bulk BLAS

Install the matching SDK with its `blas` extra. In this repository:

```sh
uv pip install --python .venv/bin/python --no-build-isolation -e '.[blas]'
```

The extra installs SciPy; the backend lazily obtains function pointers from
the public `scipy.linalg.cython_blas` capsules. It needs an LP64 SciPy build,
such as the usual wheels. ILP64 signatures are rejected before invocation.
Do not use private NumPy symbols, assume a provider's direct Fortran ABI, or
cast an unvalidated capsule to a guessed function type. Building exports
requires no scientific headers or BLAS linker flags.

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

Python calls are positional: `scale(2.0, x)`, `dot(x, y)`, `axpy(2.0, x, y)`,
and `matmul(a, b, out)`. Coefficients must be actual Python floats, narrowed
to F32 by the SDK's strict conversion. Scale returns `x`; axpy returns `y`;
matmul returns `out`, each preserving the original object. Dot returns an F32
result widened to a Python float. Empty vectors produce a zero dot; a zero
matrix inner dimension fills nonempty output with zeros.

Vectors require rank one, aligned native float32 storage, and positive strides
in whole elements. Matrices require rank two and column-major compatibility:
consecutive rows and a positive whole-element leading dimension at least the
row count; padding between columns is accepted. Degenerate/empty axes have
only the layout requirements of their accessed cells. Use reusable NumPy
`order="F"` outputs or transposes of contiguous rank-two CPU tensors. Ordinary
row-major matrices are rejected instead of silently copied. Dimensions,
increments, and leading dimensions must fit a signed 32-bit BLAS integer.

Inputs can be read-only. Outputs must be writable and their storage spans
must not intersect input spans. This conservative check includes gaps in
strided views; do not expect disjoint interleaved views to be accepted.
Logical and physical validation precede data writes. The bridge passes
retained pointers directly; provider workspace is outside that guarantee.
`export_torch_scale`, `export_torch_dot`, `export_torch_axpy`, and
`export_torch_matmul` apply the same PyTorch borrow restrictions above.

Custom Bend programs can call `Python.blas_scale`, `blas_dot`, `blas_axpy`,
and `blas_matmul` on borrowed views. Every primitive returns every live input
lease (nested pairs for dot/matmul), plus the dot scalar; release or reuse
those returned views. Static permission indices supplement the native boundary
checks. View constructors are public, so proofs cannot justify trusting
unchecked seals, lifetimes, or physical metadata.

`True{}` releases the GIL during native kernels as well as Bend evaluation;
3.14t always detaches. Reattachment precedes Python effects and cleanup.
BLAS providers may use multiple native threads even though each Bend evaluator
has one worker. Mapping checks cancellation between bounded batches; bulk
BLAS checks after the kernel returns. Cancellation leaves completed writes
in place and does not roll back an operation.

## Contracts and verification

State logical shape and ownership laws before implementation, using the
pending/prove/promote workflow in [PROOFS.md](PROOFS.md). Specify independent
conclusions: exact reconstructed shape, equal vector counts, and each matrix
equation (`a.cols == b.rows`, `out.rows == a.rows`, `out.cols == b.cols`).
Include acceptance for supported shapes so rejection of every input cannot
satisfy only vacuous success laws. Mutation-check each law with type-correct
changes; omit each dimension guard separately.

The SDK's tensor laws cover a pure storage/lease model, canonical map
semantics, exact dependent step counts, and cursor bounds/conservation.
BLAS laws cover logical rank/dimension policy. Neither proves C correspondence,
buffer metadata, pointer arithmetic, capsule ABI, refcounts, or native BLAS
arithmetic. F32 rounding, NaNs, infinities, and reduction order prevent general
real-number associativity/equivalence laws; use appropriate numeric references
and tolerances rather than asserting exact identities without evidence.

Rebuild after source edits and use fresh processes; a loaded extension keeps
its previous machine code. Compare actual kernel results with real NumPy,
SciPy, or PyTorch, using distinct coefficients/operands to expose swaps. Check
identity/data pointers, alias updates, stride gaps, read-only permissions,
shape/layout rejection before writes, empty dimensions, cleanup after errors,
and concurrent independent buffers. For 3.14t, verify the GIL remains disabled
after importing dependencies and exercising kernels.

Check packaging separately: the base wheel has no mandatory scientific
dependencies, optional requirements have an extra marker, and generic exports
work in a clean environment without scientific packages. For large-array memory
claims, pre-touch inputs, warm the provider before measuring, inspect peak RSS
and pointer identity, and separate bridge allocation from provider workspace.
Benchmarks are local diagnostics; do not infer throughput guarantees from a
single run.
