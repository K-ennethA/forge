"""The animation-chain tools: `rigforge_walk`, `animation_check`,
`rigforge_punch`, `rigforge_correctives`.

Same shape as ``test_rigforge.py``: a fake NDJSON add-on on an ephemeral port
(never 9876), pinned to the *contract* the socket commands actually accept
(``addon/forge/tools/rigforge_anim.py``, ``rigcheck.py``, ``correctives.py``).
What goes on the wire and what the report says are both pinned here so a
change to either side breaks a test first. "Blender is down" propagation for
these three tools is covered by the shared parametrized test in
``test_server_tools.py`` (``test_blender_tools_report_the_addon_is_down``);
this file is request-shaping and report-formatting only.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from forge_mcp import config, server
from forge_mcp.errors import ForgeError

from .test_blender_client import FakeBlender

# --- fakes -------------------------------------------------------------------


def router(results: dict[str, Any]) -> Callable[[dict[str, Any], Any], None]:
    """Answer each command with its canned `result`, keyed by command type."""

    def respond(request: dict[str, Any], conn: Any) -> None:
        command = request.get("type")
        payload = results.get(command)
        if payload is None:
            body = {
                "id": request.get("id"),
                "status": "error",
                "result": None,
                "message": f"unexpected command '{command}'",
            }
        else:
            if callable(payload):
                payload = payload(request)
            body = {
                "id": request.get("id"),
                "status": "success",
                "result": payload,
                "message": "",
            }
        conn.sendall(json.dumps(body).encode("utf-8") + b"\n")

    return respond


@pytest.fixture
def blender(monkeypatch):
    """Factory: start a fake add-on for these canned results and aim the client."""
    started: list[FakeBlender] = []

    def make(results: dict[str, Any], connections: int = 1) -> FakeBlender:
        fake = FakeBlender(router(results), connections=connections)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "BLENDER_PORT", fake.port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 10.0)
        monkeypatch.setattr(config, "PREVIEW_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


def sent(fake: FakeBlender, command: str) -> dict[str, Any]:
    """The params of the first request of that type."""
    for request in fake.requests:
        if request.get("type") == command:
            return request["params"]
    raise AssertionError(
        f"{command} was never sent; saw {[r.get('type') for r in fake.requests]}"
    )


# --- canned results, shaped like the addon's own return dicts ---------------

WALK_RESULT = {
    "rig": "goblin_rig",
    "action": "walk-loop",
    "created": True,
    "loop": True,
    "travel": True,
    "cycle_frames": 32,
    "frame_range": [1, 33],
    "stance_fraction": 0.6,
    "step_length_m": 0.395,
    "stride_m": 0.79,
    "feet": [
        {"foot": "L", "target": "foot_ik.L", "stance_runs": [[1, 19]], "stance_frames": 19},
        {"foot": "R", "target": "foot_ik.R", "stance_runs": [[17, 33]], "stance_frames": 17},
    ],
    "bones": ["foot_ik.L", "foot_ik.R", "root", "torso"],
    "keys_set": 48,
    "interpolation": "LINEAR",
    "warnings": [],
    "says": "walk-loop: 32-frame cycle, 790 mm stride, feet keyed on foot_ik.L and "
            "foot_ik.R with 60% of the cycle planted. The root carries the travel "
            "(export with root_motion).",
    "seconds": 0.42,
}

ANIMATION_CHECK_OK = {
    "rig": "goblin_rig",
    "action": "walk-loop",
    "mode": "planted",
    "mode_reason": "asked for by the caller",
    "frames": [1, 32],
    "frame_step": 1,
    "samples": 32,
    "looping": True,
    "body_bone": "root",
    "body_travel_mm": 790.0,
    "feet": [
        {
            "foot": "L", "bone": "DEF-toe.L", "steps_measured": 1,
            "worst_drift_mm": 1.1, "verdict": "ok",
        },
        {
            "foot": "R", "bone": "DEF-toe.R", "steps_measured": 1,
            "worst_drift_mm": 0.8, "verdict": "ok",
        },
    ],
    "steps_measured": 2,
    "worst_step": {
        "bone": "DEF-toe.L", "frames": [1, 19], "drift_mm": 1.1, "verdict": "ok",
    },
    "worst_drift_mm": 1.1,
    "gate": "ok",
    "threshold_tier": "heuristic (proxy tier): the scale at which a slide becomes "
                      "visible, not values calibrated against artist accept/reject "
                      "decisions.",
    "says": "Feet hold. Worst step slides 1.1 mm across 2 measured step(s).",
    "warnings": [],
    "seconds": 0.31,
}

PUNCH_RESULT = {
    "rig": "goblin_rig",
    "action": "punch.R",
    "created": True,
    "loop": False,
    "side": "R",
    "frames": 24,
    "frame_range": [1, 24],
    "strike_frame": 11,
    "chamber_frame": 4,
    "lead_frames": 2,
    "peak_frames": {"pelvis": 5, "chest": 7, "shoulder": 9, "fist": 11},
    "rotation_lead": {
        "pelvis": {"frame": 5, "degrees": 22.4},
        "chest": {"frame": 7, "degrees": 17.6},
        "shoulder": {"frame": 9, "degrees": 9.4},
        "fist": {"frame": 11, "degrees": None},
    },
    "rotation_leads_in_order": True,
    "hip_rotation_deg": 22.0,
    "chest_rotation_deg": 18.0,
    "shoulder_rotation_deg": 10.0,
    "arm_reach_m": 0.62,
    "extension_m": 0.605,
    "extension_planned_m": 0.605,
    "extension_ratio": 0.976,
    "max_extension_ratio": 0.98,
    "extension_tolerance": 0.01,
    "extension_within_cap": True,
    "fist_landed_mm": 3.2,
    "target": [0.1, 0.6, 1.3],
    "target_distance_m": 0.58,
    "target_height_m": 1.3,
    "target_reach_clamped": False,
    "peak_fist_speed_frame": 9,
    "peak_fist_speed_m_per_frame": 0.083,
    "peak_fist_speed_m_per_s": 2.13,
    "fps": 24.0,
    "guard": [0.15, 0.35, 1.45],
    "guard_rise_m": 0.15,
    "chamber_draw_m": 0.05,
    "weight_shift_m": 0.03,
    "weight_shift_clamped": False,
    "hip_lower_m": 0.0,
    "hip_lower_deepened": False,
    "shoulder_half_width_m": 0.18,
    "leg_length_m": 0.9,
    "forward_axis": [0.0, 1.0, 0.0],
    "aim_yaw_deg": 12.5,
    "feet_planted": ["foot_ik.L", "foot_ik.R"],
    "convention": "rigify",
    "poles": True,
    "bones": ["foot_ik.L", "foot_ik.R", "hips", "chest", "shoulder.R", "hand_ik.R"],
    "keys_set": 96,
    "cleared_fcurves": 0,
    "fcurves": 18,
    "interpolation": "LINEAR",
    "interpolated_points": 96,
    "rotation_modes": {},
    "actions_in_file": ["punch.R"],
    "says": "punch.R: 24 frames, right fist fires from the chamber on frame 4 and "
            "lands on frame 11. Peak fist speed 2.13 m/s on frame 9 — before the "
            "strike, not on it. Full extension 605 mm against a measured reach of "
            "620 mm (98%, cap 98%). Rotation peaks pelvis 22.4 deg (f5) -> chest "
            "17.6 deg (f7) -> shoulder 9.4 deg (f9) -> fist (f11). Both feet keyed "
            "on foot_ik.L and foot_ik.R and never moved.",
    "warnings": [],
    "seconds": 0.51,
}

CORRECTIVES_AUTHOR_RESULT = {
    "rig": "goblin_rig",
    "mesh": "goblin_retopo",
    "action": "author",
    "shape_keys": ["corr_knee_L_090"],
    "table": [
        {
            "joint": "knee.L", "label": "knee.L", "flex_deg": 90.0,
            "volume_loss_before_pct": 29.7, "volume_loss_after_pct": 13.6,
            "improvement_pct": 54.2,
        }
    ],
    "gate_before": "fail",
    "gate_after": "attention",
    "warnings": [],
    "says": "Wrote 1 corrective shape key(s), each driven by its joint's own bend "
            "angle. Measured with the harness, drivers live: knee.L at 90 deg: "
            "29.7% -> 13.6% volume loss.",
    "seconds": 4.8,
}

CORRECTIVES_REPORT_RESULT = {
    "rig": "goblin_rig",
    "mesh": "goblin_retopo",
    "action": "report",
    "correctives": [
        {
            "name": "corr_knee_L_090", "value": 0.0, "muted": False,
            "driven": True, "driver_bones": ["knee.L"], "driver_ramp": [[0, 0], [90, 1]],
        }
    ],
    "count": 1,
    "undriven": [],
    "says": "'goblin_retopo' carries 1 corrective shape key(s); 1 of them are driven "
            "by a bend angle.",
    "seconds": 0.02,
}

CORRECTIVES_CLEAR_RESULT = {
    "rig": "goblin_rig",
    "mesh": "goblin_retopo",
    "action": "clear",
    "removed": ["corr_knee_L_090"],
    "remaining": [],
    "says": "Removed 1 corrective shape key(s) and their drivers: corr_knee_L_090.",
    "seconds": 0.05,
}


# --- rigforge_walk ------------------------------------------------------------


def test_walk_sends_the_contract_defaults(blender) -> None:
    fake = blender({"rigforge_walk": WALK_RESULT})
    server.rigforge_walk(rig="goblin_rig")

    assert fake.requests[0]["type"] == "rigforge_walk"
    assert sent(fake, "rigforge_walk") == {
        "travel": True,
        "loop": True,
        "clear": True,
        "interpolation": "LINEAR",
        "rig": "goblin_rig",
    }


def test_walk_passes_through_every_optional_parameter(blender) -> None:
    fake = blender({"rigforge_walk": WALK_RESULT})
    server.rigforge_walk(
        rig="goblin_rig",
        action="walk",
        cycle_frames=40,
        step_length=0.3,
        step_height=0.05,
        stance_fraction=0.55,
        hip_drop=0.02,
        hip_sway=0.01,
        hip_twist_deg=5.0,
        arm_swing_deg=20.0,
        elbow_bend_deg=15.0,
        foot_roll_deg=10.0,
        travel=False,
        loop=False,
        clear=False,
        interpolation="BEZIER",
        stride_width=0.1,
        reach_margin=0.9,
    )

    assert sent(fake, "rigforge_walk") == {
        "travel": False,
        "loop": False,
        "clear": False,
        "interpolation": "BEZIER",
        "rig": "goblin_rig",
        "action": "walk",
        "cycle_frames": 40,
        "step_length": 0.3,
        "step_height": 0.05,
        "stance_fraction": 0.55,
        "hip_drop": 0.02,
        "hip_sway": 0.01,
        "hip_twist_deg": 5.0,
        "arm_swing_deg": 20.0,
        "elbow_bend_deg": 15.0,
        "foot_roll_deg": 10.0,
        "stride_width": 0.1,
        "reach_margin": 0.9,
    }


def test_walk_without_rig_or_action_sends_neither(blender) -> None:
    fake = blender({"rigforge_walk": WALK_RESULT})
    server.rigforge_walk()

    params = sent(fake, "rigforge_walk")
    assert "rig" not in params and "action" not in params


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"cycle_frames": 2}, "cycle_frames must be between 4 and 600"),
        ({"cycle_frames": 700}, "cycle_frames must be between 4 and 600"),
        ({"cycle_frames": 12.5}, "cycle_frames must be a whole number"),
        ({"stance_fraction": 0.05}, "stance_fraction must be between 0.2 and 0.95"),
        ({"reach_margin": 2.0}, "reach_margin must be between 0.5 and 1.2"),
        ({"arm_swing_deg": 120.0}, "arm_swing_deg must be between 0.0 and 90.0"),
        ({"elbow_bend_deg": -1.0}, "elbow_bend_deg must be between 0.0 and 120.0"),
        ({"foot_roll_deg": 90.0}, "foot_roll_deg must be between 0.0 and 60.0"),
        ({"hip_twist_deg": 60.0}, "hip_twist_deg must be between 0.0 and 45.0"),
        ({"step_length": -0.1}, "step_length must be zero or positive"),
        ({"step_height": -0.1}, "step_height must be zero or positive"),
        ({"hip_drop": -0.1}, "hip_drop must be zero or positive"),
        ({"hip_sway": -0.1}, "hip_sway must be zero or positive"),
        ({"step_length": "far"}, "step_length must be a number"),
    ],
)
def test_walk_rejects_out_of_range_numbers_before_the_wire(
    kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment.replace(".", r"\.")):
        server.rigforge_walk(**kwargs)


def test_walk_stride_width_accepts_any_sign(blender) -> None:
    """`stride_width` has no addon-side bound (0.0 default, either direction)."""
    fake = blender({"rigforge_walk": WALK_RESULT})
    server.rigforge_walk(stride_width=-0.05)
    assert sent(fake, "rigforge_walk")["stride_width"] == -0.05


def test_walk_report_carries_says_and_per_foot_stance(blender) -> None:
    blender({"rigforge_walk": WALK_RESULT})
    report = server.rigforge_walk(rig="goblin_rig", cycle_frames=32)

    assert "Walk cycle on 'walk-loop' (new action)" in report
    assert "48 key(s)" in report
    assert "frames 1-33" in report
    assert WALK_RESULT["says"] in report
    assert "L (foot_ik.L): planted 19 of 32 frame(s)" in report
    assert "R (foot_ik.R): planted 17 of 32 frame(s)" in report
    assert "next: animation_check" in report


def test_walk_report_relays_warnings(blender) -> None:
    result = dict(WALK_RESULT, warnings=["step_length was shortened to 0.300 m"])
    blender({"rigforge_walk": result})
    report = server.rigforge_walk()

    assert "WARNINGS (1):" in report
    assert "! step_length was shortened" in report


def test_walk_summary_reflects_travel_and_loop_choice(blender) -> None:
    blender({"rigforge_walk": WALK_RESULT})
    report = server.rigforge_walk(travel=False, loop=False, clear=False)

    assert "in place" in report
    assert "one-shot" in report
    assert "layered onto existing keys" in report


# --- animation_check -----------------------------------------------------------


def test_animation_check_sends_the_contract_defaults(blender) -> None:
    fake = blender({"animation_check": ANIMATION_CHECK_OK})
    server.animation_check(rig="goblin_rig", action="walk-loop")

    assert fake.requests[0]["type"] == "animation_check"
    assert sent(fake, "animation_check") == {
        "mode": "auto",
        "rig": "goblin_rig",
        "action": "walk-loop",
    }


def test_animation_check_passes_through_every_optional_parameter(blender) -> None:
    fake = blender({"animation_check": ANIMATION_CHECK_OK})
    server.animation_check(
        mode="planted",
        frame_step=2,
        contact_band=0.2,
        min_stance_frames=4,
        feet=["DEF-toe.L", "DEF-toe.R"],
    )

    assert sent(fake, "animation_check") == {
        "mode": "planted",
        "frame_step": 2,
        "contact_band": 0.2,
        "min_stance_frames": 4,
        "feet": ["DEF-toe.L", "DEF-toe.R"],
    }


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"frame_step": 0}, "frame_step must be between 1 and 10"),
        ({"frame_step": 11}, "frame_step must be between 1 and 10"),
        ({"frame_step": 1.5}, "frame_step must be a whole number"),
        ({"contact_band": 0.0}, "contact_band must be between 0.01 and 0.9"),
        ({"contact_band": 1.0}, "contact_band must be between 0.01 and 0.9"),
        ({"min_stance_frames": 1}, "min_stance_frames must be between 2 and 1000"),
        ({"min_stance_frames": 1001}, "min_stance_frames must be between 2 and 1000"),
    ],
)
def test_animation_check_rejects_out_of_range_numbers(
    kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment.replace(".", r"\.")):
        server.animation_check(**kwargs)


def test_animation_check_report_states_the_gate_and_per_foot_table(blender) -> None:
    blender({"animation_check": ANIMATION_CHECK_OK})
    report = server.animation_check(rig="goblin_rig")

    assert "gate: OK" in report
    assert "planted mode" in report
    assert ANIMATION_CHECK_OK["says"] in report
    assert "worst step: DEF-toe.L frames 1-19, 1.1 mm" in report
    assert "heuristic (proxy tier)" in report


def test_animation_check_report_fails_loudly_when_the_gate_fails(blender) -> None:
    failing = dict(
        ANIMATION_CHECK_OK,
        gate="fail",
        worst_drift_mm=42.0,
        says="Feet slide. The worst planted step moves 42.0 mm.",
    )
    blender({"animation_check": failing})
    report = server.animation_check()

    assert "gate: FAIL" in report
    assert "Feet slide" in report


def test_animation_check_report_relays_warnings(blender) -> None:
    result = dict(ANIMATION_CHECK_OK, warnings=["Only one foot (L) was found"])
    blender({"animation_check": result})
    report = server.animation_check()

    assert "WARNINGS (1):" in report
    assert "! Only one foot" in report


# --- rigforge_punch ------------------------------------------------------------


def test_punch_sends_the_contract_defaults(blender) -> None:
    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch(rig="goblin_rig")

    assert fake.requests[0]["type"] == "rigforge_punch"
    assert sent(fake, "rigforge_punch") == {
        "loop": False,
        "clear": True,
        "interpolation": "LINEAR",
        "poles": True,
        "rig": "goblin_rig",
    }


def test_punch_passes_through_every_optional_parameter(blender) -> None:
    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch(
        rig="goblin_rig",
        action="jab",
        side="left",
        frames=30,
        strike_fraction=0.4,
        lead_frames=3,
        target_distance=0.5,
        target_height=1.2,
        hip_rotation_deg=20.0,
        chest_rotation_deg=15.0,
        shoulder_rotation_deg=8.0,
        weight_shift=0.04,
        hip_lower=0.02,
        guard_rise=0.1,
        chamber_draw=0.06,
        reach_margin=0.9,
        max_extension_ratio=0.95,
        loop=True,
        clear=False,
        interpolation="BEZIER",
        poles=False,
    )

    assert sent(fake, "rigforge_punch") == {
        "loop": True,
        "clear": False,
        "interpolation": "BEZIER",
        "poles": False,
        "rig": "goblin_rig",
        "action": "jab",
        "side": "L",
        "frames": 30,
        "strike_fraction": 0.4,
        "lead_frames": 3,
        "target_distance": 0.5,
        "target_height": 1.2,
        "hip_rotation_deg": 20.0,
        "chest_rotation_deg": 15.0,
        "shoulder_rotation_deg": 8.0,
        "weight_shift": 0.04,
        "hip_lower": 0.02,
        "guard_rise": 0.1,
        "chamber_draw": 0.06,
        "reach_margin": 0.9,
        "max_extension_ratio": 0.95,
    }


def test_punch_without_rig_or_action_sends_neither(blender) -> None:
    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch()

    params = sent(fake, "rigforge_punch")
    assert "rig" not in params and "action" not in params and "side" not in params


@pytest.mark.parametrize(
    "value,normalized",
    [("L", "L"), ("left", "L"), ("R", "R"), ("RIGHT", "R"), ("r", "R")],
)
def test_punch_side_is_case_insensitive_and_accepts_full_words(
    blender, value: str, normalized: str
) -> None:
    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch(side=value)
    assert sent(fake, "rigforge_punch")["side"] == normalized


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"side": "up"}, "side must be one of L, LEFT, R, RIGHT"),
        ({"frames": 7}, "frames must be between 8 and 600"),
        ({"frames": 601}, "frames must be between 8 and 600"),
        ({"frames": 12.5}, "frames must be a whole number"),
        (
            {"strike_fraction": 0.1},
            "strike_fraction must be between 0.15 and 0.85",
        ),
        (
            {"strike_fraction": 0.9},
            "strike_fraction must be between 0.15 and 0.85",
        ),
        ({"hip_rotation_deg": -1.0}, "hip_rotation_deg must be between 0.0 and 60.0"),
        ({"hip_rotation_deg": 61.0}, "hip_rotation_deg must be between 0.0 and 60.0"),
        (
            {"chest_rotation_deg": 61.0},
            "chest_rotation_deg must be between 0.0 and 60.0",
        ),
        (
            {"shoulder_rotation_deg": 46.0},
            "shoulder_rotation_deg must be between 0.0 and 45.0",
        ),
        (
            {"max_extension_ratio": 0.2},
            "max_extension_ratio must be between 0.3 and 1.0",
        ),
        (
            {"max_extension_ratio": 1.1},
            "max_extension_ratio must be between 0.3 and 1.0",
        ),
        ({"reach_margin": 0.1}, "reach_margin must be between 0.2 and 0.98"),
        ({"reach_margin": 1.0}, "reach_margin must be between 0.2 and 0.98"),
        ({"lead_frames": -1}, "lead_frames must be zero or positive"),
        ({"lead_frames": 2.5}, "lead_frames must be a whole number"),
        (
            {"target_distance": -0.1},
            "target_distance must be zero or positive",
        ),
        ({"weight_shift": -0.1}, "weight_shift must be zero or positive"),
        ({"hip_lower": -0.1}, "hip_lower must be zero or positive"),
        ({"guard_rise": -0.1}, "guard_rise must be zero or positive"),
        ({"chamber_draw": -0.1}, "chamber_draw must be zero or positive"),
        ({"target_distance": "far"}, "target_distance must be a number"),
    ],
)
def test_punch_rejects_out_of_range_arguments_before_the_wire(
    kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment.replace(".", r"\.")):
        server.rigforge_punch(**kwargs)


def test_punch_reach_margin_bound_tracks_a_given_max_extension_ratio(
    blender,
) -> None:
    """`reach_margin`'s upper bound is `max_extension_ratio` itself when the
    caller passes one, not the add-on's own 0.98 default."""
    with pytest.raises(ForgeError, match=r"reach_margin must be between 0\.2 and 0\.5"):
        server.rigforge_punch(max_extension_ratio=0.5, reach_margin=0.9)

    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch(max_extension_ratio=0.5, reach_margin=0.4)
    assert sent(fake, "rigforge_punch")["reach_margin"] == 0.4


