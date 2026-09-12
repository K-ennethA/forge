"""Mechanism demos (Phase 17) and the deformation harness — the MCP mirrors.

Blender is the NDJSON fake from ``test_blender_client`` on an ephemeral port —
never 9876 — so these run with Blender closed and against the *contract* in
addon/README.md rather than a live add-on. Three things are pinned:

* **what goes on the wire.** Command name and every parameter, because these are
  thin mirrors and a renamed key is a silently broken tool.
* **what is refused before the socket.** A key that moves nothing, both units on
  one channel, a colour in 0-255, a film longer than a demo. Each of those would
  otherwise reach Blender and come back as a stack trace or, worse, as a
  successful no-op.
* **the path is CHOSEN here.** ``render_animation`` exposes no ``path``, the way
  ``render_preview`` does not, and with a ``project`` the film lands in
  ``projects/<slug>/renders/`` — the exact folder the assistant bridge's Library
  plays demos from.

Nothing here renders anything: a real ``.mp4`` is Blender's job and takes tens of
seconds on a GPU. What is tested is the request, the refusal and the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import ForgeError

from .test_rigforge import blender, router, sent  # noqa: F401  (fixtures)

# --- canned results, shaped exactly as addon/README.md documents them --------

ANIMATE_RESULT = {
    "object": "flame_cap",
    "action": "flame_capAction",
    "created_action": True,
    "keys": 3,
    "keys_set": 3,
    "channels": ["location"],
    "frame_range": [1, 20],
    "frames": 20,
    "action_frame_range": [1.0, 20.0],
    "interpolation": "BEZIER",
    "interpolated_points": 9,
    "cleared_fcurves": 0,
    "fcurves": 3,
    "rotation_mode": "XYZ",
    "rotation_mode_changed": None,
    "units": ("location is scene metres, location_mm is millimetres, "
              "rotation_euler_deg is degrees"),
    "warnings": [],
    "seconds": 0.02,
}

EMISSION_RESULT = {
    "object": "led_lens",
    "material": "Forge Glow",
    "created_material": True,
    "node": "Forge Emission",
    "node_type": "EMISSION",
    "strength": 6.0,
    "color": [1.0, 0.62, 0.2],
    "frame": 8,
    "keyed": ["strength", "color"],
    "keyframed": True,
    "interpolation": "CONSTANT",
    "interpolated_points": 5,
    "action": "Shader NodetreeAction",
    "fcurves": 5,
    "notes": ["'led_lens' had no material, so it got a new one."],
    "seconds": 0.01,
}

ANIMATION_RESULT = {
    "objects": ["flame_cap", "led_lens"],
    "frame_start": 1,
    "frame_end": 48,
    "frames": 48,
    "fps": 24,
    "duration_s": 2.0,
    "resolution": 640,
    "view": "ISO",
    "engine": "eevee",
    "engine_requested": "eevee",
    "container": "MPEG4",
    "codec": "H264",
    "size_bytes": 41230,
    "framed_all_visible": True,
    "framed_over_frames": [1, 7, 14, 21, 28, 35, 42, 48],
    "bounds_mm": {"size": [42.0, 42.0, 61.0]},
    "ortho_scale_mm": 78.0,
    "duration_ms": 6900,
    "honesty": ("This is an illustration of the intended motion, not a "
                "simulation."),
    "notes": [],
}

RIG_CHECK_RESULT = {
    "rig": "goblin_rig",
    "mesh": "goblin_retopo",
    "pose_set": "extreme",
    "poses_per_joint": 3,
    "poses_run": 33,
    "joints_measured": 11,
    "joints_skipped": [
        {"joint": "tail", "label": "tail",
         "reason": "posing 'tail_fk' moved no geometry (largest move 0.01 mm)"},
    ],
    "rest_intersections": 4,
    "rest_volume_mm3": 1820000.0,
    "thresholds": {"volume_loss_pct": [8, 20]},
    "threshold_tier": "heuristic (proxy tier): visible-artefact bands",
    "gate": "fail",
    "says": "Breaks down: elbow.R, knee.L.",
    "joints": [
        {"joint": "knee.L", "label": "knee.L", "control": "shin_fk.L",
         "worst_volume_loss_pct": 41.7, "worst_twist_collapse_pct": -0.7,
         "worst_new_intersections": 108, "verdict": "fail"},
        {"joint": "neck", "label": "neck", "control": "neck",
         "worst_volume_loss_pct": 2.6, "worst_twist_collapse_pct": 8.1,
         "worst_new_intersections": 12, "verdict": "attention"},
        {"joint": "elbow.R", "label": "elbow.R", "control": "forearm_fk.R",
         "worst_volume_loss_pct": 52.7, "worst_twist_collapse_pct": 5.5,
         "worst_new_intersections": 96, "verdict": "fail"},
        {"joint": "hip.L", "label": "hip.L", "control": "thigh_fk.L",
         "worst_volume_loss_pct": 1.1, "worst_twist_collapse_pct": None,
         "worst_new_intersections": 0, "verdict": "pass"},
    ],
    "pose_restored": True,
    "warnings": ["tail was not measured: rotating 'tail_fk' moved nothing."],
    "seconds": 0.67,
}

METARIG_WITH_JOINTS = {
    "metarig": "goblin_metarig",
    "bone_count": 29,
    "preset": "human",
    "mapping": {"Head": ["spine.006"], "Torso": ["spine", "spine.001"]},
    "chains": [],
    "landmarks": {},
    "joints": {
        "enabled": True,
        "joints": 22,
        "named_joints": 0,
        "placeholder_names": True,
        "inside_fraction": 0.95,
        "refined": [
            {"role": "spine", "tag_mm": [0, 0, 900], "predicted_mm": [0, 0, 916],
             "used_mm": [0, 0, 908], "moved_mm": 8.0},
            {"role": "knee.L", "tag_mm": [0, 0, 420], "predicted_mm": [0, 0, 402],
             "used_mm": [0, 0, 411], "moved_mm": 9.0},
        ],
        "disagreements": [
            {"role": "shoulder.L", "tag_mm": [0, 0, 0], "predicted_mm": [0, 0, 253]},
        ],
        "best_effort": [{"role": "hand.L"}],
        "considered": 22,
        "unused_joints": 19,
    },
    "warnings": ["1 prediction disagreed with the tags and was overruled."],
}

PRESS_KEYS = [
    {"frame": 1, "location_mm": [0, 0, 0]},
    {"frame": 8, "location_mm": [0, 0, -2.1]},
    {"frame": 20, "location_mm": [0, 0, 0]},
]


@pytest.fixture(autouse=True)
def scratch_dirs(tmp_path: Path, monkeypatch) -> Path:
    """Never write a demo into the repo's projects/ or the real previews dir."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    monkeypatch.setattr(config, "PREVIEWS_DIR", str(tmp_path / "previews"))
    return root


