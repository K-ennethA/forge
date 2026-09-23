"""Sufficiency: does a candidate run earn a SHIP over its baseline?

The rule is the owner's, verbatim in intent:

    A change ships only if time decreased OR quality increased,
    AND NEITHER regressed.

* **Time** is ``wall_seconds``.  *Decreased*: the candidate is faster than the
  baseline at all.  *Regressed*: the candidate is more than
  :data:`TIME_REGRESSION_PCT` (5 %) slower.  In between is "held".
* **Quality** is ordered, not summed: gates passed first, then fidelity
  passes, then score (:func:`benchmark.quality.quality_key`).  Higher tuple =
  increased, lower = regressed, equal = held.

Two runs that did not measure the same thing (a different task, a different
gate total, a different metric set) are not compared at all: that is a
REJECT saying why, because a verdict across different yardsticks would be
invented.

Usage::

    python benchmark/compare.py <baseline.json> <candidate.json>

prints the verdict as JSON and exits 0 for SHIP, 1 for REJECT.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Mapping

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark.quality import quality_key  # noqa: E402
from benchmark.report import validate_report  # noqa: E402

TIME_REGRESSION_PCT = 5.0


def _comparability(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    problems = []
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        for problem in validate_report(report):
            problems.append("%s report invalid: %s" % (label, problem))
    if problems:
        return problems
    if baseline["task"] != candidate["task"]:
        problems.append("different tasks: %r vs %r" % (baseline["task"], candidate["task"]))
    if baseline["gates"]["total"] != candidate["gates"]["total"]:
        problems.append("different gate totals: %d vs %d"
                        % (baseline["gates"]["total"], candidate["gates"]["total"]))
    if set(baseline["fidelity"]) != set(candidate["fidelity"]):
        problems.append("different fidelity metrics: %s vs %s"
                        % (sorted(baseline["fidelity"]), sorted(candidate["fidelity"])))
    return problems


def sufficiency(baseline: Mapping[str, Any], candidate: Mapping[str, Any],
                regression_pct: float = TIME_REGRESSION_PCT) -> Dict[str, Any]:
    """``{"verdict": "SHIP"|"REJECT", "reasons": [...], "time": {...}, "quality": {...}}``."""
    problems = _comparability(baseline, candidate)
    if problems:
        return {"verdict": "REJECT", "reasons": ["not comparable: " + p for p in problems]}

    t0 = float(baseline["wall_seconds"])
    t1 = float(candidate["wall_seconds"])
    time_decreased = t1 < t0
    time_regressed = t1 > t0 * (1.0 + regression_pct / 100.0)
    delta_pct = ((t1 - t0) / t0 * 100.0) if t0 > 0 else (0.0 if t1 == t0 else float("inf"))

    q0 = quality_key(baseline)
    q1 = quality_key(candidate)
    quality_increased = q1 > q0
    quality_regressed = q1 < q0

    reasons = []
    time_word = ("decreased" if time_decreased else
                 "regressed" if time_regressed else "held")
    reasons.append("time %s: %.3fs -> %.3fs (%+.1f%%, regression past +%g%%)"
                   % (time_word, t0, t1, delta_pct, regression_pct))
    quality_word = ("increased" if quality_increased else
                    "regressed" if quality_regressed else "held")
    reasons.append("quality %s: (gates, fidelity passes, score) %s -> %s"
                   % (quality_word, list(q0), list(q1)))

    improved = time_decreased or quality_increased
    regressed = time_regressed or quality_regressed
    ship = improved and not regressed
    if not improved:
        reasons.append("neither time decreased nor quality increased")
    if time_regressed:
        reasons.append("time regressed")
    if quality_regressed:
        reasons.append("quality regressed")

    return {
        "verdict": "SHIP" if ship else "REJECT",
        "reasons": reasons,
        "time": {"baseline_s": t0, "candidate_s": t1, "delta_pct": delta_pct,
                 "decreased": time_decreased, "regressed": time_regressed},
        "quality": {"baseline": list(q0), "candidate": list(q1),
                    "increased": quality_increased, "regressed": quality_regressed},
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[0], "r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with open(argv[1], "r", encoding="utf-8") as handle:
        candidate = json.load(handle)
    result = sufficiency(baseline, candidate)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "SHIP" else 1


if __name__ == "__main__":
    sys.exit(main())
