"""Floor plans (Phase 19) — the MCP mirror: validate, diff, build.

Three things are pinned, and they are the three ways this group could be
silently wrong:

* **the plan file is the model, so the WRITE obeys `save_design_doc`'s rules.**
  Same slug, same filename alphabet, same second check of the resolved path
  against the folder it must be under, and — the one that matters here —
  the *normalised* plan is what lands on disk, never the resolved one, so the
  `defaults` block stays live and raising `ceiling_mm` next week still moves
  every wall that was taking its height from it.
* **refusals cross verbatim.** `service.floorplan`'s messages name the entry
  they are about (`wall 'wall-03'`), and that id is the only word the artist and
  the model share about a drawing. A rewritten refusal is a refusal that no
  longer locates the problem.
* **the diff report carries the IDS.** "This edit rebuilds 2 walls, adds 1
  fixture and touches nothing else" is a promise about the hand-sculpted sofa in
  the next room surviving, and an unqualified "rebuilding the level" is not.

Blender is the NDJSON fake from `test_blender_client` on an ephemeral port —
never 9876 — so these run with Blender closed and against the *contract* in
docs/architecture.md. `projects/` is redirected to a `tmp_path`, so no test here
can write into the real repo folder.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, floorplan, server, util
from forge_mcp.errors import ForgeError

from .test_rigforge import blender, router, sent  # noqa: F401  (fixtures)

# --- a plan that is deliberately ordinary ------------------------------------
#
# One room, three walls, a door and a gap, a washer/dryer the appliance table
# knows and a sofa it does not. Everything optional is left out on purpose, so
# `fill_defaults` has something to resolve and the report has something to say.

PLAN: dict[str, Any] = {
    "version": 1,
    "units": "mm",
    "defaults": {"ceiling_mm": 2400},
    "rooms": [
        {"id": "room-kitchen", "label": "kitchen",
         "polygon_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]},
    ],
    "walls": [
        {"id": "wall-01", "from_mm": [0, 0], "to_mm": [4000, 0],
         "openings": [{"id": "door-01", "kind": "door", "at_mm": 2000,
                       "swing": "in"}]},
        {"id": "wall-02", "from_mm": [4000, 0], "to_mm": [4000, 3000]},
        {"id": "wall-03", "from_mm": [4000, 3000], "to_mm": [0, 3000],
         "openings": [{"id": "gap-01", "kind": "gap", "at_mm": 1500}]},
    ],
    "labels": [
        {"id": "wd-01", "label": "washer/dryer", "footprint_mm": [600, 400, 700, 700]},
        {"id": "sofa-01", "label": "nan's ottoman thing",
         "footprint_mm": [2000, 2000, 1800, 900]},
    ],
}


def plan() -> dict[str, Any]:
    """A fresh copy — every test may edit its own."""
    return copy.deepcopy(PLAN)


#: `build_floorplan`'s result, shaped exactly as docs/architecture.md documents
#: it: the four allocation lists are lists of IDS, not counts.
BUILD_RESULT = {
    "collection": "Floorplan",
    "built": ["wall-03", "gap-01"],
    "updated": ["wall-01"],
    "deleted": ["old-wall-09"],
    "unchanged": ["room-kitchen", "wall-02", "door-01"],
    "kept": [
        {"id": "wd-01", "object": "FP:wd-01",
         "why": "its mesh has 512 vertices where this entry builds 8, so it has "
                "been edited by hand"},
    ],
    "objects": 6,
    "object_names": ["FP:door-01", "FP:gap-01", "FP:room-kitchen", "FP:wall-01",
                     "FP:wall-02", "FP:wall-03"],
    "walls": 3,
    "openings": 2,
    "fixtures": 2,
    "floors": 1,
    "rooms": 1,
    "pieces": 9,
    "mechanisms": [
        {"id": "door-01", "opening": "door-01", "wall": "wall-01",
         "object": "FP:wall-01", "joint_type": "revolute", "axis": [0.0, 0.0, 1.0],
         "origin_mm": [1590.0, 0.0, 0.0], "range_deg": 90, "direction": 1,
         "swing": "in", "hinge": "left", "width_mm": 820.0, "height_mm": 2040.0,
         "actuated_by": "hand",
         "note": "intended motion only — the opening is a hole in the greybox"},
    ],
    "bounds_mm": {"min": [-50.0, -50.0, -50.0], "max": [4050.0, 3050.0, 2400.0],
                  "size": [4100.0, 3100.0, 2450.0]},
    "dimensions_mm": [4100.0, 3100.0, 2450.0],
    "mode": "update",
    "floor": True,
    "defaults_mm": {"ceiling_mm": 2400.0, "wall_mm": 100.0},
    "plan_version": 1,
    "units": "mm",
    "honesty": "This is a prototype greybox at real sizes, not a construction "
               "drawing.",
    "notes": ["Walls are built as solid pieces."],
    "warnings": ["Kept 'FP:wd-01' rather than rebuilding it."],
    "seconds": 0.047,
}


@pytest.fixture(autouse=True)
def never_the_real_blender(dead_backends) -> None:
    """Every test here starts pointed at a CLOSED port, before anything else.

    `floorplan_build` is the one tool in this file that changes a scene, and the
    artist's own Blender may be listening on 9876 right now. A test that forgot
    the `blender` fixture would build a greybox into their open session — so the
    default is a port nothing answers on, and the fake overrides it afterwards.
    """


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect projects/ so no test can write into the real repo folder."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


def saved_plan(projects_dir: Path, slug: str) -> dict[str, Any]:
    path = projects_dir / slug / "design" / "floorplan.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# floorplan_validate — the reading, in words the artist can correct
# ===========================================================================


def test_the_report_counts_every_kind_of_entry() -> None:
    report = server.floorplan_validate(plan=plan())
    assert "1 room, 3 walls, 2 openings, 2 fixtures" in report


def test_the_report_names_the_resolved_defaults() -> None:
    """The numbers a build will use, said out loud before anything is built."""
    report = server.floorplan_validate(plan=plan())
    assert "ceiling 2400 mm" in report
    assert "wall 100 mm" in report
    assert "door 820 x 2040 mm" in report


def test_the_report_says_which_entries_took_a_default() -> None:
    report = server.floorplan_validate(plan=plan())
    assert "took a default" in report
    assert "wall-02" in report and "thickness_mm" in report


def test_a_plan_where_nothing_was_defaulted_says_so() -> None:
    resolved = floorplan.resolve(plan())
    resolved.pop("provenance", None)
    report = server.floorplan_validate(plan=resolved)
    assert "every number is the artist's own" in report


def test_an_appliance_match_carries_its_route_and_its_confidence() -> None:
    """'I read this as a washer/dryer, on an alias match' is correctable in one
    word on the sheet; correcting it after the build costs a rebuild."""
    report = server.floorplan_validate(plan=plan())
    assert "wd-01" in report
    assert "washer/dryer" in report
    assert "confidence" in report


def test_an_unrecognised_label_keeps_the_footprint_they_drew() -> None:
    report = server.floorplan_validate(plan=plan())
    assert "no appliance match" in report
    assert "sofa-01" in report


def test_the_report_says_the_footprint_is_never_overridden() -> None:
    """A plan is top-down: only the HEIGHT ever comes from the table."""
    report = server.floorplan_validate(plan=plan())
    assert "FOOTPRINT IS NEVER OVERRIDDEN" in report


def test_the_report_ends_at_the_echo_back_gate() -> None:
    report = server.floorplan_validate(plan=plan())
    assert "nothing is built by this call" in report
    assert "floorplan.svg" in report
    assert "after they say yes" in report


def test_a_plan_or_a_project_is_required() -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate()
    assert "needs a plan" in str(caught.value)


def test_a_plan_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(plan=["wall-01"])  # type: ignore[arg-type]
    assert "floor-plan object" in str(caught.value)


# --- refusals, verbatim -----------------------------------------------------


def test_a_duplicate_id_is_refused_by_name() -> None:
    """Ids name Blender objects, so they are unique across all four kinds."""
    broken = plan()
    broken["labels"][1]["id"] = "wd-01"
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(plan=broken)
    assert "wd-01" in str(caught.value)


def test_a_missing_id_is_refused_rather_than_generated() -> None:
    """An invented id changes next run and takes the hand edits with it."""
    broken = plan()
    del broken["walls"][1]["id"]
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(plan=broken)
    assert "id" in str(caught.value).lower()


def test_an_opening_past_the_end_of_its_wall_is_refused_naming_both() -> None:
    broken = plan()
    broken["walls"][0]["openings"][0]["at_mm"] = 3990
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(plan=broken)
    message = str(caught.value)
    assert "door-01" in message and "wall-01" in message


def test_the_service_refusal_crosses_word_for_word(tmp_path: Path) -> None:
    """The sentence the service wrote is the sentence the model reads."""
    from service import floorplan as service_floorplan  # noqa: PLC0415

    broken = plan()
    broken["units"] = "inches"
    try:
        service_floorplan.validate_plan(broken)
    except Exception as exc:  # noqa: BLE001 — that IS the message under test
        expected = str(exc)
    else:  # pragma: no cover — the plan above is invalid by construction
        pytest.fail("the service accepted a plan in inches")

    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(plan=broken)
    assert str(caught.value) == expected


def test_a_refusal_writes_nothing(projects_dir: Path) -> None:
    broken = plan()
    broken["walls"][0]["openings"][0]["width_mm"] = 9000  # wider than the wall
    with pytest.raises(ForgeError):
        server.floorplan_validate(plan=broken, project="upstairs flat")
    assert not (projects_dir / "upstairs-flat" / "design" /
                "floorplan.json").exists()


# --- the save-back ----------------------------------------------------------


def test_the_plan_lands_in_the_projects_design_folder(projects_dir: Path) -> None:
    report = server.floorplan_validate(plan=plan(), project="upstairs flat")
    path = projects_dir / "upstairs-flat" / "design" / "floorplan.json"
    assert path.is_file()
    assert str(path) in report


def test_the_saved_plan_is_normalised_not_resolved(projects_dir: Path) -> None:
    """The `defaults` block has to stay LIVE on disk.

    Saving the resolved plan would bake every default into every entry, and then
    raising `defaults.ceiling_mm` next week would move nothing — the exact
    opposite of what the block is for.
    """
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    on_disk = saved_plan(projects_dir, "upstairs-flat")
    wall = next(w for w in on_disk["walls"] if w["id"] == "wall-02")
    assert "height_mm" not in wall
    assert "thickness_mm" not in wall
    assert "provenance" not in on_disk
    assert on_disk["defaults"]["ceiling_mm"] == 2400


def test_the_saved_plan_reads_back_through_the_same_tools(
    projects_dir: Path,
) -> None:
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    report = server.floorplan_validate(project="upstairs flat")
    assert "1 room, 3 walls, 2 openings, 2 fixtures" in report


def test_saving_again_updates_the_same_file(projects_dir: Path) -> None:
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    report = server.floorplan_validate(plan=plan(), project="upstairs flat")
    assert "updated the normalised plan" in report


def test_save_false_reports_without_writing(projects_dir: Path) -> None:
    report = server.floorplan_validate(plan=plan(), project="upstairs flat",
                                       save=False)
    assert not (projects_dir / "upstairs-flat" / "design" /
                "floorplan.json").exists()
    assert "normalised plan" not in report


def test_a_plan_with_no_project_writes_nothing(projects_dir: Path) -> None:
    server.floorplan_validate(plan=plan())
    assert not any(projects_dir.rglob("floorplan.json"))


@pytest.mark.parametrize(
    "given",
    ["../evil", "..\\evil", "design/upstairs", "C:\\Windows\\System32",
     "/etc/passwd", "~/notes", "%APPDATA%", ".."],
)
def test_a_path_shaped_project_is_refused_not_cleaned(given: str) -> None:
    """`save_design_doc`'s rules, because this is a second door onto the same
    folder and a laxer one would be the bug."""
    with pytest.raises(ForgeError):
        server.floorplan_validate(plan=plan(), project=given)


def test_the_plan_only_ever_lands_under_projects(projects_dir: Path) -> None:
    slug, path = util.floorplan_path("upstairs flat")
    assert slug == "upstairs-flat"
    assert path == (projects_dir / "upstairs-flat" / "design" /
                    "floorplan.json").resolve()


def test_a_missing_saved_plan_says_how_to_make_one() -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(project="never drawn")
    message = str(caught.value)
    assert "has no saved floor plan" in message
    assert "save_design_doc" in message


def test_a_corrupt_saved_plan_is_refused_without_touching_it(
    projects_dir: Path,
) -> None:
    design = projects_dir / "upstairs-flat" / "design"
    design.mkdir(parents=True)
    (design / "floorplan.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(project="upstairs flat")
    assert "not valid JSON" in str(caught.value)
    assert (design / "floorplan.json").read_text(encoding="utf-8") == "{not json"


def test_a_saved_file_that_is_not_an_object_is_refused(projects_dir: Path) -> None:
    design = projects_dir / "upstairs-flat" / "design"
    design.mkdir(parents=True)
    (design / "floorplan.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ForgeError) as caught:
        server.floorplan_validate(project="upstairs flat")
    assert "not a plan object" in str(caught.value)


def test_a_plan_that_will_not_serialise_is_not_written(projects_dir: Path) -> None:
    with pytest.raises(ForgeError) as caught:
        util.write_floorplan("upstairs flat", {"version": float("nan")})
    assert "will not serialise" in str(caught.value)
    assert not (projects_dir / "upstairs-flat" / "design" /
                "floorplan.json").exists()


# ===========================================================================
# floorplan_diff — the sentence to say BEFORE building
# ===========================================================================


def test_the_diff_names_the_ids_that_rebuild() -> None:
    revised = plan()
    revised["walls"][2]["thickness_mm"] = 150
    report = server.floorplan_diff(revised, against=plan())
    assert "rebuilds (1): wall-03" in report


def test_the_diff_summary_counts_nouns_and_ends_with_touches_nothing_else() -> None:
    """The whole point: 'this edit rebuilds 1 wall, adds 1 fixture and touches
    nothing else' is a promise about everything it did not name."""
    revised = plan()
    revised["walls"][2]["thickness_mm"] = 150
    revised["labels"].append({"id": "fridge-01", "label": "fridge",
                              "footprint_mm": [3500, 500, 700, 700]})
    report = server.floorplan_diff(revised, against=plan())
    assert "rebuilds 1 wall" in report
    assert "adds 1 fixture" in report
    assert "touches nothing else" in report
    assert "fridge-01" in report


