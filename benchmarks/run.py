"""Fresh-process CPU/CUDA diagnostics; run from any directory, see README.md."""

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
from importlib.metadata import distributions
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time

if __package__:
    from .host import discover_p_cores, parse_cpu_list, preflight
else:
    from host import discover_p_cores, parse_cpu_list, preflight

DIRECTORY = Path(__file__).resolve().parent
PATTERN = (1.25, -2.5, 3.75, -4.0)
LIBRARIES = ("numpy", "torch_cpu", "torch_cuda")


def wait_for_idle(cpus, *, cuda, gpu_index, seconds):
    attempts = []
    for attempt in range(4):
        receipt = preflight(cpus, cuda=cuda, gpu_index=gpu_index, seconds=seconds)
        attempts.append(receipt)
        if receipt["ok"] or "metrics" not in receipt["evidence"]:
            break
        if attempt < 3:
            time.sleep(5)
    receipt["earlier_attempts"] = attempts[:-1]
    return receipt


def cases(sizes, matrix_sizes, layouts, libraries):
    for library in libraries:
        for size in sizes:
            for layout in layouts:
                if layout == "reversed" and library != "numpy":
                    continue  # PyTorch does not support negative strides.
                backends = ["native", "bend_dispatch"]
                if library != "torch_cuda":
                    backends.append("bend_map")
                    if layout in ("contiguous", "stride2"):
                        backends.append("bend_blas")
                for backend in backends:
                    yield dict(
                        operation="negate",
                        library=library,
                        size=size,
                        layout=layout,
                        backend=backend,
                    )
        for size in matrix_sizes:
            for backend in ("native", "bend_dispatch", "bend_blas"):
                if library == "torch_cuda" and backend == "bend_blas":
                    continue
                yield dict(
                    operation="matmul",
                    library=library,
                    size=size,
                    layout="column_major",
                    backend=backend,
                )


def cpu_rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def thread_affinities(cpus):
    rows = {
        path.name: sorted(os.sched_getaffinity(int(path.name)))
        for path in Path("/proc/self/task").iterdir()
    }
    if any(not set(affinity) <= set(cpus) for affinity in rows.values()):
        raise RuntimeError(f"A benchmark thread escaped selected P cores: {rows}")
    return rows


def validate_negation(np, actual, layout, calls):
    expected = np.array(PATTERN, dtype=np.float32)
    if calls % 2:
        if layout == "stride2":
            expected[::2] *= -1
        else:
            expected *= -1
    actual = actual.reshape(-1, 4)
    np.testing.assert_array_equal(actual, np.broadcast_to(expected, actual.shape))


def measure(call, synchronize, *, samples, target_seconds, cuda, torch):
    # Calibration and warmup are excluded. Negation toggles sign without decay.
    calls = 0
    warm_until = time.perf_counter() + 0.2
    while time.perf_counter() < warm_until:
        for _ in range(4):
            call()
        calls += 4
        synchronize()
    start = time.perf_counter_ns()
    call()
    synchronize()
    one_seconds = (time.perf_counter_ns() - start) / 1e9
    calls += 1
    iterations = max(1, min(10000, math.ceil(target_seconds / one_seconds)))
    if cuda:
        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        # Initialize events outside both timing and allocation measurements.
        start_event.record()
        end_event.record()
        end_event.synchronize()
        torch.cuda.reset_peak_memory_stats()
        cuda_before = torch.cuda.memory_allocated()
        cuda_reserved_before = torch.cuda.memory_reserved()
    rss_before = cpu_rss()
    wall_samples, event_samples = [], []
    for _ in range(samples):
        synchronize()
        if cuda:
            start_event.record()
        start = time.perf_counter_ns()
        for _ in range(iterations):
            call()
        if cuda:
            end_event.record()
        synchronize()
        wall_samples.append((time.perf_counter_ns() - start) / 1e9 / iterations)
        if cuda:
            # Includes stream gaps caused by host submission; not kernel-only time.
            event_samples.append(start_event.elapsed_time(end_event) / 1000 / iterations)
        calls += iterations
    result = {
        "iterations_per_sample": iterations,
        "samples_seconds": wall_samples,
        "median_seconds": statistics.median(wall_samples),
        "min_seconds": min(wall_samples),
        "max_seconds": max(wall_samples),
        "relative_iqr": (
            statistics.quantiles(wall_samples, n=4)[2] - statistics.quantiles(wall_samples, n=4)[0]
        )
        / statistics.median(wall_samples),
        "cpu_peak_rss_before_bytes": rss_before,
        "cpu_peak_rss_after_bytes": cpu_rss(),
    }
    result["cpu_peak_rss_growth_bytes"] = max(0, result["cpu_peak_rss_after_bytes"] - rss_before)
    if cuda:
        result.update(
            cuda_event_samples_seconds=event_samples,
            cuda_event_median_seconds=statistics.median(event_samples),
            cuda_allocated_before_bytes=cuda_before,
            cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
            cuda_reserved_before_bytes=cuda_reserved_before,
            cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        )
        result["cuda_peak_allocated_growth_bytes"] = (
            result["cuda_peak_allocated_bytes"] - cuda_before
        )
    return result, calls


