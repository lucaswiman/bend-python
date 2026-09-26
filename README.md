# bend-python

Write native Python extensions in **Bend 2**, with Bend functions, export helpers,
and checked laws. The small Python SDK drives setuptools; the C bridge handles
CPython and Bend's private runtime. Supported targets are **CPython 3.10–3.14,
including 3.14 free-threaded, on Linux x86_64**. Bend is pinned to **2.0.28**.

Python calls execute in-process. Installed wheels require neither Bend nor a C
compiler. This is an experimental binding implementation, not an upstream Bend
API or a formally verified CPython bridge.

**Bend 2 is still immature, and string-heavy code can be very inefficient.**
The pinned native runtime represents strings as linked character nodes, with
substantial memory and traversal costs. Treat this project as experimental and
benchmark your actual workloads before relying on it for performance.

## Try the examples

With `uv`, Clang 14+, `curl`, and `tar` installed:

```sh
bash scripts/bootstrap.sh
uv pip install --python .venv/bin/python --no-build-isolation -e .
(cd examples && CC=clang ../.venv/bin/python setup.py build_ext --inplace)
.venv/bin/python -m unittest discover -s tests
cd examples && ../.venv/bin/python
```

```python
import bend_example as bend

bend.square(12)                    # 144
bend.add(12, 30)                   # 42
bend.increment(2**32 - 1)          # 0: U32 arithmetic wraps
bend.echo_string("Bend 🦉\0")      # exact Unicode, including embedded NUL
bend.pack(1, "two", None)          # (1, "two", None)
bend.construct("complex", 1, 2)    # (1+2j)

value = []
value.append(value)
assert bend.echo(value) is value   # identity and cycles preserved
assert bend.setitem(value, 0, 42) is value
bend.call(lambda x: bend.square(x), 12)  # 144; reentrant callbacks work
```

The bootstrap installs Python 3.14, the checksum-verified Bend compiler, and
build tools under this checkout. Set `BEND` to use another installation of the
same compiler version. The `bend-python` SDK itself is a pure Python wheel; the
example in [`examples/`](examples) is a separate project (`bend-python-example`)
built with that SDK, exactly as your own extension would be. Rebuild it after
editing Bend with the `build_ext --inplace` command above; its `setup.py`
re-vendors the SDK's Bend library into the untracked `examples/bend/`.

## Create your own extension

Install the SDK wheel into your build environment, then vendor its Bend library:

```sh
python -m bend_python vendor bend
```

Vendoring preserves modified files unless explicitly passed `--force`.
`bend_python.get_include()` and `library_path()` locate the installed library.

Write `module.bend`:

```bend
import Base
import ./bend/python.bend as Python

# The + binder permits using x twice.
def square(+x: U32) -> U32:
  (x * x : U32)

# Python.Object can represent any Python object. This returns the same object.
def echo(request: Python.Call) -> IO(Python.Object):
  Python.unary(request)

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export_u32(~(x => square(x)), "square", True{})
    Python.export("echo", echo, False{})
```

The template argument `~` produces a captureless typed adapter. Its conversion,
exact-arity checks, and keyword rejection are implemented in Bend. There is no
per-function C wrapper.

Add `setup.py`:

```python
from setuptools import setup
from bend_python import BendExtension, BendBuildExt

setup(
    name="my-bend-extension",
    version="0.1.0",
    ext_modules=[BendExtension("my_bend", "module.bend", proofs=["PROOF.bend"])],
    cmdclass={"build_ext": BendBuildExt},
)
```

`proofs` lists your application proof entry points; omit it if you have none.
The SDK's own proofs are checked automatically. For source distributions, add
`include *.bend` and `recursive-include bend *.bend *.c` to `MANIFEST.in`.
[`examples/setup.py`](examples/setup.py) instead calls `vendor(..., force=True)`
on every build, keeping an untracked copy in sync with the installed SDK.

Add a `pyproject.toml` build-system section:

```toml
[build-system]
requires = ["setuptools>=80", "bend-python==0.1.0"]
build-backend = "setuptools.build_meta"
```

