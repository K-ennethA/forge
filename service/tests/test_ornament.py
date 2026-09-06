"""The ornament half of ``forge_lib``: decoration that is generated, not sculpted.

Same three layers as ``test_forge_lib.py``, cheapest first:

* the ``*_plan()`` functions are arithmetic on a printer profile, so the
  clamping rules are tested without build123d at all;
* the geometry is measured -- a leaf really is a prism of the thickness the plan
  claims, a peg really does mate the socket cut from the same spec;
* ``samples/eevee_style_bowl_base.py`` goes through the real ``/check``
  endpoint, once per piece, because "the base passes all four with the collar
  on it" is the claim the sample exists to make.

The failures worth naming, because they are what these tests defend:

* two elements a tenth of a millimetre apart look identical on screen and come
  back as ``min_wall: fail``.  Hence the clearance tests.
* a splined outline extruded straight gives a mesh whose side wall pinches
  ~0.03 mm inside its own cap face, and the check's rays walk out through the
  pinch.  Hence the "one element measures its own thickness" tests.
* a CURVED element built as a constant-wall shell measured 0.15 to 0.38 mm on
  blades 1.2, 2.6 and 5.0 mm thick, and 0.16 mm on all three at once for one
  setting -- because the measurement was an acute dihedral at the shell's own
  radial end face and never touched the wall at all.  Hence
  ``test_cupped_collar_is_one_watertight_solid_that_measures_its_own_wall``,
  which asserts the measured wall across thicknesses rather than at one.
* a collar can pass every check and still look like a ring of paper.  Hence
  ``test_two_layers_close_the_gaps_the_first_rank_leaves``, which casts rays
  out of the band's axis instead of trusting a screenshot.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from service import forge_lib
from service.printer import DEFAULT_PROFILE

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

MIN_WALL = float(DEFAULT_PROFILE["min_wall_thickness"])          # 0.8
MIN_FEATURE = float(DEFAULT_PROFILE["min_feature_size"])         # 1.0
OVERHANG = float(DEFAULT_PROFILE["max_unsupported_overhang_deg"])  # 50

#: A ten-point ear: proportions, not a trace.
EAR = [
    [0.0, 0.0],
    [11.0, 6.0],
    [15.0, 24.0],
    [12.0, 46.0],
    [4.0, 62.0],
    [0.0, 70.0],
    [-6.0, 58.0],
    [-13.0, 34.0],
    [-14.0, 12.0],
    [-8.0, 2.0],
]


# ==========================================================================
# Determinism -- "same seed, same collar" has to be literally true
# ==========================================================================


def test_jitter_noise_is_a_hash_not_a_random_number():
    first = [forge_lib._noise(7, i, 0) for i in range(20)]
    second = [forge_lib._noise(7, i, 0) for i in range(20)]
    assert first == second
    assert first != [forge_lib._noise(8, i, 0) for i in range(20)]
    assert first != [forge_lib._noise(7, i, 1) for i in range(20)]
    assert all(-1.0 <= value < 1.0 for value in first)
    # Not all the same number, and not obviously biased to one side.
    assert len(set(first)) == len(first)
    assert 0.2 < sum(1 for v in first if v > 0) / len(first) < 0.8


# ==========================================================================
# leaf_collar -- the plan
# ==========================================================================


def test_leaf_collar_widens_a_leaf_that_would_leave_a_gap():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 4.0, 16, overlap=0.35)
    assert plan["width_mm"] > 4.0
    assert plan["overlap"] == pytest.approx(0.35, abs=0.02)
    assert any("width" in note for note in plan["clamped"])


def test_leaf_collar_honours_a_wider_leaf_than_the_overlap_asks_for():
    narrow = forge_lib.leaf_collar_plan(45.0, 26.0, 4.0, 16, overlap=0.2)
    wide = forge_lib.leaf_collar_plan(45.0, 26.0, 25.0, 16, overlap=0.2)
    assert wide["width_mm"] > narrow["width_mm"]


def test_leaf_collar_clamps_droop_into_the_printable_window():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=80.0)
    assert plan["lean_deg"] == pytest.approx(OVERHANG - forge_lib.OVERHANG_SAFETY_DEG)
    assert any("droop_deg" in note for note in plan["clamped"])


def test_leaf_collar_tip_is_a_land_never_a_point():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16)
    assert plan["tip_land_mm"] >= MIN_FEATURE
    tiny = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, tip_land=0.05)
    assert tiny["tip_land_mm"] >= MIN_FEATURE
    assert any("tip_land" in note for note in tiny["clamped"])


def test_leaf_collar_thickness_is_clamped_up_to_the_minimum_feature():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, thickness=0.2)
    assert plan["thickness_mm"] == MIN_FEATURE
    assert any("thickness" in note for note in plan["clamped"])


def test_leaf_collar_layers_are_far_enough_apart_to_measure():
    """The layer gap is solved, not guessed: it has to clear a real air gap."""
    plan = forge_lib.leaf_collar_plan(45.0, 30.0, 22.0, 16, droop_deg=44.0)
    assert plan["layers"] == 2
    assert plan["layer_gap_mm"] > plan["thickness_mm"]
    assert plan["layer_clearance_mm"] >= plan["keep_apart_mm"] - 1e-6
    assert plan["keep_apart_mm"] >= MIN_FEATURE


def test_leaf_collar_ranks_step_along_the_element_not_straight_out():
    """A rank steps out AND up, which is what keeps the band off the one inside.

    Straight out, a step of ``g`` buys only ``g cos(lean)`` of the separation
    that matters, and all of ``g`` lands in the band's outer radius -- which is
    the material that hides the inner rank.
    """
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0)
    gap = plan["layer_gap_mm"]
    assert plan["layer_step_r_mm"] == pytest.approx(
        gap * math.cos(math.radians(44.0)), abs=1e-4
    )
    assert plan["layer_rise_mm"] == pytest.approx(
        gap * math.sin(math.radians(44.0)), abs=1e-4
    )
    # The radial step is strictly cheaper than the separation it buys.
    assert plan["layer_step_r_mm"] < gap
    # The outer rank starts higher, so it lies over the inner one.
    assert plan["layer_z_mm"][0] < plan["layer_z_mm"][1] == 0.0
    # ... and it is a little smaller.
    assert plan["layer_scale"][1] < plan["layer_scale"][0] == 1.0


def test_petal_crown_ranks_still_step_straight_out():
    """A crown's band is below its petals, so nothing is hidden either way."""
    plan = forge_lib.petal_crown_plan(45.0, 26.0, 20.0, 16)
    assert plan["layer_step_r_mm"] == pytest.approx(plan["layer_gap_mm"])
    assert plan["layer_rise_mm"] == 0.0


