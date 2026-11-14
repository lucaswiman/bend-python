"""Read-only Linux topology and congestion checks for numerical benchmarks."""

import csv
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import subprocess
import time

P_CORE_SOURCE = Path("/sys/bus/event_source/devices/cpu_core/cpus")
CPU_SYSFS = Path("/sys/devices/system/cpu")
THRESHOLDS = {
    "selected_sibling_busy_percent": 5.0,
    "overall_cpu_busy_percent": 10.0,
    "cpu_pressure_percent": 1.0,
    "io_pressure_percent": 1.0,
    "memory_pressure_percent": 0.1,
    "minimum_mem_available_bytes": 4 * 1024**3,
    "maximum_swap_pages": 0,
    "gpu_utilization_percent": 5.0,
    "gpu_memory_used_mib": 2,
}


def parse_cpu_list(value):
    """Parse Linux cpulist syntax without assuming logical CPU numbering."""
    cpus = set()
    for field in value.strip().split(","):
        bounds = field.split("-")
        if len(bounds) not in (1, 2) or not all(part.isdecimal() for part in bounds):
            raise ValueError(f"Invalid Linux CPU list: {value!r}")
        first, last = int(bounds[0]), int(bounds[-1])
        if first > last:
            raise ValueError(f"Descending Linux CPU range: {field!r}")
        cpus.update(range(first, last + 1))
    return sorted(cpus)


def discover_p_cores():
    """Require Intel hybrid PMU evidence and verify physical core sibling groups."""
    try:
        raw_cpus = P_CORE_SOURCE.read_text().strip()
        p_cpus = parse_cpu_list(raw_cpus)
        online = set(parse_cpu_list((CPU_SYSFS / "online").read_text()))
        groups = set()
        for cpu in p_cpus:
            group = tuple(
                parse_cpu_list((CPU_SYSFS / f"cpu{cpu}/topology/thread_siblings_list").read_text())
            )
            if cpu not in group or not set(group) <= set(p_cpus):
                raise ValueError(f"Inconsistent P-core siblings for CPU {cpu}: {group}")
            groups.add(group)
        if not set(p_cpus) <= online:
            raise ValueError("P-core PMU lists offline CPUs")
        for group in groups:
            if any(set(group) & set(other) for other in groups if other != group):
                raise ValueError("P-core sibling groups overlap inconsistently")
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot verify P cores from {P_CORE_SOURCE} and CPU topology: {exc}"
        ) from exc
    return {
        "p_cpus": p_cpus,
        "core_sibling_groups": [list(group) for group in sorted(groups)],
        "detection_source": str(P_CORE_SOURCE),
        "raw_p_cpu_list": raw_cpus,
        "online_cpus": sorted(online),
        "allowed_cpus": sorted(os.sched_getaffinity(0)),
    }


def _selection_reasons(cpus, topology):
    reasons = []
    if not cpus or any(type(cpu) is not int or cpu < 0 for cpu in cpus):
        return ["Select at least one nonnegative integer CPU"]
    if len(cpus) != len(set(cpus)):
        reasons.append("Selected CPU list contains duplicates")
    outside_p = sorted(set(cpus) - set(topology["p_cpus"]))
    if outside_p:
        reasons.append(f"CPUs {outside_p} are not verified P cores")
    outside_affinity = sorted(set(cpus) - set(topology["allowed_cpus"]))
    if outside_affinity:
        reasons.append(f"CPUs {outside_affinity} are outside caller affinity")
    for group in topology["core_sibling_groups"]:
        if len(set(cpus) & set(group)) > 1:
            reasons.append(f"Selected CPUs share one physical P core: {group}")
    return reasons


def _parse_cpu_stat(value):
    counters = {}
    for line in value.splitlines():
        fields = line.split()
        if (
            fields
            and fields[0].startswith("cpu")
            and (fields[0] == "cpu" or fields[0][3:].isdecimal())
        ):
            # Guest counters are already included in user/nice; never sum them twice.
            ticks = [int(field) for field in fields[1:9]]
            if len(ticks) < 5:
                raise ValueError(f"Incomplete /proc/stat CPU row: {line}")
            counters[fields[0]] = ticks
    return counters


