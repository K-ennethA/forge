"""Build the three calibration sandboxes and their task files.

The calibration is the go/no-go evidence for the whole worker tier, so it runs on
REAL repo code -- but in scoped copies under ``worker/sandboxes/<id>/``, never the
live tree.  (Copies live under ``sandboxes/`` rather than ``runs/`` because
``dispatch`` deletes and recreates ``runs/<id>/`` on every dispatch; a sandbox
that the dispatcher wipes is a sandbox you can only use once.)

    python worker/calibration/setup.py            # (re)build all three
    python worker/calibration/setup.py --only errors-bugfix

Then:

    python worker/dispatch.py worker/calibration/tasks/errors-bugfix.json

Rebuild a sandbox before re-running a task that PASSED -- a pass leaves the
worker's changes in place on purpose, so the next run would start from them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_DIR.parent
TASKS = Path(__file__).resolve().parent / "tasks"
BRIEFS = Path(__file__).resolve().parent / "briefs"

sys.path.insert(0, str(REPO_ROOT))
from worker.dispatch import _rmtree_resilient, load_config  # noqa: E402

#: Sandboxes live outside the repo -- and specifically outside OneDrive.
#: Measured during the first calibration: a gate that ran ``py_compile`` on a
#: file aider had just written failed with ``[Errno 13] Permission denied``,
#: and the identical command passed seconds later.  OneDrive had grabbed the
#: fresh write.  A gate that fails for reasons the worker cannot see or fix is
#: worse than no gate, so the scratch copies moved off the synced drive.
SANDBOXES = Path(load_config().get("sandbox_root") or (WORKER_DIR / "sandboxes"))

SERVICE_PY = REPO_ROOT / "service" / ".venv" / "Scripts" / "python.exe"
STDLIB_PY = Path(sys.executable)

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "forge_service.egg-info", ".ruff_cache"}


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"),
        dirs_exist_ok=True,
    )


def _fresh(name: str) -> Path:
    # Windows holds transient handles on freshly written trees -- OneDrive does
    # it, and so do the indexer and Defender, which is why this bit outside
    # OneDrive too.  Same resilient delete the dispatcher uses.
    sandbox = SANDBOXES / name
    _rmtree_resilient(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


# --------------------------------------------------------------------------
# (a) three pytest cases for printer.normalize_printer's merge behaviour
# --------------------------------------------------------------------------


#: Mutants for the printer merge tests.  Aimed at the *branch logic*, never at
#: the constants: the natural way to write test 1 is
#: ``assert result["bed"]["y"] == DEFAULT_PROFILE["bed"]["y"]``, which compares
#: the module to itself, so a mutated default moves both sides and survives for
#: a reason that says nothing about the test.
PRINTER_MUTANTS = [
    {
        "name": "nested merge disabled for bed (a partial bed override replaces the whole dict)",
        "old": 'if key in ("bed", "tolerances", "layer_heights") and isinstance(value, Mapping):',
        "new": 'if key in ("layer_heights",) and isinstance(value, Mapping):',
    },
    # NOTE: the obvious mutant here -- disabling the
    # `for required in ("press_fit", "magnet_pocket_extra"): setdefault(...)`
    # backfill -- is UNKILLABLE, and shipping it cost two calibration runs
    # before anyone noticed.  Because the nested merge always seeds `tolerances`
    # from DEFAULT_PROFILE first, those keys are present before the backfill
    # runs; the loop is unreachable through the public API.  No correct test can
    # fail when it is removed, so the gate rejected work that was fine.  Mutate
    # the assignment instead, which is reachable and semantic.
    {
        "name": "caller's tolerance overrides ignored (defaults win)",
        "old": '    merged["tolerances"] = clean_tolerances',
        "new": '    merged["tolerances"] = copy.deepcopy(DEFAULT_PROFILE["tolerances"])',
    },
    {
        "name": "non-nested keys silently dropped instead of replaced",
        "old": "        else:\n            merged[key] = value",
        "new": "        else:\n            pass",
    },
]


#: What a correct answer to brief (a) looks like.  Every mutant above must make
#: at least one of these fail; ``setup.py --verify-mutants`` checks exactly that.
REFERENCE_TESTS = '''\
"""Gold-standard answer to the printer-merge brief, used only to prove that the
mutation gate is winnable.  The worker never sees this file."""

from service.printer import DEFAULT_PROFILE, normalize_printer


def test_bed_nested_merge():
    result = normalize_printer({"bed": {"x": 300.0}})
    assert result["bed"]["x"] == 300.0
    assert result["bed"]["y"] == 256.0
    assert result["bed"]["z"] == 256.0


def test_tolerances_nested_merge():
    result = normalize_printer({"tolerances": {"press_fit": 0.15}})
    assert result["tolerances"]["press_fit"] == 0.15
    assert result["tolerances"]["slide_fit"] == 0.2
    assert result["tolerances"]["loose_fit"] == 0.3
    assert result["tolerances"]["magnet_pocket_extra"] == 0.05


def test_non_nested_replacement():
    result = normalize_printer({"nozzle_diameter": 0.6})
    assert result["nozzle_diameter"] == 0.6
    assert normalize_printer(None) == DEFAULT_PROFILE
'''


def build_printer_merge_tests(
    name: str = "printer-merge-tests",
    brief_file: str = "../briefs/printer-merge-tests.md",
) -> dict:
    """Sandbox (a).  Two variants share it: the plain brief and the explicit one.

    The explicit variant exists because the plain one failed the mutation gate
    twice, and the question that decides how this tier gets used is *why* --
    a model that cannot write a meaningful test at all is a different tool from
    one that can when the trap is named for it.
    """
    sandbox = _fresh(name)
    _copy_tree(REPO_ROOT / "service", sandbox / "service")
    _copy_tree(REPO_ROOT / "templates", sandbox / "templates")

    import json as _json
    mutants_path = sandbox / "mutants.json"
    mutants_path.write_text(_json.dumps(PRINTER_MUTANTS, indent=2), encoding="utf-8")

    # The gold-standard tests.  Not shown to the worker (it only ever sees
    # files_scope) -- they exist so `--verify-mutants` can prove the gate is
    # winnable before its verdict is trusted.  See PRINTER_MUTANTS above for
    # what happens when it is not.
    reference = sandbox / "reference"
    reference.mkdir(exist_ok=True)
    (reference / "test_reference_merge.py").write_text(REFERENCE_TESTS, encoding="utf-8")

    py = f'"{SERVICE_PY}"'
    check_count = f'"{WORKER_DIR / "verifiers" / "check_test_count.py"}"'
    check_mutants = f'"{WORKER_DIR / "verifiers" / "check_tests_catch_mutants.py"}"'
    gate = " && ".join([
        f'{py} {check_count} service/tests/test_printer_merge.py --min 3',
        f'{py} -m pytest service/tests/test_printer_merge.py -q -p no:cacheprovider',
        f'{py} -m pytest service/tests/test_checks.py -q -p no:cacheprovider -k printer',
        # The gate that actually decides whether the tests are worth anything:
        # break normalize_printer three ways and require the tests to notice.
        f'{py} {check_mutants} --module service/printer.py '
        f'--tests service/tests/test_printer_merge.py --pytest {py} '
        f'--mutants-file mutants.json',
    ])
    return {
        "id": name,
        "brief_file": brief_file,
        "cwd": str(sandbox),
        "files_scope": ["service/tests/test_printer_merge.py"],
        "acceptance_cmd": gate,
        "max_attempts": 2,
    }


def build_printer_merge_tests_explicit() -> dict:
    return build_printer_merge_tests(
        "printer-merge-tests-explicit",
        "../briefs/printer-merge-tests-explicit.md",
    )


# --------------------------------------------------------------------------
# (b) Google-style docstrings for every public function in meshgen/glb.py
# --------------------------------------------------------------------------


def build_glb_docstrings() -> dict:
    sandbox = _fresh("glb-docstrings")
    (sandbox / "meshgen").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "meshgen" / "glb.py", sandbox / "meshgen" / "glb.py")

    py = f'"{STDLIB_PY}"'
    check_docs = f'"{WORKER_DIR / "verifiers" / "check_docstrings.py"}"'
    gate = " && ".join([
        f'{py} -m py_compile meshgen/glb.py',
        f'{py} {check_docs} meshgen/glb.py --min-functions 2',
    ])
    return {
        "id": "glb-docstrings",
        "brief_file": "../briefs/glb-docstrings.md",
        "cwd": str(sandbox),
        "files_scope": ["meshgen/glb.py"],
        "acceptance_cmd": gate,
        "max_attempts": 2,
    }


# --------------------------------------------------------------------------
# (c) fix a deliberately broken copy of service/errors.py
# --------------------------------------------------------------------------

#: The two-line regression.  Both lines break the wire contract documented in
#: docs/architecture.md, and both are the kind of thing a careless edit produces:
#: a status code copied from the wrong sibling, and a payload key shortened.
BUG_LINES = [
    ('    http_status = 400\n\n\nclass ParamError',
     '    http_status = 404\n\n\nclass ParamError'),
    ('        return {"error": self.message, "traceback": self.traceback_text}',
     '        return {"error": self.message, "trace": self.traceback_text}'),
]

TEST_ERRORS = '''\
"""The error contract from docs/architecture.md, asserted.