def test_an_opening_change_is_reported_as_its_walls_change_too() -> None:
    """A door is a hole cut in a wall, so a moved door rebuilds that wall."""
    revised = plan()
    revised["walls"][0]["openings"][0]["at_mm"] = 1200
    report = server.floorplan_diff(revised, against=plan())
    assert "door-01" in report
    assert "wall-01" in report
    assert "rebuilds 2" in report or "2 walls" in report or "1 wall" in report


def test_a_removed_entry_is_reported_as_a_delete() -> None:
    revised = plan()
    revised["labels"] = [revised["labels"][0]]
    report = server.floorplan_diff(revised, against=plan())
    assert "deletes (1): sofa-01" in report
    assert "deletes 1 fixture" in report


def test_an_identical_plan_touches_nothing() -> None:
    report = server.floorplan_diff(plan(), against=plan())
    assert "nothing changed" in report
    assert "would touch not one object" in report


def test_the_diff_compares_resolved_values_so_a_default_moves_walls() -> None:
    """Raising `ceiling_mm` marks every wall taking its height from the block,
    and leaves a wall with its own explicit height alone."""
    before = plan()
    before["walls"][1]["height_mm"] = 3000
    revised = copy.deepcopy(before)
    revised["defaults"]["ceiling_mm"] = 2700
    report = server.floorplan_diff(revised, against=before)
    assert "wall-01" in report and "wall-03" in report
    changed = floorplan.diff(before, revised)["changed"]
    assert "wall-02" not in changed