def test_punch_target_height_accepts_a_negative_number(blender) -> None:
    """Unlike the other lengths, `target_height` has no addon-side floor."""
    fake = blender({"rigforge_punch": PUNCH_RESULT})
    server.rigforge_punch(target_height=-0.2)
    assert sent(fake, "rigforge_punch")["target_height"] == -0.2


def test_punch_report_carries_says_and_the_measured_numbers(blender) -> None:
    blender({"rigforge_punch": PUNCH_RESULT})
    report = server.rigforge_punch(rig="goblin_rig")

    assert "Punch on 'punch.R' (new action)" in report
    assert "right side" in report
    assert "frames 1-24" in report
    assert "chamber f4 -> strike f11" in report
    assert "96 key(s)" in report
    assert PUNCH_RESULT["says"] in report
    assert "peak fist speed 2.13 m/s on frame 9" in report
    assert "extension 0.605 m of 0.62 m reach" in report
    assert "97.6% of reach, cap 98%" in report
    assert "rotation lead: pelvis 22.4 deg (f5) -> chest 17.6 deg (f7)" in report
    assert "shoulder 9.4 deg (f9)" in report
    assert "feet planted (never moved): foot_ik.L, foot_ik.R" in report


def test_punch_report_relays_warnings(blender) -> None:
    result = dict(
        PUNCH_RESULT,
        warnings=["weight_shift was shortened to 30 mm"],
        extension_within_cap=False,
    )
    blender({"rigforge_punch": result})
    report = server.rigforge_punch()

    assert "WARNINGS (1):" in report
    assert "! weight_shift was shortened" in report
    assert "OVER CAP" in report


