# Bend/Python bindings

- Target Victor Taelin's **Bend 2**, pinned **2.0.28** (`.tools/bend/bin/bend` or `BEND`); never use Bend 1/HVM instructions.
- Linux x86_64, Clang 14+, CPython **3.10–3.14 and 3.14t**. Development venv: `bash scripts/bootstrap.sh`; `uv pip install --python .venv/bin/python --no-build-isolation -e .`.
- The SDK is a pure wheel; `examples/` is a separate project built with it. Build: `cd examples && CC=clang ../.venv/bin/python setup.py build_ext --inplace`. Test: `.venv/bin/python -m unittest discover -s tests`.
- `bend/{PROOF,THREAD_PROOF}.bend` and `examples/PROOF.bend` are build gates (`scripts/check-proofs.sh`). Lint: `uvx prek run --all-files` (ruff, hygiene, proofs). Docker/GitHub wheel recipe and usage are in README.
- Mutation-check every new law: break the code it covers and confirm the gate fails.
- Keep application logic, export adapters, and proved invariants in Bend. C owns CPython/runtime integration; Python owns build orchestration. Canonical `bend/` files are bundled in the SDK wheel; do not duplicate tracked copies (`examples/bend/` is an untracked vendored copy).

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
- Handles are sealed per invocation (`bp_object`/`bp_get`); never pack or accept a raw arena index. Bend has no private constructors, so sealing is what rejects forged `PyObject{n}`.
- Detach for the short idle-cache lock and optionally during pure work (always on free-threaded builds); reattach before Python effects/refcounts. Concurrent and reentrant calls own separate contexts. No Python API inside native evaluation. Each call uses one worker; upstream pool entry is rejected.
- Free-threaded Python needs attachment plus thread-safe APIs, not a GIL. C boundaries check attachment; traditional builds check GIL ownership too. Main interpreter only, including checks on every call.
- THREAD_PROOF proves attachment and context-lease models, **not C correspondence or CPython**. Lease laws assume distinct allocations and atomic cache operations; do not claim end-to-end GIL/refcount verification.
- `_patch_runtime` zero-initializes forwarded registers, so it also disables `preserve_none`: together they exhaust registers on Clang 19+ for wide segments. The example's `reverse5` keeps a wide segment under CI's Clang.
- Avoid Bend's signal-installing pool_stack/io_loop. Patched err_fail raises a Python exception and discards that runtime instance; unrelated calls remain usable. Ordinary Python exceptions only abort their invocation.
- Native faults still follow host signal handling. Each context reserves about 8 GiB heap + 2 GiB stack virtually, not resident RAM. Cache at most eight idle contexts whose dynamic heap high-water mark stayed within 32 MiB, excluding the sparse metadata prefix; this is not a total RSS bound. Unmap failed/large/excess contexts. Never cap active leases: callbacks can nest.
