"""Deterministic gate: do these tests actually FAIL when the code is wrong?

The hole this closes was found by the first worker calibration. Asked for three
tests of `normalize_printer`'s merge behaviour, a 14B worker produced three green,
assert-carrying, correctly-named tests -- one of which asserted
``normalize_printer({"nozzle_diameter": 0.4})["nozzle_diameter"] == 0.4`` where
0.4 is *already the default*. It tests nothing. Every presence-and-greenness gate
in the world passes it.

So stop asking "do the tests pass" and ask the only question that matters:
**break the code on purpose, and see whether the tests notice.**

    python worker/verifiers/check_tests_catch_mutants.py \
        --module service/printer.py \
        --tests service/tests/test_printer_merge.py \
        --pytest "<abs python>" \
        --mutants-file mutants.json

``mutants.json`` is a list of ``{"name": ..., "old": ..., "new": ...}`` where
``old`` is a literal substring of the module (it may span lines) and ``new`` is
what replaces it. Single-line mutants can also be passed inline as
``--mutant 'OLD~~>NEW'`` -- the separator is ``~~>`` rather than ``=`` because
the interesting mutants are assignments and every one of them contains ``=``.

For every mutant the module is patched, the test file is run, and the mutant must
make it **fail**. A mutant the tests still pass is a survivor, and the gate goes
red naming it. The module is always restored, including on crash.

Mutants are written by whoever writes the brief, which is the point: choosing
them is the same act as deciding what the tests are *for*, and it takes about a
minute. Keep them small and semantic, and aim them at the **merge/branch logic**
rather than at constants -- a test written as
``assert result["bed"]["y"] == DEFAULT_PROFILE["bed"]["y"]`` compares the module
to itself, so mutating the default moves both sides and survives for the wrong
reason.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def _run_pytest(python: str, tests: str, cwd: Path) -> Tuple[int, str]:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        [python, "-m", "pytest", tests, "-q", "-p", "no:cacheprovider"],
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, **kwargs,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


SEP = "~~>"


def load_mutants(args) -> List[dict]:
    mutants: List[dict] = []
    if args.mutants_file:
        raw = json.loads(Path(args.mutants_file).read_text(encoding="utf-8"))
        for i, entry in enumerate(raw):
            if "old" not in entry or "new" not in entry:
                raise SystemExit(f"mutant {i} needs both 'old' and 'new'")
            mutants.append({
                "name": entry.get("name") or f"mutant {i + 1}",
                "old": entry["old"],
                "new": entry["new"],
            })
    for spec in args.mutant or []:
        if SEP not in spec:
            raise SystemExit(f"--mutant must look like 'old{SEP}new', got {spec!r}")
        old, new = spec.split(SEP, 1)
        if not old:
            raise SystemExit(f"--mutant has an empty 'old' side: {spec!r}")
        mutants.append({"name": f"{old[:40]!r} -> {new[:40]!r}", "old": old, "new": new})
    if not mutants:
        raise SystemExit("no mutants given -- pass --mutants-file or --mutant")
    return mutants


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--module", required=True, help="the file to break")
    parser.add_argument("--tests", required=True, help="the test file that must notice")
    parser.add_argument("--pytest", required=True, help="python interpreter to run pytest with")
    parser.add_argument("--mutant", action="append", metavar=f"OLD{SEP}NEW")
    parser.add_argument("--mutants-file", default=None, help="JSON list of {name, old, new}")
    parser.add_argument("--cwd", default=".", help="working directory for pytest")
    args = parser.parse_args(argv)
    mutants = load_mutants(args)

    cwd = Path(args.cwd).resolve()
    module = (cwd / args.module).resolve()
    if not module.exists():
        print(f"FAIL: module {module} does not exist")
        return 2

    original = module.read_text(encoding="utf-8")

    # A baseline that is not green means the mutants tell you nothing.
    code, output = _run_pytest(args.pytest, args.tests, cwd)
    if code != 0:
        print("FAIL: the tests do not pass against the UNMODIFIED module -- "
              "fix that before asking whether they catch mutants")
        print(output[-2000:])
        return 1
    print(f"baseline: {args.tests} passes against the unmodified {args.module}")

    survivors: List[str] = []
    try:
        for mutant in mutants:
            name, old, new = mutant["name"], mutant["old"], mutant["new"]
            if old not in original:
                print(f"  FAIL      {name}: text not found in {args.module}")
                survivors.append(name)
                continue
            module.write_text(original.replace(old, new, 1), encoding="utf-8")
            code, _ = _run_pytest(args.pytest, args.tests, cwd)
            if code == 0:
                print(f"  SURVIVED  {name}  (tests still passed with the code broken)")
                survivors.append(name)
            else:
                print(f"  caught    {name}")
    finally:
        module.write_text(original, encoding="utf-8")

    if survivors:
        print(f"\nFAILED: {len(survivors)} of {len(mutants)} mutant(s) survived -- the "
              f"tests are green but they are not testing the behaviour they claim to")
        return 1
    print(f"\nOK: all {len(mutants)} mutant(s) caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
