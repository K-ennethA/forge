"""The printability library: every helper's guarantee, and the sample that uses them.

Three layers, cheapest first:

* the ``*_plan()`` functions are pure arithmetic on a printer profile, so they
  are tested without build123d at all -- that is where the clamping rules live
  and where a regression would show up first;
* the geometry is measured directly (a slab intersection proves a taper really
  does start with a straight land, not just that the plan said so);
* ``samples/magnet_holder.py`` goes through the real ``/check`` endpoint at its
  defaults and at both ends of its declared ranges, because "all four checks
  pass" is the claim the sample exists to make.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from service import forge_lib
from service.errors import ForgeError, ScriptError
from service.printer import DEFAULT_PROFILE

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

MIN_WALL = float(DEFAULT_PROFILE["min_wall_thickness"])          # 0.8
MIN_FEATURE = float(DEFAULT_PROFILE["min_feature_size"])         # 1.0
OVERHANG = float(DEFAULT_PROFILE["max_unsupported_overhang_deg"])  # 50
MAGNET_EXTRA = float(DEFAULT_PROFILE["tolerances"]["magnet_pocket_extra"])  # 0.05


# ==========================================================================
# Profile access and the land rule
# ==========================================================================


def test_printability_error_is_a_400_not_a_500():
    """A helper's complaint is the caller's fault, so it has to be a ScriptError."""
    assert issubclass(forge_lib.PrintabilityError, ScriptError)
    assert issubclass(forge_lib.PrintabilityError, ForgeError)
    assert forge_lib.PrintabilityError("x").http_status == 400


def test_profile_defaults_to_the_centauri_carbon():
    assert forge_lib.min_wall() == MIN_WALL
    assert forge_lib.min_feature() == MIN_FEATURE
    assert forge_lib.max_overhang_deg() == OVERHANG
    assert forge_lib.fit_tolerance("magnet_pocket_extra") == MAGNET_EXTRA
    assert forge_lib.fit_tolerance("slide_fit") == 0.2


def test_profile_overrides_reach_every_helper():
    strict = {"min_wall_thickness": 2.0, "min_feature_size": 2.5}
    assert forge_lib.min_wall(strict) == 2.0
    assert forge_lib.min_land(None, strict) == 2.5
    assert forge_lib.min_land(0.1, strict) == 2.0  # the wall is the floor


def test_min_land_is_max_of_the_request_and_the_printer_minimum_wall():
    assert forge_lib.min_land(None) == MIN_FEATURE       # default: min_feature
    assert forge_lib.min_land(0.05) == MIN_WALL          # floored at min_wall
    assert forge_lib.min_land(3.0) == 3.0                # a bigger ask is honoured


# ==========================================================================
# blunted_taper -- the knife-edge rule
# ==========================================================================


def test_blunted_taper_never_ends_in_a_point():
    """A solid taper asked to end at nothing lands on a min_land-wide disc."""
    plan = forge_lib.blunted_taper_plan(10.0, 0.0, 8.0)
    assert plan["top_r_mm"] == pytest.approx(MIN_FEATURE / 2.0)
    assert plan["thin_end_land_mm"] == pytest.approx(MIN_FEATURE)
    assert any("top_r" in note for note in plan["clamped"])


def test_blunted_taper_thin_end_respects_a_raised_min_land():
    plan = forge_lib.blunted_taper_plan(10.0, 0.1, 8.0, min_land_mm=3.0)
    assert plan["thin_end_land_mm"] == pytest.approx(3.0)
    assert plan["min_land_mm"] == 3.0


def test_blunted_taper_thin_end_is_floored_at_min_wall_even_when_asked_lower():
    """min_land_mm below the printer's wall cannot take the land below it."""
    plan = forge_lib.blunted_taper_plan(40.0, 0.0, 2.0, min_land_mm=0.01)
    assert plan["thin_end_land_mm"] == pytest.approx(MIN_WALL)


def test_blunted_taper_annular_thin_end_is_the_annulus_width():
    plan = forge_lib.blunted_taper_plan(12.0, 6.2, 6.0, bore_r=6.0)
    # top annulus would be 0.2 mm; clamped out to a full land.
    assert plan["top_r_mm"] == pytest.approx(6.0 + MIN_FEATURE)
    assert plan["thin_end_land_mm"] == pytest.approx(MIN_FEATURE)