# ===========================================================================
# animate_object
# ===========================================================================

def test_animate_object_sends_the_keys_and_the_contract_parameters(
    blender,  # noqa: F811
) -> None:
    fake = blender({"animate_object": ANIMATE_RESULT})
    server.animate_object(PRESS_KEYS, object="flame_cap", interpolation="LINEAR",
                          clear=True)

    params = sent(fake, "animate_object")
    assert params["object"] == "flame_cap"
    assert params["interpolation"] == "LINEAR"
    assert params["clear"] is True
    assert [key["frame"] for key in params["keys"]] == [1, 8, 20]
    # location_mm travels VERBATIM: the add-on owns the mm -> m conversion, and
    # converting it here as well would halve the stroke.
    assert params["keys"][1]["location_mm"] == [0.0, 0.0, -2.1]
    assert "location" not in params["keys"][1]


def test_animate_object_defaults_to_the_active_object_and_bezier(
    blender,  # noqa: F811
) -> None:
    fake = blender({"animate_object": ANIMATE_RESULT})
    server.animate_object(PRESS_KEYS)
    params = sent(fake, "animate_object")
    assert "object" not in params
    assert params["interpolation"] == "BEZIER"
    assert params["clear"] is False


def test_animate_object_accepts_metres_rotation_and_scale(
    blender,  # noqa: F811
) -> None:
    fake = blender({"animate_object": ANIMATE_RESULT})
    server.animate_object([
        {"frame": 1, "location": [0, 0, 0.05]},
        {"frame": 12, "rotation_euler_deg": [0, 0, 90], "scale": [1, 1, 1.2]},
    ])
    keys = sent(fake, "animate_object")["keys"]
    assert keys[0]["location"] == [0.0, 0.0, 0.05]
    assert keys[1]["rotation_euler_deg"] == [0.0, 0.0, 90.0]
    assert keys[1]["scale"] == [1.0, 1.0, 1.2]