def test_dropping_a_redundant_explicit_value_marks_nothing() -> None:
    before = plan()
    before["walls"][1]["thickness_mm"] = 100  # already the default
    report = server.floorplan_diff(plan(), against=before)
    assert "nothing changed" in report


def test_the_diff_promises_that_unnamed_ids_keep_their_objects() -> None:
    revised = plan()
    revised["walls"][2]["thickness_mm"] = 150
    report = server.floorplan_diff(revised, against=plan())
    assert "hand edits included" in report


def test_the_project_is_the_other_side_when_no_against_is_given(
    projects_dir: Path,
) -> None:
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    revised = plan()
    revised["walls"][2]["thickness_mm"] = 150
    report = server.floorplan_diff(revised, project="upstairs flat")
    assert "rebuilds (1): wall-03" in report
    assert "floorplan.json" in report


def test_a_diff_needs_something_to_compare_with() -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_diff(plan())
    assert "compares two plans" in str(caught.value)


def test_giving_both_sides_two_ways_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_diff(plan(), against=plan(), project="upstairs flat")
    assert "not both" in str(caught.value)


def test_a_diff_of_a_broken_plan_refuses_with_the_services_sentence() -> None:
    broken = plan()
    broken["walls"][0]["to_mm"] = [0, 0]  # zero-length
    with pytest.raises(ForgeError) as caught:
        server.floorplan_diff(broken, against=plan())
    assert "wall-01" in str(caught.value)


# ===========================================================================
# floorplan_build — what goes on the wire, and what the report says
# ===========================================================================