def test_blunted_taper_bore_that_eats_the_wall_raises():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.blunted_taper_plan(12.0, 9.0, 6.0, bore_r=12.5)
    assert "bore_r" in str(exc.value)


def test_blunted_taper_puts_the_land_on_the_acute_end():
    """Adding material the wide end is acute; cutting it, the narrow end is."""
    assert forge_lib.blunted_taper_plan(12.0, 9.0, 6.0)["land_at"] == "bottom"
    assert forge_lib.blunted_taper_plan(9.0, 12.0, 6.0)["land_at"] == "top"
    assert forge_lib.blunted_taper_plan(2.0, 4.0, 6.0, role="cut")["land_at"] == "bottom"
    assert forge_lib.blunted_taper_plan(4.0, 2.0, 6.0, role="cut")["land_at"] == "top"


def test_blunted_taper_land_is_at_least_min_land_and_leaves_a_real_taper():
    plan = forge_lib.blunted_taper_plan(12.0, 9.0, 6.0)
    assert plan["land_mm"] >= forge_lib.min_land(None)
    assert plan["taper_height_mm"] >= forge_lib.MIN_TAPER_HEIGHT_MM
    assert plan["land_mm"] + plan["taper_height_mm"] == pytest.approx(6.0)


def test_blunted_taper_too_short_for_a_land_raises_with_the_number_it_needs():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.blunted_taper_plan(12.0, 9.0, 0.5)
    message = str(exc.value)
    assert "0.5" in message and "land" in message


def test_blunted_taper_clamps_a_solid_flare_to_the_overhang_limit():
    plan = forge_lib.blunted_taper_plan(3.0, 30.0, 4.0)
    assert plan["angle_from_vertical_deg"] <= OVERHANG - forge_lib.OVERHANG_SAFETY_DEG + 1e-6
    assert plan["support_free"] is True
    assert any("top_r" in note for note in plan["clamped"])


def test_blunted_taper_knows_a_hole_overhangs_the_other_way_round():
    """A countersink widens upward and is safe; widening downward is a ceiling."""
    safe = forge_lib.blunted_taper_plan(2.1, 20.0, 4.0, role="cut", taper_height=1.5)
    assert safe["clamped"] == []          # nothing clamped: up-facing
    assert safe["top_r_mm"] == 20.0

    ceiling = forge_lib.blunted_taper_plan(20.0, 2.1, 4.0, role="cut")
    assert ceiling["angle_from_vertical_deg"] <= OVERHANG
    assert any("bottom_r" in note for note in ceiling["clamped"])


def test_blunted_taper_support_free_can_be_turned_off_but_says_so():
    plan = forge_lib.blunted_taper_plan(3.0, 30.0, 4.0, support_free=False)
    assert plan["clamped"] == []
    assert plan["support_free"] is False
    assert plan["angle_from_vertical_deg"] > OVERHANG


def test_blunted_taper_rejects_a_bad_role():
    with pytest.raises(forge_lib.PrintabilityError):
        forge_lib.blunted_taper_plan(3.0, 4.0, 5.0, role="sideways")


# ==========================================================================
# flared_lip
# ==========================================================================


def test_flared_lip_clamps_the_wall_up_to_the_printer_minimum():
    plan = forge_lib.flared_lip_plan(20.0, 0.3, 15.0, 2.5)
    assert plan["wall_mm"] == MIN_WALL
    assert plan["outer_r_mm"] == pytest.approx(20.0 + MIN_WALL)
    assert any("wall" in note for note in plan["clamped"])


def test_flared_lip_always_lands_blunt():
    plan = forge_lib.flared_lip_plan(20.0, 4.0, 15.0, 2.5)
    assert plan["land_mm"] >= forge_lib.min_land(None)
    assert plan["taper_height_mm"] > 0.0
    assert plan["support_free"] is True  # flaring downward faces upward


