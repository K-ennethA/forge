"""Phase 11: the drawn door, and the component tree becoming one shell.

Same rules as the rest of the suite — Blender is the NDJSON fake on an ephemeral
port, never 9876 — so these run with Blender closed and pin the *contract*.

What is being proved:

1. both samplers send the command and the parameters ``docs/architecture.md``
   says they do, and an omitted option is **absent from the wire** rather than
   sent as a null the add-on has to interpret;
2. their reports hand back the control points in the shape a PARAMS script
   wants, and say out loud that nothing was built — the drawn curve seeds a
   still-parametric part, which is the whole reason this door exists;
3. ``merge_for_print`` resolves its pieces the documented way (names, else a
   collection, else the selection) and its report carries the two things the
   artist must hear: the voxel trade, and that the originals are hidden rather
   than deleted;
4. the report always names ``check_model`` as the next call, because a merged
   shell is a new mesh nobody has print-checked;
5. all three are legal flow steps, and ``flows/merge-and-check.json`` passes the
   very validation ``flow_save`` applies — "scrap the collar, then merge what is
   left and check it" is the repeatable sequence flows exist for, and a mirrored
   op list that had drifted would refuse to save it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forge_mcp import server, util

from .test_new_part import projects_dir  # noqa: F401 - autouse: redirects projects/
from .test_print_readiness import service  # noqa: F401 - pytest fixture
from .test_rigforge import blender, sent  # noqa: F401 - pytest fixtures

# --- canned add-on results --------------------------------------------------

PROFILE_RESULT: dict[str, Any] = {
    "object": "BowlProfile",
    "points_mm": [[38.0, 0.0], [52.0, 18.0], [61.5, 44.0], [58.0, 72.0],
                  [46.0, 95.0], [44.0, 104.0]],
    "height_mm": 104.0,
    "max_radius_mm": 61.5,
    "min_radius_mm": 38.0,
    "base_radius_mm": 38.0,
    "plane": "XZ",
    "plane_normal": "Y",
    "point_count": 6,
    "sample_count": 193,
    "spline_type": "BEZIER",
    "closed_curve": False,
    "close_bottom": True,
    "z_offset_mm": -12.0,
    "flatness_mm": 0.0,
    "helper": "forge_lib.soft_body",
    "notes": ["2 sample(s) doubled back downward and were dropped."],
}

OUTLINE_RESULT: dict[str, Any] = {
    "object": "EarOutline",
    "points_mm": [[0.0, 0.0], [11.0, 6.0], [15.0, 24.0], [12.0, 46.0],
                  [4.0, 62.0], [0.0, 70.0], [-6.0, 58.0], [-13.0, 34.0]],
    "width_mm": 28.0,
    "height_mm": 70.0,
    "plane": "XZ",
    "plane_normal": "Y",
    "point_count": 8,
    "sample_count": 128,
    "spline_type": "BEZIER",
    "closed_curve": True,
    "cyclic_flag": True,
    "recentered": True,
    "offset_mm": [-1.0, -2.0],
    "self_intersections": 0,
    "flatness_mm": 0.0,
    "helper": "forge_lib.silhouette_part",
    "notes": [],
}

MERGE_RESULT: dict[str, Any] = {
    "object": "gecko-bowl-merged",
    "vertex_count": 210400,
    "face_count": 420100,
    "edge_count": 630000,
    "voxel_size_mm": 0.2,
    "voxel_size_requested_mm": None,
    "voxel_source": "nozzle",
    "nozzle_mm": 0.4,
    "printer_source": "printer.json",
    "predicted_face_count": 402000,
    "surface_area_mm2": 16080.0,
    "watertight_input_count": 2,
    "watertight": True,
    "loose_vertices": 0,
    "sources": [
        {"object": "gecko-bowl", "vertex_count": 4200, "face_count": 8400,
         "watertight": True},
        {"object": "gecko-bowl-collar", "vertex_count": 2100, "face_count": 4200,
         "watertight": True},
        {"object": "gecko-bowl-ear-l", "vertex_count": 900, "face_count": 1800,
         "watertight": False},
    ],
    "source_count": 3,
    "resolved_by": "collection",
    "collection": "gecko-bowl",
    "kept_originals": True,
    "hidden": ["gecko-bowl", "gecko-bowl-collar", "gecko-bowl-ear-l"],
    "deleted": [],
    "dimensions_mm": [152.4, 152.4, 104.0],
    "remesh_method": "operator",
    "next": "check_model",
    "notes": ["The 3 original piece(s) are hidden, not deleted."],
}


# --- profile_from_curve -----------------------------------------------------


def test_profile_sends_the_contract_command_and_the_curve(blender) -> None:
    fake = blender({"profile_from_curve": PROFILE_RESULT})
    server.profile_from_curve("BowlProfile")

    assert fake.requests[0]["type"] == "profile_from_curve"
    assert sent(fake, "profile_from_curve") == {
        "curve_object": "BowlProfile", "close_bottom": True}


def test_profile_only_sends_a_point_count_when_asked(blender) -> None:
    """An omitted count is the sampler's default, not a null on the wire."""
    fake = blender({"profile_from_curve": PROFILE_RESULT})
    server.profile_from_curve("BowlProfile", points=9, close_bottom=False)

    assert sent(fake, "profile_from_curve") == {
        "curve_object": "BowlProfile", "points": 9, "close_bottom": False}