This project has not been published to PyPI. Until it is, install its local wheel
plus setuptools/build and use `python -m build --no-isolation`, or make the SDK
wheel available to your build environment through a local wheel index.

With Bend 2.0.28 available and `CC=clang`, build and install:

```sh
python -m build --wheel --no-isolation
python -m pip install dist/*.whl
python -c 'import my_bend; print(my_bend.square(12))'
```

Dotted extension names such as `mypackage._native` are supported. Include the
corresponding Python package in your setuptools configuration.

## Python values and helpers

**All Python objects can cross the generic interface unchanged.** This includes
`None`, booleans, arbitrary-precision integers, doubles, complex numbers,
strings, bytes, bytearrays, lists, tuples, dicts, sets, frozensets, ranges, slices,
memoryviews, sentinels, classes, callables, generators, and user-defined objects.
There is no recursive serialization: aliases, cycles, numeric precision, and
object identity survive. NumPy objects can pass through as opaque objects;
array/buffer computation support is future work.

A `Python.Object` is an invocation-scoped handle backed by a strong Python
reference. It is not the object's memory address. Do not construct handles
manually or save them for later calls. The bridge bounds-checks handles against
the current invocation, so a forged handle is either rejected or aliases another
object from the same call; it cannot reach freed memory or another call's objects.
All temporary references are released when the invocation finishes, preserving
its result.

| Bend API | Behavior |
|---|---|
| `export(name, function, release_gil)` | Exposes `Call -> IO(Object)` with positional and keyword arguments |
| `unary(request)`, `binary(request)` | Require exactly one/two positional arguments and no keywords |
| `export_u32(~f, name, flag)`, `export_binary_u32(~f, name, flag)` | Typed one/two-argument unsigned 32-bit helpers |
| `export_f32`, `export_bool`, `export_string` | Typed unary helpers with the same template/name/flag convention |
| `to_u32` / `from_u32`, etc. | Explicit conversion between Python objects and Bend scalars |
| `get_item`, `set_item`, `getattr`, `len` | Python operations with ordinary Python exception behavior |
| `tuple`, `list`, `dict`, `empty_dict`, `none` | Construct Python objects from handles |
| `call(function, args_tuple, kwargs_dict)` | Call a Python callable, including callbacks into Bend |
| `invoke(function, arguments)` | Call with a Bend list of arguments and no keywords |
| `builtins(name)`, `construct(name, arguments)` | Access/call Python builtins, including all builtin type constructors |

Typed U32 conversion rejects booleans/non-integers and values outside
`[0, 2**32 - 1]`. Typed F32 conversion requires a Python float and explicitly
narrows it to single precision. Use the generic object API when exact Python
integer/float behavior is required. String conversion preserves Unicode
codepoints, including lone surrogates. Typed adapters reject unexpected arity
and keywords with `TypeError`.