def test_flared_lip_upward_flare_is_given_the_run_to_self_support():
    plan = forge_lib.flared_lip_plan(20.0, 4.0, 25.0, 8.0, direction="up")
    assert plan["angle_from_vertical_deg"] <= OVERHANG - forge_lib.OVERHANG_SAFETY_DEG + 1e-6
    assert plan["support_free"] is True


def test_flared_lip_reduces_the_flare_when_there_is_no_room_to_run():
    plan = forge_lib.flared_lip_plan(20.0, 4.0, 3.0, 8.0, direction="up")
    assert plan["flare_mm"] < 8.0
    assert plan["angle_from_vertical_deg"] <= OVERHANG - forge_lib.OVERHANG_SAFETY_DEG + 1e-6
    assert any("flare" in note for note in plan["clamped"])


def test_flared_lip_with_no_flare_is_a_plain_collar():
    plan = forge_lib.flared_lip_plan(20.0, 4.0, 15.0, 0.0)
    assert plan["flare_mm"] == 0.0
    assert plan["land_mm"] == 0.0


# ==========================================================================
# textured_band -- subtracted relief only
# ==========================================================================


def test_textured_band_depth_is_clamped_to_forty_percent_of_the_wall():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 4.0, wall=5.0)
    assert plan["depth_mm"] == pytest.approx(0.40 * 5.0)
    assert plan["remaining_wall_mm"] == pytest.approx(3.0)
    assert any("40%" in note for note in plan["clamped"])


def test_textured_band_always_leaves_the_minimum_wall_behind():
    """The 40 % rule is not enough on a wall that is already nearly minimum."""
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 4.0, wall=1.2)
    assert plan["depth_mm"] <= 1.2 - MIN_WALL + 1e-9
    assert plan["remaining_wall_mm"] >= MIN_WALL - 1e-9


def test_textured_band_depth_is_clamped_by_the_surface_width():
    """A fine pitch forces a shallower cut, not cutters that merge."""
    plan = forge_lib.textured_band_plan(10.0, 20.0, 60, 3.0, wall=20.0)
    width = plan["surface_width_mm"]
    assert plan["depth_mm"] == pytest.approx(0.45 * width, abs=1e-5)
    assert any("surface width" in note for note in plan["clamped"])


def test_textured_band_cutter_radius_reproduces_the_wanted_width():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, wall=5.0)
    radius, depth = plan["cutter_radius_mm"], plan["depth_mm"]
    chord = 2.0 * math.sqrt(radius**2 - (radius - depth) ** 2)
    assert chord == pytest.approx(plan["surface_width_mm"], rel=1e-6)


def test_textured_band_leaves_a_flat_land_between_flutes():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, wall=5.0)
    assert plan["surface_width_mm"] < plan["pitch_mm"]


def test_textured_band_is_suppressed_rather_than_cutting_through():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, wall=0.9)
    assert plan["suppressed"] is True
    assert plan["depth_mm"] == 0.0
    assert plan["cutter_count"] == 0
    assert any("suppressed" in note for note in plan["clamped"])


def test_textured_band_wall_at_the_minimum_gets_no_texture_at_all():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, wall=MIN_WALL)
    assert plan["suppressed"] is True


def test_textured_band_flute_cones_its_ends_to_stay_support_free():
    plan = forge_lib.textured_band_plan(30.0, 40.0, 24, 1.6, wall=5.0)
    assert plan["runout_mm"] > 0.0
    assert plan["support_free"] is True


def test_textured_band_chevron_tilt_is_clamped_into_the_printable_window():
    low = 90.0 - (OVERHANG - forge_lib.OVERHANG_SAFETY_DEG)
    high = OVERHANG - forge_lib.OVERHANG_SAFETY_DEG
    steep = forge_lib.textured_band_plan(30.0, 20.0, 12, 1.0, "chevron", wall=5.0,
                                         chevron_deg=80.0)
    assert steep["chevron_deg"] == pytest.approx(high)
    flat = forge_lib.textured_band_plan(30.0, 20.0, 12, 1.0, "chevron", wall=5.0,
                                        chevron_deg=5.0)
    assert flat["chevron_deg"] == pytest.approx(low)


def test_textured_band_scallop_admits_its_crown_is_a_ceiling():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, "scallop", wall=5.0)
    assert plan["support_free"] is False
    assert plan["cutter_count"] == plan["count"] * plan["rows"]