def worker(config):
    case = config["case"]
    cpus = config["cpus"]
    cuda = case["library"] == "torch_cuda"
    # Verify siblings before pinning; imports and all child threads inherit affinity.
    receipt = wait_for_idle(
        cpus, cuda=cuda, gpu_index=config["gpu"], seconds=config["preflight_seconds"]
    )
    if not receipt["ok"]:
        return {"case": case, "status": "congested", "preflight": receipt}
    os.sched_setaffinity(0, cpus)

    import bend_benchmark as bend
    import numpy as np
    import scipy
    from threadpoolctl import threadpool_info, threadpool_limits
    import torch

    torch.set_num_threads(config["threads"])
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    if cuda:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but the installed PyTorch cannot use CUDA")
        torch.cuda.set_device(0)
    synchronize = torch.cuda.synchronize if cuda else lambda: None
    size, layout, backend = case["size"], case["layout"], case["backend"]
    is_numpy = case["library"] == "numpy"
    device = "cuda" if cuda else "cpu"

    def allocate(shape, *, column_major=False):
        if is_numpy:
            return np.empty(shape, dtype=np.float32, order="F" if column_major else "C")
        tensor = torch.empty(shape, dtype=torch.float32, device=device)
        return tensor.T if column_major else tensor

    def fill_pattern(value, pattern):
        if is_numpy:
            value[...] = pattern
        else:
            value.copy_(torch.tensor(pattern, dtype=torch.float32, device=device))

    with threadpool_limits(limits=config["threads"]), torch.no_grad():
        if case["operation"] == "negate":
            base = allocate(size * (2 if layout == "stride2" else 1))
            fill_pattern(base.reshape(-1, 4), PATTERN)
            x = base[::2] if layout == "stride2" else base
            if layout == "reversed":
                x = x[::-1]
            elif layout == "transposed":
                x = x.reshape(256, -1).T
            arguments = (x, x) if is_numpy else (x,)
            target = np.negative if is_numpy else torch.Tensor.neg_
            map_call = partial(bend.numpy_negate if is_numpy else bend.torch_negate, x)
            blas_call = partial(bend.numpy_scale if is_numpy else bend.torch_scale, -1.0, x)
            output = x
            storage_bytes = base.size * 4 if is_numpy else base.numel() * 4
            useful_bytes = size * 8  # One F32 read and write, excluding stride gaps.
            flops = size
        else:
            a, b, output = (allocate((size, size), column_major=True) for _ in range(3))
            rows = np.resize(np.array(PATTERN, dtype=np.float32), size).reshape(-1, 1)
            columns = np.resize(np.array((0.5, 1.5, -2.0, 4.0), dtype=np.float32), size)
            fill_pattern(a, rows)
            fill_pattern(b, columns)
            output.fill(0) if is_numpy else output.zero_()
            target = partial(np.matmul if is_numpy else torch.mm, out=output)
            arguments = (a, b)
            blas_call = partial(bend.numpy_matmul if is_numpy else bend.torch_matmul, a, b, output)
            storage_bytes = size * size * 12
            useful_bytes = storage_bytes
            flops = 2 * size**3

        if backend == "native":
            call = partial(target, *arguments)
        elif backend == "bend_dispatch":
            call = partial(bend.invoke, target, *arguments)
        elif backend == "bend_map":
            call = map_call
        else:
            call = blas_call

        def validate(calls):
            if case["operation"] == "negate":
                for start in range(0, len(base), 65536):
                    chunk = base[start : start + 65536]
                    actual = chunk if is_numpy else chunk.cpu().numpy()
                    validate_negation(np, actual, layout, calls)
            else:
                for start in range(0, size, 64):
                    chunk = output[start : start + 64]
                    actual = chunk if is_numpy else chunk.cpu().numpy()
                    expected = rows[start : start + 64] * columns * np.float32(size)
                    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-5)

        def pointer(value):
            return value.__array_interface__["data"][0] if is_numpy else value.data_ptr()

        original_pointer = pointer(output)
        if call() is not output:
            raise AssertionError("Operation did not return the original output object")
        synchronize()
        validate(1)  # An even timing loop alone would accept an unchanged input.
        result, calls = measure(
            call,
            synchronize,
            samples=config["samples"],
            target_seconds=config["sample_seconds"],
            cuda=cuda,
            torch=torch,
        )
        calls += 1  # Identity check above is also a negation.
        affinities = thread_affinities(cpus)
        if pointer(output) != original_pointer:
            raise AssertionError("Operation replaced output storage")
        # Check every output cell (and untouched stride gaps) AFTER RSS/VRAM readings.
        validate(calls)
        result.update(
            case=case,
            status="ok",
            preflight=receipt,
            cpu_threads=config["threads"],
            thread_affinities=affinities,
            packages={
                "numpy": np.__version__,
                "scipy": scipy.__version__,
                "torch": torch.__version__,
                "torch_cuda": torch.version.cuda,
            },
            thread_pools=threadpool_info(),
            logical_elements=size if case["operation"] == "negate" else size * size,
            input_output_storage_bytes=storage_bytes,
            output_pointer=original_pointer,
            storage_preserved=True,
            values_verified=True,
            extension_path=bend.__file__,
            extension_sha256=hashlib.sha256(Path(bend.__file__).read_bytes()).hexdigest(),
            installed_packages={dist.metadata["Name"]: dist.version for dist in distributions()},
            cuda_tf32_allowed=torch.backends.cuda.matmul.allow_tf32,
            useful_gb_per_second=useful_bytes / result["median_seconds"] / 1e9,
            gflops_per_second=flops / result["median_seconds"] / 1e9,
        )
        if cuda:
            result["cuda_device"] = torch.cuda.get_device_properties(0).name
    return result


