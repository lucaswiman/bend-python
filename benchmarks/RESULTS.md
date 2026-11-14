# Initial numerical results

Local diagnostics from 2026-10-02 on the Intel i7-14700K and RTX 4060 Ti (8 GiB).
Harness revision: `6f96efee17fce547c190207c1245fbdd04d111de`.
Regular CPython 3.14.3; NumPy 2.5.3; SciPy 1.18.1; PyTorch 2.10.0+cu126;
CUDA runtime 12.6; NVIDIA driver 560.35.03.
CPU governors stayed `powersave`; clocks were not locked. All data is float32.

## CPU medians

One-core runs were pinned to logical CPU 6. Eight-core runs used CPUs
0, 2, 4, 6, 8, 10, 12, 14, one thread per physical P core.
NumPy ufuncs and Bend scalar maps remain single-worker operations.

| Work | Library | Backend | One P core, ms | Eight P cores, ms |
|---|---|---|---:|---:|
| Negate 16.8M cells | numpy | native | 2.525 | 2.536 |
| Negate 16.8M cells | numpy | bend_dispatch | 2.537 | 2.588 |
| Negate 16.8M cells | numpy | bend_blas | 2.420 | 1.000 |
| Negate 16.8M cells | numpy | bend_map | 209.368 | 217.111 |
| Negate 16.8M cells | torch_cpu | native | 2.472 | 1.117 |
| Negate 16.8M cells | torch_cpu | bend_dispatch | 2.503 | 0.998 |
| Negate 16.8M cells | torch_cpu | bend_blas | 2.372 | 1.111 |
| Negate 16.8M cells | torch_cpu | bend_map | 216.863 | 217.166 |
| Matmul 2048×2048 | numpy | native | 101.172 | 13.267 |
| Matmul 2048×2048 | numpy | bend_dispatch | 101.191 | 13.287 |
| Matmul 2048×2048 | numpy | bend_blas | 101.024 | 13.285 |
| Matmul 2048×2048 | torch_cpu | native | 102.010 | 13.689 |
| Matmul 2048×2048 | torch_cpu | bend_dispatch | 101.997 | 13.713 |
| Matmul 2048×2048 | torch_cpu | bend_blas | 101.571 | 13.262 |

## CUDA medians

Data was already on the GPU; transfers are excluded. Submission was pinned to
P-core CPU 6. TF32 was disabled. Bend dispatch calls PyTorch kernels on the
original tensors; this is not a Bend CUDA kernel or CUDA buffer borrow.

| Work | Direct PyTorch, ms | Through Bend dispatch, ms |
|---|---:|---:|
| Negate 16.8M cells | 0.519 | 0.516 |
| Negate 16.8M cells, stride 2 | 1.047 | 1.047 |
| Matmul 2048×2048 | 1.311 | 1.305 |

## Validation and limits

Every accepted case passed the independent value reference, output identity
and storage checks. Recorded thread masks stayed inside the selected P cores,
and recorded native thread pools matched the requested limits.

Congestion checks passed before every timed worker: overall host CPU ≤10%,
each selected CPU and SMT sibling ≤5%, pressure and swap limits satisfied.
CUDA endpoints had no other compute process, 0% GPU utilization and 2 MiB
driver memory. Busy samples were retained and waited out. The eight-core run
refused its final case after new CPU interference; its incomplete manifest was
preserved, and that case passed a separate fresh-preflight retry.

These tables report seven-sample medians, not a causal overhead estimate.
Raw samples and variability are retained in the local result directories:

- Single-core CPU/CUDA: `results/initial-20261002-cpu6/REPORT.md`
- Eight-core report before the final refusal: `results/initial-20261002-pcores8/REPORT.md`
- Final eight-core case retry: `results/initial-20261002-pcores8/retry-result.json`

Raw results are ignored by Git; this summary records the measured medians.
Warmed single-core timing increased peak RSS by at most 0.219 MiB and showed
no PyTorch CUDA allocator peak growth. This does not establish allocation-free
execution: warmed provider/runtime workspace and non-PyTorch GPU allocations
are outside that incremental measurement. The identity checks and bridge
implementation supply separate evidence for shared input/output storage.

For these workloads, bulk calls through Bend had similar timings to direct
kernel calls. Scalar Bend maps were much slower for negation. Results depend
on providers, layouts, cache state and thread counts; benchmark the actual
algorithm before drawing broader conclusions.