def test_build_sends_the_contract_command_and_every_parameter(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(plan=plan())

    assert fake.requests[0]["type"] == "build_floorplan"
    params = sent(fake, "build_floorplan")
    assert set(params) == {"plan", "collection", "mode", "floor"}
    assert params["collection"] == "Floorplan"
    assert params["mode"] == "update"
    assert params["floor"] is True


def test_update_is_the_default_mode_and_rebuild_is_opt_in(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(plan=plan())
    assert sent(fake, "build_floorplan")["mode"] == "update"

    server.floorplan_build(plan=plan(), mode="rebuild")
    modes = [r["params"]["mode"] for r in fake.requests
             if r.get("type") == "build_floorplan"]
    assert modes == ["update", "rebuild"]


def test_the_plan_crosses_the_wire_RESOLVED(blender) -> None:
    """Every number explicit, so the add-on cannot substitute a default of its
    own — `service.DEFAULTS` and the add-on's `PLAN_DEFAULTS` agree, and this is
    what keeps that agreement from ever being tested in anger."""
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(plan=plan())

    wire = sent(fake, "build_floorplan")["plan"]
    wall = next(w for w in wire["walls"] if w["id"] == "wall-02")
    assert wall["thickness_mm"] == 100.0
    assert wall["height_mm"] == 2400.0
    door = wire["walls"][0]["openings"][0]
    assert door["width_mm"] == 820.0 and door["height_mm"] == 2040.0
    fixture = next(f for f in wire["labels"] if f["id"] == "wd-01")
    assert fixture["height_mm"] > 0


def test_the_plan_on_the_wire_survives_a_json_round_trip(blender) -> None:
    """It is sent over a socket as JSON, so it has to BE JSON."""
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(plan=plan())
    wire = sent(fake, "build_floorplan")["plan"]
    assert json.loads(json.dumps(wire)) == wire


def test_a_broken_plan_never_reaches_blender(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT}, connections=0)
    broken = plan()
    broken["rooms"][0]["polygon_mm"] = [[0, 0], [10, 10]]  # two corners
    with pytest.raises(ForgeError):
        server.floorplan_build(plan=broken)
    assert fake.requests == []


def test_build_reads_the_projects_saved_plan(blender, projects_dir: Path) -> None:
    """`sync=False` here on purpose: this is about the plan the build READS.

    With a project and the default `sync=True` a build measures the scene first
    (its own section below), which is a second round trip and a second thing
    being tested. One claim per test.
    """
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(project="upstairs flat", sync=False)
    wire = sent(fake, "build_floorplan")["plan"]
    assert {w["id"] for w in wire["walls"]} == {"wall-01", "wall-02", "wall-03"}


def test_a_named_collection_crosses_and_a_blank_one_falls_back(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(plan=plan(), collection="Upstairs")
    assert sent(fake, "build_floorplan")["collection"] == "Upstairs"

    server.floorplan_build(plan=plan(), collection="   ")
    names = [r["params"]["collection"] for r in fake.requests
             if r.get("type") == "build_floorplan"]
    assert names == ["Upstairs", "Floorplan"]


# --- the report -------------------------------------------------------------


def test_the_build_report_counts_every_allocation(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "built 2" in report
    assert "updated 1" in report
    assert "deleted 1" in report
    assert "unchanged 3" in report
    assert "kept 1" in report


def test_the_build_report_names_the_ids_in_each_list(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "wall-03" in report and "gap-01" in report      # built
    assert "rebuilt: wall-01" in report                     # updated
    assert "deleted: old-wall-09" in report
    assert "NOT TOUCHED (3)" in report


def test_a_kept_object_is_reported_with_the_reason_it_was_kept(blender) -> None:
    """Promotion is one-way: an edited placeholder is never clobbered."""
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "KEPT, not rebuilt — FP:wd-01" in report
    assert "edited by hand" in report
    assert "component SLOT" in report


def test_the_build_report_carries_the_bounds(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "bounds: 4100 x 3100 x 2450 mm" in report


def test_the_build_report_lists_the_door_mechanisms_as_data_only(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "door mechanisms (1)" in report
    assert "revolute" in report and "90 deg" in report
    assert "DATA ONLY" in report


def test_the_build_report_carries_the_greybox_honesty_note(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "not a construction drawing" in report


def test_an_add_on_with_no_honesty_field_still_gets_the_note(blender) -> None:
    """The sentence is mirrored here so an older add-on cannot drop it."""
    result = dict(BUILD_RESULT)
    result.pop("honesty")
    fake = blender({"build_floorplan": result})
    report = server.floorplan_build(plan=plan())
    assert util.FLOORPLAN_HONESTY in report


def test_the_build_report_surfaces_warnings_and_notes(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "WARNINGS (1)" in report
    assert "note: Walls are built as solid pieces." in report


def test_the_build_summary_names_the_mode_and_the_counts(blender) -> None:
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan())
    assert "update mode" in report
    assert "1 room, 3 walls, 2 openings, 2 fixtures" in report


def test_build_is_documented_as_needing_a_blender_session() -> None:
    """Deliberately NOT a live "is the port closed?" test.

    `config.BLENDER_PORT` is 9876 unless a fixture moves it, and the artist's
    own Blender may well be listening there — a backend-down test with no fake
    would build a greybox into their open scene. The backend-down PATH is
    covered once, generically, in `test_server_tools.py`; what is worth pinning
    here is that the tool says it needs a session.
    """
    assert "Needs Blender running" in (server.floorplan_build.__doc__ or "")


# ===========================================================================
# Flow legality and the design sheet
# ===========================================================================


def test_build_floorplan_is_a_legal_flow_step() -> None:
    assert "build_floorplan" in util.KNOWN_BLENDER_OPS


def test_a_flow_may_end_by_rebuilding_the_level() -> None:
    steps = util.normalize_flow_steps([
        {"kind": "blender", "op": "build_floorplan",
         "args": {"plan": PLAN, "mode": "update"}},
        {"kind": "blender", "op": "render_preview", "args": {"view": "top"}},
    ])
    assert [step["op"] for step in steps] == ["build_floorplan", "render_preview"]


def test_a_typo_on_the_command_is_refused_before_the_flow_is_written() -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_flow_steps([
            {"kind": "blender", "op": "build_floor_plan", "args": {}},
            {"kind": "blender", "op": "render_preview", "args": {}},
        ])
    assert "build_floorplan" in str(caught.value)


def test_floorplan_svg_reads_right_after_the_mechanism_diagram(
    projects_dir: Path,
) -> None:
    """The echo-back is a DIAGRAM: it belongs with the other diagrams, because
    it is the document the artist is being asked to approve."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">'
           '<rect x="10" y="10" width="200" height="150" fill="#eef"/>'
           '<text x="20" y="40">kitchen</text></svg>')
    server.save_design_doc("upstairs flat", "requirements.md", "# needs\n")
    server.save_design_doc("upstairs flat", "components.md", "# parts\n")
    server.save_design_doc("upstairs flat", "mechanism.svg", svg)
    server.save_design_doc("upstairs flat", "floorplan.svg", svg)
    server.floorplan_validate(plan=plan(), project="upstairs flat")

    found = [item["file"] for item in util.design_documents("upstairs-flat")]
    assert found == ["requirements.md", "mechanism.svg", "floorplan.svg",
                     "components.md", "floorplan.json"]


def test_the_plan_file_is_a_design_sheet_member(projects_dir: Path) -> None:
    """`floorplan.json` is listed like any other design document, so the
    Library card carries it and it survives the conversation."""
    server.floorplan_validate(plan=plan(), project="upstairs flat")
    found = util.design_documents("upstairs-flat")
    assert [item["file"] for item in found] == ["floorplan.json"]
    assert found[0]["path"].endswith("floorplan.json")


# ===========================================================================
# floorplan_extract — the drawing, measured instead of looked at
# ===========================================================================
#
# This tool exists because of one real failure: a drawn plan was read BY EYE and
# the level came out with the wrong footprint, the rooms in the wrong places and
# a diagonal wall that exists nowhere in the drawing. So the tests here are
# about the two halves of the handoff — geometry comes out of the pixels, names
# come out of the model — and about the one question the report is allowed to
# ask.

pytest.importorskip("PIL.Image")


def drawing(tmp_path: Path, name: str = "sketch.png") -> str:
    """A small flat-block plan: two rooms, a shared wall, a door in it.

    Built here rather than kept as a fixture file so the geometry every
    assertion below depends on is readable in the same screen as the assertion.
    """
    import numpy as np
    from PIL import Image

    sheet = np.full((300, 400, 3), (255, 255, 255), dtype=np.uint8)
    sheet[40:140, 40:180] = (200, 200, 200)    # room on the left
    sheet[40:140, 200:340] = (168, 168, 168)   # room on the right
    sheet[60:100, 180:200] = (219, 68, 55)     # a red door in the wall between
    sheet[200:216, 340:360] = (66, 133, 244)   # a legend swatch, touching nothing
    path = tmp_path / name
    Image.fromarray(sheet, mode="RGB").save(str(path))
    return str(path)


def test_extract_reads_the_rooms_out_of_the_picture(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "2 region(s)" in report
    assert "Measured, not eyeballed" in report
    assert "room-r1" in report and "room-r2" in report


def test_extract_hands_back_a_crop_to_name_each_room(tmp_path: Path) -> None:
    """Geometry comes from the pixels; the NAMES come from looking at these."""
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "NAME THESE" in report
    assert "crop [" in report


def test_extract_says_the_law_out_loud(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "GEOMETRY CAME FROM THE PIXELS, NOT FROM LOOKING" in report
    assert "Do not adjust a coordinate here by eye" in report


def test_extract_promises_no_wall_can_be_diagonal(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "axis-aligned by construction" in report


def test_extract_asks_the_one_calibration_question_when_unscaled(
    tmp_path: Path,
) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path))
    assert "ASK THIS, and nothing else" in report
    assert "ONE real dimension" in report
    assert "mm_per_px = their millimetres" in report
    assert "do not scale the numbers by hand" in report


def test_extract_asks_nothing_once_it_has_a_scale(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "ASK THIS" not in report
    assert "scale: 10.0 mm per pixel" in report


def test_extract_names_the_colour_assumptions_it_made(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "ASSUMED:" in report
    assert "#db4437" in report


def test_a_given_legend_is_quoted_back_rather_than_assumed(tmp_path: Path) -> None:
    report = server.floorplan_extract(
        image_path=drawing(tmp_path), mm_per_px=10.0,
        legend={"#db4437": "room_door"},
    )
    assert "colour key you gave: #db4437 = room_door" in report


def test_extract_saves_the_plan_under_the_project(
    tmp_path: Path, projects_dir: Path,
) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0,
                                      project="upstairs flat")
    saved = saved_plan(projects_dir, "upstairs-flat")
    assert "saved the plan" in report
    assert [room["id"] for room in saved["rooms"]] == ["room-r1", "room-r2"]
    assert saved["walls"]


def test_the_saved_plan_is_the_normalised_one(
    tmp_path: Path, projects_dir: Path,
) -> None:
    """Same rule as floorplan_validate's: the defaults block stays live."""
    server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0,
                             project="upstairs flat")
    saved = saved_plan(projects_dir, "upstairs-flat")
    assert "provenance" not in saved
    assert saved["defaults"] == floorplan.defaults()
    # A wall the extractor did not measure a height for has none baked into it,
    # so raising ceiling_mm next week still moves it.
    assert all("height_mm" not in wall for wall in saved["walls"])


def test_the_saved_plan_validates_and_resolves(
    tmp_path: Path, projects_dir: Path,
) -> None:
    """The whole point: what comes off the drawing is buildable without edits."""
    server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0,
                             project="upstairs flat")
    report = server.floorplan_validate(project="upstairs flat")
    assert "reads clean" in report
    resolved = floorplan.resolve(saved_plan(projects_dir, "upstairs-flat"))
    assert all(wall["height_mm"] == 2400.0 for wall in resolved["walls"])


def test_extract_can_read_without_saving(
    tmp_path: Path, projects_dir: Path,
) -> None:
    server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0,
                             project="upstairs flat", save=False)
    assert not (projects_dir / "upstairs-flat" / "design" / "floorplan.json").exists()


def test_extract_with_no_project_says_nothing_was_filed(tmp_path: Path) -> None:
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "nothing was saved" in report


def test_two_reads_of_one_drawing_are_the_same_plan(
    tmp_path: Path, projects_dir: Path,
) -> None:
    """Ids are forever, so a re-read must rebuild nothing."""
    path = drawing(tmp_path)
    server.floorplan_extract(image_path=path, mm_per_px=10.0, project="upstairs flat")
    first = saved_plan(projects_dir, "upstairs-flat")
    server.floorplan_extract(image_path=path, mm_per_px=10.0, project="upstairs flat")
    second = saved_plan(projects_dir, "upstairs-flat")
    assert first == second
    report = server.floorplan_diff(plan=second, against=first)
    assert "rebuilds (0)" in report
    assert "adds (0)" in report


def test_a_missing_drawing_is_refused_by_path(tmp_path: Path) -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_extract(image_path=str(tmp_path / "nope.png"))
    assert "No file at" in str(caught.value)


def test_a_drawing_with_no_rooms_in_it_is_refused_verbatim(tmp_path: Path) -> None:
    """The service's refusal names the file and says what it saw — word for word."""
    import numpy as np
    from PIL import Image

    blank = tmp_path / "blank.png"
    Image.fromarray(np.full((120, 120, 3), 255, dtype=np.uint8), "RGB").save(str(blank))

    from service.floorplan_extract import extract_floorplan  # noqa: PLC0415
    from service.floorplan import FloorPlanError  # noqa: PLC0415

    try:
        extract_floorplan(str(blank))
    except FloorPlanError as exc:
        expected = str(exc)
    else:  # pragma: no cover
        pytest.fail("a blank sheet should be refused")

    with pytest.raises(ForgeError) as caught:
        server.floorplan_extract(image_path=str(blank))
    assert str(caught.value) == expected
    assert "do NOT type coordinates off the picture" in str(caught.value)


def test_extract_is_not_a_blender_call(tmp_path: Path, dead_backends) -> None:
    """Reading a picture touches no scene, so a closed Blender is irrelevant."""
    report = server.floorplan_extract(image_path=drawing(tmp_path), mm_per_px=10.0)
    assert "2 region(s)" in report


# ===========================================================================
# floorplan_reconcile — the artist's hand edits, read back
# ===========================================================================
#
# The incident this group exists for: `build_floorplan` re-created
# FP:wall-living-kitchen twice after the owner deleted it on purpose (an
# open-plan choice). *"the plan should auto update based off my changes, i
# deleted the wall because there isnt a wall there."*  So the two claims worth
# pinning here are the ordering — a build MEASURES before it edits — and the
# resurrection itself: the plan that crosses the wire must not contain a wall
# whose object the artist deleted.

#: `reconcile_floorplan`'s result, shaped as docs/architecture.md documents it:
#: `moved`/`resized` carry measurements, `deleted_in_scene` carries records, and
#: `unabsorbable` carries the add-on's own sentence for each one.
RECONCILE_RESULT = {
    "collection": "Floorplan",
    "objects": 6,
    "plan_entries": 7,
    "clean": ["room-kitchen", "wall-02", "door-01"],
    "moved": [
        {"id": "wall-01", "kind": "wall", "object": "FP:wall-01",
         "measured": {"from_mm": [0.0, 500.0], "to_mm": [4000.0, 500.0],
                      "thickness_mm": 100.0, "height_mm": 2400.0,
                      "base_z_mm": 0.0},
         "was": {"from_mm": [0.0, 0.0], "to_mm": [4000.0, 0.0],
                 "thickness_mm": 100.0, "height_mm": 2400.0, "base_z_mm": 0.0},
         "changed": ["from_mm", "to_mm"], "moved_mm": 500.0,
         "mesh": "intact", "confidence": "measured"},
    ],
    "resized": [
        {"id": "wd-01", "kind": "label", "object": "FP:wd-01",
         "measured": {"centre_mm": [600.0, 400.0], "size_mm": [1400.0, 700.0],
                      "height_mm": 965.0, "rotation_deg": 0.0, "base_z_mm": 0.0},
         "was": {"centre_mm": [600.0, 400.0], "size_mm": [700.0, 700.0],
                 "height_mm": 965.0, "rotation_deg": 0.0, "base_z_mm": 0.0},
         "changed": ["size_mm"], "moved_mm": 0.0,
         "mesh": "edited-but-still-a-box", "confidence": "measured"},
    ],
    "stale": [],
    "deleted_in_scene": [
        {"id": "wall-03", "kind": "wall", "object": "FP:wall-03",
         "was": {"from_mm": [4000.0, 3000.0], "to_mm": [0.0, 3000.0],
                 "thickness_mm": 100.0, "height_mm": 2400.0, "base_z_mm": 0.0}},
    ],
    "candidates": [
        {"id": "sofa-99", "object": "FP:sofa-99",
         "bbox_mm": {"min": [0.0, 0.0, 0.0], "max": [1800.0, 900.0, 850.0],
                     "size": [1800.0, 900.0, 850.0]},
         "suggested": {"footprint_mm": [900.0, 450.0, 1800.0, 900.0],
                       "height_mm": 850.0, "rotation_deg": 0.0},
         "why": "it is named FP: but carries no Forge fingerprint"},
    ],
    "unabsorbable": [
        {"id": "sofa-01", "kind": "label", "object": "FP:sofa-01",
         "why": "its mesh is no longer a plain box (512 vertices now, 8 when "
                "Forge built it), so it has been SCULPTED rather than resized"},
    ],
    "tolerance_mm": {"move": 0.5, "size": 0.5, "angle_deg": 0.05},
    "floor": True,
    "plan_version": 1,
    "units": "mm",
    "honesty": "These are measurements of the scene, not decisions about the plan.",
    "notes": ["1 plan entry has no object in 'Floorplan'."],
    "warnings": [],
    "seconds": 0.012,
}


def quiet_reconcile() -> dict[str, Any]:
    """The same report with nothing to absorb in it."""
    body = copy.deepcopy(RECONCILE_RESULT)
    body["moved"] = []
    body["resized"] = []
    body["deleted_in_scene"] = []
    body["candidates"] = []
    body["unabsorbable"] = []
    body["clean"] = ["room-kitchen", "wall-01", "wall-02", "wall-03",
                     "door-01", "gap-01", "wd-01", "sofa-01"]
    return body


def filed(projects_dir: Path) -> None:
    """The plan on disk, the way the flow always reaches reconcile."""
    server.floorplan_validate(plan=plan(), project="upstairs flat")


# --- what goes on the wire --------------------------------------------------


def test_reconcile_sends_the_contract_command_with_the_resolved_plan(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT})
    server.floorplan_reconcile(project="upstairs flat")

    assert fake.requests[0]["type"] == "reconcile_floorplan"
    params = sent(fake, "reconcile_floorplan")
    assert set(params) == {"plan", "collection", "floor"}
    assert params["collection"] == "Floorplan"
    assert params["floor"] is True
    wall = next(w for w in params["plan"]["walls"] if w["id"] == "wall-02")
    assert wall["thickness_mm"] == 100.0 and wall["height_mm"] == 2400.0


def test_reconcile_needs_a_saved_plan_and_says_how_to_make_one(
    blender, projects_dir: Path
) -> None:
    with pytest.raises(ForgeError) as caught:
        server.floorplan_reconcile(project="upstairs flat")
    assert "no saved floor plan" in str(caught.value)


def test_a_named_collection_and_floor_false_both_cross(
    blender, projects_dir: Path
) -> None:
    """`floor=false` matters more here than anywhere: with no slabs built, every
    room would otherwise read as deleted."""
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT})
    server.floorplan_reconcile(project="upstairs flat", collection="Level A",
                               floor=False)
    params = sent(fake, "reconcile_floorplan")
    assert params["collection"] == "Level A"
    assert params["floor"] is False


# --- the report -------------------------------------------------------------


def test_the_report_names_what_moved_and_by_how_many_mm(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "MOVED wall-01 (wall) by 500 mm" in report
    assert "from_mm (0, 0) -> (0, 500)" in report


def test_the_report_names_what_was_resized_and_how(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "RESIZED wd-01 (fixture)" in report
    assert "size_mm (700, 700) -> (1400, 700)" in report
    assert "its mesh was edited too" in report


def test_the_report_names_what_was_deleted_in_the_scene(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "DELETED IN THE SCENE — wall-03 (wall)" in report


def test_what_cannot_be_absorbed_carries_the_add_ons_own_reason(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "CANNOT ABSORB — sofa-01" in report
    assert "SCULPTED" in report


def test_a_box_forge_did_not_build_is_a_candidate_and_needs_a_name(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "NEW BOX — FP:sofa-99" in report
    assert "1800 x 900 x 850 mm" in report
    assert "id and a label" in report


def test_the_report_carries_the_law_and_the_honesty_line(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert floorplan.RECONCILE_LAW in report
    assert "never re-add something they deleted" in report
    assert "measurements of the scene" in report


def test_without_apply_nothing_is_written_and_the_report_says_so(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    before = saved_plan(projects_dir, "upstairs-flat")
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "NOTHING WAS CHANGED" in report
    assert "apply=true" in report
    assert saved_plan(projects_dir, "upstairs-flat") == before


def test_a_quiet_scene_says_there_is_nothing_to_absorb(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": quiet_reconcile()})
    report = server.floorplan_reconcile(project="upstairs flat")
    assert "nothing to absorb" in report


# --- apply ------------------------------------------------------------------


def test_apply_writes_the_measurements_into_the_saved_plan(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat", apply=True)

    on_disk = saved_plan(projects_dir, "upstairs-flat")
    wall = next(w for w in on_disk["walls"] if w["id"] == "wall-01")
    assert wall["from_mm"] == [0.0, 500.0] and wall["to_mm"] == [4000.0, 500.0]
    fixture = next(f for f in on_disk["labels"] if f["id"] == "wd-01")
    assert fixture["footprint_mm"] == [600.0, 400.0, 1400.0, 700.0]
    assert "ABSORBED INTO THE PLAN" in report
    assert str(projects_dir) in report


def test_apply_absorbs_the_deletion_rather_than_asking_about_it(
    blender, projects_dir: Path
) -> None:
    """The incident, in one test: they deleted it, so the plan drops it."""
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat", apply=True)

    on_disk = saved_plan(projects_dir, "upstairs-flat")
    assert {w["id"] for w in on_disk["walls"]} == {"wall-01", "wall-02"}
    assert "absorbed your deletion of wall-03" in report
    assert "deletions absorbed, not queried" in report


def test_confirm_deletions_leaves_the_entry_and_asks(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat", apply=True,
                                        confirm_deletions=True)
    on_disk = saved_plan(projects_dir, "upstairs-flat")
    assert {w["id"] for w in on_disk["walls"]} == {"wall-01", "wall-02", "wall-03"}
    assert "say the word" in report


def test_apply_quotes_the_diff_it_just_made(blender, projects_dir: Path) -> None:
    """The same sentence every other edit gets, for an edit made with a mouse."""
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat", apply=True)
    assert "Floor-plan diff" in report
    assert "wall-01" in report and "wall-03" in report


def test_apply_rebuilds_NOTHING(blender, projects_dir: Path) -> None:
    """The fingerprints match again by arithmetic, not by building."""
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT})
    report = server.floorplan_reconcile(project="upstairs flat", apply=True)
    assert [r["type"] for r in fake.requests] == ["reconcile_floorplan"]
    assert "NOTHING WAS REBUILT" in report


def test_apply_never_adds_the_candidate_box(blender, projects_dir: Path) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    server.floorplan_reconcile(project="upstairs flat", apply=True)
    on_disk = saved_plan(projects_dir, "upstairs-flat")
    assert {f["id"] for f in on_disk["labels"]} == {"wd-01", "sofa-01"}


def test_apply_saves_a_plan_that_reads_back_through_the_same_tools(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT})
    server.floorplan_reconcile(project="upstairs flat", apply=True)
    report = server.floorplan_validate(project="upstairs flat")
    assert "1 room, 2 walls, 1 opening, 2 fixtures" in report


def test_a_quiet_scene_writes_nothing_even_with_apply(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    before = saved_plan(projects_dir, "upstairs-flat")
    blender({"reconcile_floorplan": quiet_reconcile()})
    server.floorplan_reconcile(project="upstairs flat", apply=True)
    assert saved_plan(projects_dir, "upstairs-flat") == before


# ===========================================================================
# floorplan_build's sync — absorb the scene FIRST, then build
# ===========================================================================


def test_a_build_measures_before_it_builds(blender, projects_dir: Path) -> None:
    """The ordering IS the guarantee. Edit-then-build cannot know that the wall
    it is about to create was deleted on purpose."""
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT,
                    "build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(project="upstairs flat")
    assert [r["type"] for r in fake.requests] == ["reconcile_floorplan",
                                                  "build_floorplan"]


def test_a_build_does_not_resurrect_a_wall_they_deleted(
    blender, projects_dir: Path
) -> None:
    """FP:wall-living-kitchen came back twice. This is that, prevented."""
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT,
                    "build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(project="upstairs flat")

    wire = sent(fake, "build_floorplan")["plan"]
    assert {w["id"] for w in wire["walls"]} == {"wall-01", "wall-02"}
    assert {w["id"] for w in saved_plan(projects_dir, "upstairs-flat")["walls"]} \
        == {"wall-01", "wall-02"}


def test_a_build_does_not_snap_back_something_they_moved(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT,
                    "build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(project="upstairs flat")
    wire = sent(fake, "build_floorplan")["plan"]
    wall = next(w for w in wire["walls"] if w["id"] == "wall-01")
    assert wall["from_mm"] == [0.0, 500.0]


def test_the_build_report_LEADS_with_what_it_absorbed(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    blender({"reconcile_floorplan": RECONCILE_RESULT,
             "build_floorplan": BUILD_RESULT}, connections=2)
    report = server.floorplan_build(project="upstairs flat")
    assert report.startswith("Absorbed your scene edits into the plan first")
    assert "absorbed your deletion of wall-03" in report
    assert "Built the greybox level" in report


def test_the_callers_own_plan_edit_wins_over_the_scene(
    blender, projects_dir: Path
) -> None:
    """Absorb first, edit second: an id in both places belongs to the edit."""
    filed(projects_dir)
    edited = plan()
    for wall in edited["walls"]:
        if wall["id"] == "wall-01":
            wall["from_mm"] = [0, 900]
            wall["to_mm"] = [4000, 900]
    fake = blender({"reconcile_floorplan": RECONCILE_RESULT,
                    "build_floorplan": BUILD_RESULT}, connections=2)
    server.floorplan_build(plan=edited, project="upstairs flat")

    wire = sent(fake, "build_floorplan")["plan"]
    wall = next(w for w in wire["walls"] if w["id"] == "wall-01")
    assert wall["from_mm"] == [0.0, 900.0]          # the plan edit, not the scene
    assert {w["id"] for w in wire["walls"]} == {"wall-01", "wall-02"}  # still absorbed


def test_sync_false_builds_the_plan_as_it_stands(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(project="upstairs flat", sync=False)
    assert [r["type"] for r in fake.requests] == ["build_floorplan"]
    wire = sent(fake, "build_floorplan")["plan"]
    assert {w["id"] for w in wire["walls"]} == {"wall-01", "wall-02", "wall-03"}


def test_a_plan_with_no_project_has_nothing_to_sync_against(
    blender, projects_dir: Path
) -> None:
    """No project means no saved plan to absorb into, so there is one round trip."""
    fake = blender({"build_floorplan": BUILD_RESULT})
    server.floorplan_build(plan=plan())
    assert [r["type"] for r in fake.requests] == ["build_floorplan"]


def test_an_add_on_too_old_to_reconcile_still_builds_and_says_so(
    blender, projects_dir: Path
) -> None:
    """A measurement that cannot happen is a line in the report, never a refusal:
    the build is what the artist asked for."""
    filed(projects_dir)
    fake = blender({"build_floorplan": BUILD_RESULT}, connections=2)
    report = server.floorplan_build(project="upstairs flat")
    assert [r["type"] for r in fake.requests] == ["reconcile_floorplan",
                                                  "build_floorplan"]
    assert "Could not read your scene edits back first" in report
    assert "Built the greybox level" in report


def test_a_quiet_scene_adds_no_lead_and_writes_nothing(
    blender, projects_dir: Path
) -> None:
    filed(projects_dir)
    before = saved_plan(projects_dir, "upstairs-flat")
    blender({"reconcile_floorplan": quiet_reconcile(),
             "build_floorplan": BUILD_RESULT}, connections=2)
    report = server.floorplan_build(project="upstairs flat")
    assert report.startswith("Built the greybox level")
    assert saved_plan(projects_dir, "upstairs-flat") == before


def test_a_project_with_no_saved_plan_yet_still_builds(
    blender, projects_dir: Path
) -> None:
    """The first build of a project nobody filed a plan for: there is nothing to
    absorb INTO, so the sync says so and gets out of the way."""
    fake = blender({"build_floorplan": BUILD_RESULT})
    report = server.floorplan_build(plan=plan(), project="upstairs flat")
    assert [r["type"] for r in fake.requests] == ["build_floorplan"]
    assert "Could not read your scene edits back first" in report


def test_sync_is_documented_on_the_tool_and_defaults_to_on() -> None:
    import inspect

    signature = inspect.signature(server.floorplan_build)
    assert signature.parameters["sync"].default is True
    assert "resurrect" in (server.floorplan_build.__doc__ or "")
