"""Phase 8: the workspace copilot and the buddy tools.

Same rules as the rest of the suite — Blender is the NDJSON fake on an ephemeral
port, never 9876, so these run with Blender closed and pin the *contract*.

What is actually being proved here:

1. every workspace tool sends the command and the parameters
   ``docs/architecture.md`` says it does;
2. every report carries **both halves of the teaching contract** — what changed
   AND where the switch lives — plus the instruction not to write out the steps,
   because a nine-step tutorial for a one-line change is the bug this whole
   phase exists to fix;
3. the motivating case itself: "enable grid view for x, y, z axis" is one call
   with the axes on the wire;
4. ``mesh_diagnose`` renders a critique with a PLACE against every defect;
5. ``check_my_work`` composes the same three calls the panel button does, in
   order, and tells the model to Read the pictures before it says anything —
   and that one missing piece (no viewport in a headless Blender) degrades to a
   note rather than losing the whole check-in.
"""

from __future__ import annotations

from typing import Any

import pytest

from forge_mcp import server, util
from forge_mcp.errors import ForgeError

from .test_rigforge import blender, sent  # noqa: F401 - pytest fixtures

# --- canned add-on results --------------------------------------------------

OVERLAY_RESULT = {
    "applied": {"grid": True, "axes": ["X", "Y", "Z"]},
    "requested": {"grid": True, "axes": ["X", "Y", "Z"]},
    "viewports": 2,
    "changed": "Grid on, and the X, Y and Z axis lines are showing, in every "
               "3D viewport.",
    "where": "the Overlays dropdown — the two overlapping circles at the top "
             "right of the viewport",
}

VIEW_RESULT = {
    "view": "front", "ortho": True, "viewports": 1, "regions": 1,
    "changed": "Turned the view to front, flat (orthographic).",
    "where": "the View menu at the top of the viewport, or the numpad",
}

MODE_RESULT = {
    "mode": "sculpt", "blender_mode": "SCULPT", "object": "goblin",
    "object_type": "MESH", "previous": "object",
    "changed": "'goblin' is in Sculpt Mode now.",
    "where": "the mode dropdown at the top LEFT of the viewport",
}

BRUSH_RESULT = {
    "object": "goblin", "brush": "Clay Strips", "brush_type": "CLAY_STRIPS",
    "size": 60, "strength": 0.5,
    "symmetry": {"x": True, "y": False, "z": False},
    "dyntopo": False, "mode": "sculpt",
    "available": ["Clay", "Clay Strips", "Crease Sharp", "Draw", "Grab",
                  "Inflate/Deflate", "Mask", "Pinch/Magnify", "Scrape/Fill",
                  "Smooth", "Snake Hook", "Thumb", "Twist"],
    "changed": "Sculpt brush is Clay Strips, size 60 px, X symmetry on.",
    "where": "the toolbar down the left of the viewport (press T)",
}

DIAGNOSE_RESULT = {
    "object": "goblin",
    "vertex_count": 120400,
    "face_count": 240000,
    "edge_count": 360000,
    "evaluated": True,
    "self_intersections": {
        "count": 3, "faces": 6, "scanned": True,
        "examples": [{"location_mm": [-42.5, 18.2, 96.4], "faces": [1200, 4400]}],
    },
    "topology": {
        "non_manifold_edges": 12, "boundary_edges": 12, "wire_edges": 0,
        "multi_face_edges": 0, "non_manifold_vertices": 0, "loose_vertices": 0,
        "watertight": False,
        "edge_examples": [{"location_mm": [10.0, 0.0, 3.0], "faces_on_edge": 1,
                           "kind": "open hole"}],
        "vertex_examples": [],
    },
    "zero_area_faces": {"count": 0, "examples": []},
    "ngons": {"count": 4, "max_sides": 9,
              "examples": [{"location_mm": [1.0, 2.0, 3.0], "sides": 9,
                            "face": 7}]},
    "loose": {"vertices": 0, "wire_edges": 0, "shells": 1},
    "density": {
        "faces": {"count": 240000, "mean_mm2": 0.4, "median_mm2": 0.35,
                  "min_mm2": 0.0001, "max_mm2": 22.0, "p05_mm2": 0.1,
                  "p95_mm2": 0.9},
        "ratio": 4.0,
        "dense": [],
        "starved": [{"faces": 210, "mean_area_mm2": 6.2,
                     "location_mm": [0.0, -30.0, 140.0], "times_median": 17.7}],
    },
    "scale": {"object_scale": [1.0, 1.0, 1.0], "non_uniform": False,
              "unapplied": False, "mirrored": False,
              "dimensions_mm": [120.0, 90.0, 180.0], "problems": []},
    "notes": [],
    "duration_ms": 640,
    "verdict": [
        "The surface passes through itself in 3 places (clipping) — first one "
        "around -42.5, 18.2, 96.4 mm.",
        "12 edges are not sealed (12 open holes, 0 loose wires, 0 where more "
        "than two faces meet), so this is not a solid.",
        "There is a starved patch around 0, -30, 140 mm — faces about 17.7x "
        "bigger than the rest, nothing to sculpt into. Remesh there.",
    ],
    "clean": False,
}