400 for anything the caller can fix in their script or overrides, 500 for our
own bugs, and one exact wire body:  {"error": ..., "traceback": ...}.
"""

from __future__ import annotations

import pytest

from service.errors import (
    ForgeError,
    ParamError,
    ScriptError,
    ServiceError,
    TimeoutError_,
)


@pytest.mark.parametrize(
    "cls, status",
    [
        (ForgeError, 500),
        (ScriptError, 400),
        (ParamError, 400),
        (TimeoutError_, 400),
        (ServiceError, 500),
    ],
)
def test_http_status_per_error_class(cls, status):
    assert cls.http_status == status
    assert cls("boom").http_status == status


def test_payload_shape_is_exactly_error_and_traceback():
    err = ParamError("bad override", "Traceback (most recent call last): ...")
    assert err.to_payload() == {
        "error": "bad override",
        "traceback": "Traceback (most recent call last): ...",
    }


def test_payload_traceback_is_none_when_not_supplied():
    assert ScriptError("nope").to_payload() == {"error": "nope", "traceback": None}


def test_message_survives_str_and_attribute():
    err = ServiceError("worker died")
    assert str(err) == "worker died"
    assert err.message == "worker died"


def test_script_errors_are_forge_errors():
    assert issubclass(ParamError, ScriptError)
    assert issubclass(ScriptError, ForgeError)
    assert issubclass(ServiceError, ForgeError)
'''


def build_errors_bugfix() -> dict:
    sandbox = _fresh("errors-bugfix")
    pkg = sandbox / "service"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        '"""Minimal package shim so the sandbox imports `service.errors`."""\n',
        encoding="utf-8",
    )

    source = (REPO_ROOT / "service" / "errors.py").read_text(encoding="utf-8")
    for before, after in BUG_LINES:
        if before not in source:
            raise SystemExit(
                "service/errors.py no longer contains the text this calibration "
                f"breaks:\n{before!r}\nUpdate BUG_LINES in worker/calibration/setup.py."
            )
        source = source.replace(before, after, 1)
    (pkg / "errors.py").write_text(source, encoding="utf-8")

    tests = sandbox / "tests"
    tests.mkdir()
    (tests / "test_errors.py").write_text(TEST_ERRORS, encoding="utf-8")

    py = f'"{SERVICE_PY}"'
    gate = f'{py} -m pytest tests/test_errors.py -q -p no:cacheprovider'
    return {
        "id": "errors-bugfix",
        "brief_file": "../briefs/errors-bugfix.md",
        "cwd": str(sandbox),
        "files_scope": ["service/errors.py"],
        "acceptance_cmd": gate,
        "max_attempts": 2,
    }


BUILDERS = {
    "printer-merge-tests": build_printer_merge_tests,
    "printer-merge-tests-explicit": build_printer_merge_tests_explicit,
    "glb-docstrings": build_glb_docstrings,
    "errors-bugfix": build_errors_bugfix,
}


def verify_mutants(sandbox: Path) -> int:
    """Prove the mutation gate is winnable before trusting a single verdict.

    A strong verifier that is *wrong* is worse than a weak one, because it looks
    rigorous while rejecting correct work.  This runs the same mutants against
    the reference tests: every one must be caught.  A mutant the gold-standard
    answer cannot kill is a bug in the gate, not a finding about the worker.
    """
    import subprocess

    tests = "reference/test_reference_merge.py"
    if not (sandbox / tests).exists():
        print(f"no reference tests in {sandbox} -- nothing to verify")
        return 0
    cmd = [
        str(SERVICE_PY),
        str(WORKER_DIR / "verifiers" / "check_tests_catch_mutants.py"),
        "--module", "service/printer.py",
        "--tests", tests,
        "--pytest", str(SERVICE_PY),
        "--mutants-file", "mutants.json",
    ]
    proc = subprocess.run(cmd, cwd=str(sandbox), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print("\nGATE IS UNFAIR: the reference answer cannot pass it. "
              "Fix the mutants before running the worker against this task.")
    else:
        print("\nGATE IS FAIR: the reference answer catches every mutant.")
    return proc.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(BUILDERS), default=None)
    parser.add_argument("--verify-mutants", action="store_true",
                        help="after building, check every mutant is killable by the reference tests")
    args = parser.parse_args(argv)

    import json

    TASKS.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else list(BUILDERS)
    status = 0
    for name in names:
        task = BUILDERS[name]()
        (TASKS / f"{name}.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
        print(f"built sandbox {task['cwd']}")
        print(f"   task -> {TASKS / (name + '.json')}")
        if args.verify_mutants:
            status |= verify_mutants(Path(task["cwd"]))
    return status


if __name__ == "__main__":
    sys.exit(main())
