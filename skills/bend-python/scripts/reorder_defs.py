#!/usr/bin/env python3
"""Reorder a Bend file's top-level blocks so each safe def follows the defs it calls.

Bend lets a def call only defs above it (unsafe `name?` defs are exempt).
This keeps the file's order where possible, moving callees up just enough.
Comments directly above a block move with it. Reports safe mutual recursion,
which Bend rejects anyway.

Usage: python3 reorder_defs.py FILE.bend [--check]
"""

from pathlib import Path
import re
import sys

HEAD = re.compile(r"(?:@unsafe\s+)?def ([\w.]+)(\??)\(")
CALL = re.compile(r"(?<![\w.])([A-Za-z_][\w]*(?:\.[\w]+)*)(?=\()")


def blocks(text):
    lines = text.split("\n")
    result, current, pending = [], [], []
    for line in lines:
        top = bool(line) and not line[0].isspace()
        if top and line.startswith("#"):
            pending.append(line)
        elif top:
            if current:
                result.append(current)
            current, pending = [*pending, line], []
        elif pending:
            pending.append(line)
        else:
            current.append(line)
    if current:
        result.append(current)
    return result, pending


def info(block):
    head = next((line for line in block if line and not line.startswith("#")), "")
    match = HEAD.match(head)
    return (match[1], bool(match[2]) or head.startswith("@unsafe")) if match else (None, False)


def main():
    path = Path(sys.argv[1])
    parsed, tail = blocks(path.read_text())
    index = {info(b)[0]: i for i, b in enumerate(parsed) if info(b)[0]}
    order, state = [], {}

    def visit(i):
        if state.get(i) == 2:
            return
        if state.get(i) == 1:
            sys.exit(f"reorder_defs: mutual recursion through {info(parsed[i])[0]}")
        state[i] = 1
        name, unsafe = info(parsed[i])
        if name and not unsafe:
            body = "\n".join(line for line in parsed[i] if not line.startswith("#"))
            for callee in CALL.findall(body):
                j = index.get(callee)
                if j is not None and j != i:
                    visit(j)
        state[i] = 2
        order.append(i)

    for i in range(len(parsed)):
        visit(i)
    text = "\n".join("\n".join(parsed[i]) for i in order)
    if tail:
        text += "\n" + "\n".join(tail)
    if "--check" in sys.argv:
        sys.exit(0 if text == path.read_text() else 1)
    path.write_text(text)


if __name__ == "__main__":
    main()
