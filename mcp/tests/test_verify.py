"""The geometric gate: ``verify_design`` and ``turntable`` over the MCP surface.

Same rules as the rest of the suite — Blender is the NDJSON fake on an ephemeral
port, never 9876, so these run with Blender closed and pin the *contract*.

What is actually being proved:

1. both tools send the socket command and the parameters
   ``addon/README.md`` says they do, including the ``for`` rename (the tool's
   argument is ``profile`` because ``for`` is a Python keyword; the wire keeps
   the natural word);
2. the report is a **scored gate**: a pass/attention verdict per axis, ordered
   with the gated ones first, and a headline that answers "is this done" without
   reading a number;
3. **every claim carries its credibility tier**, and the report says what the
   two tiers mean — a silhouette thresholded off a photograph and a face count
   are both numbers and are not both facts;
4. the ``print`` profile POINTS at ``partforge_check`` rather than growing a
   second, weaker opinion about wall thickness;
5. symmetry is rendered as REPORTED and never as a fault;
6. the turntable report carries the reading order, the fixed-framing guarantee
   and the **order-swap law** — the one thing a model reading two of these has
   to know;
7. bad input (an impossible view count, a negative budget, a reference that is
   not an image) is refused HERE, before the socket, with a sentence naming what
   is accepted.
"""

from __future__ import annotations

from typing import Any

import pytest

from forge_mcp import server, util
from forge_mcp.errors import ForgeError

from .test_rigforge import blender, sent  # noqa: F401 - pytest fixtures

# --- canned add-on results --------------------------------------------------


def _claim(value: Any, tier: str = "measured", note: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"value": value, "tier": tier}
    if note:
        entry["note"] = note
    return entry


VERIFY_ATTENTION = {
    "object": "goblin",
    "for": "game",
    "gated_axes": ["defects", "poly_budget", "uv", "loops", "silhouette"],
    "attention": ["poly_budget", "loops"],
    "passed": ["defects", "uv", "silhouette"],
    "gate": "attention",
    "face_count": 61000,
    "vertex_count": 30600,
    "axes": {
        "defects": {
            "status": "pass",
            "summary": "No numeric defects: sealed, no clipping, even density.",
            "tier": "measured",
            "source": "mesh_diagnose",
            "detail": {"clean": True, "verdict": []},
        },
        "poly_budget": {
            "status": "attention",
            "summary": "61000 faces against a 15000 budget — 4.1x over; "
                       "decimate or retopo.",
            "tier": "measured",
            "faces": _claim(61000),
            "budget": _claim(15000),
            "ratio": _claim(4.067),
        },
        "uv": {
            "status": "pass",
            "summary": "6 island(s), no flips, density even within 1.4x.",
            "tier": "measured",
            "present": _claim(True),
            "detail": {
                "layer": "UVMap",
                "islands": _claim(6),
                "flipped_faces": _claim(0),
                "degenerate_faces": _claim(0),
                "out_of_bounds_loops": _claim(0),
                "area_distortion": _claim(1.4),
                "overlap": _claim(False, "heuristic",
                                  "summed UV area is 3% above the rasterised "
                                  "coverage at 512x512"),
                "coverage": _claim(0.82),
            },
        },
        "loops": {
            "status": "attention",
            "summary": "2 of 9 deformation zone(s) have fewer than 3 loops "
                       "across them (elbow.L: 1, knee.R: 2) — those will "
                       "crease rather than bend.",
            "tier": "heuristic",
            "detail": {
                "source": "goblin-rig",
                "wanted": 3,
                "zone_count": 9,
                "thin_zones": 2,
                "zones": [
                    {"joint": "elbow.L", "loops": _claim(1, "heuristic"),
                     "vertices_in_band": 42},
                    {"joint": "knee.R", "loops": _claim(2, "heuristic"),
                     "vertices_in_band": 51},
                ],
            },
        },
        "symmetry": {
            "status": "reported",
            "summary": "Mirror residual on X: 4.1 mm mean, 18.9 mm worst "
                       "(2.30% of the model's diagonal) — clearly asymmetric. "
                       "Asymmetry is usually a decision; this is a number, not "
                       "a fault.",
            "tier": "measured",
            "judged": False,
            "detail": {
                "axis": "X",
                "mean_mm": _claim(4.1),
                "p95_mm": _claim(12.0),
                "max_mm": _claim(18.9),
                "fraction_of_size": _claim(0.023),
                "near_symmetric": _claim(False, "heuristic"),
            },
        },
        "silhouette": {
            "status": "pass",
            "summary": "Front silhouette overlaps the reference 91% (IoU 0.91, "
                       "at or above 0.85), proportions off by 3%.",
            "tier": "measured",
            "confidence": "medium",
            "reference": r"C:\refs\goblin.png",
            "detail": {
                "iou": _claim(0.91),
                "aspect_delta": _claim(0.03),
                "centroid_delta": _claim(0.01),
                "reference_mask": _claim("background threshold", "heuristic",
                                         "the reference has no alpha, so its "
                                         "silhouette was thresholded against "
                                         "the median border colour"),
                "confidence": "medium",
                "confidence_reasons": [],
            },
        },
    },
    "tiers": {
        "measured": "a computed number with a definition behind it",
        "heuristic": "a number that took a judgement call to compute",
    },
    "notes": ["Game profile: edge loops, UVs and the polygon budget are gated; "
              "symmetry is reported, never judged."],
    "duration_ms": 412,
    "verdict": [
        "[measured] 61000 faces against a 15000 budget — 4.1x over; decimate "
        "or retopo.",
        "[heuristic] 2 of 9 deformation zone(s) have fewer than 3 loops across "
        "them (elbow.L: 1, knee.R: 2) — those will crease rather than bend.",
        "[report only] Mirror residual on X: 4.1 mm mean.",
    ],
}