# ==========================================================================
# cup -- the arched front that turns a cut-out into a leaf
# ==========================================================================


def test_cup_is_off_by_default_and_reported_when_on():
    flat = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0)
    assert flat["cup"] == 0.0
    assert flat["cup_rise_mm"] == 0.0
    assert flat["stack_mm"] == flat["thickness_mm"]

    cupped = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0, cup=0.32)
    assert cupped["cup"] == pytest.approx(0.32)
    assert cupped["cup_rise_mm"] > 0.0
    assert cupped["cup_flank_deg"] > 20.0
    # The element is thin but no longer shallow, and every envelope knows it.
    assert cupped["stack_mm"] == pytest.approx(
        cupped["thickness_mm"] + cupped["cup_rise_mm"]
    )
    assert cupped["stack_mm"] > flat["stack_mm"]


def test_cup_rise_follows_the_element_and_eases_to_a_flat_tip():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0, cup=0.32)
    assert plan["cup_rise_mm"] == pytest.approx(0.32 * plan["width_mm"] / 2.0, rel=0.05)

    flank = forge_lib._element_flank(
        plan["length_mm"], plan["width_mm"], plan["tip_land_mm"],
        plan["root_width_mm"], plan["embed_mm"], "leaf",
    )
    geom = forge_lib._cup_geometry(
        plan["cup"], flank, plan["thickness_mm"],
        plan["cup_rake_tan"], plan["cup_ascent_tan"],
    )
    rises = [rise for _, _, rise in geom["stations"]]
    # Flat at the tip, so the tip land is the same rectangle a prism's is.
    assert rises[-1] == 0.0
    # Fullest somewhere in the middle, not at either end.
    peak = rises.index(max(rises))
    assert 0 < peak < len(rises) - 1


def test_cup_is_clamped_to_something_that_still_reads_as_a_leaf():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, cup=5.0)
    assert plan["cup"] == forge_lib.MAX_CUP_RATIO
    assert any("cup" in note for note in plan["clamped"])


def test_cup_is_suppressed_when_it_would_be_under_a_layer_of_relief():
    """A cup shallower than 0.3 mm is not a cup; the element stays a prism."""
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, cup=0.002)
    assert plan["cup_rise_mm"] == 0.0
    assert plan["stack_mm"] == plan["thickness_mm"]


def test_cup_refuses_a_number_that_is_not_one():
    for bad in (-0.2, float("nan"), True, "deep"):
        with pytest.raises(forge_lib.PrintabilityError) as exc:
            forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, cup=bad)
        assert "cup" in str(exc.value)


def test_cup_costs_a_crown_droop_window_but_not_a_collar():
    """Which way the arched front faces decides who pays.

    A collar's crown points outward and UP -- the face the overhang check
    measures is the flat back, at exactly the droop.  A crown's arched face is
    the one pointing down, so its climb out of the root rides on top of the
    flare and the window has to give it room.
    """
    collar = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=46.0, cup=0.42)
    assert collar["cup_slope_deg"] == 0.0
    assert collar["lean_deg"] == pytest.approx(46.0)

    crown = forge_lib.petal_crown_plan(45.0, 26.0, 20.0, 16, flare_deg=46.0, cup=0.42)
    assert crown["cup_slope_deg"] > 0.0
    assert crown["lean_deg"] < 46.0
    assert any("flare_deg" in note for note in crown["clamped"])
    # Never so far that the printable window closes: the tip land has to stay
    # inside the limit as well as the underside.
    assert crown["lean_deg"] >= 90.0 - OVERHANG


def test_cup_pushes_the_ranks_apart_and_the_band_out():
    flat = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0)
    cupped = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0, cup=0.32)
    assert cupped["layer_gap_mm"] > flat["layer_gap_mm"]
    assert cupped["band_outer_radius_mm"] > flat["band_outer_radius_mm"]
    # And the solved gap still clears a real air gap.
    assert cupped["layer_clearance_mm"] >= cupped["keep_apart_mm"] - 1e-6


def test_leaf_collar_one_layer_keeps_the_elements_apart_instead():
    plan = forge_lib.leaf_collar_plan(45.0, 30.0, 40.0, 16, overlap=0.5, layers=1)
    assert plan["layer_gap_mm"] == 0.0
    assert plan["width_mm"] < plan["pitch_mm"]
    assert any("layers=1" in note for note in plan["clamped"])


def test_leaf_collar_reports_the_band_rim_it_cannot_avoid():
    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0)
    assert plan["support_free"] is False
    reasons = {entry["what"] for entry in plan["unsupported"]}
    assert "band bottom rim" in reasons
    rim = [e for e in plan["unsupported"] if e["what"] == "band bottom rim"][0]
    assert rim["angle_from_vertical_deg"] == 90.0
    assert rim["area_mm2"] > 0.0
    assert "union the collar onto a base" in rim["why"].lower()


def test_leaf_collar_names_the_tips_only_when_the_droop_is_outside_the_window():
    shallow = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=20.0)
    inside = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, droop_deg=44.0)
    assert "element tips" in {e["what"] for e in shallow["unsupported"]}
    assert "element tips" not in {e["what"] for e in inside["unsupported"]}
    tips = [e for e in shallow["unsupported"] if e["what"] == "element tips"][0]
    assert tips["angle_from_vertical_deg"] == pytest.approx(70.0)
    assert "40 deg" in tips["why"]  # the droop that would fix it


def test_leaf_collar_jitter_never_leans_an_element_less_far_than_asked():
    """Printability wins: the plan's verdict has to hold at every leaf."""
    for jitter in (0.0, 0.5, 1.0):
        plan = forge_lib.leaf_collar_plan(
            45.0, 26.0, 20.0, 16, droop_deg=44.0, jitter=jitter
        )
        assert plan["lean_floor_deg"] == plan["lean_deg"]
        assert plan["worst_lean_deg"] == plan["lean_deg"]
        assert plan["support_free"] is False  # the band rim, always
        assert "element tips" not in {e["what"] for e in plan["unsupported"]}


def test_leaf_collar_negative_clearance_bites_into_the_base():
    slip = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16)
    bite = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, clearance=-1.0)
    assert slip["bore_radius_mm"] == pytest.approx(45.0 + 0.2)
    assert bite["bore_radius_mm"] == pytest.approx(44.0)


def test_leaf_collar_refuses_a_clearance_that_is_a_hole_not_a_fit():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, clearance=-30.0)
    assert "not a fit" in str(exc.value)


