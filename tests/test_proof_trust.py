"""Fail-closed classification of the new checker's explicit trust refusal."""

from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
import unittest

from setuptools.errors import CompileError

from bend_python.build import check_proof
from bend_python.proof_trust import SDK_PROOF_TRUST, classify_proof_check

PASS = "ALL PROOFS CHECK\nUse --verdict for mathematical validity.\n"
TRUST = (
    "SOME PROOFS FAIL\n"
    "Error: 2 defs rely on unsafe or foreign code:\n"
    "defs that depend on foreign effects: F.effect, Laws.wrapper\n"
)
POLICY = {"foreign effects": ["F.effect", "Laws.wrapper"]}
HINT = (
    "Hint: no law or proof uses these defs. Move them to a file this proof does not import, "
    "and the proofs will check: "
)


class ProofTrustTests(unittest.TestCase):
    def reject(self, returncode=1, stdout="", stderr=TRUST, policy=POLICY):
        with self.assertRaises(ValueError):
            classify_proof_check(returncode, stdout, stderr, policy)

    def test_strict_roots_have_no_declared_trust(self):
        self.assertEqual(classify_proof_check(0, PASS, ""), "strict_bend_check_no_declared_trust")
        for root in ("THREAD_PROOF.bend", "BLAS_PROOF.bend"):
            self.assertEqual(SDK_PROOF_TRUST[root], {})
        self.reject(returncode=0, stdout=PASS, stderr="")
        self.reject(returncode=0, stdout=PASS, stderr="warning\n", policy={})
        self.reject(returncode=0, stdout="ALL PROOFS CHECK\n", stderr="", policy={})
        self.reject(policy={})

    def test_reviewed_trust_is_distinct_from_strict_success(self):
        self.assertEqual(
            classify_proof_check(1, "", TRUST, POLICY), "typed_with_declared_trust_boundary"
        )
        self.reject(stdout="ALL PROOFS CHECK\n")

    def test_subprocess_preserves_diagnostic_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "diagnostic.py"
            for newline in ("\n", "\r\n", "\r"):
                with self.subTest(newline=repr(newline)):
                    diagnostic = TRUST.replace("\n", newline).encode("utf-8")
                    program.write_text(
                        f"import os\nos.write(2, {diagnostic!r})\nraise SystemExit(1)\n"
                    )
                    with redirect_stderr(io.StringIO()):
                        if newline == "\n":
                            self.assertEqual(
                                check_proof(sys.executable, program, trust=POLICY),
                                "typed_with_declared_trust_boundary",
                            )
                        else:
                            with self.assertRaises(CompileError):
                                check_proof(sys.executable, program, trust=POLICY)

    def test_complete_exact_unused_import_hint(self):
        self.assertEqual(
            classify_proof_check(1, "", TRUST + HINT + "F.effect, Laws.wrapper\n", POLICY),
            "typed_with_declared_trust_boundary",
        )
        self.reject(stderr=TRUST + HINT + "F.effect\n")
        self.reject(stderr=TRUST + HINT + "Laws.wrapper, F.effect\n")
        self.reject(stderr=TRUST + "Hint: ignored errors\n")

    def test_singular_and_union_count(self):
        singular = TRUST.replace("2 defs rely", "1 def relies").replace(
            "F.effect, Laws.wrapper", "F.effect"
        )
        self.assertEqual(
            classify_proof_check(1, "", singular, {"foreign effects": ["F.effect"]}),
            "typed_with_declared_trust_boundary",
        )
        overlap = TRUST.replace(
            "defs that depend on foreign effects:",
            "defs that depend on @unsafe: F.effect\ndefs that depend on foreign effects:",
        )
        self.assertEqual(
            classify_proof_check(1, "", overlap, {"@unsafe": ["F.effect"], **POLICY}),
            "typed_with_declared_trust_boundary",
        )
        for bad in (
            singular.replace("def relies", "defs rely"),
            TRUST.replace("defs rely", "def relies"),
            TRUST.replace("2 defs", "3 defs"),
        ):
            with self.subTest(output=bad):
                self.reject(stderr=bad)

    def test_new_removed_renamed_or_reclassified_dependencies_fail(self):
        for bad in (
            TRUST.replace("F.effect", "Other.effect"),
            TRUST.replace("F.effect, Laws.wrapper", "F.effect"),
            TRUST.replace("2 defs", "3 defs").replace("Laws.wrapper", "Laws.wrapper, F.new"),
            TRUST.replace("foreign effects:", "@unsafe:"),
        ):
            with self.subTest(output=bad):
                self.reject(stderr=bad)

    def test_real_error_shapes_never_become_trust_success(self):
        for bad in (
            "SOME PROOFS FAIL\nError: 1 TODO found.\nThe code is incomplete.\n",
            "SOME PROOFS FAIL\nType mismatch:\nexpected True observed False\n",
            "SOME PROOFS FAIL\nSorry - this is a mismatch between the TypeScript "
            "implementation, and the formalized BendTT kernel.\n",
            "Type mismatch\n" + TRUST,
            TRUST + "Error: a remaining hole\n",
            TRUST.rstrip("\n"),
            TRUST.replace("\n", "\r\n"),
            TRUST + "\n",
        ):
            with self.subTest(output=bad):
                self.reject(stderr=bad)
        for returncode in (-9, -15, 2, 124):
            with self.subTest(returncode=returncode):
                self.reject(returncode=returncode)

    def test_duplicate_or_unknown_categories_and_names_fail(self):
        for bad in (
            TRUST.replace("F.effect, Laws.wrapper", "F.effect, F.effect"),
            TRUST + "defs that depend on foreign effects: F.effect\n",
            TRUST.replace("foreign effects:", "unknown effects:"),
            TRUST.replace("F.effect, Laws.wrapper", ""),
            TRUST.replace("F.effect, Laws.wrapper", " F.effect, Laws.wrapper"),
            TRUST + "defs that depend on @unsafe: F.effect\n",
        ):
            with self.subTest(output=bad):
                self.reject(stderr=bad)

    def test_invalid_policy_fails(self):
        for policy in (
            [],
            {"foreign effects": "F.effect"},
            {"unknown": ["F.effect"]},
            {"foreign effects": ["F.effect", "F.effect"]},
            {"foreign effects": []},
            {"foreign effects": [None]},
            {"foreign effects": 1},
        ):
            with self.subTest(policy=policy):
                self.reject(policy=policy)


if __name__ == "__main__":
    unittest.main()