def test_one_key_may_be_passed_unwrapped() -> None:
    assert util.normalize_object_keys({"frame": 4, "scale": [1, 1, 2]}) == [
        {"frame": 4, "scale": [1.0, 1.0, 2.0]}
    ]


def test_both_units_on_one_key_is_an_error_not_a_merge() -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_object_keys([
            {"frame": 1, "location": [0, 0, 0], "location_mm": [0, 0, -2.1]},
        ])
    assert "same channel" in str(excinfo.value)
    assert "location_mm" in str(excinfo.value)


def test_a_key_that_moves_nothing_is_refused_before_the_socket() -> None:
    """It would reach Blender as a silent no-op — the worst outcome for a demo."""
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_object_keys([{"frame": 1}])
    assert "sets no channel" in str(excinfo.value)
    assert "location_mm" in str(excinfo.value)


def test_a_key_with_no_frame_is_refused() -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_object_keys([{"location_mm": [0, 0, -2.1]}])
    assert "no `frame`" in str(excinfo.value)


@pytest.mark.parametrize("given", ["nope", 4, [], {}, None])
def test_keys_that_are_not_a_list_of_objects_are_refused(given: Any) -> None:
    with pytest.raises(ForgeError):
        util.normalize_object_keys(given)


def test_a_channel_that_is_not_three_numbers_is_refused() -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_object_keys([{"frame": 1, "location_mm": [0, 0]}])
    assert "three numbers" in str(excinfo.value)


def test_object_keys_frame_range_is_the_span_the_film_needs() -> None:
    assert util.object_keys_frame_range(
        util.normalize_object_keys(PRESS_KEYS)) == (1, 20)
    assert util.object_keys_frame_range([]) is None


def test_the_animate_report_says_what_landed_and_that_it_is_not_physics(
    blender,  # noqa: F811
) -> None:
    blender({"animate_object": ANIMATE_RESULT})
    report = server.animate_object(PRESS_KEYS, object="flame_cap")
    assert "flame_cap" in report
    assert "frames 1-20" in report
    assert "3 key(s)" in report or "keys set" in report
    assert "not a simulation" in report
    # And the next step, so a demo does not stop at a keyframe.
    assert "render_animation" in report


def test_a_cleared_animation_is_reported_as_replaced_not_layered(
    blender,  # noqa: F811
) -> None:
    result = dict(ANIMATE_RESULT, cleared_fcurves=3)
    blender({"animate_object": result})
    report = server.animate_object(PRESS_KEYS, clear=True)
    assert "cleared first" in report
    assert "3 existing curve(s)" in report


# ===========================================================================
# set_material_emission
# ===========================================================================

def test_emission_sends_strength_colour_frame_and_interpolation(
    blender,  # noqa: F811
) -> None:
    fake = blender({"set_material_emission": EMISSION_RESULT})
    server.set_material_emission(6.0, object="led_lens",
                                 color=[1.0, 0.62, 0.2], frame=8)

    params = sent(fake, "set_material_emission")
    assert params["object"] == "led_lens"
    assert params["strength"] == 6.0
    assert params["color"] == [1.0, 0.62, 0.2]
    assert params["frame"] == 8
    # CONSTANT by default: an LED is off and then it is on.
    assert params["interpolation"] == "CONSTANT"