def test_leaf_collar_refuses_an_element_too_short_to_be_one():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.leaf_collar_plan(45.0, 1.0, 20.0, 16)
    assert "too short" in str(exc.value)


def test_leaf_collar_refuses_a_negative_overlap():
    with pytest.raises(forge_lib.PrintabilityError):
        forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, overlap=-0.5)


def test_ornament_refuses_more_elements_than_it_will_build():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.scale_band_plan(90.0, 14.0, 20.0, 60, rows=4)
    assert str(forge_lib.MAX_ORNAMENT_ELEMENTS) in str(exc.value)


def test_ornament_plan_follows_a_stricter_printer():
    strict = {"min_wall_thickness": 2.0, "min_feature_size": 3.0,
              "max_unsupported_overhang_deg": 40.0}
    plan = forge_lib.leaf_collar_plan(
        45.0, 26.0, 20.0, 16, droop_deg=44.0, thickness=1.0, printer=strict
    )
    assert plan["thickness_mm"] == 3.0
    assert plan["lean_deg"] == pytest.approx(38.0)
    assert plan["tip_land_mm"] >= 3.0


# ==========================================================================
# petal_crown / scale_band -- the cheaper siblings
# ==========================================================================


def test_petal_crown_is_the_support_free_direction():
    plan = forge_lib.petal_crown_plan(45.0, 26.0, 20.0, 14)
    assert plan["direction"] == "up"
    assert plan["support_free"] is True
    assert plan["unsupported"] == []


def test_scale_band_rows_are_spaced_to_clear_the_row_above():
    plan = forge_lib.scale_band_plan(45.0, 14.0, 20.0, 16, rows=3, droop_deg=34.0)
    assert plan["rows"] == 3
    assert plan["row_step_mm"] > 0.0
    assert plan["row_clearance_mm"] >= plan["keep_apart_mm"] - 1e-6
    # Rows follow the band's skirt outward as they fall.
    assert plan["row_radius_step_mm"] == pytest.approx(
        plan["row_step_mm"] * math.tan(math.radians(plan["lean_deg"])), rel=1e-6
    )
    assert plan["band_outer_radius_mm"] > plan["layer_radius_mm"][-1]


def test_scale_band_refuses_rows_that_would_land_on_each_other():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.scale_band_plan(45.0, 14.0, 20.0, 16, rows=3, droop_deg=0.0)
    assert "lean out" in str(exc.value)


def test_the_three_siblings_share_one_core():
    """Same machinery, three element shapes and two directions."""
    collar = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 14)
    crown = forge_lib.petal_crown_plan(45.0, 26.0, 20.0, 14)
    band = forge_lib.scale_band_plan(45.0, 14.0, 20.0, 14, rows=2)
    assert (collar["shape"], crown["shape"], band["shape"]) == (
        "leaf",
        "petal",
        "scale",
    )
    for plan in (collar, crown, band):
        assert set(plan) >= {
            "layer_gap_mm",
            "band_profile_mm",
            "tip_land_mm",
            "support_free",
            "unsupported",
            "clamped",
        }


# ==========================================================================
# silhouette_part -- the plan
# ==========================================================================


def test_silhouette_refuses_too_few_points_with_a_plain_message():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.silhouette_part_plan([[0, 0], [10, 0], [5, 9]], 6.0)
    message = str(exc.value)
    assert "at least 6" in message and "triangle" in message


def test_silhouette_refuses_too_many_points_and_says_why():
    points = [[math.cos(i) * 10.0, math.sin(i) * 10.0] for i in range(20)]
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.silhouette_part_plan(points, 6.0)
    assert "tracing pixels" in str(exc.value)


def test_silhouette_refuses_an_outline_that_crosses_itself():
    bowtie = [[0, 0], [10, 0], [10, 10], [0, 10], [10, 5], [0, 5]]
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.silhouette_part_plan(bowtie, 6.0)
    message = str(exc.value)
    assert "crosses itself" in message and "point" in message


def test_silhouette_refuses_a_shape_under_the_minimum_feature():
    sliver = [[0, 0], [40, 0.2], [80, 0.4], [80, 0.6], [40, 0.5], [0, 0.3]]
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.silhouette_part_plan(sliver, 6.0)
    assert "minimum feature" in str(exc.value)


def test_silhouette_rejects_points_that_are_not_numbers():
    with pytest.raises(forge_lib.PrintabilityError):
        forge_lib.silhouette_part_plan([[0, 0], [1, "x"]] * 3, 6.0)


def test_silhouette_clamps_thickness_and_rounding():
    thin = forge_lib.silhouette_part_plan(EAR, 0.3)
    assert thin["thickness_mm"] == MIN_WALL
    assert any("thickness" in note for note in thin["clamped"])

    fat = forge_lib.silhouette_part_plan(EAR, 9.0, rounding=20.0)
    assert fat["rounding_mm"] <= 0.45 * 9.0 + 1e-9
    assert any("rounding" in note for note in fat["clamped"])


def test_silhouette_taper_is_clamped_to_something_the_outline_can_carry():
    plan = forge_lib.silhouette_part_plan(EAR, 9.0, taper=80.0)
    assert plan["taper_deg"] <= 45.0
    assert any("taper" in note for note in plan["clamped"])


def test_silhouette_refuses_a_peg_the_part_cannot_bury():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.silhouette_part_plan(EAR, 6.0, peg={"d": 6.0, "l": 10.0})
    assert "thick" in str(exc.value)


def test_silhouette_peg_lands_at_the_outline_s_bottom_centre():
    plan = forge_lib.silhouette_part_plan(EAR, 9.0, peg={"d": 6.0, "l": 10.0})
    assert plan["peg"]["at_mm"][1] == pytest.approx(min(p[1] for p in EAR))
    assert plan["peg"]["diameter_mm"] == 6.0


def test_silhouette_is_support_free_by_construction():
    assert forge_lib.silhouette_part_plan(EAR, 9.0)["support_free"] is True


# ==========================================================================
# soft_body -- the plan
# ==========================================================================

BOWL = [(30.0, 0.0), (40.0, 10.0), (45.0, 26.0), (44.0, 46.0), (38.0, 66.0), (33.0, 78.0)]


def test_soft_body_refuses_a_profile_too_short_to_be_a_curve():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan([(10.0, 0.0), (12.0, 5.0), (14.0, 10.0)])
    assert "at least 5" in str(exc.value)


def test_soft_body_refuses_a_traced_profile():
    points = [(10.0 + i, float(i)) for i in range(14)]
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan(points)
    assert "tracing a photograph" in str(exc.value)