def test_profile_report_hands_back_pasteable_points(blender) -> None:
    fake = blender({"profile_from_curve": PROFILE_RESULT})
    report = server.profile_from_curve("BowlProfile")

    assert "profile_points = [(38" in report
    assert "(61.5, 44" in report      # the widest point survived the reduction
    assert "104" in report            # the height
    assert "XZ" in report             # which plane it was read on
    assert fake.requests


def test_profile_report_says_nothing_was_built(blender) -> None:
    """The drawn curve SEEDS a parametric part; it does not become a mesh."""
    blender({"profile_from_curve": PROFILE_RESULT})
    report = server.profile_from_curve("BowlProfile")

    assert "Nothing was built" in report
    assert "partforge_new_part" in report
    assert "slider" in report
    assert "soft_body" in report


def test_profile_report_carries_the_add_ons_notes(blender) -> None:
    blender({"profile_from_curve": PROFILE_RESULT})
    report = server.profile_from_curve("BowlProfile")

    assert "doubled back downward" in report


# --- outline_from_curve -----------------------------------------------------


def test_outline_sends_the_contract_command(blender) -> None:
    fake = blender({"outline_from_curve": OUTLINE_RESULT})
    server.outline_from_curve("EarOutline", points=8)

    assert sent(fake, "outline_from_curve") == {
        "curve_object": "EarOutline", "points": 8, "recenter": True}


def test_outline_report_is_silhouette_parts_own_shape(blender) -> None:
    blender({"outline_from_curve": OUTLINE_RESULT})
    report = server.outline_from_curve("EarOutline")

    assert "points = [(0" in report
    assert "silhouette_part" in report
    assert "28" in report and "70" in report      # width x height
    assert "peg" in report                        # how it plugs into the core


def test_outline_report_warns_when_the_loop_crosses_itself() -> None:
    """silhouette_part refuses a crossing outline — say so before it does."""
    result = dict(OUTLINE_RESULT, self_intersections=2)
    report = util.fmt_outline_report(result)

    assert "crosses itself" in report
    assert "fewer points" in report


# --- merge_for_print --------------------------------------------------------


def test_merge_with_nothing_named_merges_the_selection(blender) -> None:
    fake = blender({"merge_for_print": MERGE_RESULT})
    server.merge_for_print()

    assert sent(fake, "merge_for_print") == {"keep_originals": True}


def test_merge_passes_the_names_and_the_collection_through(blender) -> None:
    fake = blender({"merge_for_print": MERGE_RESULT})
    server.merge_for_print(
        objects=["gecko-bowl", " gecko-bowl-collar "],
        collection="gecko-bowl",
        voxel_size_mm=0.3,
        name="gecko-bowl-merged",
        keep_originals=False,
    )

    assert sent(fake, "merge_for_print") == {
        "keep_originals": False,
        "objects": ["gecko-bowl", "gecko-bowl-collar"],
        "collection": "gecko-bowl",
        "voxel_size_mm": 0.3,
        "name": "gecko-bowl-merged",
    }


def test_merge_report_states_the_voxel_trade(blender) -> None:
    blender({"merge_for_print": MERGE_RESULT})
    report = server.merge_for_print()

    assert "0.2 mm voxel" in report
    assert "nozzle" in report
    assert "hidden, not deleted" in report