def test_emission_without_a_frame_sends_no_frame_and_no_interpolation(
    blender,  # noqa: F811
) -> None:
    """No frame means a STATE, and an interpolation for no key is noise."""
    fake = blender({"set_material_emission": dict(EMISSION_RESULT, frame=None,
                                                  keyed=[], keyframed=False)})
    server.set_material_emission(6.0)
    params = sent(fake, "set_material_emission")
    assert "frame" not in params and "interpolation" not in params
    assert "color" not in params


@pytest.mark.parametrize("given", [-1.0, 1001.0, "bright", True])
def test_a_strength_outside_the_range_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_emission_strength(given)
    assert "strength" in str(excinfo.value)


def test_a_colour_in_0_255_is_refused_with_the_right_range() -> None:
    """The likeliest mistake by far, and silently clipping it would hide it."""
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_emission_color([255, 158, 51])
    assert "0-1" in str(excinfo.value)


def test_no_colour_means_leave_the_colour_alone() -> None:
    assert util.normalize_emission_color(None) is None
    assert util.normalize_emission_color([1, 0.5, 0]) == [1.0, 0.5, 0.0]


def test_the_emission_report_names_the_material_and_the_engine_it_needs(
    blender,  # noqa: F811
) -> None:
    blender({"set_material_emission": EMISSION_RESULT})
    report = server.set_material_emission(6.0, object="led_lens", frame=8)
    assert "Forge Glow" in report and "(new)" in report
    assert "keyed at frame 8" in report and "CONSTANT" in report
    # The trap this report exists to prevent: a Workbench demo of an LED.
    assert "eevee" in report and "Workbench" in report


def test_an_unkeyed_emission_says_it_is_a_state(blender) -> None:  # noqa: F811
    blender({"set_material_emission": dict(EMISSION_RESULT, frame=None,
                                           keyed=[], keyframed=False)})
    report = server.set_material_emission(6.0)
    assert "not keyed" in report and "state" in report


# ===========================================================================
# render_animation
# ===========================================================================

def test_render_animation_exposes_no_path_parameter() -> None:
    """`render_preview`'s rule: the server picks, so a tool call cannot write
    an .mp4 anywhere on the artist's disk."""
    import inspect

    assert "path" not in inspect.signature(server.render_animation).parameters


def test_render_animation_files_a_project_demo_into_renders(
    blender,  # noqa: F811
    scratch_dirs: Path,
) -> None:
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 48, project="Litwick Lamp")

    params = sent(fake, "render_animation")
    path = Path(params["path"])
    assert path.suffix == ".mp4"
    assert path.parent == (scratch_dirs / "litwick-lamp" / "renders").resolve()
    # The folder exists before Blender is asked to write into it.
    assert path.parent.is_dir()


def test_a_demo_with_no_project_lands_in_the_scratch_previews_folder(
    blender,  # noqa: F811
    tmp_path: Path,
) -> None:
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 24)
    path = Path(sent(fake, "render_animation")["path"])
    assert path.parent == (tmp_path / "previews").resolve()


def test_two_demos_in_one_session_are_two_files(
    blender,  # noqa: F811
) -> None:
    """A demo is compared against the one before it; overwriting loses that."""
    fake = blender({"render_animation": ANIMATION_RESULT}, connections=2)
    server.render_animation(1, 24)
    server.render_animation(1, 24)
    paths = [request["params"]["path"] for request in fake.requests
             if request.get("type") == "render_animation"]
    assert len(paths) == 2 and paths[0] != paths[1]


def test_a_named_demo_is_named_for_what_it_shows(
    blender,  # noqa: F811
    scratch_dirs: Path,
) -> None:
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 48, project="litwick lamp", name="Litwick press!")
    path = Path(sent(fake, "render_animation")["path"])
    assert path.name == "litwick-press.mp4"
    assert path.parent == (scratch_dirs / "litwick-lamp" / "renders").resolve()


def test_re_rendering_the_same_name_replaces_that_take(
    blender,  # noqa: F811
) -> None:
    """Iterating on one demo is free; iterating into a folder of takes is not."""
    fake = blender({"render_animation": ANIMATION_RESULT}, connections=2)
    server.render_animation(1, 48, project="litwick lamp", name="litwick press")
    server.render_animation(1, 48, project="litwick lamp", name="litwick press")
    paths = [request["params"]["path"] for request in fake.requests
             if request.get("type") == "render_animation"]
    assert paths[0] == paths[1]


