"""Fail closed when the private runtime no longer matches the reviewed ABI."""

from pathlib import Path
import unittest

from setuptools.errors import CompileError

from bend_python import BendExtension
from bend_python.build import _patch_runtime

ROOT = Path(__file__).resolve().parents[1]


class RuntimePatchTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "tests/fixtures/runtime-2.0.38.c").read_text()
        self.source += (ROOT / "bend/python.c").read_text()

    def test_real_runtime_sections_relocate_lease_state(self):
        result = _patch_runtime(self.source, "fixture.module")
        self.assertEqual(result.count("static _Thread_local BpRuntime* bp_current;"), 1)
        self.assertLess(result.index("#define CORPUS"), result.index("void err_fail"))
        self.assertNotIn("static u64*    CORPUS;", result)
        self.assertNotIn("static u64 corpus_size;", result)
        self.assertIn("#define corpus_size (bp_current->heap_size)", result)
        self.assertIn("  corpus_size = size;", result)
        setup = result[result.index("static u64* corpus_setup(") :]
        self.assertLess(setup.index("  corpus_size = size;"), setup.index("  corpus_lay(H, size);"))
        self.assertIn("static void err_fail(const char* msg) { bendpy_panic(msg); }", result)
        self.assertIn("Bend worker threads are unsupported", result)
        self.assertNotIn("pthread_create(&tid", result)
        self.assertNotIn("#define PRESERVE(A) __attribute__((A))", result)
        self.assertIn("#define WL_BANK Term r0 = 0, r1 = 0, r2 = 0, r3 = 0;", result)
        self.assertIn("#define WL_OPEN    { WL_BANK u32 rn = 0;", result)

    def test_changed_private_runtime_sections_are_rejected(self):
        changes = [
            ("static u64*    CORPUS;", "static void* CORPUS;"),
            ("3 * NCLS_ALL", "3 * UNKNOWN_CLASSES"),
            ("static DEV Term*  io_stk;", "static void* io_stk;"),
            ("  if (err_claim()) fflush(stdout);", "  fflush(stdout);"),
            (
                "  for (u32 w = 1; w < pool_size; w += 1)",
                "  for (u32 w = 0; w < pool_size; w += 1)",
            ),
            ("  corpus_size = size;", "  corpus_size = 0;"),
            (
                "  CORPUS     = gpu ? gpu_map(size) : corpus_map(size);",
                "  CORPUS = corpus_map(size);",
            ),
            ("  && __OPTIMIZE__", "  && UNKNOWN_CONVENTION"),
            ("#define WL_BANK Term r0, r1, r2, r3;", "#define WL_BANK Term r[4];"),
            ("#define WL_OPEN    { WL_BANK u32 rn;", "#define WL_OPEN    { WL_BANK u64 rn;"),
        ]
        for old, new in changes:
            with self.subTest(section=old):
                self.assertIn(old, self.source)
                with self.assertRaises(CompileError):
                    _patch_runtime(self.source.replace(old, new), "fixture.module")

    def test_reference_counter_mode_changes_only_the_two_updates(self):
        self.assertIs(BendExtension("fixture", "module.bend").bend_atomic_refcounts, True)
        self.assertIs(
            BendExtension("fixture", "module.bend", atomic_refcounts=False).bend_atomic_refcounts,
            False,
        )
        with self.assertRaisesRegex(TypeError, "atomic_refcounts must be a bool"):
            BendExtension("fixture", "module.bend", atomic_refcounts="False")

        default = _patch_runtime(self.source, "fixture.module")
        self.assertEqual(
            default, _patch_runtime(self.source, "fixture.module", atomic_refcounts=True)
        )
        expected = default.replace(
            "  u32 c = a32_add(a32_at(e.mem, r), k);",
            "  DEV u32* p = a32_at(e.mem, r);\n  u32 c = a32_load(p);\n  a32_store(p, c + k);",
        ).replace(
            "      if ((a32_sub_rel(p, 1) & RFC_CNT) != 1) {",
            "      u32 c = a32_load(p);\n"
            "      a32_store_rel(p, c - 1);\n"
            "      if ((c & RFC_CNT) != 1) {",
        )
        self.assertNotEqual(default, expected)
        self.assertEqual(
            expected, _patch_runtime(self.source, "fixture.module", atomic_refcounts=False)
        )

    def test_changed_reference_counter_sites_are_rejected_when_opted_in(self):
        for old, new in [
            (
                "  u32 c = a32_add(a32_at(e.mem, r), k);",
                "  u32 c = a32_add(a32_at(e.mem, r), k + 1);",
            ),
            (
                "      if ((a32_sub_rel(p, 1) & RFC_CNT) != 1) {",
                "      if ((a32_sub_rel(p, 2) & RFC_CNT) != 1) {",
            ),
        ]:
            self.assertEqual(self.source.count(old), 1)
            for changed in (self.source.replace(old, new), self.source + old):
                with self.subTest(section=old, changed=changed[-len(old) :]):
                    _patch_runtime(changed, "fixture.module")
                    with self.assertRaises(CompileError):
                        _patch_runtime(changed, "fixture.module", atomic_refcounts=False)

    def test_ambiguous_context_and_duplicate_global_are_rejected(self):
        for extra in [
            "// BENDPY_RUNTIME_CONTEXT_BEGIN\n// BENDPY_RUNTIME_CONTEXT_END\n",
            self.source[
                self.source.index("static u64*    CORPUS;") : self.source.index(
                    "static u32             pool_size;"
                )
                + len("static u32             pool_size;")
            ],
        ]:
            with self.subTest(extra=extra[:50]), self.assertRaises(CompileError):
                _patch_runtime(self.source + extra, "fixture.module")


if __name__ == "__main__":
    unittest.main()