def test_soft_body_refuses_a_profile_that_doubles_back():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan(
            [(10.0, 0.0), (12.0, 5.0), (14.0, 3.0), (16.0, 10.0), (12.0, 20.0)]
        )
    assert "not above" in str(exc.value)


def test_soft_body_refuses_a_negative_radius():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan(
            [(-1.0, 0.0), (12.0, 5.0), (14.0, 10.0), (16.0, 15.0), (12.0, 20.0)]
        )
    assert "zero or more" in str(exc.value)


def test_soft_body_refuses_something_too_small_to_be_a_body():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan(
            [(10.0, 0.0), (12.0, 0.4), (14.0, 0.8), (16.0, 1.2), (12.0, 1.6)]
        )
    assert "tall" in str(exc.value)


def test_soft_body_refuses_a_wall_that_closes_the_body_up():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.soft_body_plan(BOWL, wall=40.0)
    assert "cavity left" in str(exc.value)


def test_soft_body_clamps_the_base_to_a_land_and_the_wall_to_the_minimum():
    plan = forge_lib.soft_body_plan(
        [(0.05, 0.0), (12.0, 5.0), (14.0, 10.0), (16.0, 15.0), (12.0, 20.0)],
        wall=0.2,
    )
    assert plan["base_radius_mm"] >= forge_lib.min_land()
    assert plan["wall_mm"] == MIN_WALL
    assert any("base radius" in note for note in plan["clamped"])
    assert any("wall" in note for note in plan["clamped"])


def test_soft_body_names_a_silhouette_that_hangs_over_itself():
    steep = [(6.0, 0.0), (10.0, 2.0), (30.0, 6.0), (34.0, 20.0), (30.0, 30.0)]
    plan = forge_lib.soft_body_plan(steep)
    assert plan["support_free"] is False
    assert "outside of the body" in {e["what"] for e in plan["unsupported"]}
    assert plan["outer_steepest_deg"] > OVERHANG


def test_soft_body_solid_and_hollow_agree_about_the_outside():
    solid = forge_lib.soft_body_plan(BOWL)
    hollow = forge_lib.soft_body_plan(BOWL, wall=3.0)
    assert solid["hollow"] is False and hollow["hollow"] is True
    assert solid["outline_mm"] == hollow["outline_mm"]
    assert hollow["floor_mm"] >= MIN_WALL
    assert len(hollow["inner_mm"]) > 2


# ==========================================================================
# Geometry -- measured, not asserted from the plan
# ==========================================================================

build123d = pytest.importorskip("build123d", reason="build123d is not installed yet")


from service import checks  # noqa: E402


def _thinnest(solid) -> float:
    """The minimum-wall probe the real check runs, on one solid."""
    from service.printer import normalize_printer
    from service.runner import tessellate_shape

    vertices, triangles = tessellate_shape(solid)
    geometry = checks.MeshGeometry(vertices, triangles)
    result = checks.check_min_wall(geometry, normalize_printer(None))
    return result["data"].get("min_measured_thickness_mm", float("inf"))


def _steepest_overhang(solid) -> float:
    """The steepest downward facet that is not resting on the plate."""
    from service.runner import tessellate_shape

    vertices, triangles = tessellate_shape(solid)
    geometry = checks.MeshGeometry(vertices, triangles)
    base = min(v[2] for v in geometry.vertices)
    worst = 0.0
    for index, normal in enumerate(geometry.normals):
        down = -normal[2]
        if down <= 0.0:
            continue
        triangle = geometry.triangles[index]
        if max(geometry.vertices[i][2] for i in triangle) <= base + 0.05:
            continue
        worst = max(worst, math.degrees(math.asin(min(1.0, down))))
    return worst


def test_one_element_measures_its_own_thickness():
    """The splined outline is sampled before it is extruded; this is why.

    Extruding the B-spline directly gives a mesh whose side wall pinches inside
    its own cap face, and the check reads 0.1-0.8 mm on a 2.4 mm blade.
    """
    from build123d import Plane, extrude

    for shape, length, width in (
        ("leaf", 30.0, 26.6),
        ("leaf", 18.0, 19.4),
        ("petal", 30.0, 22.9),
        ("scale", 16.0, 26.6),
    ):
        face = forge_lib._element_face(
            length, width, 0.12 * width, 0.6 * width, 6.0, shape
        )
        blade = extrude(Plane.XY * face, amount=2.4)
        assert blade.is_valid
        assert _thinnest(blade) >= 2.4 - 1e-3, f"{shape} {length}x{width}"


@pytest.mark.parametrize(
    "label, kwargs",
    [
        ("defaults", {}),
        ("jitter", {"jitter": 0.6, "seed": 5}),
        ("full jitter", {"jitter": 1.0, "seed": 7}),
        ("one layer", {"layers": 1}),
        ("few wide leaves", {"count": 8, "leaf_length": 36.0}),
        ("many narrow leaves", {"count": 28, "leaf_length": 20.0}),
        ("no overlap", {"overlap": 0.0}),
        ("heavy overlap", {"overlap": 0.8}),
        ("upright", {"droop_deg": 0.0}),
        ("thin leaves", {"thickness": 1.2}),
    ],
)
def test_leaf_collar_is_one_watertight_solid_at_every_extreme(label, kwargs):
    args = dict(
        ring_radius=45.0,
        leaf_length=28.0,
        leaf_width=20.0,
        count=16,
        overlap=0.35,
        droop_deg=44.0,
        thickness=2.4,
    )
    args.update(kwargs)
    collar = forge_lib.leaf_collar(**args)
    assert collar.is_valid, label
    assert collar.volume > 0.0
    assert len(collar.solids()) == 1, f"{label}: the collar came apart"
    assert collar.bounding_box().min.Z == pytest.approx(0.0, abs=1e-6)
    assert _thinnest(collar) >= MIN_WALL, label


@pytest.mark.parametrize(
    "label, kwargs",
    [
        ("defaults", {}),
        ("jitter", {"jitter": 0.8, "seed": 11}),
        ("many leaves", {"count": 26, "leaf_length": 22.0}),
    ],
)
def test_leaf_collar_in_the_droop_window_has_no_overhang_but_its_rim(label, kwargs):
    """44 deg: the leaf undersides AND their tip lands are both inside the limit."""
    args = dict(
        ring_radius=45.0,
        leaf_length=28.0,
        leaf_width=20.0,
        count=16,
        overlap=0.35,
        droop_deg=44.0,
        thickness=2.4,
    )
    args.update(kwargs)
    collar = forge_lib.leaf_collar(**args)
    # The only thing over the limit is the band's bottom rim, which is a flat
    # annulus -- exactly 90 degrees, and exactly what the plan names.
    assert _steepest_overhang(collar) == pytest.approx(90.0, abs=1e-6), label


