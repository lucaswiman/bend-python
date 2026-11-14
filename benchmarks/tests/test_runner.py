"""Benchmark correctness must reject no-ops and writes into strided gaps."""

import importlib.util
import unittest

from benchmarks.run import PATTERN, validate_negation


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy benchmark dependency is optional")
class BenchmarkValidationTests(unittest.TestCase):
    def test_initial_negation_rejects_noop_even_when_final_parity_would_pass(self):
        import numpy as np

        original = np.tile(np.array(PATTERN, dtype=np.float32), 8)
        validate_negation(np, original, "contiguous", 10)
        with self.assertRaises(AssertionError):
            validate_negation(np, original, "contiguous", 1)
        np.negative(original, out=original)
        validate_negation(np, original, "contiguous", 1)

    def test_all_strided_cells_and_gaps_are_checked(self):
        import numpy as np

        original = np.tile(np.array(PATTERN, dtype=np.float32), 8)
        np.negative(original[::2], out=original[::2])
        validate_negation(np, original, "stride2", 1)
        original[5] *= -1
        with self.assertRaises(AssertionError):
            validate_negation(np, original, "stride2", 1)


if __name__ == "__main__":
    unittest.main()