VERIFY_PRINT = {
    "object": "bracket",
    "for": "print",
    "gated_axes": ["defects", "silhouette"],
    "attention": [],
    "passed": ["defects"],
    "gate": "pass",
    "face_count": 2400,
    "vertex_count": 1220,
    "axes": {
        "defects": {
            "status": "pass",
            "summary": "No numeric defects: sealed, no clipping, even density.",
            "tier": "measured",
        },
        "silhouette": {
            "status": "not_applicable",
            "summary": "No reference image given, so nothing says whether this "
                       "is the SHAPE of the thing that was asked for.",
            "tier": "measured",
        },
    },
    "notes": ["Print profile: only defects and silhouette are gated here. Bed "
              "fit, wall thickness and overhangs are partforge_check / "
              "check_model's question against the real printer profile — run "
              "one of those, this does not duplicate them."],
    "verdict": ["[measured] Every gated axis passes: no defects, and nothing "
                "else was asked for."],
}

TURNTABLE_RESULT = {
    "path": r"C:\Temp\forge-previews\turntable-001-24v.png",
    "objects": ["goblin"],
    "views": 24,
    "resolution": 256,
    "columns": 5,
    "rows": 5,
    "sheet_size": [1280, 1280],
    "elevation_deg": 15.0,
    "reading_order": "left to right, top to bottom: tile 0 is 0 degrees and "
                     "each step is 15.0 degrees around Z",
    "framed_all_visible": False,
    "fixed_framing": True,
    "size_bytes": 402118,
    "duration_ms": 3100,
    "notes": ["Every tile is framed identically (one ortho scale from the "
              "bounding sphere), so a change between tiles is a change in the "
              "MODEL and never in the camera."],
}


# --- verify_design: the wire ------------------------------------------------


def test_verify_design_sends_the_contract_command_and_defaults(blender) -> None:
    fake = blender({"verify_design": VERIFY_PRINT})
    server.verify_design()

    assert fake.requests[0]["type"] == "verify_design"
    assert sent(fake, "verify_design") == {"for": "any", "symmetry_axis": "X"}


def test_the_profile_argument_goes_on_the_wire_as_for(blender) -> None:
    """`for` is a Python keyword, so the tool says `profile` and renames here.

    The socket protocol keeps the natural word; the rename happens in exactly
    one place, which is the whole reason to test it.
    """
    fake = blender({"verify_design": VERIFY_ATTENTION})
    server.verify_design(object="goblin", profile="game", poly_budget=8000)

    params = sent(fake, "verify_design")
    assert params["for"] == "game"
    assert params["poly_budget"] == 8000
    assert params["object"] == "goblin"
    assert "profile" not in params


def test_a_reference_image_is_resolved_to_an_absolute_path(blender, tmp_path) -> None:
    picture = tmp_path / "goblin.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake = blender({"verify_design": VERIFY_ATTENTION})
    server.verify_design(object="goblin", reference_image=str(picture))

    assert sent(fake, "verify_design")["reference_image"] == str(picture.resolve())