def test_leaf_collar_is_reproducible_from_its_seed():
    first = forge_lib.leaf_collar(45.0, 26.0, 20.0, 14, jitter=0.7, seed=42)
    again = forge_lib.leaf_collar(45.0, 26.0, 20.0, 14, jitter=0.7, seed=42)
    other = forge_lib.leaf_collar(45.0, 26.0, 20.0, 14, jitter=0.7, seed=43)
    assert first.volume == pytest.approx(again.volume, rel=1e-9)
    assert first.volume != pytest.approx(other.volume, rel=1e-6)


def test_leaf_collar_bore_takes_the_cylinder_it_says_it_will():
    from build123d import Cylinder, Pos

    plan = forge_lib.leaf_collar_plan(30.0, 22.0, 16.0, 12)
    collar = forge_lib.leaf_collar(30.0, 22.0, 16.0, 12)
    post = Pos(0, 0, plan["height_mm"] / 2.0) * Cylinder(
        radius=30.0, height=plan["height_mm"] * 2.0
    )
    # A slide fit means the post does not touch the collar anywhere.
    assert (collar & post).volume == pytest.approx(0.0, abs=1e-6)


def test_petal_crown_geometry_is_genuinely_support_free():
    crown = forge_lib.petal_crown(45.0, 26.0, 20.0, 14, jitter=0.5, seed=2)
    assert crown.is_valid
    assert len(crown.solids()) == 1
    assert _steepest_overhang(crown) <= OVERHANG
    assert _thinnest(crown) >= MIN_WALL


def test_scale_band_rows_stay_one_solid():
    band = forge_lib.scale_band(45.0, 14.0, 20.0, 16, rows=3, droop_deg=44.0)
    assert band.is_valid
    assert len(band.solids()) == 1
    assert _thinnest(band) >= MIN_WALL
    assert _steepest_overhang(band) <= OVERHANG


@pytest.mark.parametrize(
    "label, kwargs",
    [
        ("defaults", {}),
        ("full jitter", {"jitter": 1.0, "seed": 7}),
        ("thin leaves", {"thickness": 1.2}),
        ("max cup", {"cup": forge_lib.MAX_CUP_RATIO}),
        ("short wide", {"count": 22, "leaf_length": 16.0}),
    ],
)
def test_cupped_collar_is_one_watertight_solid_that_measures_its_own_wall(label, kwargs):
    """The claim the cup exists to make good: puffy AND min_wall-safe.

    The section is never thinner than ``thickness`` -- it is ``thickness`` at
    the element's edges and ``thickness + rise`` at its crown -- so the wall
    holds by construction, not by being measured afterwards.
    """
    args = dict(
        ring_radius=45.0,
        leaf_length=26.0,
        leaf_width=20.0,
        count=16,
        overlap=0.35,
        droop_deg=44.0,
        thickness=2.6,
        jitter=0.35,
        seed=3,
        cup=0.32,
    )
    args.update(kwargs)
    collar = forge_lib.leaf_collar(**args)
    assert collar.is_valid, label
    assert len(collar.solids()) == 1, f"{label}: the collar came apart"
    assert collar.bounding_box().min.Z == pytest.approx(0.0, abs=1e-6)
    assert _thinnest(collar) >= MIN_WALL, label
    # And it never measures LESS than the element it was asked for, either.
    plan = forge_lib.leaf_collar_plan(**args)
    assert _thinnest(collar) >= min(plan["thickness_mm"], MIN_WALL) - 1e-3, label


def test_a_cupped_element_is_thicker_at_its_crown_than_at_its_edges():
    """It really is arched: the section is a chord, not a slab."""
    from build123d import Axis

    plan = forge_lib.leaf_collar_plan(45.0, 26.0, 20.0, 16, thickness=2.6, cup=0.32)
    flat = forge_lib._element_solid(
        26.0, plan["width_mm"], plan["tip_land_mm"], plan["root_width_mm"],
        plan["embed_mm"], "leaf", 2.6, 0.0,
    )
    cupped = forge_lib._element_solid(
        26.0, plan["width_mm"], plan["tip_land_mm"], plan["root_width_mm"],
        plan["embed_mm"], "leaf", 2.6, plan["cup"],
        plan["cup_rake_tan"], plan["cup_ascent_tan"],
    )
    assert cupped.is_valid and len(cupped.solids()) == 1
    # The back stays flat on Z = 0; only the front rises.
    assert cupped.bounding_box().min.Z == pytest.approx(0.0, abs=1e-6)
    assert cupped.bounding_box().max.Z > flat.bounding_box().max.Z + 1.0
    assert cupped.bounding_box().max.Z == pytest.approx(
        2.6 + plan["cup_rise_mm"], rel=0.02
    )
    # Arched, not merely taller: it holds more material over the same footprint.
    assert cupped.volume > flat.volume
    # Its widest section is still the outline's, so the collar's arithmetic holds.
    assert cupped.bounding_box().size.X == pytest.approx(
        flat.bounding_box().size.X, rel=0.01
    )
    assert cupped.faces().sort_by(Axis.Z)[0].area == pytest.approx(
        flat.faces().sort_by(Axis.Z)[0].area, rel=0.02
    )


def test_a_cupped_collar_hangs_no_more_than_a_flat_one():
    """The droop window is untouched: the face pointing down is still flat."""
    args = dict(
        ring_radius=45.0, leaf_length=26.0, leaf_width=20.0, count=16,
        overlap=0.35, droop_deg=44.0, thickness=2.6,
    )
    flat = _steepest_overhang(forge_lib.leaf_collar(**args))
    cupped = _steepest_overhang(forge_lib.leaf_collar(**args, cup=0.32))
    # Both leave nothing but the band's own bottom rim, which is 90 deg flat and
    # disappears the moment the collar is unioned onto a base.
    assert flat == pytest.approx(90.0, abs=1e-6)
    assert cupped == pytest.approx(90.0, abs=1e-6)


def test_a_cupped_crown_is_still_support_free():
    crown = forge_lib.petal_crown(45.0, 26.0, 20.0, 14, cup=0.32, jitter=0.4, seed=2)
    assert crown.is_valid
    assert len(crown.solids()) == 1
    assert _steepest_overhang(crown) <= OVERHANG
    assert _thinnest(crown) >= MIN_WALL