CAPTURE_RESULT = {
    "path": r"C:\Temp\forge-previews\checkin-001-viewport.png",
    "resolution": [1024, 620], "size_bytes": 240000,
    "area": {"width": 1574, "height": 954}, "shading": "solid",
    "perspective": "persp", "mode": "sculpt", "viewports": 1,
}

PREVIEW_RESULT = {
    "path": r"C:\Temp\forge-previews\checkin-002-render.png",
    "objects": ["goblin"], "resolution": 768, "view": "iso",
    "shading": "solid", "engine": "BLENDER_WORKBENCH", "size_bytes": 90000,
    "framed_all_visible": False,
    "bounds_mm": {"min": [0, 0, 0], "max": [120, 90, 180],
                  "size": [120.0, 90.0, 180.0]},
    "notes": [],
}

SCENE_RESULT = {
    "objects": [{"name": "goblin", "type": "MESH", "location": [0, 0, 0],
                 "dimensions": [0.12, 0.09, 0.18], "vertex_count": 120400,
                 "face_count": 240000, "modifiers": []}],
    "active": "goblin",
}


# --- the seven workspace commands -------------------------------------------


def test_set_view_sends_the_view_and_reports_both_halves(blender) -> None:
    fake = blender({"set_view": VIEW_RESULT})
    report = server.set_view("front")

    assert fake.requests[0]["type"] == "set_view"
    assert sent(fake, "set_view") == {"view": "front"}
    assert "Turned the view to front" in report
    assert "where the artist would do it themselves" in report
    assert "numpad" in report.lower()


def test_set_view_only_sends_ortho_when_it_was_asked_for(blender) -> None:
    """Omitted means "Blender's own habit", not "perspective"."""
    fake = blender({"set_view": VIEW_RESULT})
    server.set_view("iso", ortho=False)
    assert sent(fake, "set_view") == {"view": "iso", "ortho": False}


def test_every_workspace_report_forbids_writing_out_the_steps(blender) -> None:
    """The motivating bug, guarded at the place the model reads."""
    fake = blender({"set_view": VIEW_RESULT})
    report = server.set_view("top")
    assert "Do not write out the steps" in report
    assert "ONE line" in report
    assert fake.requests


def test_frame_object_targets_by_name(blender) -> None:
    fake = blender({"frame_object": {
        "objects": ["goblin"], "framed_all_visible": False,
        "center_mm": [0, 0, 90], "size_mm": [120, 90, 180], "radius_mm": 118.0,
        "view_distance": 0.35, "viewports": 1,
        "changed": "Zoomed the view onto goblin (120 x 90 x 180 mm).",
        "where": "View menu > Frame Selected, or Numpad .",
    }})
    report = server.frame_object("goblin", margin=1.5)
    assert sent(fake, "frame_object") == {"object": "goblin", "margin": 1.5}
    assert "Zoomed the view onto goblin" in report
    assert "Frame Selected" in report


def test_frame_object_all_frames_the_scene(blender) -> None:
    fake = blender({"frame_object": {"changed": "Zoomed onto 3 objects.",
                                     "where": "View menu > Frame All"}})
    server.frame_object(all=True)
    assert sent(fake, "frame_object") == {"all": True}


def test_local_view_toggles_when_no_state_is_given(blender) -> None:
    fake = blender({"local_view": {
        "enabled": True, "object": "goblin", "viewports": 1,
        "viewports_changed": 1,
        "changed": "Isolated goblin — everything else is hidden for now.",
        "where": "View menu > Local View, or press /",
    }})
    report = server.local_view(object="goblin")
    assert sent(fake, "local_view") == {"object": "goblin"}
    assert "Isolated goblin" in report

    fake2 = blender({"local_view": {"enabled": False, "changed": "Back to the "
                                    "whole scene.", "where": "press /"}})
    server.local_view(enable=False)
    assert sent(fake2, "local_view") == {"enable": False}