def write_report(directory, rows, manifest):
    text = [
        "# Numerical benchmark",
        "",
        f"Started: {manifest['started_utc']}",
        "",
        f"Completed cases: {len(rows)}/{len(manifest['schedule'])}.",
        "",
        f"P-core CPUs: {manifest['cpus']}; CPU threads: {manifest['threads']}. "
        "CUDA timings exclude host/device transfers. All rows use float32.",
        "",
        "| Operation | Library | Backend | Size | Layout | Median ms | IQR / median | "
        "RSS growth MiB | CUDA allocated growth MiB |",
        "|---|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        case = row["case"]
        text.append(
            f"| {case['operation']} | {case['library']} | {case['backend']} | "
            f"{case['size']} | {case['layout']} | {row['median_seconds'] * 1000:.4f} | "
            f"{row['relative_iqr']:.1%} | {row['cpu_peak_rss_growth_bytes'] / 2**20:.2f} | "
            f"{row.get('cuda_peak_allocated_growth_bytes', 0) / 2**20:.2f} |"
        )
    text.extend(
        [
            "",
            "Raw samples, warm memory baselines, per-thread affinity, library versions "
            "and each preflight receipt are in results.jsonl. RSS growth is incremental "
            "process high-water growth after pre-touch and warmup; zero growth is not "
            "proof of no allocation. CUDA allocator statistics exclude driver/library "
            "allocations. CUDA event intervals include host submission gaps. Matrix "
            "throughput is dense 2*n^3 work; negation bandwidth counts useful cells only.",
            "",
        ]
    )
    (directory / "REPORT.md").write_text("\n".join(text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--cpus", help="One logical thread per verified P core; default: first P core"
    )
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--libraries", nargs="+", choices=LIBRARIES, default=list(LIBRARIES))
    parser.add_argument(
        "--layouts",
        nargs="+",
        choices=("contiguous", "stride2", "transposed", "reversed"),
        default=["contiguous", "stride2"],
    )
    parser.add_argument("--sizes", default="4096,1048576,16777216")
    parser.add_argument("--matrix-sizes", default="512,2048")
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--sample-seconds", type=float, default=0.05)
    parser.add_argument("--preflight-seconds", type=float, default=3.0)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.loads(args.worker))))
        return
    if args.output is None:
        parser.error("--output is required (an unused directory)")
    sizes = [int(value) for value in args.sizes.split(",")]
    matrix_sizes = [int(value) for value in args.matrix_sizes.split(",")]
    if any(size <= 0 or size % 4 for size in sizes + matrix_sizes):
        parser.error("sizes must be positive multiples of four")
    if "transposed" in args.layouts and any(size % 256 for size in sizes):
        parser.error("transposed vector sizes must be multiples of 256")
    if args.samples < 4 or not math.isfinite(args.sample_seconds) or args.sample_seconds <= 0:
        parser.error("use at least four samples and a finite positive sample duration")
    topology = discover_p_cores()
    cpus = parse_cpu_list(args.cpus) if args.cpus else [topology["core_sibling_groups"][0][0]]
    if not 1 <= args.threads <= len(cpus):
        parser.error("CPU threads must be between one and the number of selected P cores")
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = wait_for_idle(
        cpus,
        cuda="torch_cuda" in args.libraries,
        gpu_index=args.gpu,
        seconds=args.preflight_seconds,
    )
    (args.output / "preflight.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not receipt["ok"]:
        sys.exit("Benchmark refused: " + "; ".join(receipt["reasons"]))
    schedule = list(cases(sizes, matrix_sizes, args.layouts, args.libraries))
    random.Random(20261002).shuffle(schedule)
    sources = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (DIRECTORY / name for name in ("run.py", "host.py", "module.bend"))
    }
    manifest = dict(
        complete=False,
        started_utc=datetime.now(timezone.utc).isoformat(),
        command=sys.argv,
        python=sys.version,
        cpus=cpus,
        threads=args.threads,
        topology=topology,
        source_sha256=sources,
        schedule=schedule,
        git_head=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=DIRECTORY, text=True
        ).strip(),
        platform=platform.platform(),
        cpu_model=next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        cpu_governors={
            str(cpu): Path(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor")
            .read_text()
            .strip()
            for cpu in cpus
        },
        git_status=subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=DIRECTORY, text=True
        ),
        vendored_sdk_sha256={
            str(path.relative_to(DIRECTORY)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((DIRECTORY / "bend").glob("*"))
            if path.is_file()
        },
        configuration=vars(args) | {"output": str(args.output)},
    )
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    environment = os.environ | {
        name: str(args.threads)
        for name in (
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "BLIS_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        )
    }
    if "torch_cuda" in args.libraries:
        environment["CUDA_VISIBLE_DEVICES"] = receipt["evidence"]["gpu_before"]["uuid"]
    rows = []
    with (args.output / "results.jsonl").open("w") as stream:
        for number, case in enumerate(schedule, 1):
            config = dict(
                case=case,
                cpus=cpus,
                threads=args.threads,
                gpu=args.gpu,
                samples=args.samples,
                sample_seconds=args.sample_seconds,
                preflight_seconds=args.preflight_seconds,
            )
            # CUDA-visible ordinal is zero, but preflight uses the physical index.
            command = [sys.executable, str(DIRECTORY / "run.py"), "--worker", json.dumps(config)]
            stderr_path = args.output / f"worker-{number:03d}.stderr"
            with stderr_path.open("w") as stderr:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    env=environment,
                    cwd=DIRECTORY,
                    timeout=600,
                )
            if result.returncode:
                sys.exit(f"Worker failed for {case}: {stderr_path.read_text()}")
            row = json.loads(result.stdout)
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            if row["status"] != "ok":
                sys.exit("Benchmark refused: " + "; ".join(row["preflight"]["reasons"]))
            rows.append(row)
            write_report(args.output, rows, manifest)
            print(
                f"[{number}/{len(schedule)}] {case}: {row['median_seconds'] * 1000:.4f} ms",
                flush=True,
            )
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["complete"] = True
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
