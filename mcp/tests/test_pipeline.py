"""The staged build pipeline — `projects/<slug>/design/build-plan.json`.

The owner's directive, which is what every test here is measuring:

    Character (and similar) builds are a STAGED PIPELINE the artist drives step
    by step — "make the mesh", "check it", "now rig it" — never an assumed
    one-shot. Each stage ends with its gate verdict and artifacts; a red gate
    blocks the next stage.

So five things have to hold, and each is a section below:

* **Every task has a chain, and the chain is the contract.** Ten stages for a
  character, shorter ones for a part, a device, a floor plan and a mould. A
  stage with no gate and no id is not a stage.
* **A verdict survives the turn.** Record it, and it is on disk — numbers,
  artifacts, history — because the entire reason the board is a file is that the
  conversation is not one.
* **A red gate really blocks.** Advancing over a failed stage is refused in a
  sentence that names it and quotes what it measured.
* **An override is signed, and never silent.** It takes a who and a why, the
  stepped-over stage is marked `overridden` rather than `passed`, and both
  stages carry the signature for good.
* **Nothing already on disk is lost.** The werewolf's real build-plan predates
  stages entirely; it has to load, render and round-trip with its notes and its
  components byte for byte.

`projects/` is redirected to a tmp_path for every test in this module, exactly
as `test_task_config.py` and `test_design.py` do it, so the real one is never
touched. Nothing here binds a port or talks to a backend: a build plan is a
state machine over JSON, and the tools deliberately build nothing themselves.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client

from forge_mcp import config, pipeline, server, task_config
from forge_mcp.errors import ForgeError


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect projects/ so no test can write into the real repo folder."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


def call(name: str, arguments: dict[str, Any] | None = None):
    """One tools/call over an in-memory session; returns the CallToolResult."""

    async def run():
        async with Client(server.app) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def text_of(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def plan_on_disk(projects_dir: Path, slug: str) -> dict:
    path = projects_dir / slug / "design" / "build-plan.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sheet(slug: str, task: str = "character") -> Path:
    """Materialise the settings sheet, which is where the task comes from."""
    return task_config.write(slug, task_config.new_sheet(slug, task))


def start(slug: str, task: str = "character") -> dict:
    """A project with a sheet and a written plan at its first stage."""
    sheet(slug, task)
    _slug, plan, _path, _fresh = pipeline.load(slug)
    pipeline.write(slug, plan)
    return plan


#: The werewolf's real plan, shortened but SHAPED exactly like it: version 1,
#: a notes paragraph, and components carrying deeply nested measurement blocks.
#: It has no `task` and no `stages` — it was written before either existed.
WEREWOLF_PLAN: dict = {
    "version": 1,
    "project": "werewolf",
    "notes": "Three-form player character. Forms A/B share one mesh+skeleton.",
    "components": [
        {
            "id": "form-a-human-base",
            "label": "Form A — human base mesh",
            "status": "built",
            "scene_objects": {"sculpt_source": "werewolf-form-a",
                              "game_mesh": "werewolf-form-a_retopo"},
            "description": "REBUILT 2026-09-18 as the definitive Form A.",
            "technique_note": "task-config's symmetry is not wired in yet.",
        },
        {
            "id": "form-a-rig",
            "label": "Form A/B — shared biped skeleton",
            "status": "built",
            "rig_check_2026_09_18_full": {
                "gate": "FAIL overall",
                "rest_intersections": 608,
                "centering": {
                    "verdict": "attention",
                    "gated_bones_worst_to_best": [
                        ["DEF-thigh.L.001/.R.001", 54.1, "attention"],
                        ["DEF-shin.L/.R", 17.3, "ok"],
                    ],
                },
                "volume_table": {"knee_L": {"vol_pct": 24.66, "new_clips": 1564}},
            },
            "animation_check_2026_09_18": {"gate": "ok", "worst_drift_mm": 1.1},
            "known_issues": ["Overlap is genuinely worse than last session."],
        },
        {"id": "form-b-shape-keys", "label": "Form B", "status": "pending",
         "description": "Same mesh/skeleton as Form A."},
    ],
}


# ---------------------------------------------------------------------------
# The stage templates
# ---------------------------------------------------------------------------


def test_every_task_has_a_chain() -> None:
    """One table per task, exactly as task_config does it — a new task is one
    entry in each, and a task with a settings template and no chain would be a
    build nobody could stage."""
    assert set(pipeline.STAGE_TEMPLATES) == set(task_config.TASKS)


@pytest.mark.parametrize("task", task_config.TASKS)
def test_a_chain_is_a_real_chain(task: str) -> None:
    stages = pipeline.stage_template(task)
    assert stages, task
    ids = [stage["id"] for stage in stages]
    assert len(set(ids)) == len(ids), f"{task}: duplicate stage id"
    for stage in stages:
        assert stage["status"] == "pending"
        assert stage["title"].strip()
        assert stage["does"].strip()
        assert stage["gate"], f"{task}.{stage['id']} decides nothing"
        assert stage["artifacts"] == [] and stage["numbers"] == {}
        assert stage["history"] == []
        assert set(stage) <= set(pipeline.STAGE_KEYS), stage["id"]


@pytest.mark.parametrize("task", task_config.TASKS)
def test_a_chain_is_json_round_trippable(task: str) -> None:
    plan = pipeline.new_plan("demo", task)
    assert json.loads(json.dumps(plan)) == plan


def test_the_character_chain_is_the_owners_ten_stages_in_order() -> None:
    """The directive named these, in this order, and the order IS the pipeline:
    it is what "the next stage" means and what a skip is measured against."""
    assert pipeline.chain_ids("character") == (
        "reference", "design", "generate", "clean", "verify_mesh",
        "rig", "skin", "correctives", "animate", "export",
    )


def test_the_other_tasks_get_shorter_chains() -> None:
    assert pipeline.chain_ids("part") == (
        "design", "author", "generate", "check", "export")
    assert pipeline.chain_ids("device") == (
        "design", "circuit", "author", "generate", "check", "export")
    assert pipeline.chain_ids("floorplan") == (
        "extract", "validate", "echo", "build", "reconcile")
    assert pipeline.chain_ids("mold") == (
        "design", "source", "undercut", "mold", "export")
    for task in ("part", "device", "floorplan", "mold"):
        assert len(pipeline.chain_ids(task)) < len(
            pipeline.chain_ids("character")), task


def test_the_character_gates_name_the_real_forge_measurements() -> None:
    """A gate that named a check Forge cannot run is a gate nobody can close."""
    gates = {stage["id"]: stage["gate"]
             for stage in pipeline.stage_template("character")}
    assert "symmetry_residual_mm" in gates["clean"]
    assert "rig_check.centering" in gates["rig"]
    assert "rig_check.overlap" in gates["skin"]
    assert "rig_check.volume" in gates["correctives"]
    assert "animation_check.foot_slide_mm" in gates["animate"]
    assert "morph_targets" in gates["export"]


def test_the_rig_stage_gates_on_all_seven_rig_check_placement_checks() -> None:
    """wip-14 taught rig_check four more placement checks — hand containment,
    foot height, rest-stance IK headroom, corrective driver domain — beside the
    three the rig stage already gated on, and bend_direction had been measured
    since the backward-knee bug without ever being named in the plan. A
    werewolf whose hand bones ran outboard of the mesh, or whose rest stance
    stood at 0.9984 of its own IK reach, passed every one of the old three
    gates; a named gate is the only thing that makes a build stop for it."""
    gates = {stage["id"]: stage["gate"]
             for stage in pipeline.stage_template("character")}
    assert gates["rig"] == [
        "rig_check.asymmetry_mm", "rig_check.side_naming",
        "rig_check.centering", "rig_check.bend_direction",
        "rig_check.hand_containment",
        "rig_check.foot_height", "rig_check.ik_reach_headroom_rest",
    ]


def test_the_correctives_stage_gates_on_its_own_driver_domain_check() -> None:
    """corrective_driver_domain only means something once a corrective shape
    key exists to have a driver domain — that is the correctives stage, not
    rig, so it lands beside `volume` rather than up with rig's landmark
    checks."""
    gates = {stage["id"]: stage["gate"]
             for stage in pipeline.stage_template("character")}
    assert gates["correctives"] == [
        "rig_check.volume", "rig_check.corrective_driver_domain",
    ]


def test_the_animate_stage_gates_on_all_five_deformation_checks() -> None:
    """The audit's finding, verbatim: the animate stage gated on foot_slide_mm
    alone, so a clip that stretched its DEF bones past their stretch budget, or
    a loop that did not close its own seam, or an anticipation crouch that
    never read, would sail the whole wip-14 wave through the build plan."""
    gates = {stage["id"]: stage["gate"]
             for stage in pipeline.stage_template("character")}
    assert gates["animate"] == [
        "animation_check.foot_slide_mm", "animation_check.bone_stretch_budget",
        "animation_check.ik_reach_headroom", "animation_check.loop_seam_closure",
        "animation_check.anticipation_reads",
    ]


def test_an_unknown_task_is_refused_with_the_list() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.stage_template("statue")
    assert "character" in str(exc.value) and "floorplan" in str(exc.value)


# ---------------------------------------------------------------------------
# Where the chain comes from
# ---------------------------------------------------------------------------


def test_the_task_comes_off_the_settings_sheet() -> None:
    sheet("gecko", "character")
    assert pipeline.resolve_task("gecko") == "character"
    _slug, plan, _path, fresh = pipeline.load("gecko")
    assert fresh is True
    assert [stage["id"] for stage in plan["stages"]] == list(
        pipeline.chain_ids("character"))


def test_the_plans_own_task_beats_the_sheet() -> None:
    """Plan -> sheet, the same precedence floorplan_validate uses: a task
    written into THIS plan is a decision about this plan."""
    sheet("hybrid", "character")
    assert pipeline.resolve_task("hybrid", {"task": "mold"}) == "mold"


def test_a_project_with_no_task_is_refused_naming_task_config_init() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.load("nameless")
    message = str(exc.value)
    assert "task_config_init" in message
    assert "guess" in message.lower() or "guessing" in message.lower()


def test_status_materialises_a_board_without_writing_it(
        projects_dir: Path) -> None:
    sheet("ghost", "character")
    report = text_of(call("pipeline_status", {"project": "ghost"}))
    assert "NOT ON DISK YET" in report
    assert "reference" in report and "export" in report
    assert not (projects_dir / "ghost" / "design" / "build-plan.json").exists()


# ---------------------------------------------------------------------------
# Status round-trip
# ---------------------------------------------------------------------------


def test_a_recorded_verdict_is_on_disk(projects_dir: Path) -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "artifacts": ["projects/gecko/design/refs/front.png"],
    })

    plan = plan_on_disk(projects_dir, "gecko")
    stage = next(item for item in plan["stages"] if item["id"] == "reference")
    assert stage["status"] == "passed"
    assert stage["numbers"] == {"references_on_disk": 4}
    assert stage["artifacts"] == ["projects/gecko/design/refs/front.png"]
    actions = [item["action"] for item in stage["history"]]
    assert actions == ["advance", "record"]
    assert pipeline.stage_status("gecko", "reference") == "passed"


def test_the_plan_settles_at_read_time(projects_dir: Path) -> None:
    """An edit made in a text editor is a supported way to move a stage, and
    the next call has to see it — nothing here caches."""
    start("gecko")
    path = projects_dir / "gecko" / "design" / "build-plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["stages"][0]["status"] = "passed"
    plan["stages"][0]["numbers"] = {"references_on_disk": 9}
    path.write_text(json.dumps(plan), encoding="utf-8")

    assert pipeline.stage_status("gecko", "reference") == "passed"
    assert "references_on_disk 9" in text_of(
        call("pipeline_status", {"project": "gecko"}))


def test_numbers_and_artifacts_merge_unless_replace_is_asked_for() -> None:
    start("gecko")
    pipeline_calls = [
        {"numbers": {"verify_design": "pass"}, "artifacts": ["a.png"]},
        {"numbers": {"mesh_diagnose": 0}, "artifacts": ["b.png"]},
    ]
    for payload in pipeline_calls:
        call("pipeline_record", dict(
            {"project": "gecko", "stage": "verify_mesh",
             "status": "in_progress"}, **payload))
    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "verify_mesh")
    assert stage["numbers"] == {"verify_design": "pass", "mesh_diagnose": 0}
    assert stage["artifacts"] == ["a.png", "b.png"]

    call("pipeline_record", {
        "project": "gecko", "stage": "verify_mesh", "status": "in_progress",
        "numbers": {"silhouette_iou": 0.91}, "replace": True})
    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "verify_mesh")
    assert stage["numbers"] == {"silhouette_iou": 0.91}
    assert stage["artifacts"] == []


def test_a_stage_cannot_pass_with_nothing_measured() -> None:
    """A green verdict with no number beside it is an opinion."""
    start("gecko")
    result = call("pipeline_record", {
        "project": "gecko", "stage": "generate", "status": "passed"})
    assert result.is_error
    message = text_of(result)
    assert "opinion" in message
    assert "sign_off" in message
    assert pipeline.stage_status("gecko", "generate") == "pending"


def test_overridden_is_not_a_verdict_anybody_records() -> None:
    start("gecko")
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_status("overridden")
    assert "pipeline_advance" in str(exc.value)


def test_a_measurement_that_is_not_a_measurement_is_refused() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_numbers({"worst": {"deep": {"deeper": 1}}})
    assert "flat" in str(exc.value)
    with pytest.raises(ForgeError):
        pipeline.coerce_numbers(["worst_drift_mm", 1.1])
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_artifacts(["ok.png", 7])
    assert "paths" in str(exc.value)
    # A flat table of numbers IS a measurement — the volume table is one.
    assert pipeline.coerce_numbers({"volume": {"knee_L": 24.66}}) == {
        "volume": {"knee_L": 24.66}}


def test_an_unknown_stage_is_refused_with_the_chain() -> None:
    start("gecko")
    result = call("pipeline_advance", {"project": "gecko", "stage": "sculpt"})
    assert result.is_error
    message = text_of(result)
    assert "correctives" in message and "verify_mesh" in message


# ---------------------------------------------------------------------------
# A red gate blocks the next stage
# ---------------------------------------------------------------------------


def _through_verify(slug: str, verdict: str, numbers: dict) -> None:
    """Drive gecko to verify_mesh and settle it green or red."""
    start(slug)
    for stage, measured in (
        ("reference", {"references_on_disk": 4}),
        ("design", {"sign_off": True}),
        ("generate", {"watertight": True}),
        ("clean", {"symmetry_residual_mm": 0.0}),
    ):
        pipeline_advance_and_pass(slug, stage, measured)
    call("pipeline_advance", {"project": slug, "stage": "verify_mesh"})
    call("pipeline_record", {"project": slug, "stage": "verify_mesh",
                             "status": verdict, "numbers": numbers})


def pipeline_advance_and_pass(slug: str, stage: str, numbers: dict) -> None:
    call("pipeline_advance", {"project": slug, "stage": stage})
    call("pipeline_record", {"project": slug, "stage": stage,
                             "status": "passed", "numbers": numbers})


def test_advance_refuses_over_a_red_gate_and_quotes_it() -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})

    result = call("pipeline_advance", {"project": "gecko", "stage": "rig"})
    assert result.is_error
    message = text_of(result)
    assert "verify_mesh" in message
    assert "608" in message, "a refusal has to quote what the gate measured"
    assert "override=true" in message
    assert pipeline.stage_status("gecko", "rig") == "pending"


def test_advance_refuses_a_skip_over_unfinished_work() -> None:
    start("gecko")
    result = call("pipeline_advance", {"project": "gecko", "stage": "rig"})
    assert result.is_error
    message = text_of(result)
    assert "skip" in message
    assert "reference" in message and "pending" in message


def test_the_next_stage_is_allowed_once_the_gate_is_green() -> None:
    _through_verify("gecko", "passed", {"verify_design": "pass"})
    report = text_of(call("pipeline_advance",
                          {"project": "gecko", "stage": "rig"}))
    assert "Started rig" in report
    assert pipeline.stage_status("gecko", "rig") == "in_progress"
    assert pipeline.blocked("gecko") is None


def test_a_red_stage_is_what_blocked_answers() -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})
    assert pipeline.blocked("gecko") == "verify_mesh"


def test_re_advancing_a_passed_stage_re_opens_it_and_says_so() -> None:
    _through_verify("gecko", "passed", {"verify_design": "pass"})
    report = text_of(call("pipeline_advance",
                          {"project": "gecko", "stage": "clean"}))
    assert "Re-opened clean" in report
    assert pipeline.stage_status("gecko", "clean") == "in_progress"


# ---------------------------------------------------------------------------
# An override is signed, and never silent
# ---------------------------------------------------------------------------


def test_an_override_without_a_name_and_a_reason_is_refused() -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})
    result = call("pipeline_advance",
                  {"project": "gecko", "stage": "rig", "override": True})
    assert result.is_error
    assert "who" in text_of(result) and "why" in text_of(result)
    assert pipeline.stage_status("gecko", "verify_mesh") == "failed"

    result = call("pipeline_advance", {
        "project": "gecko", "stage": "rig", "override": True, "who": "kenneth"})
    assert result.is_error
    assert "why" in text_of(result)
    assert pipeline.stage_status("gecko", "rig") == "pending"


def test_an_override_marks_the_red_stage_overridden_never_passed(
        projects_dir: Path) -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})
    report = text_of(call("pipeline_advance", {
        "project": "gecko", "stage": "rig", "override": True,
        "who": "kenneth", "why": "the intersections are cloth contact, not mesh",
    }))

    assert "OVERRIDE signed by kenneth" in report
    assert "cloth contact" in report
    assert "OVERRIDDEN" in report

    plan = plan_on_disk(projects_dir, "gecko")
    red = next(item for item in plan["stages"] if item["id"] == "verify_mesh")
    assert red["status"] == "overridden"
    assert red["status"] != "passed"
    signed = [item for item in red["history"] if item["action"] == "overridden"]
    assert len(signed) == 1
    assert signed[0]["who"] == "kenneth"
    assert signed[0]["why"].startswith("the intersections")
    assert signed[0]["by"] == "rig"

    # ...and the stage that stepped over it carries it too, so neither end of
    # the decision can be read without the other.
    started = next(item for item in plan["stages"] if item["id"] == "rig")
    override = started["history"][-1]["override"]
    assert override["who"] == "kenneth"
    assert override["stepped_over"] == ["verify_mesh"]
    assert started["status"] == "in_progress"


def test_an_overridden_stage_is_still_marked_on_the_board() -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})
    call("pipeline_advance", {
        "project": "gecko", "stage": "rig", "override": True,
        "who": "kenneth", "why": "cloth contact, not mesh"})
    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "[~]" in board
    assert "OVERRIDDEN by kenneth" in board
    assert "self_intersections 608" in board


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


def test_the_board_names_the_numbers_and_the_artifact_paths(
        projects_dir: Path) -> None:
    start("gecko")
    made = projects_dir / "gecko" / "models" / "gecko-wip-1.blend"
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_text("not really a blend", encoding="utf-8")

    pipeline_advance_and_pass("gecko", "reference", {"references_on_disk": 4})
    call("pipeline_advance", {"project": "gecko", "stage": "design"})
    call("pipeline_record", {
        "project": "gecko", "stage": "design", "status": "passed",
        "numbers": {"sign_off": True, "settings_sheet": "materialised"},
        "artifacts": [str(made), "projects/gecko/design/concept.svg"]})

    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "[x] 1. reference" in board
    assert "references_on_disk 4" in board
    assert "sign_off true" in board
    assert str(made) in board
    # A path is a claim, and the board checks it.
    assert "projects/gecko/design/concept.svg" in board
    assert "(NOT ON DISK)" in board
    # The stages that have not happened advertise their gate and their tools.
    assert "gate: mesh_exists, watertight" in board
    assert "rigforge_export_godot" in board
    assert "next: start generate" in board
    assert "NEVER A ONE-SHOT" in board or "NOT A ONE-SHOT" in board


def test_the_board_says_what_next_is_for_every_shape_of_board() -> None:
    start("gecko")
    assert "next: start reference" in text_of(
        call("pipeline_status", {"project": "gecko"}))

    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    assert "next: finish reference" in text_of(
        call("pipeline_status", {"project": "gecko"}))

    call("pipeline_record", {"project": "gecko", "stage": "reference",
                             "status": "failed", "numbers": {"refs": 0}})
    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "next: fix reference" in board and "RED" in board


def test_a_finished_chain_says_it_is_done() -> None:
    start("gecko", "floorplan")
    for stage in pipeline.chain_ids("floorplan"):
        pipeline_advance_and_pass("gecko", stage, {"ok": True})
    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "every stage is green" in board
    assert "[ ]" not in board


def test_the_record_report_names_an_uncovered_gate() -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "clean"})
    report = text_of(call("pipeline_record", {
        "project": "gecko", "stage": "clean", "status": "passed",
        "numbers": {"symmetry_residual_mm": 0.0}}))
    assert "gate not fully covered" in report
    assert "poly_budget_triangles" in report and "uv_coverage" in report


def test_a_red_record_says_the_stages_after_it_are_blocked() -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    report = text_of(call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "failed",
        "numbers": {"references_on_disk": 0}}))
    assert "RED" in report and "blocked" in report


# ---------------------------------------------------------------------------
# Visibility: the reports that name the design sheet name the board too
# ---------------------------------------------------------------------------


def test_save_design_doc_mentions_the_board_when_one_exists() -> None:
    start("gecko")
    pipeline_advance_and_pass("gecko", "reference", {"references_on_disk": 4})
    report = text_of(call("save_design_doc", {
        "project": "gecko", "filename": "requirements.md",
        "content": "# Gecko\n"}))
    assert "build plan:" in report
    assert "1/10 stages green" in report
    assert "next design" in report


def test_save_design_doc_points_at_the_board_when_there_is_none() -> None:
    report = text_of(call("save_design_doc", {
        "project": "newt", "filename": "requirements.md",
        "content": "# Newt\n"}))
    assert "no build plan yet" in report
    assert "never one shot" in report


def test_the_settings_sheet_report_names_the_board_too() -> None:
    start("gecko")
    _through_verify("gecko", "failed", {"self_intersections": 608})
    report = text_of(call("task_config_get", {"project": "gecko"}))
    assert "build plan:" in report
    assert "RED: verify_mesh" in report


def test_floorplan_validate_names_the_board() -> None:
    start("house", "floorplan")
    plan = {
        "version": 1,
        "units": "mm",
        "rooms": [{"id": "room-kitchen", "label": "kitchen",
                   "polygon_mm": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]}],
        "walls": [{"id": "wall-01", "from_mm": [0, 0], "to_mm": [3000, 0]}],
    }
    report = text_of(call("floorplan_validate",
                          {"plan": plan, "project": "house"}))
    assert "build plan:" in report
    assert "next extract" in report


# ---------------------------------------------------------------------------
# The werewolf-shaped plan loads without loss
# ---------------------------------------------------------------------------


def write_werewolf(projects_dir: Path) -> Path:
    design = projects_dir / "werewolf" / "design"
    design.mkdir(parents=True)
    path = design / "build-plan.json"
    path.write_text(json.dumps(WEREWOLF_PLAN, indent=2), encoding="utf-8")
    sheet("werewolf", "character")
    return path


def test_the_existing_werewolf_plan_reads(projects_dir: Path) -> None:
    write_werewolf(projects_dir)
    plan = pipeline.read("werewolf")
    assert plan["notes"] == WEREWOLF_PLAN["notes"]
    assert len(plan["components"]) == 3


def test_the_werewolf_plan_gains_stages_and_loses_nothing(
        projects_dir: Path) -> None:
    """The migration, measured: the chain materialises BESIDE what is already
    there, and every component — nested measurement blocks and all — comes back
    byte for byte after a write."""
    write_werewolf(projects_dir)
    slug, plan, _path, fresh = pipeline.load("werewolf")
    assert fresh is True, "a plan with no stages block gets one materialised"
    assert plan["task"] == "character"
    assert [stage["id"] for stage in plan["stages"]] == list(
        pipeline.chain_ids("character"))

    pipeline.write(slug, plan)
    after = plan_on_disk(projects_dir, "werewolf")
    assert after["notes"] == WEREWOLF_PLAN["notes"]
    assert after["components"] == WEREWOLF_PLAN["components"]
    assert after["version"] == WEREWOLF_PLAN["version"] == pipeline.PLAN_VERSION
    assert after["project"] == "werewolf"
    note = after["history"][-1]["note"]
    assert "3 components already on the plan were left exactly as they were" \
        in note


def test_the_werewolf_board_shows_its_components_beside_the_stages(
        projects_dir: Path) -> None:
    write_werewolf(projects_dir)
    board = text_of(call("pipeline_status", {"project": "werewolf"}))
    assert "3 components (2 built)" in board
    assert "what the build is MADE of" in board
    assert "[ ] 1. reference" in board


def test_driving_the_werewolf_plan_keeps_its_components(
        projects_dir: Path) -> None:
    write_werewolf(projects_dir)
    call("pipeline_advance", {"project": "werewolf", "stage": "reference"})
    call("pipeline_record", {
        "project": "werewolf", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 6}})
    after = plan_on_disk(projects_dir, "werewolf")
    assert after["components"] == WEREWOLF_PLAN["components"]
    assert after["notes"] == WEREWOLF_PLAN["notes"]
    stage = next(item for item in after["stages"] if item["id"] == "reference")
    assert stage["status"] == "passed"


#: A plan shaped like the werewolf's real one ALREADY IS today: stages
#: materialised under the pre-remedy templates, rig/correctives/animate
#: carrying the old, shorter gate lists, and rig already `passed` on them.
ALREADY_STAGED_PLAN: dict = {
    "version": 1,
    "project": "werewolf",
    "task": "character",
    "stages": [
        {"id": "rig", "title": "Rig — landmarks, metarig, generated control rig",
         "status": "passed",
         "gate": ["rig_check.asymmetry_mm", "rig_check.side_naming",
                  "rig_check.centering"],
         "artifacts": [], "numbers": {"asymmetry_mm": 0.4}, "history": []},
        {"id": "correctives",
         "title": "Correctives — the collapsed knee, fixed with a number",
         "status": "overridden", "gate": ["rig_check.volume"],
         "artifacts": [], "numbers": {}, "history": []},
        {"id": "animate", "title": "Animate — clips that do not slide",
         "status": "passed", "gate": ["animation_check.foot_slide_mm"],
         "artifacts": [], "numbers": {"foot_slide_mm": 3.1}, "history": []},
    ],
    "history": [{"date": "2026-09-18T00:00:00", "note": "materialised"}],
}


def test_an_existing_plans_gate_lists_are_never_rebuilt_behind_the_artist(
        projects_dir: Path) -> None:
    """The invariant `load` documents: 'a plan that predates the stages block
    ... gets the chain materialised in memory' — but only when `stages` is
    missing or empty. A plan that already has a `stages` block, even one
    written under the pre-remedy templates, keeps exactly the gate lists it
    was written with; the new six-gate rig / five-gate animate / two-gate
    correctives lists only ever appear in a plan materialised fresh. This is
    the werewolf as it stands today — passed on the old three-name rig gate —
    and this remedy does not go back and rewrite that history."""
    design = projects_dir / "werewolf" / "design"
    design.mkdir(parents=True)
    (design / "build-plan.json").write_text(
        json.dumps(ALREADY_STAGED_PLAN, indent=2), encoding="utf-8")
    sheet("werewolf", "character")

    slug, plan, _path, fresh = pipeline.load("werewolf")
    assert fresh is False, "a plan with a stages block already on it is read, " \
        "not rematerialised"
    gates = {stage["id"]: stage["gate"] for stage in plan["stages"]}
    assert gates["rig"] == [
        "rig_check.asymmetry_mm", "rig_check.side_naming", "rig_check.centering",
    ], "the old three-gate rig list survives untouched"
    assert gates["correctives"] == ["rig_check.volume"]
    assert gates["animate"] == ["animation_check.foot_slide_mm"]
    assert plan["stages"][0]["status"] == "passed", \
        "already-recorded verdicts are not disturbed either"

    # Writing it back changes nothing about the gate lists: the new template's
    # richer gates are not silently folded onto a plan that never asked for a
    # rematerialise.
    pipeline.write(slug, plan)
    after = plan_on_disk(projects_dir, "werewolf")
    gates_after = {stage["id"]: stage["gate"] for stage in after["stages"]}
    assert gates_after == gates, \
        "a write never upgrades an already-staged plan's gate lists"

    # The NEW template, materialised fresh for a different project, does carry
    # the remedy's fuller gate lists — proving the two plans diverge only
    # because one already existed and one did not.
    fresh_gates = {stage["id"]: stage["gate"]
                   for stage in pipeline.stage_template("character")}
    assert fresh_gates["rig"] != gates["rig"]
    assert len(fresh_gates["rig"]) == 7
    assert fresh_gates["correctives"] != gates["correctives"]
    assert fresh_gates["animate"] != gates["animate"]


def test_a_plan_that_is_not_a_plan_is_refused_and_nothing_is_rebuilt(
        projects_dir: Path) -> None:
    design = projects_dir / "werewolf" / "design"
    design.mkdir(parents=True)
    (design / "build-plan.json").write_text('{"components": {"a": 1}}',
                                            encoding="utf-8")
    sheet("werewolf", "character")
    with pytest.raises(ForgeError) as exc:
        pipeline.read("werewolf")
    assert "components" in str(exc.value)
    assert "Nothing was changed" in str(exc.value)


def test_broken_json_is_never_silently_replaced(projects_dir: Path) -> None:
    design = projects_dir / "werewolf" / "design"
    design.mkdir(parents=True)
    (design / "build-plan.json").write_text("{not json", encoding="utf-8")
    sheet("werewolf", "character")
    result = call("pipeline_status", {"project": "werewolf"})
    assert result.is_error
    message = text_of(result)
    assert "not valid JSON" in message
    assert "already been built" in message
    assert (design / "build-plan.json").read_text(encoding="utf-8") == "{not json"


def test_duplicate_stage_ids_are_refused(projects_dir: Path) -> None:
    plan = pipeline.new_plan("gecko", "character")
    plan["stages"][1]["id"] = "reference"
    with pytest.raises(ForgeError) as exc:
        pipeline.validate(plan)
    assert "two stages are both called 'reference'" in str(exc.value)


# ---------------------------------------------------------------------------
# The tools build nothing
# ---------------------------------------------------------------------------


def test_no_pipeline_tool_touches_a_backend(dead_backends) -> None:
    """The state machine is the whole contract: the assistant does the work
    between the stages, so none of these three may need Blender to answer."""
    start("gecko")
    for name, arguments in (
        ("pipeline_status", {"project": "gecko"}),
        ("pipeline_advance", {"project": "gecko", "stage": "reference"}),
        ("pipeline_record", {"project": "gecko", "stage": "reference",
                             "status": "passed",
                             "numbers": {"references_on_disk": 2}}),
    ):
        result = call(name, arguments)
        assert not result.is_error, f"{name}: {text_of(result)}"
