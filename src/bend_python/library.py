"""Find or vendor the Bend library shipped with the Python build helper."""

from pathlib import Path
import shutil


def library_path():
    """Return the directory containing python.bend, python.c, and their laws."""
    installed = Path(__file__).with_name("lib")
    checkout = Path(__file__).resolve().parents[2] / "bend"
    for candidate in (installed, checkout):
        if (candidate / "python.bend").is_file() and (candidate / "python.c").is_file():
            return candidate
    raise RuntimeError("The bend-python installation is missing its Bend library")


def get_include():
    """Return the library directory as a string, for build-tool compatibility."""
    return str(library_path())


def vendor(destination="bend", *, force=False):
    """Copy the library into a project for Bend's relative imports.

    Existing identical files are accepted. Different files are preserved unless
    ``force=True``; unrelated files in the directory are always preserved.
    """
    source = library_path()
    target = Path(destination)
    files = sorted(path for path in source.iterdir() if path.suffix in {".bend", ".c"})
    conflicts = [
        target / path.name for path in files
        if (target / path.name).exists()
        and (not (target / path.name).is_file()
             or (target / path.name).read_bytes() != path.read_bytes())
    ]
    if conflicts and not force:
        names = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(f"Refusing to replace modified library files: {names}; use --force")
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination_file = target / path.name
        if destination_file.resolve() != path.resolve():
            shutil.copyfile(path, destination_file)
    return target.resolve()