def test_punch_summary_reflects_frames_side_and_loop_choice(blender) -> None:
    blender({"rigforge_punch": PUNCH_RESULT})
    report = server.rigforge_punch(frames=30, side="L", loop=True, clear=False)

    assert "30-frame clip" in report
    assert "L side" in report
    assert "looping (-loop)" in report
    assert "layered onto existing keys" in report


# --- rigforge_correctives -----------------------------------------------------


def test_correctives_author_defaults_to_the_harnesss_own_worst_joints(blender) -> None:
    fake = blender({"rigforge_correctives": CORRECTIVES_AUTHOR_RESULT})
    server.rigforge_correctives(rig="goblin_rig", mesh="goblin_retopo")

    assert fake.requests[0]["type"] == "rigforge_correctives"
    assert sent(fake, "rigforge_correctives") == {
        "action": "author",
        "rig": "goblin_rig",
        "mesh": "goblin_retopo",
    }


def test_correctives_author_passes_joints_and_tuning(blender) -> None:
    fake = blender({"rigforge_correctives": CORRECTIVES_AUTHOR_RESULT})
    server.rigforge_correctives(
        joints=["knee.L"],
        angle_samples=[0.5, 1.0],
        strength=0.8,
        smooth=0.4,
        weight_floor=0.1,
        verify=False,
    )

    assert sent(fake, "rigforge_correctives") == {
        "action": "author",
        "joints": ["knee.L"],
        "angle_samples": [0.5, 1.0],
        "strength": 0.8,
        "smooth": 0.4,
        "weight_floor": 0.1,
        "verify": False,
    }