def test_a_cupped_scale_band_keeps_its_rows_and_its_wall():
    """Cup and rows together: the third helper, and the one with two knobs."""
    band = forge_lib.scale_band(
        45.0, 14.0, 20.0, 16, rows=3, droop_deg=44.0, cup=0.32, jitter=0.3, seed=4
    )
    assert band.is_valid
    assert len(band.solids()) == 1
    assert _thinnest(band) >= MIN_WALL
    assert _steepest_overhang(band) <= OVERHANG


# ==========================================================================
# Density -- measured with rays, not looked at
# ==========================================================================


def _gap_azimuths(plan):
    """Halfway between each neighbouring pair of the FIRST rank's elements.

    Where the elements actually end up, jitter included -- the same hash
    ``_ornament_solid`` uses -- so the probe lands in the real gap and not in
    the one an un-jittered ring would have had.
    """
    first = []
    for index in range(plan["count"]):
        if index % plan["layers"]:
            continue
        phi = 360.0 * index / plan["count"]
        if plan["jitter"] > 0.0:
            phi += (
                0.30 * plan["jitter"]
                * forge_lib._noise(plan["seed"], index, 2)
                * 360.0 / plan["count"]
            )
        first.append(math.radians(phi))
    first.sort()
    return [
        0.5 * (a + b)
        for a, b in zip(first, first[1:] + [first[0] + 2.0 * math.pi])
    ]


def _shoulder_coverage(plan, solid, bands=7, top=0.03, bottom=0.33):
    """Do rays out of the axis, in the gaps, meet an element?

    The probe is horizontal and it leaves the band's own axis, so anything it
    can hit is element material: the band itself is higher up.  The band is
    ``height_mm - band_height_mm`` above the collar's lowest point, and the
    zone probed is the top third of the skirt below it -- the shoulder, where
    the elements are at their widest and where a gap is a hole in the collar
    right beside the band.  Further down every ring of drooping elements fans
    out; that is what a fur collar does, and it is not what this measures.
    """
    from service.runner import tessellate_shape

    vertices, triangles = tessellate_shape(solid)
    geometry = checks.MeshGeometry(vertices, triangles)
    low, _ = geometry.bounds()
    base = low[2]
    band_bottom = base + plan["height_mm"] - plan["band_height_mm"]
    skirt = band_bottom - base
    reach = 3.0 * plan["outer_radius_mm"] + 50.0

    hits = total = 0
    for phi in _gap_azimuths(plan):
        direction = (math.cos(phi), math.sin(phi), 0.0)
        for index in range(bands):
            frac = top + (bottom - top) * index / (bands - 1)
            origin = (0.0, 0.0, band_bottom - frac * skirt)
            total += 1
            for ia, ib, ic in geometry.triangles:
                distance = checks._ray_triangle(
                    origin,
                    direction,
                    geometry.vertices[ia],
                    geometry.vertices[ib],
                    geometry.vertices[ic],
                )
                if distance is not None and 1e-6 < distance <= reach:
                    hits += 1
                    break
    return hits, total


@pytest.mark.parametrize("label, cup", [("cupped", 0.32), ("flat", 0.0)])
def test_two_layers_close_the_gaps_the_first_rank_leaves(label, cup):
    """``layers=2`` is dense at the defaults, and it is the second rank doing it.

    The gaps between one rank's elements are two pitches wide and no element
    can be that wide, so a single rank ALWAYS shows the band between its
    elements.  The second rank sits in those gaps, and this measures whether it
    really covers them rather than hiding behind the band's own skirt -- which
    is what it did when the ranks were stepped straight outward.
    """
    args = dict(
        ring_radius=45.0, leaf_length=26.0, leaf_width=18.0, count=16,
        overlap=0.35, droop_deg=44.0, thickness=2.6, jitter=0.35, seed=3,
        clearance=-1.0, cup=cup,
    )
    plan = forge_lib.leaf_collar_plan(**args)
    hits, total = _shoulder_coverage(plan, forge_lib.leaf_collar(**args))
    assert total > 0
    assert hits / total >= 0.9, f"{label}: {hits}/{total} of the shoulder covered"

    one = dict(args, layers=1)
    lone_plan = forge_lib.leaf_collar_plan(**one)
    lone_hits, lone_total = _shoulder_coverage(lone_plan, forge_lib.leaf_collar(**one))
    assert lone_hits == 0, (
        f"{label}: one rank should show straight through its own gaps, "
        f"got {lone_hits}/{lone_total}"
    )


# ==========================================================================
# soft_body -- the geometry
# ==========================================================================


def test_soft_body_solid_is_one_watertight_body_flat_on_the_plate():
    body = forge_lib.soft_body(BOWL)
    assert body.is_valid
    assert len(body.solids()) == 1
    box = body.bounding_box()
    assert box.min.Z == pytest.approx(0.0, abs=1e-6)
    assert box.size.Z == pytest.approx(78.0)
    assert _thinnest(body) >= MIN_WALL
    assert _steepest_overhang(body) <= OVERHANG


@pytest.mark.parametrize("wall", [1.6, 3.0, 6.0])
def test_soft_body_hollow_keeps_the_wall_it_was_given(wall):
    body = forge_lib.soft_body(BOWL, wall=wall)
    assert body.is_valid
    assert len(body.solids()) == 1
    assert _thinnest(body) >= MIN_WALL
    # An open top: the rim is a land, and nothing is a ceiling over the cavity.
    assert _steepest_overhang(body) <= OVERHANG
    solid = forge_lib.soft_body(BOWL)
    assert body.volume < solid.volume


def test_soft_body_is_the_curve_its_plan_says_it_is():
    """Polygonised before it is revolved -- the law from :func:`_sample_curve`."""
    plan = forge_lib.soft_body_plan(BOWL)
    body = forge_lib.soft_body(BOWL)
    assert plan["segments"] > 4 * len(BOWL)
    assert body.bounding_box().size.X / 2.0 == pytest.approx(
        plan["max_radius_mm"], rel=1e-3
    )
    assert plan["max_radius_mm"] > max(r for r, _ in BOWL) - 1e-6


def test_soft_body_takes_a_bore_and_a_socket_like_any_other_solid():
    """The composition idiom the docstring promises, exercised."""
    from build123d import Pos, Rot

    plan = forge_lib.soft_body_plan(BOWL)
    spec = forge_lib.peg_spec(d=6.0, l=10.0)
    body = forge_lib.soft_body(BOWL)
    bored = body - Pos(0, 0, plan["height_mm"] + 0.5) * Rot(180, 0, 0) * (
        forge_lib.blunted_taper(9.0, 9.0, 30.0, role="cut")
    )
    seated = bored - Pos(20.0, 0.0, plan["height_mm"] + 0.5) * Rot(180, 0, 0) * (
        forge_lib.socket_for(spec, forge_lib.fit_tolerance("slide_fit"))
    )
    assert seated.is_valid
    assert len(seated.solids()) == 1
    assert seated.volume < bored.volume < body.volume
    assert _thinnest(seated) >= MIN_WALL