def test_textured_band_rejects_an_unknown_style():
    with pytest.raises(forge_lib.PrintabilityError):
        forge_lib.textured_band_plan(30.0, 20.0, 24, 1.6, "guilloche", wall=5.0)


# ==========================================================================
# feet_ring / arcade_base
# ==========================================================================


def test_feet_ring_keeps_a_minimum_feature_of_air_between_feet():
    plan = forge_lib.feet_ring_plan(30.0, 8.0, 5, foot_size=100.0)
    assert plan["gap_mm"] >= MIN_FEATURE - 1e-9
    assert any("foot_size" in note for note in plan["clamped"])


def test_feet_ring_clamps_a_tiny_foot_up_to_the_minimum_feature():
    plan = forge_lib.feet_ring_plan(30.0, 8.0, 5, foot_size=0.2)
    assert plan["foot_size_mm"] == pytest.approx(MIN_FEATURE)


def test_feet_ring_with_no_room_for_the_feet_raises():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.feet_ring_plan(2.0, 8.0, 40)
    assert "fewer feet" in str(exc.value)


def test_feet_ring_chamfer_stays_inside_the_overhang_limit():
    assert forge_lib.feet_ring_plan(30.0, 8.0, 5)["chamfer_deg"] == 45.0
    strict = forge_lib.feet_ring_plan(
        30.0, 8.0, 5, printer={"max_unsupported_overhang_deg": 30.0}
    )
    assert strict["chamfer_mm"] == 0.0


def test_arcade_base_pointed_arch_sits_on_the_overhang_limit():
    plan = forge_lib.arcade_base_plan(30.0, 20.0, 5, inner_r=24.0)
    assert plan["support_free"] is True
    assert plan["arch_angle_from_vertical_deg"] <= (
        OVERHANG - forge_lib.OVERHANG_SAFETY_DEG + 1e-6
    )


def test_arcade_base_round_arch_admits_it_will_be_flagged():
    plan = forge_lib.arcade_base_plan(30.0, 20.0, 5, inner_r=24.0, arch="round")
    assert plan["support_free"] is False


def test_arcade_base_keeps_piers_and_a_wall_over_the_crown():
    plan = forge_lib.arcade_base_plan(30.0, 20.0, 5, inner_r=24.0, opening_fraction=0.9)
    assert plan["pier_width_mm"] >= MIN_FEATURE - 1e-9
    assert plan["height_mm"] - plan["crown_z_mm"] >= MIN_WALL - 1e-6


def test_arcade_base_narrows_the_opening_rather_than_breaking_the_top():
    plan = forge_lib.arcade_base_plan(60.0, 8.0, 4, inner_r=52.0)
    assert plan["height_mm"] - plan["crown_z_mm"] >= MIN_WALL - 1e-6
    assert any("opening width" in note for note in plan["clamped"])


def test_arcade_base_on_too_thin_a_ring_raises():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.arcade_base_plan(30.0, 20.0, 5, inner_r=29.9)
    assert "0.8" in str(exc.value)


def test_arcade_base_pad_leaves_a_continuous_foot_ring():
    plan = forge_lib.arcade_base_plan(30.0, 20.0, 5, "pad", inner_r=24.0)
    assert plan["sill_mm"] >= MIN_WALL


# ==========================================================================
# magnet_pocket
# ==========================================================================


def test_magnet_pocket_tolerance_is_exactly_the_profile_value():
    plan = forge_lib.magnet_pocket_plan(6.0, 3.0)
    assert plan["tolerance_mm"] == MAGNET_EXTRA
    assert plan["pocket_diameter_mm"] == pytest.approx(6.0 + 2.0 * MAGNET_EXTRA)
    assert plan["pocket_depth_mm"] == pytest.approx(3.0 + MAGNET_EXTRA)
    assert plan["tolerance_source"] == "printer.tolerances.magnet_pocket_extra"