def _snapshot():
    pressure = {}
    for resource in ("cpu", "memory", "io"):
        pressure[resource] = {
            fields[0]: dict(field.split("=", 1) for field in fields[1:])
            for fields in (
                line.split() for line in Path(f"/proc/pressure/{resource}").read_text().splitlines()
            )
        }
        if "some" not in pressure[resource]:
            raise ValueError(f"Missing 'some' pressure for {resource}")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    vmstat = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
    return {
        "cpu_ticks": _parse_cpu_stat(Path("/proc/stat").read_text()),
        "pressure": pressure,
        "mem_available_bytes": int(memory["MemAvailable"].split()[0]) * 1024,
        "swap_pages": {name: int(vmstat[name]) for name in ("pswpin", "pswpout")},
        "monotonic_seconds": time.monotonic(),
    }


def _cpu_busy(before, after):
    delta = [end - start for start, end in zip(before, after, strict=True)]
    total = sum(delta)
    if total <= 0 or any(ticks < 0 for ticks in delta):
        raise ValueError("CPU accounting did not advance monotonically")
    return 100.0 * (total - delta[3] - delta[4]) / total


def _congestion(before, after, monitored_cpus):
    elapsed = after["monotonic_seconds"] - before["monotonic_seconds"]
    if elapsed <= 0:
        raise ValueError("Congestion sampling interval must be positive")
    busy = {
        name: _cpu_busy(before["cpu_ticks"][name], after["cpu_ticks"][name])
        for name in ["cpu", *(f"cpu{cpu}" for cpu in monitored_cpus)]
    }
    pressure = {}
    for resource, rows in before["pressure"].items():
        pressure[resource] = {}
        for kind, fields in rows.items():
            delta = int(after["pressure"][resource][kind]["total"]) - int(fields["total"])
            if delta < 0:
                raise ValueError(f"{resource} PSI counter decreased")
            pressure[resource][kind] = 100.0 * delta / (elapsed * 1_000_000)
    swap = {
        name: after["swap_pages"][name] - before["swap_pages"][name]
        for name in before["swap_pages"]
    }
    if any(delta < 0 for delta in swap.values()):
        raise ValueError("Swap accounting counter decreased")
    evidence = {
        "sample_seconds": elapsed,
        "busy_percent": busy,
        "pressure_percent": pressure,
        "swap_page_deltas": swap,
        "minimum_mem_available_bytes": min(
            before["mem_available_bytes"], after["mem_available_bytes"]
        ),
    }
    reasons = []
    for cpu, percent in busy.items():
        threshold = THRESHOLDS[
            "overall_cpu_busy_percent" if cpu == "cpu" else "selected_sibling_busy_percent"
        ]
        if percent > threshold:
            reasons.append(f"{cpu} busy {percent:.2f}% exceeds {threshold:.2f}%")
    for resource, kinds in pressure.items():
        threshold = THRESHOLDS[f"{resource}_pressure_percent"]
        for kind, percent in kinds.items():
            if percent > threshold:
                reasons.append(
                    f"{resource} {kind} pressure {percent:.2f}% exceeds {threshold:.2f}%"
                )
    if evidence["minimum_mem_available_bytes"] < THRESHOLDS["minimum_mem_available_bytes"]:
        reasons.append("MemAvailable is below 4 GiB")
    if any(delta > THRESHOLDS["maximum_swap_pages"] for delta in swap.values()):
        reasons.append(f"Active swap during sampling: {swap}")
    return evidence, reasons


