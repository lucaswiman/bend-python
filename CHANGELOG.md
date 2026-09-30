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
  reorderer.

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