def test_a_name_with_mp4_already_on_it_is_not_doubled() -> None:
    assert util.animation_path(name="litwick-press.mp4").name == "litwick-press.mp4"


@pytest.mark.parametrize("given", ["../escape", "C:\\films\\x", "a/b", "!!!", ""])
def test_a_name_that_is_a_path_is_refused_never_cleaned(given: str) -> None:
    with pytest.raises(ForgeError):
        util.animation_name(given)


def test_render_animation_sends_every_contract_parameter(
    blender,  # noqa: F811
) -> None:
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 48, objects=["flame_cap", "led_lens"],
                            view="front", fps=30, resolution=800,
                            engine="workbench")
    params = sent(fake, "render_animation")
    assert params["frame_start"] == 1 and params["frame_end"] == 48
    assert params["fps"] == 30
    assert params["resolution"] == 800
    assert params["engine"] == "workbench"
    assert params["view"] == "front"
    assert params["objects"] == ["flame_cap", "led_lens"]


def test_no_objects_means_everything_visible(blender) -> None:  # noqa: F811
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 24)
    assert "objects" not in sent(fake, "render_animation")


def test_the_defaults_are_the_add_ons_defaults(blender) -> None:  # noqa: F811
    fake = blender({"render_animation": ANIMATION_RESULT})
    server.render_animation(1, 24)
    params = sent(fake, "render_animation")
    assert params["fps"] == util.ANIMATION_FPS == 24
    assert params["resolution"] == util.ANIMATION_RESOLUTION == 640
    assert params["engine"] == "eevee"
    assert params["view"] == "iso"


def test_a_backwards_range_is_refused_before_the_socket() -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_animation_frames(48, 1)
    assert "before frame_start" in str(excinfo.value)


def test_a_film_longer_than_a_demo_is_refused() -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.normalize_animation_frames(1, 1 + util.ANIMATION_MAX_FRAMES)
    assert str(util.ANIMATION_MAX_FRAMES) in str(excinfo.value)


def test_the_longest_allowed_demo_is_allowed() -> None:
    assert util.normalize_animation_frames(1, util.ANIMATION_MAX_FRAMES) == (
        1, util.ANIMATION_MAX_FRAMES)


@pytest.mark.parametrize("given", [0, 61, 24.5, "fast", True])
def test_an_fps_outside_the_range_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError):
        util.normalize_animation_fps(given)


@pytest.mark.parametrize("given", [64, 4096, 640.5, "big", True])
def test_a_resolution_outside_the_range_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError):
        util.normalize_animation_resolution(given)


def test_the_animation_report_hands_over_the_path_rather_than_reading_it(
    blender,  # noqa: F811
) -> None:
    """The one file this server makes that the model itself cannot look at."""
    blender({"render_animation": ANIMATION_RESULT})
    report = server.render_animation(1, 48, project="litwick lamp")
    assert ".mp4" in report
    assert "NAME THAT FULL PATH" in report
    assert "READ THAT FILE" not in report
    assert "48 frames at 24 fps" in report
    assert "not a simulation" in report


def test_the_report_says_where_it_was_filed(blender) -> None:  # noqa: F811
    blender({"render_animation": ANIMATION_RESULT})
    report = server.render_animation(1, 48, project="litwick lamp")
    assert "projects/litwick-lamp/renders/" in report


def test_a_slug_that_would_escape_projects_is_refused() -> None:
    with pytest.raises(ForgeError):
        util.animation_path("../../etc")


def test_the_renders_folder_is_the_one_the_bridge_plays_from() -> None:
    """Two components, one folder name. Spelled once here and once there."""
    assert util.PROJECT_RENDERS_DIRNAME == "renders"


def test_render_animation_is_a_legal_flow_step() -> None:
    for name in ("animate_object", "set_material_emission", "render_animation",
                 "rig_check"):
        assert name in util.KNOWN_BLENDER_OPS, name


# ===========================================================================
# rig_check — the deformation harness
# ===========================================================================