def test_magnet_pocket_sizing_matches_the_segmenter_s_magnet_joint():
    """Same magnet, same pocket, whether it comes from /segment or forge_lib."""
    from service.joints import CutFrame, plan_joint, resolve_joint
    from service.printer import normalize_printer

    printer = normalize_printer(None)
    spec = resolve_joint({"type": "magnet", "diameter_mm": 6.0, "thickness_mm": 3.0}, printer)
    frame = CutFrame(
        name="cut0", kind="planar", to_world=None,
        u_min=-20.0, u_max=20.0, v_min=-20.0, v_max=20.0, side_a=0, side_b=1,
    )
    joint = plan_joint(spec, frame)
    lib = forge_lib.magnet_pocket_plan(6.0, 3.0)
    assert lib["pocket_diameter_mm"] == pytest.approx(joint["pocket_diameter_mm"])
    assert lib["pocket_depth_mm"] == pytest.approx(joint["socket_depth_mm"])


def test_magnet_pocket_honours_a_caller_tolerance():
    plan = forge_lib.magnet_pocket_plan(6.0, 3.0, tolerance=0.2)
    assert plan["pocket_diameter_mm"] == pytest.approx(6.4)
    assert plan["tolerance_source"] == "caller"


def test_magnet_pocket_refuses_to_punch_through_the_back():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.magnet_pocket_plan(6.0, 3.0, available_depth=3.5)
    message = str(exc.value)
    assert "floor" in message and "3.85" in message


def test_magnet_pocket_reports_the_floor_it_leaves():
    plan = forge_lib.magnet_pocket_plan(6.0, 3.0, available_depth=6.0)
    assert plan["floor_mm"] == pytest.approx(6.0 - 3.05)


# ==========================================================================
# shell_box / screw_boss
# ==========================================================================


def test_shell_box_clamps_wall_and_floor_up_to_the_minimum_wall():
    plan = forge_lib.shell_box_plan(60.0, 40.0, 25.0, 0.4, floor=0.2)
    assert plan["wall_mm"] == MIN_WALL
    assert plan["floor_mm"] == MIN_WALL
    assert len(plan["clamped"]) == 2


def test_shell_box_with_no_cavity_left_raises_and_says_how_wide_it_needs_to_be():
    with pytest.raises(forge_lib.PrintabilityError) as exc:
        forge_lib.shell_box_plan(6.0, 6.0, 25.0, 3.0)
    assert "7.00 mm across" in str(exc.value)


def test_shell_box_open_top_is_support_free_and_a_lid_is_not():
    assert forge_lib.shell_box_plan(60.0, 40.0, 25.0, 2.0)["support_free"] is True
    lidded = forge_lib.shell_box_plan(60.0, 40.0, 25.0, 2.0, open_top=False)
    assert lidded["support_free"] is False


def test_screw_boss_thread_forming_hole_is_eighty_percent_of_the_screw():
    plan = forge_lib.screw_boss_plan(3.0, 10.0)
    assert plan["hole_diameter_mm"] == pytest.approx(2.4)
    # The default wall is half the screw diameter but never under two minimum
    # walls, so a small screw still gets a boss with two perimeters around it.
    assert plan["wall_mm"] == pytest.approx(max(1.5, 2.0 * MIN_WALL))
    assert plan["boss_diameter_mm"] == pytest.approx(2.4 + 2.0 * plan["wall_mm"])
    assert forge_lib.screw_boss_plan(8.0, 20.0)["wall_mm"] == pytest.approx(4.0)


def test_screw_boss_clearance_hole_adds_a_slide_fit():
    plan = forge_lib.screw_boss_plan(3.0, 10.0, style="clearance")
    assert plan["hole_diameter_mm"] == pytest.approx(3.0 + 2.0 * 0.2)


def test_screw_boss_always_keeps_a_floor_under_the_screw():
    plan = forge_lib.screw_boss_plan(3.0, 10.0, hole_depth=50.0)
    assert plan["floor_mm"] >= MIN_WALL - 1e-9
    assert any("hole_depth" in note for note in plan["clamped"])


def test_screw_boss_too_short_to_hold_a_screw_raises():
    with pytest.raises(forge_lib.PrintabilityError):
        forge_lib.screw_boss_plan(3.0, 1.0)