def test_correctives_angle_samples_accepts_flex_deg_objects(blender) -> None:
    fake = blender({"rigforge_correctives": CORRECTIVES_AUTHOR_RESULT})
    server.rigforge_correctives(angle_samples=[{"flex_deg": 90}])
    assert sent(fake, "rigforge_correctives")["angle_samples"] == [{"flex_deg": 90}]


def test_correctives_rejects_an_empty_angle_samples_list() -> None:
    with pytest.raises(ForgeError, match="angle_samples"):
        server.rigforge_correctives(angle_samples=[])


@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("strength", {"strength": 1.5}),
        ("strength", {"strength": -0.1}),
        ("smooth", {"smooth": 2.0}),
        ("weight_floor", {"weight_floor": -0.5}),
    ],
)
def test_correctives_rejects_tuning_values_outside_zero_to_one(
    label: str, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ForgeError, match=f"{label} must be between 0.0 and 1.0"):
        server.rigforge_correctives(**kwargs)


def test_correctives_report_action_sends_just_the_targets(blender) -> None:
    fake = blender({"rigforge_correctives": CORRECTIVES_REPORT_RESULT})
    server.rigforge_correctives(action="report", rig="goblin_rig", mesh="goblin_retopo")

    assert sent(fake, "rigforge_correctives") == {
        "action": "report",
        "rig": "goblin_rig",
        "mesh": "goblin_retopo",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"joints": ["knee.L"]},
        {"angle_samples": [0.5]},
        {"strength": 0.5},
        {"smooth": 0.5},
        {"weight_floor": 0.1},
        {"verify": False},
    ],
)
def test_correctives_report_rejects_author_only_arguments(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ForgeError, match="mean nothing to action='report'"):
        server.rigforge_correctives(action="report", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"angle_samples": [0.5]},
        {"strength": 0.5},
        {"smooth": 0.5},
        {"weight_floor": 0.1},
        {"verify": True},
    ],
)
def test_correctives_clear_rejects_author_only_arguments(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ForgeError, match="mean nothing to action='clear'"):
        server.rigforge_correctives(action="clear", **kwargs)