def test_a_soft_body_carries_a_cupped_collar_as_one_solid():
    """The composition the library is for: a soft body with fur round its waist."""
    from build123d import Pos

    plan = forge_lib.soft_body_plan(BOWL)
    args = dict(
        ring_radius=43.0, leaf_length=28.0, leaf_width=20.0, count=16,
        overlap=0.35, droop_deg=44.0, thickness=2.6, jitter=0.35, seed=3,
        clearance=-2.0, cup=0.32,
    )
    collar_plan = forge_lib.leaf_collar_plan(**args)
    part = forge_lib.soft_body(BOWL)
    part += Pos(0.0, 0.0, 44.0 - collar_plan["height_mm"]) * forge_lib.leaf_collar(
        **args
    )
    assert part.is_valid
    assert len(part.solids()) == 1, "the collar did not weld onto the body"
    assert _thinnest(part) >= MIN_WALL
    assert plan["height_mm"] > 0


def test_silhouette_part_is_a_prism_of_the_thickness_asked_for():
    ear = forge_lib.silhouette_part(EAR, 9.0, rounding=0.0)
    box = ear.bounding_box()
    assert box.min.Z == pytest.approx(0.0, abs=1e-6)
    assert box.size.Z == pytest.approx(9.0)
    assert _thinnest(ear) >= 9.0 - 1e-3
    assert _steepest_overhang(ear) == 0.0  # nothing but the bed-facing bottom


def test_silhouette_rounding_steps_down_and_reports_what_it_got():
    """A radius the kernel refuses is stepped down, not raised as an error."""
    ear = forge_lib.silhouette_part(EAR, 9.0, rounding=4.0)
    plan = ear.forge_silhouette_plan
    assert ear.is_valid
    assert plan["rounding_achieved_mm"] < 4.0
    assert plan["rounding_attempts"], "the attempts should be recorded"
    if plan["rounding_achieved_mm"] > 0.0:
        assert any("rounding" in note for note in plan["clamped"])
        # It really was rounded: a fillet removes material.
        square = forge_lib.silhouette_part(EAR, 9.0, rounding=0.0)
        assert ear.volume < square.volume


def test_silhouette_rounding_that_fits_is_applied_exactly():
    ear = forge_lib.silhouette_part(EAR, 9.0, rounding=1.5)
    assert ear.forge_silhouette_plan["rounding_achieved_mm"] == pytest.approx(1.5)
    assert ear.is_valid


def test_silhouette_taper_steps_down_rather_than_giving_up():
    tapered = forge_lib.silhouette_part(EAR, 9.0, taper=30.0)
    plan = tapered.forge_silhouette_plan
    assert tapered.is_valid
    assert 0.0 < plan["taper_deg"] < 30.0
    assert any("taper" in note for note in plan["clamped"])
    # Thinner at the top is the whole point.
    from build123d import Axis

    faces = tapered.faces().sort_by(Axis.Z)
    assert faces[-1].area < faces[0].area


def test_silhouette_taper_is_support_free_and_still_measures_its_thickness():
    tapered = forge_lib.silhouette_part(EAR, 9.0, taper=5.0)
    assert _steepest_overhang(tapered) == 0.0
    assert _thinnest(tapered) >= MIN_FEATURE


def test_silhouette_peg_mates_the_socket_cut_from_the_same_spec():
    """The peg/socket promise, on an ornament part: reuse the appendage test."""
    from build123d import Box, Pos, Rot

    spec = forge_lib.peg_spec(d=6.0, l=10.0)
    tolerance = forge_lib.fit_tolerance("slide_fit")

    ear = forge_lib.silhouette_part(EAR, 9.0, rounding=0.0, peg=spec)
    plan = ear.forge_silhouette_plan
    assert plan["peg"] is not None

    # The peg sticks out below the outline, along -Y, at mid-thickness.
    box = ear.bounding_box()
    assert box.min.Y < min(p[1] for p in EAR) - 5.0

    # A block with the matching socket bored into it, seated exactly where the
    # ear plugs in: the two must interfere nowhere.  Same transform as the peg,
    # which is the whole point of both coming from one spec.
    # The mating face is the outline's own bottom edge: ``l`` sticks out of it,
    # and the extra ``embed`` is buried in the blade, not in the socket.
    peg_x, peg_y = plan["peg"]["at_mm"]
    mount = Pos(peg_x, peg_y, 4.5) * Rot(90.0, 0.0, 0.0)
    base = Pos(peg_x, peg_y - 20.0, 4.5) * Box(40.0, 40.0, 9.0)
    solid_block = (base & ear).volume
    assert solid_block > 200.0, "the peg should run well into the block"

    seated = base - mount * forge_lib.socket_for(spec, tolerance)
    left_over = (seated & ear).volume
    # The socket swallows the peg whole; what is left is the outline's own
    # spline dipping a hair below its bottom edge, which is under 1% of it.
    assert left_over < 0.01 * solid_block

    # A press fit is tighter than a slide fit, and both still take the peg.
    press = forge_lib.fit_tolerance("press_fit")
    tight = (base - mount * forge_lib.socket_for(spec, press)) & ear
    assert tight.volume < 0.01 * solid_block
    assert forge_lib.socket_for(spec, tolerance).volume > forge_lib.socket_for(
        spec, press
    ).volume


# ==========================================================================
# The sample, through the real checks
# ==========================================================================

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def kernel(client):
    health = client.get("/health").json()
    if not health.get("build123d"):
        pytest.skip("build123d is not installed in the service environment")
    return health


@pytest.fixture(scope="module")
def sample_source() -> str:
    return (SAMPLES / "eevee_style_bowl_base.py").read_text(encoding="utf-8")


def _statuses(body):
    return {check["name"]: check["status"] for check in body["checks"]}


def test_the_sample_parses_and_declares_a_part_selector(client, sample_source):
    response = client.post("/parse_params", json={"script": sample_source})
    assert response.status_code == 200, response.text
    params = response.json()["params"]
    assert params["part"]["unit"] == "count"
    assert (params["part"]["min"], params["part"]["max"]) == (0, 2)