def test_rig_check_sends_the_contract_parameters(blender) -> None:  # noqa: F811
    fake = blender({"rig_check": RIG_CHECK_RESULT})
    server.rig_check(rig="goblin_rig", mesh="goblin_retopo", poses="full",
                     joints=["knee.L", "elbow.R"], max_poses=6,
                     intersections=False)
    params = sent(fake, "rig_check")
    assert params["rig"] == "goblin_rig"
    assert params["mesh"] == "goblin_retopo"
    assert params["poses"] == "full"
    assert params["joints"] == ["knee.L", "elbow.R"]
    assert params["max_poses"] == 6
    assert params["intersections"] is False


def test_rig_check_defaults_to_extremes_on_everything(blender) -> None:  # noqa: F811
    fake = blender({"rig_check": RIG_CHECK_RESULT})
    server.rig_check()
    params = sent(fake, "rig_check")
    assert params == {"poses": "extreme", "intersections": True}


def test_an_explicit_pose_list_travels_verbatim(blender) -> None:  # noqa: F811
    fake = blender({"rig_check": RIG_CHECK_RESULT})
    server.rig_check(poses=[90, {"label": "reach", "flex_deg": 95}])
    assert sent(fake, "rig_check")["poses"] == [
        90, {"label": "reach", "flex_deg": 95}]


def test_an_unknown_pose_set_is_refused_with_the_known_ones() -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.rig_check(poses="gentle")
    assert "extreme" in str(excinfo.value) and "quick" in str(excinfo.value)


def test_an_empty_pose_list_is_refused() -> None:
    with pytest.raises(ForgeError):
        server.rig_check(poses=[])


@pytest.mark.parametrize("given", [0, 65, 3.5, "many", True])
def test_max_poses_outside_the_ceiling_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError):
        server.rig_check(max_poses=given)


def test_the_rig_check_report_leads_with_the_gate_and_the_worst_joints(
    blender,  # noqa: F811
) -> None:
    blender({"rig_check": RIG_CHECK_RESULT})
    report = server.rig_check()
    assert "gate: FAIL" in report
    # Worst first: the two failures before the attention before the pass.
    assert report.index("elbow.R") < report.index("neck")
    assert report.index("knee.L") < report.index("hip.L")
    assert "11 joint(s) measured" in report


def test_the_rig_check_report_never_states_a_threshold_as_a_fact(
    blender,  # noqa: F811
) -> None:
    """A `fail` is a band you can argue with, not a verdict on their sculpt."""
    blender({"rig_check": RIG_CHECK_RESULT})
    report = server.rig_check()
    assert "HEURISTICS" in report
    assert "proxy tier" in report
    assert "band you can argue with" in report


def test_the_rig_check_report_names_what_it_could_not_measure(
    blender,  # noqa: F811
) -> None:
    blender({"rig_check": RIG_CHECK_RESULT})
    report = server.rig_check()
    assert "NOT MEASURED" in report and "tail" in report
    assert "pose was restored" in report


def test_a_joint_with_no_twist_number_prints_a_dash_not_none(
    blender,  # noqa: F811
) -> None:
    blender({"rig_check": RIG_CHECK_RESULT})
    report = server.rig_check()
    assert "None" not in report


def test_a_passing_gate_does_not_send_them_to_the_weights(
    blender,  # noqa: F811
) -> None:
    clean = dict(RIG_CHECK_RESULT, gate="pass", joints_skipped=[], warnings=[],
                 says="All 11 measured joint(s) held their volume.")
    blender({"rig_check": clean})
    report = server.rig_check()
    assert "gate: PASS" in report
    assert "rigforge_weights" not in report


# ===========================================================================
# rigforge_metarig's five new parameters
# ===========================================================================

def joints_file(tmp_path: Path) -> Path:
    path = tmp_path / "goblin-joints.json"
    path.write_text(json.dumps({
        "schema": "forge.joints/1",
        "source": "unirig",
        "frame": {"unit": "mm", "space": "mesh_local", "axis_up": "Z"},
        "joints": [{"index": 0, "name": None, "head_mm": [3.3, -10.0, 634.2],
                    "tail_mm": None, "parent": None, "confidence": None}],
    }), encoding="utf-8")
    return path