def test_set_shading_sends_the_mode(blender) -> None:
    fake = blender({"set_shading": {
        "mode": "wireframe", "viewports": 1,
        "changed": "Viewport shading is now wireframe (see-through, edges only).",
        "where": "the four little spheres at the top right of the viewport",
    }})
    report = server.set_shading("wireframe")
    assert sent(fake, "set_shading") == {"mode": "wireframe"}
    assert "wireframe" in report
    assert "top right" in report


# --- the motivating case ----------------------------------------------------


def test_the_grid_and_the_three_axes_are_one_call(blender) -> None:
    """"I want to enable grid view for x,y,z axis" — one call, not nine steps."""
    fake = blender({"set_overlays": OVERLAY_RESULT})
    report = server.set_overlays(grid=True, axes=["x", "y", "z"])

    assert fake.requests[0]["type"] == "set_overlays"
    assert sent(fake, "set_overlays") == {"grid": True, "axes": ["x", "y", "z"]}
    assert "Grid on" in report
    assert "X, Y and Z" in report
    assert "Overlays dropdown" in report
    assert "applied to all 2 open 3D viewports" in report


def test_overlays_only_sends_what_was_named(blender) -> None:
    """Anything not named is left exactly as the artist had it."""
    fake = blender({"set_overlays": OVERLAY_RESULT})
    server.set_overlays(wireframe=True)
    assert sent(fake, "set_overlays") == {"wireframe": True}


def test_overlays_accepts_the_word_all_for_the_axes(blender) -> None:
    fake = blender({"set_overlays": OVERLAY_RESULT})
    server.set_overlays(axes="all")
    assert sent(fake, "set_overlays") == {"axes": "all"}


def test_overlays_with_nothing_named_is_refused_before_the_socket(dead_backends) -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.set_overlays()
    assert "at least one overlay" in str(excinfo.value)


@pytest.mark.parametrize("axes", [["x", "w"], ["up"], [1]])
def test_overlays_refuses_an_axis_that_is_not_an_axis(dead_backends, axes: Any) -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.set_overlays(axes=axes)
    assert '"x", "y" or "z"' in str(excinfo.value)


def test_normalize_axes_takes_the_three_documented_forms() -> None:
    assert util.normalize_axes(["X", "z", "z"]) == ["x", "z"]
    assert util.normalize_axes("all") == "all"
    assert util.normalize_axes(True) is True
    assert util.normalize_axes(None) is None
    with pytest.raises(ForgeError):
        util.normalize_axes(7)


# --- mode and brush ---------------------------------------------------------


def test_set_mode_sends_the_mode_and_the_object(blender) -> None:
    fake = blender({"set_mode": MODE_RESULT})
    report = server.set_mode("sculpt", object="goblin")
    assert sent(fake, "set_mode") == {"object": "goblin", "mode": "sculpt"}
    assert "Sculpt Mode" in report
    assert "mode dropdown" in report


def test_sculpt_brush_sends_every_setting_it_was_given(blender) -> None:
    fake = blender({"sculpt_brush": BRUSH_RESULT})
    report = server.sculpt_brush(brush="Clay Strips", size=60, strength=0.5,
                                 symmetry_x=True)
    assert sent(fake, "sculpt_brush") == {
        "brush": "Clay Strips", "size": 60, "strength": 0.5, "symmetry_x": True,
    }
    assert "Clay Strips" in report
    # The other brushes are listed so the next request does not need a round trip.
    assert "other brushes" in report
    assert "Crease Sharp" in report


def test_sculpt_brush_with_nothing_to_change_is_refused_before_the_socket(
    dead_backends,
) -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.sculpt_brush()
    assert "Nothing to change" in str(excinfo.value)
    assert "Blender is not running" not in str(excinfo.value)


def test_sculpt_brush_settings_alone_need_no_brush_name(blender) -> None:
    fake = blender({"sculpt_brush": BRUSH_RESULT})
    server.sculpt_brush(size=120)
    assert sent(fake, "sculpt_brush") == {"size": 120}


# --- mesh_diagnose ----------------------------------------------------------


def test_diagnose_report_puts_a_place_against_every_defect(blender) -> None:
    fake = blender({"mesh_diagnose": DIAGNOSE_RESULT})
    report = server.mesh_diagnose("goblin")

    assert sent(fake, "mesh_diagnose") == {"object": "goblin"}
    assert "240000 faces" in report.replace(",", "")
    assert "clipping at (-42.5, 18.2, 96.4) mm" in report
    assert "open hole at (10, 0, 3) mm" in report
    assert "starved: 210 faces around (0, -30, 140) mm" in report
    assert "remesh there" in report
    assert "at most THREE" in report


