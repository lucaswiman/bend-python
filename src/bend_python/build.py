"""Setuptools integration for the pinned Bend native compiler."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from setuptools import Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.errors import CompileError, ExecError, PlatformError

from . import BEND_VERSION
from .library import library_path


class BendExtension(Extension):
    """A native extension compiled from one Bend entry point and optional proofs.

    The entry point's ``main`` registers functions using the vendored Python
    library. Normal setuptools Extension keyword arguments remain available.
    """

    def __init__(self, name, source, *, proofs=(), **kwargs):
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part) for part in name.split(".")):
            raise ValueError("Bend extension names must be dotted ASCII Python identifiers")
        if Path(source).suffix != ".bend":
            raise ValueError("BendExtension source must be a .bend file")
        self.bend_source = str(source)
        self.bend_proofs = [str(path) for path in proofs]
        defaults = {
            "extra_compile_args": [
                "-std=c11", "-O2", "-fvisibility=hidden", "-Wno-unused-function",
                "-Wno-unused-variable", "-Wno-unreachable-code",
            ],
            "extra_link_args": ["-pthread"],
            "libraries": ["m"],
        }
        for key, values in defaults.items():
            kwargs[key] = values + list(kwargs.get(key, ()))
        super().__init__(name, sources=[self.bend_source], **kwargs)


def _compiler():
    # A checkout's bootstrapped compiler, never one found relative to the cwd.
    local = Path(__file__).resolve().parents[2] / ".tools/bend/bin/bend"
    bend = os.environ.get("BEND") or (str(local) if local.exists() else shutil.which("bend"))
    if not bend:
        raise ExecError(f"Install Bend {BEND_VERSION} or set BEND to its executable")
    try:
        version = subprocess.run([bend, "version"], text=True, capture_output=True).stdout.strip()
    except OSError as error:
        raise ExecError(f"Cannot run Bend compiler {bend!r}: {error}") from None
    if version != f"bend {BEND_VERSION}":
        raise ExecError(f"Expected bend {BEND_VERSION} at {bend!r}, got {version!r}")
    return bend


def _bridge_libraries(source):
    """Directories of the bend-python libraries a program reaches by relative imports.

    The build must prove the library it compiles: a project's vendored copy,
    which ``vendor`` preserves when modified, not necessarily the SDK's own.
    """
    pending, seen, found = [Path(source).resolve()], set(), []
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if path.name == "python.bend" and path.with_name("python.c").is_file():
            found.append(path.parent)
        for match in re.finditer(r"^\s*import\s+(\.\.?/\S+\.bend)\b", path.read_text(), re.M):
            pending.append((path.parent / match[1]).resolve())
    return found


def _bend(bend, *args, failure):
    # Bend prints its own diagnostic; end the build with a setuptools error
    # (reported as "error: ...") rather than a CalledProcessError traceback.
    if subprocess.run([bend, *args]).returncode != 0:
        raise CompileError(f"{failure} (see Bend output above)")


def _patch_runtime(source, name):
    fatal = '''static void err_fail(const char* msg) {
  fflush(stdout);
  fprintf(stderr, "bend: %s\\n", msg);
  _exit(1);
}'''
    cpu_globals = '''static Corpus CORPUS;
static u64    ALC[CUBE_T + 1][3 * ALC_WORDS] __attribute__((aligned(128)));
static u32    KEEP_WORDS;
// the bag: 2^CUBE_LOG groups of CUBE_T lanes (a -D constant on the device)
static u32    CUBE_LOG = 7;
static u32    bank_lock;

static u32            pool_size;'''
    context_blocks = list(re.finditer(
        r"// BENDPY_RUNTIME_CONTEXT_BEGIN\n(.*?)// BENDPY_RUNTIME_CONTEXT_END\n",
        source, re.DOTALL,
    ))
    if len(context_blocks) != 1:
        raise CompileError(
            "Bend program must import the bend-python library (python.bend) exactly once"
        )
    context_block = context_blocks[0]
    contexts = context_block[1]
    source = source[:context_block.start()] + source[context_block.end():]
    pool_open = '''OUTLINE void pool_open(void) {
  static bool up;
  if (up) {
    return;
  }
  up = true;
  for (u32 w = 0; w < pool_size; w += 1) {
    pthread_t tid;
    if (pthread_create(&tid, NULL, pool_work, (void*)(uintptr_t)w)) {
      err_fail("pthread_create");
    }
  }
}'''
    for old, new in [
        (fatal, "static void err_fail(const char* msg) { bendpy_panic(msg); }"),
        (cpu_globals, contexts),
        ("static bool io_gpu;\nstatic Stk  io_stk;", ""),
        ("static u64 corpus_size;", ""),
        ("  Corpus H   = CORPUS;", "  Corpus H   = CORPUS;\n  corpus_size = size;"),
        (pool_open, '''OUTLINE void pool_open(void) {
  err_fail("Bend worker threads are unsupported by the Python embedding");
}'''),
        ("int main(int argc, char** argv) {", "static int bendpy_unused_main(int argc, char** argv) {"),
        ("#define WL_OPEN    { WL_BANK u32 rn;", "#define WL_OPEN    { WL_BANK u32 rn = 0;"),
        # Python.h is included first and already selects the GNU feature set.
        ("#define _GNU_SOURCE\n", "#ifndef _GNU_SOURCE\n#define _GNU_SOURCE\n#endif\n"),
    ]:
        if source.count(old) != 1:
            raise CompileError("Bend runtime changed; refusing an unreviewed embedding patch")
        source = source.replace(old, new)
    # The tail-call ABI forwards unused registers. Initialize those registers
    # rather than passing indeterminate C values between generated segments.
    source, count = re.subn(
        r"#define WL_BANK Term ([\w, ]+);",
        lambda match: "#define WL_BANK Term " + ", ".join(
            register + " = 0" for register in match[1].split(", ")
        ) + ";",
        source,
    )
    if count != 1:
        raise CompileError("Bend register layout changed")
    prefix = (
        "#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n"
        "static void bendpy_panic(const char*) __attribute__((noreturn));\n"
        f"#define BENDPY_MODULE_NAME {json.dumps(name)}\n"
        f"#define BENDPY_INIT PyInit_{name.rsplit('.', 1)[-1]}\n"
    )
    return prefix + source


class BendBuildExt(build_ext):
    """Proof-check, emit C, and compile BendExtension instances with Clang."""

    # build_ext --parallel shares one compiler object between threads; an
    # unserialized swap could restore another Bend extension's driver mid-build.
    _compiler_swap = threading.Lock()

    @contextmanager
    def _clang(self):
        """Compile with Clang without changing the environment or other extensions."""
        with self._compiler_swap:
            compiler = self.compiler
            commands = {name: getattr(compiler, name, None) for name in ("compiler_so", "linker_so")}
            if not commands["compiler_so"] or not commands["linker_so"]:
                raise PlatformError("Bend extensions require a Unix Clang compiler")
            replacements = {}
            if "CC" not in os.environ:
                # Replace only the driver; keep sysconfig's flags such as -pthread and -shared.
                clang = shutil.which("clang")
                if not clang:
                    raise PlatformError("Bend generated C requires Clang; install it or set CC=clang")
                replacements = {name: [clang, *command[1:]] for name, command in commands.items()}
            driver = replacements.get("compiler_so", commands["compiler_so"])
            try:
                version = subprocess.run([*driver, "--version"], text=True, capture_output=True).stdout
            except OSError:
                version = ""
            if "clang" not in version.lower():
                raise PlatformError(f"Bend generated C requires Clang, not {driver[0]!r}; set CC=clang")
            compiler.set_executables(**replacements)
            try:
                yield
            finally:
                compiler.set_executables(**commands)

    def build_extension(self, extension):
        if not isinstance(extension, BendExtension):
            return super().build_extension(extension)
        if sys.platform != "linux":
            raise PlatformError("The pinned Bend embedding runtime currently supports Linux only")
        bend = _compiler()
        libraries = _bridge_libraries(extension.bend_source) or [library_path()]
        proofs = [str(library / proof) for library in libraries
                  for proof in ("PROOF.bend", "THREAD_PROOF.bend")]
        proofs.extend(extension.bend_proofs)
        for kind, path in [("source", extension.bend_source), *(("proof", p) for p in proofs)]:
            if not Path(path).is_file():
                raise CompileError(f"Bend {kind} file not found: {path}")
        for proof in dict.fromkeys(proofs):
            _bend(bend, proof, "--check-only", failure=f"Bend proof check failed: {proof}")
        generated = Path(self.build_temp) / (extension.name + ".c")
        generated.parent.mkdir(parents=True, exist_ok=True)
        _bend(bend, extension.bend_source, "-o", str(generated),
              failure=f"Bend compilation failed: {extension.bend_source}")
        generated.write_text(_patch_runtime(generated.read_text(), extension.name))
        sources = extension.sources
        extension.sources = [str(generated)]
        try:
            with self._clang():
                super().build_extension(extension)
        finally:
            extension.sources = sources


class BuildPyWithBendLibrary(build_py):
    """Bundle this repository's canonical bend/ directory when building its SDK."""

    def run(self):
        super().run()
        target = Path(self.build_lib) / "bend_python" / "lib"
        target.mkdir(parents=True, exist_ok=True)
        for path in library_path().iterdir():
            if path.suffix in {".bend", ".c"}:
                self.copy_file(str(path), str(target / path.name))

    def get_outputs(self, include_bytecode=True):
        files = super().get_outputs(include_bytecode)
        target = Path(self.build_lib) / "bend_python" / "lib"
        files.extend(str(target / path.name) for path in library_path().iterdir()
                     if path.suffix in {".bend", ".c"})
        return files
