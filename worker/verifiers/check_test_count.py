"""Deterministic gate: does this file actually contain N distinct test functions?

pytest will happily report "1 passed" for a brief that asked for three cases, and
a green run is exactly the signal a dispatch gate reads.  This closes that hole.

    python worker/verifiers/check_test_count.py service/tests/test_x.py --min 3

Counts module-level ``def test_*`` (and ``async def``), plus ``test_*`` methods of
``Test*`` classes.  A parametrized test counts once -- the brief asked for cases,
and one parametrized function with three ids is a fair answer, so this is a floor
on *distinct* test bodies only when you want it to be; use ``--min 1`` otherwise.
Also refuses bodies that are nothing but ``pass``/``...``, which is the other way
a green run means nothing.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def _is_empty(node) -> bool:
    body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    if not body:
        return True
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis)
        for s in body
    )


def _has_assert(node) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.With):
            for item in child.items:
                call = item.context_expr
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                    if name in ("raises", "warns"):
                        return True
    return False


def collect(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found, empty, assertless = [], [], []

    def consider(node, qualname):
        found.append(qualname)
        if _is_empty(node):
            empty.append(qualname)
        elif not _has_assert(node):
            assertless.append(qualname)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            consider(node, node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    consider(sub, f"{node.name}.{sub.name}")

    return found, empty, assertless


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("--min", type=int, default=1, help="minimum number of test functions")
    parser.add_argument("--allow-assertless", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"FAIL: {path} does not exist")
        return 2

    found, empty, assertless = collect(path)
    print(f"{path}: {len(found)} test function(s): {', '.join(found) or '(none)'}")

    failed = False
    if len(found) < args.min:
        print(f"  FAIL  expected at least {args.min} test function(s), found {len(found)}")
        failed = True
    if empty:
        print(f"  FAIL  empty test bodies: {empty}")
        failed = True
    if assertless and not args.allow_assertless:
        print(f"  FAIL  no assert / pytest.raises in: {assertless}")
        failed = True

    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