def test_a_reference_that_is_not_an_image_is_refused_before_the_socket(
    dead_backends, tmp_path
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a picture", encoding="utf-8")
    with pytest.raises(ForgeError) as caught:
        server.verify_design(reference_image=str(notes))
    assert "not an image" in str(caught.value)


def test_a_missing_reference_says_so_rather_than_asking_blender(
    dead_backends, tmp_path
) -> None:
    with pytest.raises(ForgeError) as caught:
        server.verify_design(reference_image=str(tmp_path / "nope.png"))
    assert "No file at" in str(caught.value)


@pytest.mark.parametrize("budget", [-1, 3.5, True])
def test_an_impossible_poly_budget_is_refused_by_name(budget: Any) -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_poly_budget(budget)
    assert "poly_budget" in str(caught.value)


def test_no_poly_budget_means_the_profile_decides(blender) -> None:
    """An omitted budget must not become a zero on the wire: 0 means 'ungated',
    and the add-on's own per-profile default is the right answer instead."""
    fake = blender({"verify_design": VERIFY_ATTENTION})
    server.verify_design(profile="game")
    assert "poly_budget" not in sent(fake, "verify_design")

    assert util.normalize_poly_budget(None) is None
    assert util.normalize_poly_budget(0) == 0


def test_examples_is_bounded_the_same_way_mesh_diagnose_bounds_it(
    dead_backends,
) -> None:
    with pytest.raises(ForgeError) as caught:
        server.verify_design(examples=99)
    assert "1 and 25" in str(caught.value)


# --- verify_design: the report ----------------------------------------------


def test_the_headline_answers_is_this_done(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    assert "NEEDS ATTENTION" in report
    assert "3 of 5 gated axes clean" in report
    assert "profile: game" in report


def test_a_clean_gate_says_it_passes(blender) -> None:
    blender({"verify_design": VERIFY_PRINT})
    report = server.verify_design(object="bracket", profile="print")
    assert "PASSES" in report
    assert "NEEDS ATTENTION" not in report


def test_every_axis_gets_a_status_and_a_tier(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    for title in ("defects", "poly budget", "UVs", "edge loops at joints",
                  "symmetry residual", "silhouette vs reference"):
        assert title in report, title
    assert "[ATTENTION] poly budget (measured)" in report
    assert "[PASS] defects (measured)" in report
    assert "[heuristic]" in report


def test_the_report_explains_what_the_two_tiers_mean(blender) -> None:
    """The tiering is worthless if the reader has to guess what it means."""
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    assert "measured = a computed number" in report
    assert "heuristic = a number that took a judgement call" in report
    assert "wrong to quote as fact" in report


def test_symmetry_is_rendered_as_reported_never_as_a_fault(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    assert "REPORTED, never judged" in report
    assert "asymmetry is usually a decision" in report.lower()
    # and it is not counted among the things blocking "done"
    assert "[ATTENTION] symmetry" not in report


def test_the_silhouette_line_carries_its_own_mask_provenance(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    assert "IoU 0.91" in report
    assert "reference mask from background threshold [heuristic]" in report


def test_low_confidence_silhouettes_say_why(blender) -> None:
    result = {k: v for k, v in VERIFY_ATTENTION.items()}
    axes = dict(result["axes"])
    silhouette = dict(axes["silhouette"])
    detail = dict(silhouette["detail"])
    detail["confidence_reasons"] = [
        "only 41% of the reference's border is one flat colour, so the "
        "background is not plain"
    ]
    silhouette["detail"] = detail
    silhouette["confidence"] = "low"
    axes["silhouette"] = silhouette
    result["axes"] = axes
    blender({"verify_design": result})

    report = server.verify_design(object="goblin", profile="game")
    assert "silhouette confidence: low" in report
    assert "not plain" in report


def test_the_print_profile_points_at_the_real_print_checks(blender) -> None:
    """The whole point of the print profile: it does NOT grow a second, weaker
    opinion about wall thickness, it names the tool that actually answers."""
    blender({"verify_design": VERIFY_PRINT})
    report = server.verify_design(object="bracket", profile="print")

    assert "partforge_check" in report
    assert "does not duplicate" in report


def test_the_report_says_this_is_only_half_the_gate(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")

    assert "TRUTH half of the gate" in report
    assert "Renders judge beauty" in report
    assert "BOTH have to pass" in report


def test_the_report_caps_the_critique_at_three(blender) -> None:
    blender({"verify_design": VERIFY_ATTENTION})
    report = server.verify_design(object="goblin", profile="game")
    assert "at most THREE" in report


def test_the_tool_description_carries_the_bias_evidence() -> None:
    """The reason to run this at all lives where the model reads it."""
    description = server.app  # the tool is registered on the app
    assert description is not None
    doc = server.verify_design.__doc__ or ""
    assert "78 ELO" in doc
    assert "swap places" in doc
    assert "never judged" in doc.lower()


# --- turntable --------------------------------------------------------------


def test_turntable_sends_the_protocol_defaults(blender) -> None:
    fake = blender({"turntable": TURNTABLE_RESULT})
    server.turntable()

    params = sent(fake, "turntable")
    assert params["views"] == util.TURNTABLE_VIEWS == 24
    assert params["resolution"] == util.TURNTABLE_RESOLUTION == 256
    assert params["path"].endswith(".png")
    assert "objects" not in params


def test_turntable_passes_named_objects_and_an_elevation(blender) -> None:
    fake = blender({"turntable": TURNTABLE_RESULT})
    server.turntable(objects=["goblin"], views=8, resolution=128, elevation=-20.0)

    params = sent(fake, "turntable")
    assert params["objects"] == ["goblin"]
    assert params["views"] == 8
    assert params["resolution"] == 128
    assert params["elevation"] == -20.0


@pytest.mark.parametrize("views", [1, 200])
def test_an_impossible_view_count_is_refused_with_the_range(views: int) -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_turntable_views(views)
    message = str(caught.value)
    assert "views must be between 4 and 64" in message
    assert "24 is the default" in message


@pytest.mark.parametrize("resolution", [16, 4096])
def test_an_impossible_tile_size_is_refused_with_the_range(resolution: int) -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_turntable_resolution(resolution)
    assert "64 and 512" in str(caught.value)


def test_turntable_helpers_take_none_as_the_protocol_default() -> None:
    assert util.normalize_turntable_views(None) == 24
    assert util.normalize_turntable_resolution(None) == 256


def test_each_turntable_gets_its_own_file(tmp_path, monkeypatch) -> None:
    """Comparing this turntable against the last one needs both to still exist."""
    monkeypatch.setattr(util.config, "PREVIEWS_DIR", str(tmp_path))
    first = util.turntable_path(24, ["goblin"])
    second = util.turntable_path(24, ["goblin"])
    assert first != second
    assert "goblin" in first.name


def test_the_turntable_report_tells_the_model_to_read_the_sheet(blender) -> None:
    blender({"turntable": TURNTABLE_RESULT})
    report = server.turntable(objects=["goblin"])

    assert "READ THAT FILE NOW" in report
    assert TURNTABLE_RESULT["path"] in report
    assert "24 views at 256 px" in report
    assert "5 x 5 contact sheet" in report


def test_the_turntable_report_carries_the_order_swap_law(blender) -> None:
    """The one thing a model comparing two of these has to know."""
    blender({"turntable": TURNTABLE_RESULT})
    report = server.turntable(objects=["goblin"])

    assert "ORDER-SWAP LAW" in report
    assert "BOTH orders" in report
    assert "too close to call" in report


def test_the_turntable_report_says_the_framing_is_fixed(blender) -> None:
    blender({"turntable": TURNTABLE_RESULT})
    report = server.turntable(objects=["goblin"])

    assert "framed identically" in report
    assert "reading order" in report
    assert "verify_design is the other half" in report


def test_a_single_view_hiding_things_is_said_out_loud(blender) -> None:
    blender({"turntable": TURNTABLE_RESULT})
    report = server.turntable(objects=["goblin"])
    assert "hides interpenetration" in report


# --- both are legal flow steps ----------------------------------------------


def test_the_gate_can_end_a_flow() -> None:
    """A flow that builds something should be able to MEASURE it, not only
    leave a picture of it on disk."""
    assert "verify_design" in util.KNOWN_BLENDER_OPS
    assert "turntable" in util.KNOWN_BLENDER_OPS


# --- the server instructions carry the law ----------------------------------


def test_the_server_instructions_state_the_dual_gate() -> None:
    instructions = server.INSTRUCTIONS
    assert "THE GATE IS TWO GATES" in instructions
    assert "ORDER-SWAP LAW" in instructions
    assert "verify_design judges" in instructions
