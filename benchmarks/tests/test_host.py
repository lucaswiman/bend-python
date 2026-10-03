"""Verify hybrid topology selection and congestion accounting with Linux fixtures."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks import host


class BenchmarkHostTests(unittest.TestCase):
    def test_invalid_sampling_interval_and_idle_gpu_limits(self):
        for seconds in (0, -1, float("inf"), float("nan")):
            result = host.preflight([0], seconds=seconds)
            self.assertFalse(result["ok"])
            self.assertIn("finite and positive", result["reasons"][0])
        gpu = {"compute_processes": [], "memory_used_mib": 2, "utilization_percent": 0}
        self.assertFalse(host._gpu_reasons(gpu))
        gpu["compute_processes"] = [{"pid": os.getpid() + 1, "name": "python"}]
        gpu["utilization_percent"] = 6
        gpu["memory_used_mib"] = 3
        reasons = " ".join(host._gpu_reasons(gpu))
        for field in ("Other GPU compute processes", "utilization_percent", "memory_used_mib"):
            self.assertIn(field, reasons)

    def test_linux_cpu_ranges(self):
        self.assertEqual(host.parse_cpu_list("0-3,8,10-11\n"), [0, 1, 2, 3, 8, 10, 11])
        for invalid in ("", "3-1", "-1", "1-2-3", "0,wat"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                host.parse_cpu_list(invalid)

    def test_verified_topology_rejects_e_cores_smt_and_disallowed_cpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pmu = root / "cpu_core/cpus"
            pmu.parent.mkdir()
            pmu.write_text("0-15\n")
            (root / "online").write_text("0-27\n")
            for cpu in range(16):
                siblings = root / f"cpu{cpu}/topology/thread_siblings_list"
                siblings.parent.mkdir(parents=True)
                siblings.write_text(f"{cpu // 2 * 2}-{cpu // 2 * 2 + 1}\n")
            with (
                patch.object(host, "P_CORE_SOURCE", pmu),
                patch.object(host, "CPU_SYSFS", root),
                patch.object(host.os, "sched_getaffinity", return_value=set(range(14))),
            ):
                topology = host.discover_p_cores()
                self.assertEqual(topology["p_cpus"], list(range(16)))
                self.assertEqual(
                    topology["core_sibling_groups"], [[i, i + 1] for i in range(0, 16, 2)]
                )
                self.assertFalse(host._selection_reasons([0, 2, 4], topology))
                for selection, reason in (
                    ([16], "not verified P cores"),
                    ([0, 1], "share one physical P core"),
                    ([14], "outside caller affinity"),
                    ([0, 0], "duplicates"),
                ):
                    with self.subTest(selection=selection):
                        result = host.preflight(selection, seconds=0.01)
                        self.assertFalse(result["ok"])
                        self.assertIn(reason, " ".join(result["reasons"]))
                pmu.unlink()
                with self.assertRaisesRegex(RuntimeError, "Cannot verify P cores"):
                    host.discover_p_cores()

    def test_congestion_includes_unselected_smt_sibling_and_pressure_deltas(self):
        before = {
            "monotonic_seconds": 100.0,
            "cpu_ticks": host._parse_cpu_stat(
                "cpu 100 0 0 900 0 0 0 0 70 0\n"
                "cpu0 10 0 0 90 0 0 0 0 7 0\n"
                "cpu1 10 0 0 90 0 0 0 0 7 0\n"
                "intr 123\n"
            ),
            "pressure": {name: {"some": {"total": "100"}} for name in ("cpu", "io", "memory")},
            "swap_pages": {"pswpin": 0, "pswpout": 0},
            "mem_available_bytes": 8 * 1024**3,
        }
        after = copy.deepcopy(before)
        after["monotonic_seconds"] += 3
        after["cpu_ticks"] = host._parse_cpu_stat(
            "cpu 110 0 0 1890 0 0 0 0 77 0\n"
            "cpu0 11 0 0 189 0 0 0 0 8 0\n"
            "cpu1 10 0 0 190 0 0 0 0 7 0\n"
        )
        metrics, reasons = host._congestion(before, after, [0, 1])
        self.assertEqual(metrics["busy_percent"]["cpu"], 1.0)
        self.assertFalse(reasons)
        after["cpu_ticks"]["cpu1"][0] += 100
        after["pressure"]["cpu"]["some"]["total"] = "180100"
        after["pressure"]["memory"]["some"]["total"] = "60100"
        after["pressure"]["io"]["some"]["total"] = "180100"
        after["swap_pages"]["pswpout"] = 1
        after["mem_available_bytes"] = 3 * 1024**3
        _, reasons = host._congestion(before, after, [0, 1])
        for reason in (
            "cpu1 busy",
            "cpu some pressure",
            "memory some pressure",
            "io some pressure",
            "Active swap",
            "MemAvailable",
        ):
            self.assertIn(reason, " ".join(reasons))


if __name__ == "__main__":
    unittest.main()
