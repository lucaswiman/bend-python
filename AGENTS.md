# Bend/Python bindings

- Target Victor Taelin's **Bend 2**, pinned **2.0.28** (`.tools/bend/bin/bend` or `BEND`); never use Bend 1/HVM instructions.
- Linux x86_64, Clang 14+, CPython **3.10–3.14 and 3.14t**. Development venv: `bash scripts/bootstrap.sh`; `uv pip install --python .venv/bin/python --no-build-isolation -e .`.
- Build: `CC=clang .venv/bin/python setup.py build_ext --inplace`. Test: `.venv/bin/python -m unittest discover -s tests`.
- `bend/{PROOF,THREAD_PROOF}.bend` and `examples/PROOF.bend` are build gates; run each with `--check-only`. Docker/GitHub wheel recipe and usage are in README.
- Keep application logic, export adapters, and proved invariants in Bend. C owns CPython/runtime integration; Python owns build orchestration. Canonical `bend/` files are bundled in the SDK wheel; do not duplicate tracked copies.

## Language reminders

- [Pinned guide](https://github.com/bendlang/bend/blob/v2.0.28/guide/GUIDE.md): pure affine dependent types; `+` permits reuse of Data. Closures remain affine.
- Binder quantities affect function types: adapt `square(+x:U32)` to `U32 -> U32` with `x => square(x)`.
- Templates `~f` must be closed and call only earlier templates. Export helpers specialize them into captureless callbacks.
- Imported constructors need the alias (`Python.PyCall`). Computed match scrutinees need a helper. Recursive arguments decrease left-to-right.
- `{==}` proves definitional equality; erased equality expressions may mention affine inputs repeatedly. Laws about pure parsers do not prove their C callers.
- [Foreign effects](https://github.com/bendlang/bend/blob/v2.0.28/guide/EFFECTS.md): C is spliced after the runtime; CID/FID names resolve relative to the importer. No stable ABI. Erased generic arguments never enter the C argument array.

## Runtime boundary

- [Pinned compiler](https://github.com/bendlang/bend/blob/v2.0.28/bend2/comp.ts): patched CPU globals live in exclusive per-invocation runtime contexts, selected through TLS. Captureless closures can cross contexts; captured environments are consumed.
- Generic `Call -> IO(Object)` callbacks preserve every Python object's identity/precision through per-invocation strong-reference arenas. Never retain/forge handles across calls.
- **Object wrappers may be packed TAG_PAK or heap TAG_CTR**; both must unbox correctly. This matters after Bend copies/reboxes handles.
- Detach for the short idle-cache lock and optionally during pure work; reattach before Python effects/refcounts. Concurrent and reentrant calls own separate contexts. No Python API inside native evaluation. Each call uses one worker; upstream pool entry is rejected.
- Free-threaded Python needs attachment plus thread-safe APIs, not a GIL. C boundaries check attachment; traditional builds check GIL ownership too. Main interpreter only, including checks on every call.
- THREAD_PROOF proves attachment and context-lease models, **not C correspondence or CPython**. Lease laws assume distinct allocations and atomic cache operations; do not claim end-to-end GIL/refcount verification.
- Avoid Bend's signal-installing pool_stack/io_loop. Patched err_fail raises a Python exception and discards that runtime instance; unrelated calls remain usable. Ordinary Python exceptions only abort their invocation.
- Native faults still follow host signal handling. Each context reserves about 8 GiB heap + 2 GiB stack virtually, not resident RAM. Cache at most eight idle contexts; unmap failed/excess contexts. Never cap active leases: callbacks can nest.