def test_merge_report_names_check_model_as_the_next_call(blender) -> None:
    blender({"merge_for_print": MERGE_RESULT})
    report = server.merge_for_print()

    assert "check_model" in report
    assert "mesh_diagnose" in report


def test_a_coarsened_merge_says_what_it_cost() -> None:
    result = dict(MERGE_RESULT, voxel_source="coarsened", voxel_size_mm=0.35)
    report = util.fmt_merge_report(result)

    assert "coarsened" in report
    assert "0.35 mm is rounded off" in report
    assert "not a defect" in report


def test_an_unsealed_merge_is_never_reported_as_finished() -> None:
    result = dict(MERGE_RESULT, watertight=False)
    report = util.fmt_merge_report(result)

    assert "NOT sealed" in report
    assert "mesh_diagnose" in report


def test_deleted_originals_are_said_out_loud() -> None:
    result = dict(MERGE_RESULT, kept_originals=False, hidden=[],
                  deleted=["gecko-bowl", "gecko-bowl-collar"])
    report = util.fmt_merge_report(result)

    assert "DELETED" in report


# --- the component tree, generated ------------------------------------------


def test_a_component_lands_in_the_projects_collection(blender, service,
                                                      tmp_path) -> None:
    """The convention has to be reachable, not just documented.

    `partforge_generate` takes the collection so a multi-part design can land
    where merge_for_print will look for it: one collection named for the
    project, the core named for the project, each proposal `<project>-<part>`.
    """
    script = tmp_path / "part.py"
    script.write_text(
        "PARAMS = {'d': {'value': 30.0, 'unit': 'mm'}}\n\n"
        "def build(p):\n    return None\n",
        encoding="utf-8",
    )
    fake_service = service({"/generate": {
        "params": {"d": {"value": 30.0, "unit": "mm"}},
        "mesh": {"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
                 "faces": [[0, 1, 2]]},
        "stats": {"vertex_count": 3, "face_count": 1,
                  "bounding_box_mm": [10, 10, 0], "watertight": False},
    }})
    fake_blender = blender({"load_mesh": {
        "object": "gecko-bowl-collar", "vertex_count": 3, "face_count": 1}})

    report = server.partforge_generate(
        str(script), name="gecko-bowl-collar", collection="gecko-bowl")

    assert fake_service.body_for("/generate")
    assert sent(fake_blender, "load_mesh")["collection"] == "gecko-bowl"
    assert sent(fake_blender, "load_mesh")["name"] == "gecko-bowl-collar"
    assert "gecko-bowl" in report