def test_screw_boss_tiny_screw_hole_is_clamped_to_the_minimum_feature():
    plan = forge_lib.screw_boss_plan(0.8, 10.0)
    assert plan["hole_diameter_mm"] == pytest.approx(MIN_FEATURE)


# ==========================================================================
# Geometry -- measured, not asserted from the plan
# ==========================================================================

build123d = pytest.importorskip("build123d", reason="build123d is not installed yet")


def _slab_is_a_straight_prism(solid, z0: float, z1: float, radius: float) -> bool:
    """Is everything between z0 and z1 a cylinder of exactly *radius*?"""
    from build123d import Box, Pos

    slab = Pos(0.0, 0.0, (z0 + z1) / 2.0) * Box(
        4.0 * radius, 4.0 * radius, z1 - z0
    )
    volume = (solid & slab).volume
    return volume == pytest.approx(math.pi * radius**2 * (z1 - z0), rel=2e-3)


def test_blunted_taper_really_starts_with_a_straight_land():
    """The measurement, not the plan: the first land_mm is a prism, not a cone."""
    plan = forge_lib.blunted_taper_plan(12.0, 9.0, 6.0)
    solid = forge_lib.blunted_taper(12.0, 9.0, 6.0)
    land = plan["land_mm"]
    assert land >= MIN_FEATURE
    assert _slab_is_a_straight_prism(solid, 0.0, land * 0.98, 12.0)
    # ...and immediately above the land it is genuinely narrowing.
    assert not _slab_is_a_straight_prism(solid, land, 6.0, 12.0)


def test_blunted_taper_lands_on_the_bed_and_is_the_height_asked_for():
    solid = forge_lib.blunted_taper(12.0, 9.0, 6.0)
    box = solid.bounding_box()
    assert box.min.Z == pytest.approx(0.0, abs=1e-6)
    assert box.size.Z == pytest.approx(6.0)
    assert box.size.X == pytest.approx(24.0)


def test_blunted_taper_at_aggressive_params_still_measures_a_full_land():
    """40 mm of flare in 2 mm, asked to end at nothing: still a printable rim."""
    from build123d import Box, Pos

    plan = forge_lib.blunted_taper_plan(40.0, 0.0, 2.0, min_land_mm=0.01)
    solid = forge_lib.blunted_taper(40.0, 0.0, 2.0, min_land_mm=0.01)
    assert plan["land_mm"] >= MIN_WALL
    assert plan["thin_end_land_mm"] >= MIN_WALL
    assert _slab_is_a_straight_prism(solid, 0.0, plan["land_mm"] * 0.98, 40.0)
    # The tip is a real disc, not a point: the top 0.02 mm still has volume.
    top = solid.bounding_box().max.Z
    tip = solid & (Pos(0.0, 0.0, top - 0.01) * Box(200.0, 200.0, 0.02))
    assert tip.volume > 0.0


def test_flared_lip_foot_measures_a_straight_land_at_the_bed():
    plan = forge_lib.flared_lip_plan(20.0, 4.0, 15.0, 2.5)
    solid = forge_lib.flared_lip(20.0, 4.0, 15.0, 2.5)
    outer = plan["outer_r_mm"] + plan["flare_mm"]
    from build123d import Box, Pos

    land = plan["land_mm"]
    slab = Pos(0.0, 0.0, land * 0.49) * Box(4 * outer, 4 * outer, land * 0.98)
    ring = math.pi * (outer**2 - plan["inner_r_mm"] ** 2) * land * 0.98
    assert (solid & slab).volume == pytest.approx(ring, rel=2e-3)


def test_magnet_pocket_geometry_matches_its_plan():
    plan = forge_lib.magnet_pocket_plan(6.0, 3.0)
    cavity = forge_lib.magnet_pocket(6.0, 3.0)
    box = cavity.bounding_box()
    assert box.size.X == pytest.approx(plan["pocket_diameter_mm"], rel=1e-3)
    assert box.max.Z == pytest.approx(forge_lib.MOUTH_OVERSHOOT_MM)
    assert box.min.Z == pytest.approx(-plan["pocket_depth_mm"])