def test_correctives_clear_allows_joints_to_narrow_the_removal(blender) -> None:
    fake = blender({"rigforge_correctives": CORRECTIVES_CLEAR_RESULT})
    server.rigforge_correctives(action="clear", joints=["knee.L"])

    assert sent(fake, "rigforge_correctives") == {
        "action": "clear",
        "joints": ["knee.L"],
    }


def test_correctives_there_is_no_direction_parameter() -> None:
    """The addon refuses `direction` outright; the MCP tool never even offers it
    as a callable keyword — Python's own call signature is the refusal."""
    with pytest.raises(TypeError):
        server.rigforge_correctives(direction="normal")  # type: ignore[call-arg]


def test_correctives_author_report_quotes_the_before_after_table(blender) -> None:
    blender({"rigforge_correctives": CORRECTIVES_AUTHOR_RESULT})
    report = server.rigforge_correctives(rig="goblin_rig", mesh="goblin_retopo")

    assert "Correctives authored on 'goblin_retopo'" in report
    assert CORRECTIVES_AUTHOR_RESULT["says"] in report
    assert "gate: FAIL -> ATTENTION" in report
    assert "90 deg" in report and "29.7%" in report and "13.6%" in report
    assert "shape keys written" in report and "corr_knee_L_090" in report


def test_correctives_report_action_lists_driven_state(blender) -> None:
    blender({"rigforge_correctives": CORRECTIVES_REPORT_RESULT})
    report = server.rigforge_correctives(action="report")

    assert "Correctives on '(unnamed)'" not in report  # mesh name came through
    assert "corr_knee_L_090" in report
    assert "driven by knee.L" in report
    assert "active" in report


def test_correctives_clear_action_reports_removed_and_remaining(blender) -> None:
    blender({"rigforge_correctives": CORRECTIVES_CLEAR_RESULT})
    report = server.rigforge_correctives(action="clear")

    assert "Correctives cleared on 'goblin_retopo'" in report
    assert "removed" in report and "corr_knee_L_090" in report
    assert "remaining: none" in report or "remaining:" in report