def test_the_sample_base_passes_every_check_with_the_collar_on_it(
    client, kernel, sample_source
):
    """The claim the sample exists to make: function plus character, all four."""
    response = client.post("/check", json={"script": sample_source, "overrides": {}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert _statuses(body) == {
        "bed_fit": "pass",
        "min_wall": "pass",
        "overhangs": "pass",
        "watertight": "pass",
    }, body["checks"]


@pytest.mark.parametrize("part, name", [(1, "ear"), (2, "tail")])
def test_the_sample_appendages_are_clean_but_for_their_pegs(
    client, kernel, sample_source, part, name
):
    response = client.post(
        "/check", json={"script": sample_source, "overrides": {"part": part}}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = _statuses(body)
    assert statuses["bed_fit"] == "pass"
    assert statuses["min_wall"] == "pass"
    assert statuses["watertight"] == "pass"
    # The only thing hanging is the underside of the peg -- a horizontal
    # cylinder, however it is drawn -- and it is small.
    overhangs = [c for c in body["checks"] if c["name"] == "overhangs"][0]
    current = overhangs["data"]["orientations"][
        overhangs["data"]["current_orientation"]
    ]
    assert current["unsupported_area_mm2"] < 150.0, name


@pytest.mark.parametrize(
    "label, overrides",
    [
        ("no jitter", {"collar_jitter": 0.0}),
        ("full jitter", {"collar_jitter": 1.0, "collar_seed": 9}),
        ("small bowl", {"bowl_diameter": 3.0, "bowl_height": 1.5, "wall": 2.5}),
        ("big bowl", {"bowl_diameter": 6.0, "bowl_height": 4.0, "wall": 10.0}),
        ("sparse collar", {"collar_leaves": 8, "collar_overlap": 0.0}),
        ("dense collar", {"collar_leaves": 26, "collar_length": 14.0}),
        ("flat collar", {"collar_cup": 0.0}),
        ("deepest cup", {"collar_cup": 0.42}),
        ("straight body", {"body_swell": 0.0}),
        ("fullest body", {"body_swell": 0.24}),
        ("upright collar", {"collar_droop": 15.0}),
    ],
)
def test_the_sample_base_stays_printable_across_its_range(
    client, kernel, sample_source, label, overrides
):
    response = client.post(
        "/check", json={"script": sample_source, "overrides": overrides}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = _statuses(body)
    assert statuses["bed_fit"] == "pass", label
    # Never a fail across the declared range.  A warn is allowed at the
    # extremes -- a 10 mm wall makes the arcaded plinth's sill measure 0.9 mm,
    # which is between the minimum wall and the minimum feature.
    assert statuses["min_wall"] != "fail", (label, body["checks"])
    assert statuses["watertight"] == "pass", label


def test_the_sample_declares_the_two_knobs_that_make_it_soft(client, sample_source):
    """The cup and the swell are parameters, not decisions baked into the file."""
    params = client.post(
        "/parse_params", json={"script": sample_source}
    ).json()["params"]
    assert params["collar_cup"]["unit"] == "ratio"
    assert params["collar_cup"]["value"] > 0.0
    assert params["collar_cup"]["max"] <= forge_lib.MAX_CUP_RATIO
    assert params["body_swell"]["unit"] == "ratio"
    assert params["body_swell"]["value"] > 0.0


def test_the_ornament_helpers_are_reachable_from_inside_a_script(client, kernel):
    """The install mechanism reaches the new names, not just the old ones."""
    script = """
from build123d import *
PARAMS = {"count": {"value": 12, "unit": "count", "min": 6, "max": 20}}
def build(p):
    # no `import forge_lib` on purpose: the name is pre-bound in the namespace
    plan = forge_lib.leaf_collar_plan(20.0, 14.0, 10.0, int(p["count"]),
                                      droop_deg=44.0)
    assert plan["tip_land_mm"] >= 1.0
    return forge_lib.leaf_collar(20.0, 14.0, 10.0, int(p["count"]),
                                 droop_deg=44.0, clearance=-1.0) + (
        Pos(0, 0, plan["height_mm"] / 2) * Cylinder(20.0, plan["height_mm"]))
"""
    response = client.post("/check", json={"script": script})
    assert response.status_code == 200, response.text
    statuses = _statuses(response.json())
    assert statuses["min_wall"] == "pass"
    assert statuses["watertight"] == "pass"


def test_a_hand_rolled_ear_fails_where_the_helper_passes(client, kernel):
    """The failure the ornament helpers exist for, both sides of it."""
    raw = """
from build123d import *
PARAMS = {"t": {"value": 9.0, "unit": "mm"}}
def build(p):
    # A "silhouette" splined and extruded straight, the obvious way to do it.
    pts = [(0,0,0),(11,6,0),(15,24,0),(12,46,0),(4,62,0),(0,70,0),
           (-6,58,0),(-13,34,0),(-14,12,0),(-8,2,0)]
    return extrude(Plane.XY * make_face(Spline(*pts, periodic=True)), amount=p["t"])
"""
    fixed = """
from build123d import *
import forge_lib
PARAMS = {"t": {"value": 9.0, "unit": "mm"}}
def build(p):
    return forge_lib.silhouette_part(
        [[0,0],[11,6],[15,24],[12,46],[4,62],[0,70],
         [-6,58],[-13,34],[-14,12],[-8,2]], p["t"], rounding=1.5)
"""
    _run_raw_vs_fixed(client, raw, fixed)


def test_a_soft_body_is_reachable_and_printable_from_inside_a_script(client, kernel):
    """The new body vocabulary, through the real endpoint."""
    script = """
from build123d import *
PARAMS = {"wall": {"value": 3.0, "unit": "mm", "min": 1.0, "max": 8.0}}
def build(p):
    points = [(30, 0), (40, 10), (45, 26), (44, 46), (38, 66), (33, 78)]
    plan = forge_lib.soft_body_plan(points, wall=p["wall"])
    assert plan["hollow"] and plan["support_free"]
    return forge_lib.soft_body(points, wall=p["wall"])
"""
    response = client.post("/check", json={"script": script})
    assert response.status_code == 200, response.text
    assert _statuses(response.json()) == {
        "bed_fit": "pass",
        "min_wall": "pass",
        "overhangs": "pass",
        "watertight": "pass",
    }


def _run_raw_vs_fixed(client, raw, fixed):
    raw_wall = [
        c
        for c in client.post("/check", json={"script": raw}).json()["checks"]
        if c["name"] == "min_wall"
    ][0]
    assert raw_wall["status"] == "fail"
    assert raw_wall["data"]["min_measured_thickness_mm"] < MIN_WALL

    fixed_wall = [
        c
        for c in client.post("/check", json={"script": fixed}).json()["checks"]
        if c["name"] == "min_wall"
    ][0]
    assert fixed_wall["status"] == "pass", fixed_wall["details"]