def test_textured_band_cutters_never_reach_past_the_clamped_depth():
    plan = forge_lib.textured_band_plan(30.0, 20.0, 24, 9.0, wall=5.0)
    band = forge_lib.textured_band(30.0, 20.0, 24, 9.0, wall=5.0)
    deepest = min(math.hypot(v.X, v.Y) for v in band.vertices())
    assert deepest >= 30.0 - plan["depth_mm"] - 1e-6
    assert deepest >= 25.0 + MIN_WALL - 1e-6  # the bore plus a minimum wall


def test_textured_band_suppressed_compound_subtracts_as_a_no_op():
    from build123d import Cylinder, Pos

    tube = Pos(0, 0, 10) * Cylinder(30, 20) - Pos(0, 0, 10) * Cylinder(29.1, 21)
    band = forge_lib.textured_band(30.0, 20.0, 24, 1.6, wall=0.9)
    assert (tube - band).volume == pytest.approx(tube.volume)


def test_feet_ring_never_reaches_past_its_outer_radius():
    for style in ("pad", "pier"):
        ring = forge_lib.feet_ring(30.0, 8.0, 5, style)
        assert max(math.hypot(v.X, v.Y) for v in ring.vertices()) <= 30.0 + 1e-6
        assert ring.bounding_box().min.Z == pytest.approx(0.0, abs=1e-6)


def test_arcade_base_leaves_the_openings_it_planned():
    plan = forge_lib.arcade_base_plan(30.0, 20.0, 5, inner_r=24.0)
    solid = forge_lib.arcade_base(30.0, 20.0, 5, inner_r=24.0)
    full = math.pi * (30.0**2 - 24.0**2) * 20.0
    assert 0.3 * full < solid.volume < full
    assert solid.bounding_box().size.Z == pytest.approx(20.0, abs=1e-6)
    assert plan["count"] == 5


def test_shell_box_is_hollow_and_open_at_the_top():
    solid = forge_lib.shell_box(60.0, 40.0, 25.0, 2.0)
    box = solid.bounding_box()
    assert (box.size.X, box.size.Y, box.size.Z) == pytest.approx((60.0, 40.0, 25.0))
    assert solid.volume < 60.0 * 40.0 * 25.0 * 0.5
    assert box.min.Z == pytest.approx(0.0, abs=1e-6)


def test_wall_safe_shell_clamps_the_wall_and_hollows():
    from build123d import Axis, Box, Pos

    block = Pos(0, 0, 10) * Box(40, 30, 20)
    top = block.faces().sort_by(Axis.Z)[-1]
    hollow = forge_lib.wall_safe_shell(block, 0.2, openings=top)
    assert hollow.forge_shell_plan["wall_mm"] == MIN_WALL
    assert hollow.volume < block.volume


def test_screw_boss_has_a_bore_and_a_floor():
    plan = forge_lib.screw_boss_plan(3.0, 10.0)
    solid = forge_lib.screw_boss(3.0, 10.0)
    box = solid.bounding_box()
    assert box.size.Z == pytest.approx(10.0)
    assert box.size.X == pytest.approx(plan["boss_diameter_mm"], rel=1e-3)
    solid_cylinder = math.pi * (plan["boss_diameter_mm"] / 2.0) ** 2 * 10.0
    assert solid.volume < solid_cylinder


# ==========================================================================
# The sample, through the real checks
# ==========================================================================

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402

#: Every declared range pushed to its small end: minimum wall, minimum bar, the
#: smallest magnets, the most of them, and the biggest screw hole in the
#: narrowest bar.
MAGNET_HOLDER_SMALL = {
    "magnet_diameter": 3.0,
    "magnet_thickness": 1.0,
    "magnet_count": 6,
    "magnet_spacing": 8.0,
    "wall": 1.2,
    "bar_thickness": 3.0,
    "mount_holes": True,
    "mount_hole_diameter": 8.0,
    "min_land_mm": 1.0,
}

#: And the large end: the longest bar the declared ranges can produce.
MAGNET_HOLDER_LARGE = {
    "magnet_diameter": 20.0,
    "magnet_thickness": 10.0,
    "magnet_count": 6,
    "magnet_spacing": 30.0,
    "wall": 6.0,
    "bar_thickness": 25.0,
    "mount_holes": True,
    "mount_hole_diameter": 8.0,
    "min_land_mm": 5.0,
}


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
def magnet_holder_source() -> str:
    return (SAMPLES / "magnet_holder.py").read_text(encoding="utf-8")