def test_diagnose_says_so_briefly_when_the_mesh_is_clean(blender) -> None:
    clean = dict(DIAGNOSE_RESULT)
    clean["clean"] = True
    clean["verdict"] = ["Nothing is wrong with this mesh numerically."]
    clean["self_intersections"] = {"count": 0, "faces": 0, "scanned": True,
                                   "examples": []}
    clean["topology"] = dict(DIAGNOSE_RESULT["topology"],
                             non_manifold_edges=0, edge_examples=[])
    clean["density"] = dict(DIAGNOSE_RESULT["density"], starved=[], dense=[])
    clean["ngons"] = {"count": 0, "max_sides": 0, "examples": []}
    blender({"mesh_diagnose": clean})
    report = server.mesh_diagnose()
    assert "say so briefly" in report
    assert "at most THREE" not in report


@pytest.mark.parametrize("examples", [0, 26, 1.5, True])
def test_diagnose_refuses_an_impossible_example_count(dead_backends, examples) -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.mesh_diagnose(examples=examples)
    assert "examples must be" in str(excinfo.value)
    assert "Blender is not running" not in str(excinfo.value)


def test_diagnose_refuses_an_impossible_density_ratio(dead_backends) -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.mesh_diagnose(density_ratio=1.0)
    assert "density_ratio must be" in str(excinfo.value)


# --- check_my_work ----------------------------------------------------------


def test_check_my_work_gathers_all_three_in_order(blender) -> None:
    fake = blender({
        "capture_viewport": CAPTURE_RESULT,
        "render_preview": PREVIEW_RESULT,
        "mesh_diagnose": DIAGNOSE_RESULT,
        "get_scene_info": SCENE_RESULT,
    }, connections=4)
    report = server.check_my_work("goblin")

    assert [r["type"] for r in fake.requests] == [
        "capture_viewport", "render_preview", "mesh_diagnose", "get_scene_info",
    ]
    # The viewport capture is the artist's own view; the render is the clean one.
    assert sent(fake, "render_preview")["view"] == "iso"
    assert sent(fake, "render_preview")["objects"] == ["goblin"]
    assert sent(fake, "mesh_diagnose") == {"object": "goblin"}

    assert CAPTURE_RESULT["path"] in report
    assert PREVIEW_RESULT["path"] in report
    assert "READ BOTH FILES NOW" in report
    assert "what they are looking at right now" in report
    assert "clipping at" in report
    assert "one clause on what is working" in report
    assert "at most" in report and "three" in report


def test_check_my_work_survives_a_headless_blender_with_no_viewport(blender) -> None:
    """A check-in with no screenshot is still a check-in — and says so."""
    fake = blender({
        # No "capture_viewport" key: the fake answers it as an error, which is
        # exactly what a --background Blender does.
        "render_preview": PREVIEW_RESULT,
        "mesh_diagnose": DIAGNOSE_RESULT,
        "get_scene_info": SCENE_RESULT,
    }, connections=4)
    report = server.check_my_work()

    assert [r["type"] for r in fake.requests][0] == "capture_viewport"
    assert "could not gather: no viewport screenshot" in report
    assert PREVIEW_RESULT["path"] in report      # the render still landed
    assert "READ THAT FILE NOW" in report        # singular: one picture
    assert "clipping at" in report               # and the numbers still came


def test_check_my_work_reaches_blender_and_reports_it_down(dead_backends) -> None:
    with pytest.raises(Exception) as excinfo:
        server.check_my_work()
    assert "Blender is not running" in str(excinfo.value)


def test_check_in_paths_are_scratch_and_never_reused(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(util.config, "PREVIEWS_DIR", str(tmp_path / "shots"))
    first = util.checkin_path("viewport")
    second = util.checkin_path("viewport")
    assert first != second
    assert first.suffix == ".png"
    assert first.parent.is_dir()
    assert "viewport" in first.name


# --- the flow surface -------------------------------------------------------


def test_every_workspace_command_can_be_a_flow_step() -> None:
    """A flow can end by leaving the artist in the right mode, looking right."""
    for command in ("set_view", "frame_object", "local_view", "set_shading",
                    "set_overlays", "set_mode", "sculpt_brush",
                    "capture_viewport", "mesh_diagnose"):
        assert command in util.KNOWN_BLENDER_OPS, command


def test_the_instructions_tell_the_model_to_drive_not_describe() -> None:
    text = server.INSTRUCTIONS
    assert "yours to drive, not to describe" in text
    assert 'set_overlays(grid=True, axes=["x","y","z"])' in text
    assert "never a list of steps" in text