def _gpu_snapshot(index):
    commands = {
        "device": [
            "nvidia-smi",
            f"--id={index}",
            "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu,"
            "driver_version,temperature.gpu,clocks.sm,clocks.mem,power.draw",
            "--format=csv,noheader,nounits",
        ],
        "processes": [
            "nvidia-smi",
            f"--id={index}",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
    }
    raw = {
        name: subprocess.run(command, check=True, capture_output=True, text=True, timeout=10).stdout
        for name, command in commands.items()
    }
    rows = list(csv.reader(raw["device"].splitlines(), skipinitialspace=True))
    if len(rows) != 1 or len(rows[0]) != 11:
        raise ValueError(f"Expected one NVIDIA GPU for index {index}: {raw['device']!r}")
    gpu_index, uuid, name, used, total, utilization, driver, temperature, sm, mem, power = (
        field.strip() for field in rows[0]
    )
    processes = []
    for row in csv.reader(raw["processes"].splitlines(), skipinitialspace=True):
        if len(row) != 3:
            raise ValueError(f"Invalid NVIDIA compute process row: {row!r}")
        processes.append(
            {"pid": int(row[0]), "name": row[1].strip(), "memory_used_mib": row[2].strip()}
        )
    return {
        "index": int(gpu_index),
        "uuid": uuid,
        "name": name,
        "memory_used_mib": float(used),
        "memory_total_mib": float(total),
        "utilization_percent": float(utilization),
        "driver_version": driver,
        "temperature_celsius": temperature,
        "sm_clock_mhz": sm,
        "memory_clock_mhz": mem,
        "power_watts": power,
        "compute_processes": processes,
        "raw_nvidia_smi": raw,
    }


def _gpu_reasons(snapshot):
    reasons = []
    others = [item for item in snapshot["compute_processes"] if item["pid"] != os.getpid()]
    if others:
        reasons.append(f"Other GPU compute processes are active: {others}")
    for field, threshold_name in (
        ("utilization_percent", "gpu_utilization_percent"),
        ("memory_used_mib", "gpu_memory_used_mib"),
    ):
        threshold = THRESHOLDS[threshold_name]
        if snapshot[field] > threshold:
            reasons.append(f"GPU {field} {snapshot[field]} exceeds {threshold}")
    return reasons


def preflight(cpus, *, cuda=False, gpu_index=0, seconds=3.0):
    """Sample congestion without changing affinity; run before scientific imports.

    CUDA runs require an idle dedicated GPU: at most 2 MiB driver memory,
    at most 5% utilization, and no other compute process at either endpoint.
    These samples do not guarantee that interference cannot begin during a run.
    """
    cpus = list(cpus)
    result = {
        "ok": False,
        "reasons": [],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "selected_cpus": cpus,
        "thresholds": dict(THRESHOLDS),
        "evidence": {},
    }
    try:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Preflight seconds must be finite and positive")
        topology = discover_p_cores()
        result["topology"] = topology
        result["reasons"].extend(_selection_reasons(cpus, topology))
        if not result["reasons"]:
            monitored = sorted(
                {
                    cpu
                    for group in topology["core_sibling_groups"]
                    if set(group) & set(cpus)
                    for cpu in group
                }
            )
            result["monitored_sibling_cpus"] = monitored
            if cuda:
                result["evidence"]["gpu_before"] = _gpu_snapshot(gpu_index)
            before = _snapshot()
            result["evidence"]["before"] = before
            time.sleep(seconds)
            after = _snapshot()
            result["evidence"]["after"] = after
            metrics, reasons = _congestion(before, after, monitored)
            result["evidence"]["metrics"] = metrics
            result["reasons"].extend(reasons)
            if cuda:
                result["evidence"]["gpu_after"] = _gpu_snapshot(gpu_index)
                for phase in ("gpu_before", "gpu_after"):
                    result["reasons"].extend(
                        f"{phase}: {reason}" for reason in _gpu_reasons(result["evidence"][phase])
                    )
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result["reasons"].append(f"Preflight evidence unavailable: {exc}")
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    result["ok"] = not result["reasons"]
    return result