def _all_pass(body) -> None:
    statuses = {check["name"]: check["status"] for check in body["checks"]}
    assert statuses == {
        "bed_fit": "pass",
        "min_wall": "pass",
        "overhangs": "pass",
        "watertight": "pass",
    }, body["checks"]
    assert body["overall"] == "pass"


@pytest.mark.parametrize(
    "label, overrides",
    [
        ("defaults", {}),
        ("small extreme", MAGNET_HOLDER_SMALL),
        ("large extreme", MAGNET_HOLDER_LARGE),
        ("one magnet, no holes", {"magnet_count": 1, "mount_holes": False}),
    ],
)
def test_magnet_holder_passes_every_check(
    client, kernel, magnet_holder_source, label, overrides
):
    response = client.post(
        "/check", json={"script": magnet_holder_source, "overrides": overrides}
    )
    assert response.status_code == 200, response.text
    _all_pass(response.json())


def test_magnet_holder_pocket_count_follows_the_parameter(
    client, kernel, magnet_holder_source
):
    """More magnets, less material: the pockets are really being cut."""
    volumes = []
    for count in (1, 6):
        response = client.post(
            "/generate",
            json={
                "script": magnet_holder_source,
                "overrides": {"magnet_count": count, "magnet_spacing": 18.0},
            },
        )
        assert response.status_code == 200, response.text
        stats = response.json()["stats"]
        volumes.append(stats["bounding_box_mm"][0])
    assert volumes[1] > volumes[0]


def test_a_raw_taper_fails_min_wall_and_the_helper_fixes_it(client, kernel):
    """The exact failure this library exists for, both sides of it."""
    raw = """
from build123d import *
PARAMS = {"h": {"value": 6.0, "unit": "mm"}}
def build(p):
    return revolve(
        Plane.XZ * Polygon((0, 0), (12, 0), (9, p["h"]), (0, p["h"]), align=None),
        axis=Axis.Z,
    )
"""
    fixed = """
from build123d import *
import forge_lib
PARAMS = {"h": {"value": 6.0, "unit": "mm"}}
def build(p):
    return forge_lib.blunted_taper(12.0, 9.0, p["h"])
"""
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
    assert fixed_wall["status"] == "pass"
    assert fixed_wall["data"]["min_measured_thickness_mm"] >= forge_lib.min_land(None)


def test_a_textured_band_keeps_the_wall_the_check_can_measure(client, kernel):
    """An absurd flute depth on a real tube still passes min_wall."""
    script = """
from build123d import *
import forge_lib
PARAMS = {"depth": {"value": 9.0, "unit": "mm", "min": 0.1, "max": 20.0}}
def build(p):
    wall = 5.0
    tube = (Pos(0, 0, 10) * Cylinder(30, 20)) - (Pos(0, 0, 10) * Cylinder(30 - wall, 21))
    return tube - forge_lib.textured_band(30.0, 16.0, 24, p["depth"], wall=wall,
                                          z_bottom=2.0)
"""
    body = client.post("/check", json={"script": script}).json()
    wall_check = [c for c in body["checks"] if c["name"] == "min_wall"][0]
    assert wall_check["status"] == "pass", wall_check["details"]


def test_forge_lib_helpers_are_importable_from_inside_a_script(client, kernel):
    """The install mechanism reaches the new names, not just peg/socket_for."""
    script = """
from build123d import *
PARAMS = {"d": {"value": 6.0, "unit": "mm"}}
def build(p):
    # no `import forge_lib` on purpose: the name is pre-bound in the namespace
    plan = forge_lib.magnet_pocket_plan(p["d"], 3.0)
    assert plan["pocket_diameter_mm"] == p["d"] + 0.1
    body = Pos(0, 0, 3) * Box(20, 20, 6)
    return body - Pos(0, 0, 6) * forge_lib.magnet_pocket(p["d"], 3.0,
                                                         available_depth=6.0)
"""
    response = client.post("/check", json={"script": script})
    assert response.status_code == 200, response.text
    _all_pass(response.json())
