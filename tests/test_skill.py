"""The agent skill stays valid and in step with the SDK."""

import os
from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from bend_python import BEND_VERSION, vendor

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/bend-python"


def frontmatter(text):
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    fields = {}
    for line in match[1].splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields


def bend():
    local = ROOT / ".tools/bend/bin/bend"
    candidates = [os.environ.get("BEND"), str(local), shutil.which("bend")]
    for candidate in filter(None, candidates):
        try:
            version = subprocess.run([candidate, "version"], capture_output=True, text=True).stdout
        except OSError:
            continue
        if version.strip() == f"bend {BEND_VERSION}":
            return candidate
    return None


class SkillTests(unittest.TestCase):
    def test_frontmatter_follows_the_agent_skills_spec(self):
        fields = frontmatter((SKILL / "SKILL.md").read_text())
        self.assertEqual(fields["name"], SKILL.name)
        self.assertRegex(fields["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertTrue(0 < len(fields["description"]) <= 1024)
        self.assertLessEqual(len(fields["compatibility"]), 500)

    def test_versions_match_the_sdk(self):
        text = (SKILL / "SKILL.md").read_text()
        self.assertIn(f'bend-version: "{BEND_VERSION}"', text)
        version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)[1]
        self.assertIn(f'bend-python-version: "{version}"', text)
        for path in [SKILL / "SKILL.md", *SKILL.glob("references/*.md"), SKILL / "scripts/new_project.py"]:
            for pinned in re.findall(r"bend-python==([\d.]+)", path.read_text()):
                self.assertEqual(pinned, version, path)

    def test_main_file_stays_short(self):
        self.assertLess(len((SKILL / "SKILL.md").read_text().splitlines()), 500)

    def test_referenced_files_exist(self):
        for path in [SKILL / "SKILL.md", *SKILL.glob("references/*.md")]:
            for target in re.findall(r"\]\(((?:references|scripts)/[^)#]+)\)", path.read_text()):
                self.assertTrue((SKILL / target).is_file(), f"{path.name}: {target}")
            for script in re.findall(r"\$SKILL/(scripts/[\w.]+)", path.read_text()):
                self.assertTrue((SKILL / script).is_file(), f"{path.name}: {script}")

    def test_scripts_parse(self):
        for script in SKILL.glob("scripts/*.py"):
            py_compile.compile(str(script), doraise=True)
        for script in SKILL.glob("scripts/*.sh"):
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_scaffold_proves_with_the_pinned_compiler(self):
        compiler = bend()
        if compiler is None:
            self.skipTest(f"bend {BEND_VERSION} unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "proj"
            subprocess.run(
                [sys.executable, str(SKILL / "scripts/new_project.py"), str(project), "demo"],
                check=True,
                capture_output=True,
            )
            vendor(project / "src/demo/bend")
            for source in ["PROOF.bend", "src/demo/core.bend"]:
                result = subprocess.run(
                    [compiler, source, "--check-only"], cwd=project, capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("All terms check", result.stdout + result.stderr)

    def test_reorder_defs_moves_callees_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "order.bend"
            path.write_text(
                "import Base\n\n"
                "def f(x: U32) -> U32:\n  g(x)\n\n"
                "# The helper.\ndef g(x: U32) -> U32:\n  x\n"
            )
            subprocess.run([sys.executable, str(SKILL / "scripts/reorder_defs.py"), str(path)], check=True)
            text = path.read_text()
            self.assertLess(text.index("# The helper.\ndef g"), text.index("def f"))


if __name__ == "__main__":
    unittest.main()
