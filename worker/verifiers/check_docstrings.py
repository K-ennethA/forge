"""Deterministic gate: does every public function carry a Google-style docstring?

Written for worker calibration task (b), but it is a general verifier -- point it
at any module and it answers yes/no with reasons and an exit code, which is the
only thing a dispatch gate needs.

    python worker/verifiers/check_docstrings.py meshgen/glb.py
    python worker/verifiers/check_docstrings.py --classes pkg/mod.py

"Public function": a module-level ``def``/``async def`` whose name does not start
with an underscore.  Nested helpers are the author's business, not the API's.
With ``--classes``, public methods of public classes count too.

"Google-style" is checked as three concrete, arguable-with-nobody rules:

1. There is a docstring and its first line is a non-empty summary.
2. If the function takes parameters (``self``/``cls``, ``*``, ``/`` excluded),
   there is an ``Args:`` section that names every one of them.
3. If the function returns a value anywhere (a bare ``return`` does not count),
   there is a ``Returns:`` or ``Yields:`` section.

No style opinion beyond that -- a gate that argues about prose is a gate the
worker cannot satisfy and the orchestrator cannot trust.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import List, Tuple

SECTION = re.compile(r"^[ \t]*(Args|Arguments|Parameters|Returns|Yields|Raises|Example[s]?|Note[s]?|Attributes)\s*:\s*$")


def _params(node) -> List[str]:
    a = node.args
    names = [arg.arg for arg in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return [n for n in names if n not in ("self", "cls")]


def _returns_a_value(node) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            continue
        if isinstance(child, ast.Return) and child.value is not None:
            return True
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def _section_body(doc: str, *names: str) -> Tuple[bool, List[str]]:
    """Return (section present, the lines under it) for the first matching name."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        match = SECTION.match(line)
        if not match or match.group(1) not in names:
            continue
        body: List[str] = []
        for follow in lines[i + 1:]:
            if SECTION.match(follow):
                break
            body.append(follow)
        return True, body
    return False, []


def check_function(node, qualname: str) -> List[str]:
    problems: List[str] = []
    doc = ast.get_docstring(node, clean=True)
    if not doc or not doc.strip():
        return [f"{qualname}: no docstring"]

    summary = doc.strip().splitlines()[0].strip()
    if not summary:
        problems.append(f"{qualname}: docstring has no summary line")

    params = _params(node)
    if params:
        present, body = _section_body(doc, "Args", "Arguments", "Parameters")
        if not present:
            problems.append(f"{qualname}: takes {params} but has no 'Args:' section")
        else:
            blob = "\n".join(body)
            missing = [
                p for p in params
                if not re.search(rf"^\s*[*]{{0,2}}{re.escape(p)}\s*(\([^)]*\))?\s*:", blob, re.MULTILINE)
            ]
            if missing:
                problems.append(f"{qualname}: 'Args:' does not document {missing}")

    if _returns_a_value(node):
        present, _ = _section_body(doc, "Returns", "Yields")
        if not present:
            problems.append(f"{qualname}: returns a value but has no 'Returns:' section")

    return problems


def check_file(path: Path, include_methods: bool) -> Tuple[int, List[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: List[str] = []
    checked = 0

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            checked += 1
            problems.extend(check_function(node, node.name))
        elif include_methods and isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    checked += 1
                    problems.extend(check_function(sub, f"{node.name}.{sub.name}"))

    return checked, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="python files to check")
    parser.add_argument("--classes", action="store_true", help="also check public methods of public classes")
    parser.add_argument("--min-functions", type=int, default=1,
                        help="fail if fewer than this many public functions were found "
                             "(catches a 'gate passed because there was nothing to check' run)")
    args = parser.parse_args(argv)

    total = 0
    all_problems: List[str] = []
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"FAIL: {path} does not exist")
            return 2
        checked, problems = check_file(path, args.classes)
        total += checked
        all_problems.extend(problems)
        print(f"{path}: {checked} public function(s) checked, {len(problems)} problem(s)")

    if total < args.min_functions:
        print(f"FAIL: found only {total} public functions, expected at least {args.min_functions}")
        return 2

    for problem in all_problems:
        print(f"  FAIL  {problem}")

    if all_problems:
        print(f"\nFAILED: {len(all_problems)} docstring problem(s) across {total} public function(s)")
        return 1
    print(f"\nOK: all {total} public function(s) have Google-style docstrings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
