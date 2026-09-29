"""Check that a release tag matches the project version and has a changelog entry.

Usage: python scripts/check-release.py v1.2.3
"""

import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def release_errors(tag, pyproject, changelog):
    """Return the reasons a tag cannot be released (empty when it can)."""
    # tomllib is 3.11+; the [project] version line is enough here.
    project = re.search(r"^\[project\]\n(.*?)(?=^\[|\Z)", pyproject, re.M | re.S)
    found = project and re.search(r'^version\s*=\s*"([^"]+)"', project[1], re.M)
    if not found:
        return ["pyproject.toml has no [project] version"]
    version = found[1]
    errors = []
    if tag != f"v{version}":
        errors.append(f"tag {tag!r} does not match project version v{version}")
    # A "## [1.2.3] - YYYY-MM-DD" heading with at least one entry before the next heading.
    heading = re.compile(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}[ \t]*$", re.M)
    match = heading.search(changelog)
    if match is None:
        errors.append(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")
    else:
        rest = changelog[match.end() :]
        following = re.search(r"^## ", rest, re.M)
        section = rest[: following.start()] if following else rest
        if not re.search(r"^- ", section, re.M):
            errors.append(f"CHANGELOG.md section for {version} lists no changes")
    return errors


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    errors = release_errors(
        sys.argv[1],
        (ROOT / "pyproject.toml").read_text(),
        (ROOT / "CHANGELOG.md").read_text(),
    )
    # GitHub Actions shows ::error:: lines as annotations on the run.
    prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") == "true" else "error: "
    for error in errors:
        print(f"{prefix}{error}", file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