def test_metarig_sends_the_preset_and_every_joints_parameter(
    blender,  # noqa: F811
    tmp_path: Path,
) -> None:
    fake = blender({"rigforge_metarig": METARIG_WITH_JOINTS})
    path = joints_file(tmp_path)
    server.rigforge_metarig(
        object="goblin_retopo", preset="human", joints_file=str(path),
        joints_weight=0.25, joints_tolerance=0.08, joints_disagree_band=2.5,
        joints_axis_up="y")

    params = sent(fake, "rigforge_metarig")
    assert params["preset"] == "human"
    assert Path(params["joints_file"]) == path.resolve()
    assert params["joints_weight"] == 0.25
    assert params["joints_tolerance"] == 0.08
    assert params["joints_disagree_band"] == 2.5
    # Upper-cased, because the add-on's frame gate compares against "Z".
    assert params["joints_axis_up"] == "Y"


def test_without_a_joints_file_the_request_is_what_it_always_was(
    blender,  # noqa: F811
) -> None:
    """The whole promise of the refinement: the tag-only fit is untouched."""
    fake = blender({"rigforge_metarig": {"metarig": "goblin_metarig",
                                         "bone_count": 29, "mapping": {},
                                         "warnings": []}})
    server.rigforge_metarig(object="goblin_retopo")
    assert sent(fake, "rigforge_metarig") == {"object": "goblin_retopo",
                                              "archetype": "auto"}


def test_a_joints_file_that_is_not_there_is_refused_not_ignored(
    tmp_path: Path,
) -> None:
    """A typo would otherwise become a silent tag-only fit that looks like a win."""
    with pytest.raises(ForgeError):
        server.rigforge_metarig(joints_file=str(tmp_path / "nope.json"))


def test_joint_tuning_without_a_joints_file_is_refused() -> None:
    with pytest.raises(ForgeError) as excinfo:
        server.rigforge_metarig(joints_weight=0.9)
    assert "joints_file" in str(excinfo.value)


@pytest.mark.parametrize(
    ("kwargs",),
    [
        ({"joints_weight": 1.5},),
        ({"joints_weight": -0.1},),
        ({"joints_tolerance": 2.0},),
        ({"joints_disagree_band": 0.5},),
        ({"joints_disagree_band": 21.0},),
        ({"joints_weight": "half"},),
    ],
)
def test_joint_tuning_outside_its_range_is_refused(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ForgeError):
        server.rigforge_metarig(joints_file=str(joints_file(tmp_path)), **kwargs)


def test_the_metarig_report_summarises_the_hints_without_dumping_them(
    blender,  # noqa: F811
    tmp_path: Path,
) -> None:
    blender({"rigforge_metarig": METARIG_WITH_JOINTS})
    report = server.rigforge_metarig(joints_file=str(joints_file(tmp_path)))
    assert "2 landmark(s) refined" in report
    assert "largest move 9 mm" in report
    assert "1 disagreement(s) overruled" in report
    assert "TAGS won" in report
    assert "best effort" in report
    # Never the raw coordinates: the agent needs the three numbers, not 22 joints.
    assert "predicted_mm" not in report


def test_a_refused_joints_file_says_the_fit_is_tags_only(
    blender,  # noqa: F811
    tmp_path: Path,
) -> None:
    result = dict(METARIG_WITH_JOINTS,
                  joints={"enabled": False, "inside_fraction": 0.1},
                  warnings=["That joints file would land inside the mesh if it "
                            "were read as unit=m, axis_up=Y."])
    blender({"rigforge_metarig": result})
    report = server.rigforge_metarig(joints_file=str(joints_file(tmp_path)))
    assert "NOT used" in report
    assert "tag-only fit" in report


def test_a_metarig_with_no_joints_block_reports_nothing_about_them(
    blender,  # noqa: F811
) -> None:
    blender({"rigforge_metarig": {"metarig": "goblin_metarig", "bone_count": 29,
                                  "mapping": {}, "warnings": []}})
    report = server.rigforge_metarig()
    assert "predicted joints" not in report
