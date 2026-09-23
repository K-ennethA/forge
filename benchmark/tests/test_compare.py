"""compare.sufficiency — every branch of the owner's rule, plus the report schema.

    A change ships only if time decreased OR quality increased,
    AND NEITHER regressed.  Time regression = more than 5 % slower.
    Quality = gates passed, then fidelity passes, then score.
"""

import copy
import os
import sys

import pytest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark.compare import main as compare_main  # noqa: E402
from benchmark.compare import sufficiency  # noqa: E402
from benchmark.quality import quality_key  # noqa: E402
from benchmark.report import SCHEMA, validate_report  # noqa: E402


def report(wall=100.0, gates=6, fid_pass=2, score=50.0, task="t", total=8,
           metrics=("a", "b", "c", "d")):
    fidelity = {}
    for index, name in enumerate(metrics):
        fidelity[name] = {"measured": 1.0, "expected": 1.0, "tol": 0.1,
                          "pass": index < fid_pass}
    return {"schema": SCHEMA, "task": task, "git_sha": "abc1234",
            "started_at": "2026-09-23T00:00:00Z", "wall_seconds": wall,
            "final_state": "done",
            "gates": {"passed": gates, "total": total, "failures": []},
            "fidelity": fidelity, "score": score}


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------

def test_faster_same_quality_ships():
    out = sufficiency(report(wall=100), report(wall=90))
    assert out["verdict"] == "SHIP"
    assert out["time"]["decreased"] and not out["quality"]["increased"]


def test_same_time_better_quality_ships():
    out = sufficiency(report(wall=100, gates=6), report(wall=100, gates=7))
    assert out["verdict"] == "SHIP"
    assert out["quality"]["increased"] and not out["time"]["decreased"]


def test_slightly_slower_within_tolerance_better_quality_ships():
    out = sufficiency(report(wall=100, fid_pass=2), report(wall=104.9, fid_pass=3))
    assert out["verdict"] == "SHIP"
    assert not out["time"]["regressed"]


def test_faster_and_better_ships():
    assert sufficiency(report(wall=100, score=50), report(wall=80, score=60))["verdict"] == "SHIP"


def test_nothing_improved_rejects():
    out = sufficiency(report(), report())
    assert out["verdict"] == "REJECT"
    assert any("neither time decreased nor quality increased" in r for r in out["reasons"])


def test_slower_within_tolerance_same_quality_rejects():
    out = sufficiency(report(wall=100), report(wall=103))
    assert out["verdict"] == "REJECT"
    assert not out["time"]["regressed"] and not out["time"]["decreased"]


def test_faster_but_quality_regressed_rejects():
    out = sufficiency(report(wall=100, gates=6), report(wall=50, gates=5))
    assert out["verdict"] == "REJECT"
    assert out["time"]["decreased"] and out["quality"]["regressed"]
    assert "quality regressed" in out["reasons"]


def test_better_but_time_regressed_rejects():
    out = sufficiency(report(wall=100, gates=6), report(wall=106, gates=8))
    assert out["verdict"] == "REJECT"
    assert out["quality"]["increased"] and out["time"]["regressed"]
    assert "time regressed" in out["reasons"]


def test_time_regression_boundary_is_strictly_more_than_five_percent():
    at_edge = sufficiency(report(wall=100, gates=6), report(wall=105.0, gates=7))
    past_edge = sufficiency(report(wall=100, gates=6), report(wall=105.01, gates=7))
    assert at_edge["verdict"] == "SHIP" and not at_edge["time"]["regressed"]
    assert past_edge["verdict"] == "REJECT" and past_edge["time"]["regressed"]


def test_score_only_regression_rejects_even_when_faster():
    out = sufficiency(report(wall=100, score=60), report(wall=10, score=59.999))
    assert out["verdict"] == "REJECT" and out["quality"]["regressed"]


# ---------------------------------------------------------------------------
# the quality order
# ---------------------------------------------------------------------------

def test_quality_order_gates_before_fidelity_before_score():
    assert quality_key(report(gates=7, fid_pass=0, score=0)) > quality_key(
        report(gates=6, fid_pass=4, score=100))
    assert quality_key(report(gates=6, fid_pass=3, score=0)) > quality_key(
        report(gates=6, fid_pass=2, score=100))
    assert quality_key(report(gates=6, fid_pass=2, score=51)) > quality_key(
        report(gates=6, fid_pass=2, score=50))


def test_more_gates_fewer_fidelity_passes_is_an_increase():
    out = sufficiency(report(wall=100, gates=6, fid_pass=4), report(wall=100, gates=7, fid_pass=0))
    assert out["quality"]["increased"] and out["verdict"] == "SHIP"


def test_score_breaks_a_tie():
    out = sufficiency(report(wall=100, score=50.0), report(wall=100, score=50.5))
    assert out["verdict"] == "SHIP" and out["quality"]["increased"]


# ---------------------------------------------------------------------------
# comparability guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("candidate, needle", [
    (report(task="other"), "different tasks"),
    (report(total=9), "different gate totals"),
    (report(metrics=("a", "b", "c")), "different fidelity metrics"),
])
def test_incomparable_runs_reject(candidate, needle):
    out = sufficiency(report(), candidate)
    assert out["verdict"] == "REJECT"
    assert any(needle in r for r in out["reasons"])


def test_invalid_report_rejects():
    broken = report(wall=1)
    del broken["gates"]
    out = sufficiency(report(), broken)
    assert out["verdict"] == "REJECT"
    assert any("candidate report invalid" in r for r in out["reasons"])


def test_cli_exit_codes(tmp_path):
    import json

    base = tmp_path / "b.json"
    cand = tmp_path / "c.json"
    base.write_text(json.dumps(report(wall=100)))
    cand.write_text(json.dumps(report(wall=90)))
    assert compare_main([str(base), str(cand)]) == 0
    cand.write_text(json.dumps(report(wall=200)))
    assert compare_main([str(base), str(cand)]) == 1


# ---------------------------------------------------------------------------
# report schema
# ---------------------------------------------------------------------------

def test_schema_accepts_a_sound_report():
    assert validate_report(report()) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.pop("wall_seconds"), "missing 'wall_seconds'"),
    (lambda r: r.update(schema="v0"), "schema"),
    (lambda r: r.update(final_state="finished"), "final_state"),
    (lambda r: r.update(wall_seconds=-1.0), "negative"),
    (lambda r: r["gates"].update(passed=9), "outside"),
    (lambda r: r["gates"].update(passed=True), "gates.passed"),
    (lambda r: r["gates"].update(failures="x"), "failures"),
    (lambda r: r["fidelity"]["a"].pop("tol"), "missing 'tol'"),
    (lambda r: r["fidelity"]["a"].update({"pass": 1}), "not a bool"),
    (lambda r: r.update(score="high"), "'score'"),
])
def test_schema_rejects(mutate, needle):
    bad = copy.deepcopy(report())
    mutate(bad)
    problems = validate_report(bad)
    assert problems and any(needle in p for p in problems), problems
