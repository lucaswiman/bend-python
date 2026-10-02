# Changelog

All notable changes to `bend-python` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). A release is published to PyPI only
when its version has a section below.

## [Unreleased]

### Added

- An agent skill, `skills/bend-python`, installable with
  `gh skill install lucaswiman/bend-python bend-python`: setup, the build, the
  Python interface and its guarantees, Bend 2's checker rules with fixes, and
  writing and proving laws, including agreeing on law statements with the user.
  It includes a project scaffold, a first-error filter and a definition
  reorderer. The numerical-libraries reference covers borrowed arrays, optional
  native kernels, layouts, packaging, and proof boundaries.
- Zero-copy CPU float32 borrows for NumPy arrays, PyTorch tensors and Python
  buffer exporters; affine, sealed views with shape, indexed read/write and
  in-place Bend mapping. Supports strided layouts without allocating element
  storage, cleans up on errors/cancellation, and tracks PyTorch mutations.
- Permission-indexed views and dependent, affine map programs for the actual
  storage length, with mutation-checked elementwise semantics and cursor laws.
  Real NumPy/PyTorch integration tests run in CI.
- Optional SciPy BLAS scale, dot, axpy and matrix multiplication on borrowed
  float32 buffers and CPU PyTorch tensors, with explicit layout/alias checks,
  mutation-checked shape laws, and no mandatory scientific dependencies.

### Changed

- Closed-template maps avoid per-element successor objects; both map paths use
  incremental strided traversal with constant storage per axis.
- Positional Python calls use vectorcall, and native object identity avoids
  Python callback setup. BLAS exports share lazily validated capsule bindings.

### Fixed

- Runtime cache eligibility now measures dynamic heap pages, excluding the
  sparse metadata prefix that previously caused every initialized context to
  be discarded. Large and failed contexts are still evicted.

## [0.1.0] - 2026-09-29

### Added

- `BendExtension` and `BendBuildExt`: setuptools integration that proves a
  program's laws and the library's own, generates C with the pinned Bend 2.0.28
  compiler, and compiles it with Clang. Failures end in one `error:` line.
- Automatic, checksum-verified download of the pinned Bend compiler (`BEND`
  overrides it; `BEND_PYTHON_DOWNLOAD=0` disables it), and automatic vendoring
  of the Bend library when a program imports a missing copy.
- `python -m bend_python vendor` to copy the Bend library by hand.
- The Bend `Python` interface: generic `export` of `Call -> IO(Object)`; typed
  `export_u32`, `export_f32`, `export_bool`, `export_string` and
  `export_binary_u32`; conversions (`to_u32`/`from_u32`, `to_f32`/`from_f32`,
  `to_bool`/`from_bool`, `to_string`/`from_string`, `from_nat`,
  `to_bytes`/`from_bytes`, `truthy`); and Python operations (`get_item`,
  `set_item`, `getattr`, `len`, `tuple`, `list`, `dict`, `call`, `invoke`,
  `builtins`, `construct`, `import_module`).
- Concurrent calls in isolated runtime instances, with a per-export GIL policy;
  free-threaded CPython 3.14t never enables the GIL.
- Sealed object handles detect accidental fabrication or reuse from another
  call probabilistically, raising `ValueError` on an invalid decoded handle.
- Proved models of the thread-attachment and runtime-lease protocol, and laws
  for the typed adapters' argument checks.
- Linux x86_64 support for CPython 3.10–3.14 and 3.14t.

[Unreleased]: https://github.com/lucaswiman/bend-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lucaswiman/bend-python/releases/tag/v0.1.0
