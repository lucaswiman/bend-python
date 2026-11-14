"""Exact trust policy for Bend 2.0.39 ``--check-only`` diagnostics.

The checker validates filled definitions, types, and termination before reporting
these dependencies. An accepted trust report is *not* ALL PROOFS CHECK, a BendTT
kernel check, or a proof of foreign implementations. Callers must retain the
compiler version/hash, entry root, source/import provenance, and original output.
Names here are rendered diagnostic labels: Bend's ``name_key`` is lossy, so this
policy cannot distinguish internal definition keys with the same printed label.
"""

from collections.abc import Collection, Mapping
import re

# Reviewed SDK PROOF root; application roots need their own explicit policy.
# This is the imported foreign closure, not the application's old 69-name list.
_PYTHON_FOREIGN = (
    "python.export",
    "python.require_no_kwargs",
    "python.to_u32",
    "python.from_u32",
    "python.to_f32",
    "python.from_f32",
    "python.to_string",
    "python.from_string",
    "python.to_bool",
    "python.from_bool",
    "python.get_item",
    "python.set_item",
    "python.getattr",
    "python.call",
    "python.invoke",
    "python.identical",
    "python.builtins",
    "python.none",
    "python.empty_dict",
    "python.tuple",
    "python.list",
    "python.len",
    "python.from_nat",
    "python.type_error",
    "python.truthy",
    "python.to_bytes",
    "python.from_bytes",
    "python.import_module",
    "python.construct",
    "python.fill_dict",
    "python.dict",
    "python.require_singleton",
    "python.require_pair",
    "python.unary",
    "python.binary",
    "python.invoke_unary",
    "python.invoke_binary_u32_values",
    "python.invoke_binary_u32",
    "python.export_u32",
    "python.export_f32",
    "python.export_string",
    "python.export_bool",
    "python.export_binary_u32",
    "python.borrow_f32",
    "python.f32_size",
    "python.f32_shape",
    "python.f32_read",
    "python.f32_write",
    "python.f32_modify",
    "python.f32_release",
    "python.f32_map",
    "python.f32_map_closed",
    "python.blas_scale",
    "python.blas_dot",
    "python.blas_axpy",
    "python.blas_matmul",
)
_TENSOR_FOREIGN = (
    "tensor.borrow_f32",
    "tensor.require",
    "tensor.require_tensor",
    "tensor.check_forward_ad",
    "tensor.notify_write",
    "tensor.borrow_torch_f32",
    "tensor.map_inplace",
    "tensor.map_object",
    "tensor.invoke_map",
    "tensor.export_numpy_f32_inplace",
    "tensor.export_torch_f32_inplace",
)
_SELECTOR_LAWS = (
    "LAWS.singleton_accepted",
    "LAWS.singleton_rejected",
    "LAWS.pair_accepted",
    "LAWS.pair_rejected",
)
SDK_PROOF_TRUST = {
    "PROOF.bend": {"foreign effects": _PYTHON_FOREIGN + _SELECTOR_LAWS + _TENSOR_FOREIGN},
    "THREAD_PROOF.bend": {},
    "BLAS_PROOF.bend": {},
    "TENSOR_PROOF.bend": {"foreign effects": _PYTHON_FOREIGN + _TENSOR_FOREIGN},
}

_HEADER = re.compile(r"Error: ([1-9][0-9]*) (def relies|defs rely) on unsafe or foreign code:")
_CATEGORY = re.compile(r"defs that depend on (@unsafe|foreign effects): (.+)")
_HINT = (
    "Hint: no law or proof uses these defs. Move them to a file this proof does not import, "
    "and the proofs will check: "
)


def classify_proof_check(
    returncode: int,
    stdout: str,
    stderr: str,
    expected: Mapping[str, Collection[str]] | None = None,
) -> str:
    """Classify one pinned ``--check-only`` result, or raise ``ValueError``.

    Only an exact, complete trust-only refusal can match a declared policy. Pure
    roots require the strict success output. No source is rewritten, no law is
    dropped, and arbitrary exit-1 errors are never accepted. ``expected`` names
    are diagnostic labels, not authenticated internal definition identities.
    """
    if expected is not None and not isinstance(expected, Mapping):
        raise ValueError("proof trust policy must map categories to rendered names")
    policy = {}
    for category, labels in (expected or {}).items():
        if (
            category not in {"@unsafe", "foreign effects"}
            or not isinstance(labels, Collection)
            or isinstance(labels, str)
        ):
            raise ValueError("invalid proof trust category or name collection")
        names = list(labels)
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("proof trust categories require nonempty rendered names")
        if len(names) != len(set(names)):
            raise ValueError("duplicate name in proof trust policy")
        policy[category] = set(names)
    if returncode == 0:
        if (
            policy
            or stderr
            or stdout != ("ALL PROOFS CHECK\nUse --verdict for mathematical validity.\n")
        ):
            raise ValueError("unexpected strict proof output or declared trust boundary")
        return "strict_bend_check_no_declared_trust"
    if returncode != 1 or stdout:
        raise ValueError("not a trust-only proof refusal")
    lines = stderr.splitlines()
    if stderr != "\n".join(lines) + "\n" or len(lines) < 3 or lines[0] != "SOME PROOFS FAIL":
        raise ValueError("not the complete trust-only proof diagnostic")
    header = _HEADER.fullmatch(lines[1])
    if not header or header[2] != ("def relies" if header[1] == "1" else "defs rely"):
        raise ValueError("invalid trust diagnostic count or grammar")
    actual = {}
    ordered_names = []
    for line in lines[2:]:
        match = _CATEGORY.fullmatch(line)
        if not match:
            break
        category, text = match.groups()
        names = text.split(", ")
        if category in actual or any(not name or name != name.strip() for name in names):
            raise ValueError("duplicate category or malformed rendered name")
        if len(names) != len(set(names)):
            raise ValueError("duplicate rendered name in trust diagnostic")
        actual[category] = set(names)
        ordered_names.extend(names)
    if not actual:
        raise ValueError("missing trust dependency categories")
    category_order = list(actual)
    if category_order != [key for key in ("@unsafe", "foreign effects") if key in actual]:
        raise ValueError("unexpected trust diagnostic category order")
    rest = lines[2 + len(actual) :]
    unique_names = list(dict.fromkeys(ordered_names))
    if rest and rest != [_HINT + ", ".join(unique_names)]:
        raise ValueError("extra proof diagnostics or invalid unused-import hint")
    if len(unique_names) != int(header[1]) or actual != policy:
        raise ValueError("unreviewed proof trust dependency or diagnostic count")
    return "typed_with_declared_trust_boundary"