Passing a string as `Python.Object` preserves the original object without copying
its contents. Converting it with `to_string` or `export_string` copies its
codepoints and constructs Bend's linked-list `String`; converting back rebuilds
a Python string. Dynamic character nodes occupy about 16 bytes each before
allocator and sharing overhead. Memory pools and node reuse reduce allocation
costs, but do not remove that representation or the conversion costs. Large-text
workloads can therefore consume much more memory and time than expected.
See the upstream [string allocation/locality report](https://github.com/bendlang/bend/issues/1007)
and [proposal for buffer-backed strings](https://github.com/bendlang/bend/pull/873).

See [`examples/module.bend`](examples/module.bend) for generic callbacks,
container mutation, construction, keyword handling, and typed exports.

## GIL and free-threaded Python

Choose the policy per export:

```bend
Python.export_u32(~compute, "compute", True{})  # release during pure Bend work
Python.export_u32(~compute, "compute_held", False{})
```

Each invocation owns a separate Bend runtime instance, including its heap,
stack, allocator, and error state. Python threads can execute the **same
extension concurrently**. A short mutex protects the idle-instance cache;
no module-wide mutex is held during computation or Python callbacks.

The bridge detaches while borrowing or returning a runtime instance. With
`True{}`, it also detaches during pure Bend evaluation, permitting concurrent
calls on conventional CPython. Before any Python effect, it reattaches the thread.
On conventional CPython that reacquires the GIL. On free-threaded CPython it
restores the attached thread state; Python's thread-safe APIs supply the object
synchronization. Importing a free-threaded wheel does not enable the GIL.

Free-threaded builds always detach during pure Bend evaluation, whatever the
flag says: there is no GIL to keep, and an attached thread would stall other
threads' stop-the-world pauses (such as garbage collection) until it finished.

For example, use multiple Python threads with an export that releases the GIL:

```python
from concurrent.futures import ThreadPoolExecutor
import bend_example as bend

with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(bend.slow_release, [100_000_000] * 4))
```

On free-threaded Python, exports with `False{}` also run concurrently.
Callbacks can recursively call the same extension: the nested call borrows
another instance while the outer instance retains its continuation. The cache
retains at most eight idle instances; active and nested calls allocate additional
instances as needed instead of waiting for a fixed number of execution slots.
Each individual invocation currently uses one CPU worker. Bend's internal
fork-join worker pool and GPU execution are not enabled by this binding.

## Proofs and trust boundary

The build checks the library and application proof files before emitting native
code. The library laws cover the pure argument helpers: `arity`/`argument`
lookup, and `singleton`/`pair`, which succeed exactly when the list has one/two
elements (`*_exact_arity`, `*_complete`). The typed adapters use those parsers,
but the IO wrappers around them and the keyword check are not proved. Most
library and example laws restate a definition; they guard against regressions
rather than establish nontrivial properties.

The thread-state proof is a specification of the intended detach/evaluate/restore
protocol: a Python effect is accepted only in the attached state, and every
modeled call sequence for either GIL policy ends attached. The model permits
native work in any state and gives failure no separate control flow, so "no
Python API during native work" and "restore after a native failure" are
stipulations of the model, not consequences of it. The separate context-lease
model proves that a lease is granted only from an idle slot, that a leased slot
cannot be acquired again, that only a successful return with cache space makes
a slot reusable, and that updating one slot leaves the others unchanged. The
lease and thread-state models are not connected, and slot isolation is a
list-update lemma: in C it comes from separate allocations. These laws assume
distinct allocation identities and atomic acquisition/return; the model indexes
contexts by slot while the C cache is a mutex-protected linked list, and that
correspondence is trusted. **This is a protocol proof, not an end-to-end proof
of the C implementation or CPython.** A temporary instrumented extension
requires two native evaluations to enter together with distinct heaps and OS
threads; the old serialized implementation fails this test. Proving that the C
implementation refines the model remains an open obligation; the generated
compiler/runtime and C bridge are trusted code.

Bend reports foreign-code dependencies for exported wrappers. Pure proofs do not
establish correctness of that foreign code, its allocator, or Python object
reference counting.

### Core invariants

| Invariant | How it is established |
|---|---|
| A handle refers to a strong reference in the current invocation's arena; all arena references are released when the invocation ends | C: `bp_get` bounds-checks each handle against the current arena; `bp_run` decrefs the arena after returning the lease. Tested (`test_call_arena_releases_owned_references`). A manually built in-range handle aliases another object of the same call rather than failing; not prevented. |
| Handles do not outlive their invocation | Trusted: exports are captureless and Bend has no mutable global state, so a handle cannot reach a later call except by forging one. |
| Objects pass through unchanged (identity, precision, aliases, cycles) | C stores the original `PyObject*` and returns it with a new reference. Tested. |
| Python API runs only while attached, and never inside native evaluation | C: `bp_assert_attached` at every boundary (fatal error otherwise; also checks the GIL on conventional builds and that no evaluation is active on this thread). Modeled by `accepted_effect_attached`, `release_before_restore_rejected`; absence of Python calls inside native work is trusted. |
| Every call returns to Python attached | Modeled by `native_returns_attached`, `acquire_returns_attached`, `return_restores_attachment`; C's `setjmp` placement for native failures is trusted. Free-threaded builds always detach; the model covers that as the `True{}` policy. |
| One invocation per runtime instance | Modeled by `acquisition_requires_available`, `reserved_acquisition_rejected`; C removes an instance from the idle list under the pool mutex (trusted), and evaluation state is thread-local. Tested by the instrumented concurrency test. |
| A native failure affects only its instance | Modeled by `failure_isolated`, `failed_context_unavailable`. C marks the instance poisoned and unmaps it. Tested (`test_native_failure_discards_only_the_failed_context`). Ordinary Python exceptions only drop the continuation and keep the instance; its reuse is tested, not proved. |
| Poisoned or large instances are never reused | Modeled by `failed_context_discarded`, `reusable_requires_success`, `reusable_requires_cache_space`. The model's `cache_space` stands for C's "heap within 32 MiB and fewer than eight cached" (`bp_runtime_reusable`); that mapping is trusted. Tested for heap release. |
| Exported callbacks are captureless | C rejects a non-captureless closure at `export`, failing import. Typed helpers ensure it with templates. Not proved; the rejection path is untested. |
| Typed adapters reject wrong arity and keywords with `TypeError` | Pure arity parsing proved (`singleton_exact_arity`, `pair_exact_arity`, `singleton_complete`, `pair_complete`). The `require_*` IO wrappers and the C keyword check are tested, not proved. |
| U32/F32/String conversion semantics | C only; tested. U32 rejects `bool`/non-`int` and values outside `[0, 2**32 - 1]`. F32 requires a `float` and narrows with IEEE round-to-nearest. Strings copy codepoints; codepoints above U+10FFFF are rejected on return. |

## Manylinux wheels: Docker and GitHub Actions

Build and test wheels for CPython **3.10, 3.11, 3.12, 3.13, 3.14, and 3.14t**:

```sh
docker build -f docker/Dockerfile -t bend-python-manylinux .
mkdir -p wheelhouse
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/io:ro" -v "$PWD/wheelhouse:/wheelhouse" \
  bend-python-manylinux
```

To select one ABI, add `-e PYTHON_TAGS=cp314-cp314t`. The pinned
`manylinux_2_28_x86_64` image installs Clang and the checksum-pinned Bend compiler.
The script builds the pure `bend-python` SDK wheel once. For each ABI, it
installs that wheel into a fresh environment, builds the example project from a
clean source distribution against it, repairs the example wheel with
`auditwheel`, installs it, and runs tests against the installed extension, plus
temporary-extension build and concurrency tests. The checkout is mounted read-only. Use the
same helper in another project with compatible `tests/`, or
adapt that test invocation.

[`.github/workflows/wheels.yml`](.github/workflows/wheels.yml) runs the same
recipe as a six-job GitHub Actions matrix and uploads wheel artifacts; it does
not publish them. The current manylinux image supports the stable 3.14t target,
not the earlier experimental 3.13t wheel ABI. This recipe targets x86_64 Linux;
macOS, Windows, and other architectures are not validated.

## Current implementation limits

- Main Python interpreter only; subinterpreter calls are rejected. No GPU entry
  points. Exported callbacks must be captureless; helpers use templates to ensure
  this. Objects can still be passed explicitly as arguments.
- Bend's C ABI is private. The build pins 2.0.28 and checks exact runtime patches.
  It initializes unused generated registers and replaces `err_fail` process exits
  with exceptions. A native runtime failure discards the affected instance;
  other invocations and subsequent calls remain usable. Ordinary Python/type
  errors only abort their invocation.
- The shim preserves Python's signal handlers. Arbitrary native faults or stack
  exhaustion can still terminate the process; those are not caught exceptions.
- Each active or cached instance reserves about 8 GiB virtual heap and 2 GiB
  virtual stack. Most of it is never touched, but concurrent or nested calls
  each reserve their own, so an address-space limit (`ulimit -v`) or strict
  overcommit (`vm.overcommit_memory=2`) can make calls fail with `RuntimeError`.
- Up to eight idle instances remain cached per extension until process exit.
  Only instances whose heap stayed within 32 MiB are cached; failed instances,
  instances that grew larger, and returns beyond the cache limit are unmapped,
  so a large call's resident memory is released when it finishes.

[`AGENTS.md`](AGENTS.md) contains short continuation notes and pinned upstream
references. Run local checks with `.venv/bin/python -m unittest discover -s tests`.
