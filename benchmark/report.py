"""The benchmark report's shape, in one place, checked rather than trusted.

A report is one JSON object per task run::

    {"schema": "forge-benchmark-report/1",
     "task", "task_version", "mode", "git_sha", "git_dirty", "started_at",
     "wall_seconds", "final_state", "job_id", "bridge": {...},
     "artifact": {"path", "exists", "fresh", "mtime"},
     "gates": {"passed", "total", "failures": [...]},
     "fidelity": {metric: {"measured", "expected", "tol", "pass", ...}},
     "score", "appearance_score", "measure_seconds"}

``final_state`` is the bridge's own terminal vocabulary (``done`` / ``error``
/ ``timeout`` / ``cancelled`` — ``assistant/bridge.py`` "How a turn ends"),
plus ``refused`` when ``/ask`` never produced a job at all.
"""

from __future__ import annotations

from typing import Any, List, Mapping

SCHEMA = "forge-benchmark-report/1"

TERMINAL_STATES = ("done", "error", "timeout", "cancelled")
FINAL_STATES = TERMINAL_STATES + ("refused",)

REQUIRED = {
    "schema": str,
    "task": str,
    "git_sha": str,
    "started_at": str,
    "wall_seconds": (int, float),
    "final_state": str,
    "gates": dict,
    "fidelity": dict,
    "score": (int, float),
}

FIDELITY_KEYS = ("measured", "expected", "tol", "pass")


def validate_report(report: Any) -> List[str]:
    """Every way ``report`` departs from the schema; ``[]`` when it is sound."""
    if not isinstance(report, Mapping):
        return ["report is %s, not an object" % type(report).__name__]
    problems = []
    for key, kind in REQUIRED.items():
        if key not in report:
            problems.append("missing %r" % key)
        elif isinstance(report[key], bool) or not isinstance(report[key], kind):
            problems.append("%r is %s" % (key, type(report[key]).__name__))
    if problems:
        return problems
    if report["schema"] != SCHEMA:
        problems.append("schema %r is not %r" % (report["schema"], SCHEMA))
    if report["final_state"] not in FINAL_STATES:
        problems.append("final_state %r not in %s" % (report["final_state"], FINAL_STATES))
    if report["wall_seconds"] < 0:
        problems.append("wall_seconds is negative")
    gates = report["gates"]
    for key in ("passed", "total"):
        if not isinstance(gates.get(key), int) or isinstance(gates.get(key), bool):
            problems.append("gates.%s is not an integer" % key)
    if not isinstance(gates.get("failures"), list):
        problems.append("gates.failures is not a list")
    if not problems and not 0 <= gates["passed"] <= gates["total"]:
        problems.append("gates.passed %r outside 0..%r" % (gates["passed"], gates["total"]))
    for name, row in report["fidelity"].items():
        if not isinstance(row, Mapping):
            problems.append("fidelity.%s is not an object" % name)
            continue
        for key in FIDELITY_KEYS:
            if key not in row:
                problems.append("fidelity.%s missing %r" % (name, key))
        if "pass" in row and not isinstance(row["pass"], bool):
            problems.append("fidelity.%s.pass is not a bool" % name)
    return problems