def test_no_collection_means_no_collection_key(blender, service, tmp_path) -> None:
    script = tmp_path / "part.py"
    script.write_text(
        "PARAMS = {'d': {'value': 30.0, 'unit': 'mm'}}\n\n"
        "def build(p):\n    return None\n",
        encoding="utf-8",
    )
    service({"/generate": {
        "params": {},
        "mesh": {"vertices": [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
                 "faces": [[0, 1, 2]]},
        "stats": {"vertex_count": 3, "face_count": 1},
    }})
    fake_blender = blender({"load_mesh": {"object": "part"}})

    server.partforge_generate(str(script))

    assert "collection" not in sent(fake_blender, "load_mesh")


# --- spec.json records the component tree -----------------------------------

BASE_SCRIPT = (
    "PARAMS = {'bowl_diameter': {'value': 152.4, 'unit': 'mm'}}\n\n"
    "def build(p):\n    return None\n"
)
BASE_PARSED = {"bowl_diameter": {"value": 152.4, "unit": "mm"}}


def test_a_component_name_is_never_doubled() -> None:
    """The MCP mirror has to spell a component the add-on's way, exactly."""
    assert util.component_name("gecko-bowl", "collar") == "gecko-bowl-collar"
    assert util.component_name("gecko-bowl", "-collar-") == "gecko-bowl-collar"
    # already a full name — recording it twice must not make a second prefix
    assert util.component_name("gecko-bowl", "gecko-bowl-collar") == (
        "gecko-bowl-collar")
    assert util.component_name("gecko-bowl") == "gecko-bowl"
    assert len(util.component_name("g" * 80, "collar").encode("utf-8")) == 63


def test_new_part_records_the_component_tree(service, projects_dir) -> None:
    service({"/parse_params": {"params": BASE_PARSED}})
    server.partforge_new_part("gecko bowl", BASE_SCRIPT,
                              components=["collar", "ear-l", "ear-r"])

    spec = json.loads((projects_dir / "gecko-bowl" / "spec.json")
                      .read_text(encoding="utf-8"))
    assert spec["components"] == {
        "collection": "gecko-bowl",
        "core": "gecko-bowl",
        "proposals": ["gecko-bowl-collar", "gecko-bowl-ear-l",
                      "gecko-bowl-ear-r"],
    }


def test_a_one_piece_part_records_no_component_tree(service, projects_dir) -> None:
    service({"/parse_params": {"params": BASE_PARSED}})
    server.partforge_new_part("plain bowl", BASE_SCRIPT)

    spec = json.loads((projects_dir / "plain-bowl" / "spec.json")
                      .read_text(encoding="utf-8"))
    assert "components" not in spec


def test_revising_a_design_adds_the_new_piece_and_keeps_the_edits(
        service, projects_dir) -> None:
    """The spec is the artist's file: only the components key is rewritten."""
    service({"/parse_params": {"params": BASE_PARSED}})
    server.partforge_new_part("gecko bowl", BASE_SCRIPT, components=["collar"])

    spec_path = projects_dir / "gecko-bowl" / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["description"] = "the artist wrote this line themselves"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    server.partforge_new_part("gecko bowl", BASE_SCRIPT, overwrite=True,
                              components=["collar", "tail"])

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["description"] == "the artist wrote this line themselves"
    assert spec["components"]["proposals"] == ["gecko-bowl-collar",
                                               "gecko-bowl-tail"]


def test_an_unreadable_spec_is_left_alone(service, projects_dir) -> None:
    service({"/parse_params": {"params": BASE_PARSED}})
    folder = projects_dir / "gecko-bowl"
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text("{ this is not json", encoding="utf-8")

    report = server.partforge_new_part("gecko bowl", BASE_SCRIPT,
                                       components=["collar"])

    assert "left alone" in report
    assert (folder / "spec.json").read_text(encoding="utf-8") == (
        "{ this is not json")
    assert (folder / "part.py").is_file()      # the script still landed


# --- the merge is a flow step, and the shipped flow proves it ---------------


def test_the_phase_11_commands_are_legal_flow_steps() -> None:
    """A mirrored op list that drifted would refuse to save merge-and-check."""
    for name in ("profile_from_curve", "outline_from_curve", "merge_for_print"):
        assert name in util.KNOWN_BLENDER_OPS, name


def test_the_merge_and_check_flow_is_valid() -> None:
    """flows/merge-and-check.json passes the validation flow_save applies."""
    repo_flows = Path(__file__).resolve().parents[2] / "flows"
    path = repo_flows / "merge-and-check.json"
    assert path.is_file(), f"the merge flow is missing from {repo_flows}"
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert doc["name"] == "merge-and-check"
    assert len(doc["description"]) > 30
    steps = util.normalize_flow_steps(doc["steps"])
    assert [step["op"] for step in steps] == ["merge_for_print", "check_model"]
    assert [step["kind"] for step in steps] == ["blender", "blender"]
    # the check is aimed at whatever the merge just made, by reference
    assert steps[1]["args"]["object"] == "{{steps.0.result.object}}"

    params = util.normalize_flow_params(doc["params"])
    assert set(params) == {"collection", "voxel_size_mm", "keep_originals"}
    # blank collection = "merge the selection"; 0 voxel = "use the nozzle"
    assert params["collection"]["value"] == ""
    assert params["voxel_size_mm"]["value"] == 0
    assert params["keep_originals"]["value"] is True


def test_the_sculpt_ready_flow_is_valid() -> None:
    """The other end of a base shape: the handoff to the artist's stylus."""
    path = Path(__file__).resolve().parents[2] / "flows" / "sculpt-ready.json"
    assert path.is_file(), f"the sculpt handoff flow is missing from {path.parent}"
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert doc["name"] == "sculpt-ready"
    steps = util.normalize_flow_steps(doc["steps"])
    assert [step["op"] for step in steps] == ["remesh", "set_mode", "sculpt_brush"]
    assert steps[0]["args"]["mode"] == "voxel"
    assert steps[1]["args"]["mode"] == "sculpt"

    params = util.normalize_flow_params(doc["params"])
    # blank object = "the one they just generated"; the grid is in Blender metres
    assert params["object"]["value"] == ""
    assert params["voxel_size"]["value"] == 0.001
