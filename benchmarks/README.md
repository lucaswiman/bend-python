# Numerical benchmarks

Compare NumPy and PyTorch CPU float32 operations with Bend's in-place map,
optional SciPy BLAS, and ordinary Python callable dispatch. CUDA compares
PyTorch directly with the same PyTorch callable dispatched through Bend.
CUDA data stays on the GPU; Bend's borrowed-buffer and BLAS APIs remain CPU-only.
The benchmark does not implement a Bend CUDA kernel.

[Initial results on this box](RESULTS.md) include one/eight-P-core CPU runs and
GPU-resident CUDA timings, with the measured configuration and limitations.

## Build

Use an isolated environment so the CUDA wheel does not replace another
project's CPU PyTorch. From the repository root, on this host (driver 560.35.03):

```sh
uv venv --python .venv/bin/python /tmp/bend-numerical-benchmark-venv
uv pip install --python /tmp/bend-numerical-benchmark-venv/bin/python \
  'torch==2.10.0' --index-url https://download.pytorch.org/whl/cu126
uv pip install --python /tmp/bend-numerical-benchmark-venv/bin/python \
  -r benchmarks/requirements.txt
uv pip install --python /tmp/bend-numerical-benchmark-venv/bin/python \
  --no-build-isolation -e .
(cd benchmarks && CC=clang BEND=../.tools/bend/bin/bend \
  /tmp/bend-numerical-benchmark-venv/bin/python setup.py build_ext --inplace)
```

The example extension is separate from the SDK. Its build checks the SDK's
proof gates and uses fresh vendored library files. Rebuild after changing any
Bend or SDK source. The runner records both source hashes and the loaded native
extension's path/hash; these identify artifacts, not a proof of correspondence.
Scientific dependencies are benchmark dependencies only.

## Run on verified P cores

The runner reads Linux's hybrid `cpu_core` PMU mask and physical-core sibling
groups. On this i7-14700K, CPUs 0–15 are P-core threads; one thread from each
physical P core is 0, 2, 4, 6, 8, 10, 12, 14. CPUs 16–27 are E cores. Selecting
both SMT threads of one core, an E core, or a CPU outside the caller's affinity
is rejected. Inspect your own machine rather than copying these CPU numbers.

For a bounded initial comparison, choose an idle P core and an unused output
directory. For example, CPU 14 is a P core on this box:

```sh
/tmp/bend-numerical-benchmark-venv/bin/python benchmarks/run.py \
  --cpus 14 --threads 1 --gpu 0 \
  --sizes 4096,16777216 --matrix-sizes 512,2048 \
  --output benchmarks/results/initial
```

The defaults add a 1,048,576-element vector case. Use `--layouts contiguous
stride2 transposed reversed` for broader stride diagnostics. Reversed views
are NumPy-only; CPU BLAS vectors support only contiguous/positive-strided
rank-one views. Matrix inputs and output are column-major for every backend.
Use `--libraries numpy torch_cpu` to omit CUDA. For a small wiring check, use
`--sizes 4096 --matrix-sizes 64 --layouts contiguous --samples 4`.

For CPU thread scaling, repeat with `--cpus 8,10,12,14 --threads 4` and a new
output directory when all those cores and siblings are quiet. Each Bend map
still uses one evaluator worker; the numerical providers may use the requested
thread count. The runner sets common thread environment variables before
imports, limits native thread pools, configures PyTorch's intra-op/inter-op
threads, and verifies every surviving thread's affinity after the measurement.
Affinity is set before scientific imports and inherited by threads.

## Congestion gate

Before the run and each fresh worker, a three-second sample must satisfy all
of these thresholds:

- Each selected CPU and its SMT sibling: at most 5% busy.
- Whole host: at most 10% busy; CPU and I/O pressure at most 1%, memory pressure
  at most 0.1%; no swap I/O; at least 4 GiB available memory.
- CUDA device: at most 5% utilization, at most 2 MiB occupied VRAM, and no other
  compute process at either endpoint. This intentionally requires a dedicated,
  idle GPU; display/retained CUDA contexts can cause a refusal.

Preflight preserves the caller's affinity and changes no system settings or
other processes. Samples are recorded as raw counters and derived metrics;
they establish the observed interval, not exclusive access afterward. A failed
gate saves its evidence and retries at most three times, waiting five seconds
between samples. Every rejected sample is retained. If the host stays busy,
the run refuses timings or leaves an incomplete report. The output directory
is never overwritten. Retry into a new directory when the machine is quiet.

## Measurements and interpretation

Negation alternates the sign of finite, nonzero values, avoiding underflow and
changing every logical cell on every call. Validation checks the first single
operation (so an even number of no-ops cannot pass), then every cell after
timing, including untouched stride gaps. Matrix rows/columns have distinct
periodic values with an independent analytic product reference. Outputs must
retain object and storage identity. Checks and reference construction are
outside timing.

Each case runs in a fresh process. Inputs and outputs are allocated and
pre-touched before timing. After at least 200 ms warmup, calibration chooses
a bounded block size; seven samples target 50 ms each, with a maximum of
10,000 calls per sample. Slow single calls can exceed that target. The fixed
shuffle seed changes case order without running cases concurrently. Timings
include the Python call loop for every backend and are local diagnostics.

CUDA runs synchronize before/after each block and report wall-clock samples
plus CUDA event intervals. The latter include stream gaps due to host
submission, so they are not kernel-only time. Input creation and host/device
transfers are excluded; the comparison assumes GPU-resident data. TF32 is
disabled. Autograd and compilation are excluded.

`manifest.json` records the schedule, configuration, source/SDK hashes,
revision/dirty state, CPU model/governor and completion status. `results.jsonl`
records raw samples, each congestion receipt, loaded extension hash, installed
package versions, native thread pools, affinity and allocation diagnostics.
`REPORT.md` is the compact table; failed-worker stderr is retained separately.
Partial reports state their completed case count.

CPU memory is process peak RSS before/after warmed timing, including Bend
runtime and provider overhead. Zero incremental high-water growth cannot prove
absence of allocations, nor distinguish a previously warmed workspace from
bridge memory. CUDA peak allocated/reserved bytes cover PyTorch's allocator,
excluding driver and external-library allocations. No hidden transfers are
added to make CPU-only Bend APIs accept CUDA tensors.

Useful negation bandwidth counts one float32 read/write per logical cell and
excludes stride gaps. Matrix FLOPs use dense `2*n^3` work. Record variability
and library/provider differences; do not infer general speed guarantees from
one run or compare GPU-resident times with CPU times as an end-to-end workflow.
