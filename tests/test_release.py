"""The release gate: a GitHub release publishes only a changelogged version."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/check-release.py")
check_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_release)

PYPROJECT = '[build-system]\nrequires = []\n\n[project]\nname = "x"\nversion = "1.2.0"\n'
CHANGELOG = """# Changelog

## [Unreleased]

- Pending work.

## [1.2.0] - 2026-09-25

### Added

- A feature.

## [1.1.0] - 2026-01-01

- Older.
"""


class ReleaseCheckTests(unittest.TestCase):
    def errors(self, tag="v1.2.0", pyproject=PYPROJECT, changelog=CHANGELOG):
        return check_release.release_errors(tag, pyproject, changelog)

    def test_changelogged_version_is_releasable(self):
        self.assertEqual(self.errors(), [])

    def test_tag_must_match_project_version(self):
        self.assertIn("does not match", " ".join(self.errors(tag="v1.2.1")))
        self.assertIn("does not match", " ".join(self.errors(tag="1.2.0")))

    def test_version_needs_a_dated_changelog_section(self):
        unreleased_only = CHANGELOG.replace("## [1.2.0] - 2026-09-25", "## [1.2.0]")
        for changelog in (CHANGELOG.replace("1.2.0", "1.3.0"), unreleased_only):
            with self.subTest(changelog=changelog[:80]):
                self.assertIn(
                    "no '## [1.2.0] - YYYY-MM-DD' section",
                    " ".join(self.errors(changelog=changelog)),
                )

    def test_section_must_list_changes(self):
        empty = CHANGELOG.replace("### Added\n\n- A feature.\n", "")
        self.assertIn("lists no changes", " ".join(self.errors(changelog=empty)))

    def test_version_is_read_from_the_project_table(self):
        other = '[tool.x]\nversion = "1.2.0"\n\n[project]\nname = "x"\nversion = "9.9.9"\n'
        self.assertIn("v9.9.9", " ".join(self.errors(pyproject=other)))


if __name__ == "__main__":
    unittest.main()
