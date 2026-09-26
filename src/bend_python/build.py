"""Setuptools integration for the pinned Bend native compiler."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from setuptools import Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py

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
    local = Path(".tools/bend/bin/bend")
    bend = os.environ.get("BEND") or (str(local.resolve()) if local.exists() else shutil.which("bend"))
    if not bend:
        raise RuntimeError(f"Install Bend {BEND_VERSION} or set BEND to its executable")
    version = subprocess.check_output([bend, "version"], text=True).strip()
    if version != f"bend {BEND_VERSION}":
        raise RuntimeError(f"Expected bend {BEND_VERSION}, got {version!r}")
    return bend


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
        raise RuntimeError("Bend bridge runtime context declaration is missing or duplicated")
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
    ]:
        if source.count(old) != 1:
            raise RuntimeError("Bend runtime changed; refusing an unreviewed embedding patch")
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
        raise RuntimeError("Bend register layout changed")
    prefix = (
        "static void bendpy_panic(const char*) __attribute__((noreturn));\n"
        f"#define BENDPY_MODULE_NAME {json.dumps(name)}\n"
        f"#define BENDPY_INIT PyInit_{name.rsplit('.', 1)[-1]}\n"
    )
    return prefix + source


class BendBuildExt(build_ext):
    """Proof-check, emit C, and compile BendExtension instances with Clang."""

    def run(self):
        if sys.platform != "linux":
            raise RuntimeError("The pinned Bend embedding runtime currently supports Linux only")
        default_cc = "CC" not in os.environ
        if default_cc:
            os.environ["CC"] = "clang"
        try:
            super().run()
        finally:
            if default_cc:
                del os.environ["CC"]

    def build_extensions(self):
        command = getattr(self.compiler, "compiler_so", None)
        if not command:
            raise RuntimeError("Bend extensions require a Unix Clang compiler")
        version = subprocess.check_output([*command, "--version"], text=True)
        if "clang" not in version.lower():
            raise RuntimeError("Bend generated C requires Clang; set CC=clang")
        super().build_extensions()

    def build_extension(self, extension):
        if not isinstance(extension, BendExtension):
            return super().build_extension(extension)
        bend = _compiler()
        proofs = list(extension.bend_proofs)
        library = library_path()
        proofs.insert(0, str(library / "PROOF.bend"))
        proofs.insert(1, str(library / "THREAD_PROOF.bend"))
        for proof in dict.fromkeys(proofs):
            subprocess.run([bend, proof, "--check-only"], check=True)
        generated = Path(self.build_temp) / (extension.name + ".c")
        generated.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([bend, extension.bend_source, "-o", str(generated)], check=True)
        generated.write_text(_patch_runtime(generated.read_text(), extension.name))
        sources = extension.sources
        extension.sources = [str(generated)]
        try:
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
