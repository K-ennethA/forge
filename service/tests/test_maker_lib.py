"""Maker mode: the component table, the fits, the mechanisms and the circuit.

Four layers, cheapest first -- the same shape as ``test_forge_lib.py``:

* the **component table** is plain data, so its sanity (positive dimensions, an
  honesty sentence on every entry, a datum on every entry) is tested without
  importing build123d at all;
* the **plans** are pure arithmetic on a printer profile, so the fits, the
  kinematics and the beam-bend maths are tested there -- that is where the rules
  live and where a regression shows up first;
* the **geometry** is measured directly (a bounding box proves the guide really
  did grow to clear its own keyway, not just that the plan said so);
* ``samples/push_lamp_core.py`` goes through the real ``/check`` endpoint, one
  piece at a time, at its defaults and at both ends of its declared ranges,
  because "all four pieces come out printable" is the claim the sample exists to
  make.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from service import components, maker_lib, wiring
from service.components import ComponentError
from service.errors import ForgeError, ScriptError
from service.forge_lib import PrintabilityError
from service.printer import DEFAULT_PROFILE

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

MIN_WALL = float(DEFAULT_PROFILE["min_wall_thickness"])          # 0.8
MIN_FEATURE = float(DEFAULT_PROFILE["min_feature_size"])         # 1.0
PRESS_FIT = float(DEFAULT_PROFILE["tolerances"]["press_fit"])    # 0.1
SLIDE_FIT = float(DEFAULT_PROFILE["tolerances"]["slide_fit"])    # 0.2
LOOSE_FIT = float(DEFAULT_PROFILE["tolerances"]["loose_fit"])    # 0.3


# ==========================================================================
# The component table
# ==========================================================================


def test_component_error_is_a_400_not_a_500():
    """An unknown part is the caller's fault, so it has to be a ScriptError."""
    assert issubclass(ComponentError, ScriptError)
    assert issubclass(ComponentError, ForgeError)
    assert ComponentError("x").http_status == 400
    assert issubclass(wiring.CircuitError, ScriptError)


def test_the_catalog_covers_every_category_the_brief_named():
    catalog = components.catalog()
    for expected in (
        "tactile_6x6_h43", "tactile_6x6_h73", "tactile_6x6_h95",
        "tactile_6x6_latching", "push_latching_12mm", "slide_switch_sk12",
        "cr2032_cell", "cr2032_holder", "aaa_pair_box",
        "led_3mm", "led_5mm", "led_10mm",
        "m3_screw", "heat_set_m3",
        "magnet_5x2", "magnet_6x3", "magnet_8x3", "magnet_10x2",
    ):
        assert expected in catalog
    for category in components.CATEGORIES:
        assert components.catalog(category), f"{category} is empty"


@pytest.mark.parametrize("name", sorted(components.COMPONENTS))
def test_every_entry_carries_its_honesty_fields(name):
    """The verify sentence is not optional: clones vary and the table says so."""
    entry = components.component(name)
    assert entry["category"] in components.CATEGORIES
    assert entry["datum"] and "Z = 0" in entry["datum"]
    for field in ("source", "verify_against_your_part", "purchase_note", "summary"):
        assert isinstance(entry[field], str) and len(entry[field]) > 20, field
    honesty = components.honesty(entry)
    assert honesty["clone_tolerance_mm"] == components.CLONE_TOLERANCE_MM
    assert honesty["verify_against_your_part"] == entry["verify_against_your_part"]


@pytest.mark.parametrize("name", sorted(components.COMPONENTS))
def test_every_dimension_in_the_table_is_positive(name):
    entry = components.component(name)
    for key, value in entry.items():
        if key.endswith("_mm") and isinstance(value, (int, float)):
            assert value > 0.0, f"{name}.{key} = {value}"
    for part in entry["parts"]:
        assert float(part["z1_mm"]) > float(part["z0_mm"]), f"{name} part {part}"
        if part["shape"] == "cylinder":
            assert float(part["diameter_mm"]) > 0.0
        else:
            assert float(part["length_mm"]) > 0.0
            assert float(part["width_mm"]) > 0.0


def test_component_accepts_a_record_and_rejects_a_typo_with_a_hint():
    entry = components.component("led_5mm")
    assert components.component(entry)["name"] == "led_5mm"
    with pytest.raises(ComponentError) as excinfo:
        components.component("led_5")
    assert "led_5mm" in str(excinfo.value)


def test_component_returns_a_copy_so_the_table_cannot_be_edited():
    first = components.component("led_5mm")
    first["dome_diameter_mm"] = 999.0
    assert components.component("led_5mm")["dome_diameter_mm"] == 5.0


def test_require_category_says_what_would_have_worked():
    with pytest.raises(ComponentError) as excinfo:
        components.require_category("led_5mm", "switch")
    assert "tactile_6x6_latching" in str(excinfo.value)


def test_led_forward_voltages_are_the_numbers_the_resistor_maths_turns_on():
    assert components.led_forward_voltage("red")["vf"] == pytest.approx(2.0)
    assert components.led_forward_voltage("white")["vf"] == pytest.approx(3.0)
    assert components.led_forward_voltage("blue")["vf"] == pytest.approx(3.0)
    assert components.led_forward_voltage("WHITE")["color"] == "white"
    for record in components.LED_FORWARD_VOLTAGE.values():
        low, high = record["band"]
        assert low <= record["vf"] <= high
    with pytest.raises(ComponentError):
        components.led_forward_voltage("puce")


def test_the_latching_switch_really_does_latch_and_the_momentary_one_does_not():
    latching = components.component("tactile_6x6_latching")
    momentary = components.component("tactile_6x6_h43")
    assert latching["actuation"]["latching"] is True
    assert momentary["actuation"]["latching"] is False
    # The whole reason to pay for the latching one.
    assert latching["actuation"]["stroke_mm"] > 5.0 * momentary["actuation"]["stroke_mm"]


def test_every_switch_declares_what_its_button_can_take():
    for name in components.catalog("switch"):
        actuation = components.component(name)["actuation"]
        assert actuation["stroke_mm"] > 0.0
        assert actuation["max_overtravel_mm"] >= 0.0
        assert actuation["force_band_gf"][0] <= actuation["force_gf"] <= actuation["force_band_gf"][1]


# ==========================================================================
# envelope / cutout / mount -- fits come from printer.json, never from here
# ==========================================================================


def test_envelope_grows_by_the_profiles_loose_fit_by_default():
    plan = maker_lib.envelope_plan("cr2032_cell")
    assert plan["clearance_mm"] == pytest.approx(LOOSE_FIT)
    assert plan["clearance_source"] == "printer.tolerances.loose_fit"
    assert plan["length_mm"] == pytest.approx(20.0 + 2.0 * LOOSE_FIT)
    assert plan["height_mm"] == pytest.approx(3.2 + 2.0 * LOOSE_FIT)


def test_envelope_can_leave_the_leads_out():
    withal = maker_lib.envelope_plan("led_5mm", leads=True)
    without = maker_lib.envelope_plan("led_5mm", leads=False)
    assert withal["height_mm"] > without["height_mm"] + 20.0
    assert without["includes_leads"] is False


def test_led_cutout_is_a_press_fit_bore_with_a_slide_fit_flange_seat():
    plan = maker_lib.cutout_plan("led_5mm", depth=3.0)
    assert plan["bore_diameter_mm"] == pytest.approx(5.0 + 2.0 * PRESS_FIT)
    assert plan["counterbore_diameter_mm"] == pytest.approx(5.8 + 2.0 * SLIDE_FIT)
    fits = [stage["fit"] for stage in plan["stages"]]
    assert fits == ["press_fit", "slide_fit", "loose_fit"]
    for stage in plan["stages"]:
        assert stage["fit_source"] == f"printer.tolerances.{stage['fit']}"


def test_the_press_fit_band_is_a_tenth_of_a_millimetre_per_side_and_says_so():
    """The interference is the printer's, not the model's -- and the plan is honest."""
    plan = maker_lib.cutout_plan("led_5mm", depth=3.0)
    interference = plan["bore_diameter_mm"] - 5.0
    assert 0.0 < interference <= 2.0 * PRESS_FIT + 1e-9
    assert "undersize" in plan["press_fit_note"]


def test_a_thin_wall_gets_told_the_led_will_not_grip():
    thin = maker_lib.cutout_plan("led_5mm", depth=MIN_WALL)
    assert any("collar" in note for note in thin["notes"])
    thick = maker_lib.cutout_plan("led_5mm", depth=maker_lib.LED_GRIP_TARGET_MM)
    assert not any("collar" in note for note in thick["notes"])


def test_switch_bodies_get_a_slide_fit_because_forcing_a_moulding_cracks_it():
    plan = maker_lib.cutout_plan("tactile_6x6_latching")
    body = plan["stages"][0]
    assert body["fit"] == "slide_fit"
    assert body["length_mm"] == pytest.approx(6.0 + 2.0 * SLIDE_FIT)
    assert body["width_mm"] == pytest.approx(6.0 + 2.0 * SLIDE_FIT)
    assert plan["stages"][1]["fit"] == "loose_fit"


def test_a_deeper_switch_pocket_moves_the_lead_relief_with_it():
    shallow = maker_lib.cutout_plan("tactile_6x6_latching")
    deep = maker_lib.cutout_plan("tactile_6x6_latching", depth=9.0)
    assert deep["stages"][0]["z0_mm"] == pytest.approx(-9.0)
    # The relief keeps its own 3.5 mm; it does not scale with the seat.
    relief = deep["stages"][1]
    assert relief["z1_mm"] - relief["z0_mm"] == pytest.approx(
        shallow["stages"][1]["z1_mm"] - shallow["stages"][1]["z0_mm"]
    )


def test_every_fit_in_the_table_follows_a_stricter_printer():
    strict = {"tolerances": {"press_fit": 0.3, "slide_fit": 0.6, "loose_fit": 0.9}}
    plan = maker_lib.cutout_plan("led_5mm", depth=3.0, printer=strict)
    assert plan["bore_diameter_mm"] == pytest.approx(5.0 + 0.6)
    assert plan["counterbore_diameter_mm"] == pytest.approx(5.8 + 1.2)


def test_heat_set_and_screw_holes_take_no_printer_tolerance_at_all():
    """Those diameters are somebody else's standard; adding ours adds it twice."""
    insert = maker_lib.cutout_plan("heat_set_m3")
    assert insert["hole_diameter_mm"] == pytest.approx(4.0)
    assert insert["stages"][0]["fit"] is None
    assert "insert" in insert["hole_source"].lower()

    strict = {"tolerances": {"slide_fit": 0.9, "press_fit": 0.9}}
    assert maker_lib.cutout_plan("heat_set_m3", printer=strict)[
        "hole_diameter_mm"
    ] == pytest.approx(4.0)

    screw = maker_lib.cutout_plan("m3_screw", depth=5.0)
    assert screw["hole_diameter_mm"] == pytest.approx(3.4)


def test_a_clearance_hole_with_no_depth_says_what_it_needs():
    with pytest.raises(PrintabilityError) as excinfo:
        maker_lib.cutout_plan("m3_screw")
    assert "depth" in str(excinfo.value)


def test_magnets_go_through_forge_libs_own_pocket_maths():
    plan = maker_lib.cutout_plan("magnet_6x3")
    extra = float(DEFAULT_PROFILE["tolerances"]["magnet_pocket_extra"])
    assert plan["pocket_diameter_mm"] == pytest.approx(6.0 + 2.0 * extra)
    assert plan["depth_mm"] == pytest.approx(3.0 + extra)
    with pytest.raises(PrintabilityError):
        maker_lib.cutout_plan("magnet_6x3", depth=3.2)   # no floor left under it


def test_a_panel_switch_refuses_a_wall_its_thread_cannot_clamp():
    ok = maker_lib.cutout_plan("push_latching_12mm", depth=3.0)
    assert ok["hole_diameter_mm"] == pytest.approx(
        max(12.2, 11.9 + 2.0 * SLIDE_FIT)
    )
    assert ok["panel_thickness_range_mm"] == [1.0, 4.0]
    with pytest.raises(PrintabilityError) as excinfo:
        maker_lib.cutout_plan("push_latching_12mm", depth=8.0)
    assert "thread" in str(excinfo.value)


def test_mount_styles_are_per_component_and_a_bad_one_lists_the_good_ones():
    assert maker_lib.mount_plan("led_5mm")["style"] == "collar"
    with pytest.raises(ComponentError) as excinfo:
        maker_lib.mount_plan("led_5mm", style="shelf")
    assert "collar" in str(excinfo.value)
    with pytest.raises(ComponentError):
        maker_lib.mount_plan("cr2032_cell")     # has no mount styles at all


def test_the_led_collar_and_its_cutout_cannot_disagree():
    collar = maker_lib.mount_plan("led_5mm", style="collar", grip=4.0)
    bore = maker_lib.cutout_plan("led_5mm", depth=2.0 + 4.0)
    assert collar["grip_mm"] == pytest.approx(4.0)
    assert collar["outer_diameter_mm"] > bore["counterbore_diameter_mm"]
    assert "depth=wall + 4" in collar["usage"]


def test_the_heat_set_boss_is_walled_for_melted_plastic_not_for_a_screw():
    plan = maker_lib.mount_plan("heat_set_m3", style="boss", height=8.0)
    assert plan["hole_diameter_mm"] == pytest.approx(4.0)
    assert plan["wall_mm"] >= 1.5
    assert plan["outer_diameter_mm"] >= 7.0
    assert "INSERT MAKER" in plan["hole_source"]
    # A thin wall is clamped up: melted plastic needs more than a screw does.
    thin = maker_lib.mount_plan("heat_set_m3", style="boss", height=8.0, wall=1.0)
    assert thin["wall_mm"] >= 1.5
    assert any("heat-set" in note for note in thin["clamped"])
    with pytest.raises(PrintabilityError):
        maker_lib.mount_plan("heat_set_m3", style="boss", height=6.0)


def test_standing_ribs_straddle_the_holders_thickness_not_its_length():
    flat = maker_lib.mount_plan("cr2032_holder", style="rib")
    edge = maker_lib.mount_plan("cr2032_holder", style="rib", standing=True)
    holder = components.component("cr2032_holder")
    assert flat["gap_mm"] == pytest.approx(holder["body_length_mm"] + 2.0 * SLIDE_FIT)
    assert edge["gap_mm"] == pytest.approx(holder["body_height_mm"] + 2.0 * SLIDE_FIT)
    # The whole point: a much smaller footprint on the floor.
    assert edge["footprint_width_mm"] < flat["footprint_width_mm"] / 2.0


# ==========================================================================
# plunger -- the push mechanic
# ==========================================================================


def test_the_stroke_is_never_less_than_the_switch_needs():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    switch = components.component("tactile_6x6_latching")
    stroke = float(switch["actuation"]["stroke_mm"])
    assert plan["switch_stroke_mm"] == pytest.approx(stroke)
    assert plan["stroke_mm"] >= stroke
    assert plan["stroke_mm"] == pytest.approx(stroke + plan["overtravel_mm"])
    assert plan["travel_mm"] == pytest.approx(plan["stroke_mm"] + plan["free_play_mm"])


def test_a_travel_shorter_than_the_switch_needs_is_clamped_up_and_named():
    plan = maker_lib.plunger_plan(6.0, 0.5, switch="tactile_6x6_latching")
    assert plan["travel_mm"] > 0.5
    assert any("travel 0.5 ->" in note for note in plan["clamped"])


def test_a_longer_travel_becomes_free_play_and_the_plan_says_so():
    plan = maker_lib.plunger_plan(6.0, 5.0, switch="tactile_6x6_latching")
    assert plan["travel_mm"] == pytest.approx(5.0)
    assert plan["free_play_mm"] == pytest.approx(5.0 - plan["stroke_mm"])
    assert any("free play" in note for note in plan["notes"])


def test_overtravel_is_clamped_to_what_the_switch_can_physically_take():
    """0.5 mm past actuation would crush a 6x6 tactile's dome."""
    switch = components.component("tactile_6x6_latching")
    limit = float(switch["actuation"]["max_overtravel_mm"])
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching", overtravel=0.5)
    assert plan["overtravel_requested_mm"] == pytest.approx(0.5)
    assert plan["overtravel_mm"] == pytest.approx(limit)
    assert any("overtravel 0.5 ->" in note for note in plan["clamped"])
    assert plan["switch_compression_headroom_mm"] >= 0.0


def test_the_end_stop_is_the_only_thing_between_a_finger_and_the_switch():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    switch = components.component("tactile_6x6_latching")
    # The gap the cap falls through IS the travel, so the rim arrests it there.
    assert plan["end_stop_gap_mm"] == pytest.approx(plan["travel_mm"])
    # And what the switch sees at that stop is its stroke plus the overtravel,
    # never more than the switch's own maximum.
    assert plan["switch_max_compression_mm"] == pytest.approx(
        plan["switch_stroke_mm"] + plan["overtravel_mm"]
    )
    assert plan["switch_max_compression_mm"] <= (
        float(switch["actuation"]["stroke_mm"])
        + float(switch["actuation"]["max_overtravel_mm"])
        + 1e-9
    )
    assert plan["end_stop_protects_switch"] is True


def test_the_guide_is_never_shorter_than_two_stem_diameters():
    """The anti-cocking rule -- a short pin jams on the first off-centre push."""
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching", guide_length=4.0)
    assert plan["guide_length_mm"] == pytest.approx(
        maker_lib.GUIDE_ENGAGEMENT_RATIO * 6.0
    )
    assert plan["guide_engagement_ratio"] == pytest.approx(
        maker_lib.GUIDE_ENGAGEMENT_RATIO
    )
    assert any("cocks" in note for note in plan["clamped"])
    generous = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching",
                                      guide_length=30.0)
    assert generous["guide_engagement_mm"] == pytest.approx(30.0)
    assert generous["clamped"] == [] or all(
        "guide_length" not in note for note in generous["clamped"]
    )


def test_the_bore_is_a_slide_fit_from_the_printer_profile():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    assert plan["clearance_per_side_mm"] == pytest.approx(SLIDE_FIT)
    assert plan["bore_diameter_mm"] == pytest.approx(6.0 + 2.0 * SLIDE_FIT)
    assert plan["fit_source"] == "printer.tolerances.slide_fit"
    loose = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching", fit="loose_fit")
    assert loose["bore_diameter_mm"] == pytest.approx(6.0 + 2.0 * LOOSE_FIT)


def test_the_retention_flange_cannot_pass_through_its_own_bore():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    assert plan["flange_diameter_mm"] > plan["bore_diameter_mm"]
    # And the sleeve is wide enough that the flange lands on a real rim.
    assert plan["guide_outer_diameter_mm"] >= plan["flange_diameter_mm"]
    assert "cannot come out the front" in plan["retention_note"]


def test_the_keyway_does_not_cut_through_the_sleeve_wall():
    """The bore is not a circle when the stem is keyed, and the sleeve knows it."""
    keyed = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching", keyed=True)
    round_ = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching", keyed=False)
    assert keyed["key_reach_mm"] > round_["key_reach_mm"]
    assert keyed["guide_outer_diameter_mm"] > round_["guide_outer_diameter_mm"]
    assert keyed["guide_outer_diameter_mm"] == pytest.approx(
        2.0 * (keyed["bore_outer_radius_mm"] + keyed["guide_wall_mm"])
    )


def test_the_flange_never_lands_on_the_switch_before_the_button_does():
    for stem in (4.0, 6.0, 10.0):
        for switch in ("tactile_6x6_latching", "tactile_6x6_h43", "tactile_6x6_h95"):
            plan = maker_lib.plunger_plan(stem, switch=switch)
            assert plan["flange_clearance_at_full_press_mm"] > 0.0, (stem, switch)


def test_the_latching_return_is_the_switchs_own_spring_and_the_plan_says_so():
    latching = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    assert "LATCHING" in latching["return"]
    assert "own spring" in latching["return"]
    assert latching["latched_drop_mm"] == pytest.approx(latching["switch_stroke_mm"])
    # Latched, the cap has not jammed against the guide: there is gap left.
    assert latching["latched_cap_gap_mm"] > 0.0

    momentary = maker_lib.plunger_plan(6.0, switch="tactile_6x6_h95")
    assert "MOMENTARY" in momentary["return"]
    assert momentary["latched_drop_mm"] is None


def test_a_press_too_short_to_feel_is_called_out():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_h43")
    assert plan["stroke_mm"] < maker_lib.MIN_SATISFYING_TRAVEL_MM
    assert any("fingertip" in note for note in plan["notes"])


def test_the_force_note_quotes_the_switchs_own_datasheet_band():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    assert "250 gf" in plan["force_note"]
    assert "180 to 350 gf" in plan["force_note"]


def test_a_slide_switch_refuses_a_plunger_and_names_one_that_works():
    with pytest.raises(PrintabilityError) as excinfo:
        maker_lib.plunger_plan(6.0, switch="slide_switch_sk12")
    message = str(excinfo.value)
    assert "sideways" in message
    assert "tactile_6x6_latching" in message


def test_a_panel_switch_refuses_a_plunger_because_it_already_is_one():
    with pytest.raises(PrintabilityError) as excinfo:
        maker_lib.plunger_plan(6.0, switch="push_latching_12mm")
    assert "already IS the plunger" in str(excinfo.value)


def test_a_stem_under_the_minimum_feature_is_refused_outright():
    with pytest.raises(PrintabilityError):
        maker_lib.plunger_plan(0.4, switch="tactile_6x6_latching")


def test_the_assembly_order_exists_and_puts_the_cap_last():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    steps = plan["assembly"]
    assert any("INSIDE" in step for step in steps)
    assert "LAST" in steps[-2] or "LAST" in steps[-1]


# ==========================================================================
# plunger geometry
# ==========================================================================


@pytest.fixture(scope="module")
def kernel_available():
    try:
        import build123d  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        pytest.skip("build123d is not installed in the service environment")
    return True


def test_the_two_solids_come_back_in_one_frame_at_rest(kernel_available):
    rig = maker_lib.plunger(6.0, switch="tactile_6x6_latching")
    plan = rig["plan"]
    guide = rig["guide"].bounding_box()
    piece = rig["plunger"].bounding_box()

    # The guide sleeve: bottom rim on Z = 0, top rim at the guide length.
    assert guide.min.Z == pytest.approx(0.0, abs=1e-6)
    assert guide.max.Z == pytest.approx(plan["guide_length_mm"], abs=1e-6)
    assert guide.size.X == pytest.approx(plan["guide_outer_diameter_mm"], abs=1e-3)

    # The plunger, at rest, in that same frame. The tip is the lowest point --
    # the flange's lead cone lives INSIDE the tip's length, which is why
    # tip_length is floored at the lead plus a minimum feature.
    assert piece.min.Z == pytest.approx(plan["stem_tip_z_mm"], abs=1e-6)
    assert piece.max.Z == pytest.approx(plan["stem_top_z_mm"], abs=1e-6)
    assert piece.size.X == pytest.approx(plan["flange_diameter_mm"], abs=1e-3)
    assert plan["flange_lead_mm"] < plan["tip_length_mm"]


def test_the_flange_is_wider_than_the_bore_in_the_actual_geometry(kernel_available):
    rig = maker_lib.plunger(6.0, switch="tactile_6x6_latching")
    plan = rig["plan"]
    assert rig["plunger"].bounding_box().size.X > plan["bore_diameter_mm"]


def test_the_cap_socket_opens_downward_from_its_own_datum(kernel_available):
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    socket = maker_lib.plunger_cap_socket(plan)
    box = socket.bounding_box()
    assert box.max.Z == pytest.approx(0.0, abs=1e-6)
    assert box.min.Z < 0.0
    assert abs(box.min.Z) >= plan["cap_engagement_mm"]


def test_the_cutouts_hang_below_zero_with_a_mouth_overshoot(kernel_available):
    from service.forge_lib import MOUTH_OVERSHOOT_MM  # noqa: PLC0415

    for name, kwargs in (
        ("led_5mm", {"depth": 3.0}),
        ("tactile_6x6_latching", {}),
        ("magnet_6x3", {}),
        ("cr2032_holder", {}),
    ):
        solid = maker_lib.cutout(name, **kwargs)
        box = solid.bounding_box()
        assert box.max.Z == pytest.approx(MOUTH_OVERSHOOT_MM, abs=1e-6), name
        assert box.min.Z < 0.0, name


def test_every_mount_style_builds_standing_on_z_zero(kernel_available):
    for name, style in (
        ("led_5mm", "collar"),
        ("tactile_6x6_latching", "shelf"),
        ("tactile_6x6_latching", "pocket_boss"),
        ("cr2032_holder", "screw"),
        ("cr2032_holder", "rib"),
        ("aaa_pair_box", "screw"),
        ("m3_screw", "boss"),
        ("heat_set_m3", "boss"),
    ):
        solid = maker_lib.mount(name, style=style)
        box = solid.bounding_box()
        assert box.min.Z == pytest.approx(0.0, abs=1e-6), (name, style)
        assert box.max.Z > 0.0, (name, style)


def test_the_envelope_spans_the_range_the_plan_promised(kernel_available):
    for name in ("led_5mm", "tactile_6x6_latching", "cr2032_holder",
                 "push_latching_12mm", "cr2032_cell"):
        plan = maker_lib.envelope_plan(name)
        box = maker_lib.envelope(name).bounding_box()
        assert box.min.Z == pytest.approx(plan["z_min_mm"], abs=1e-6), name
        assert box.max.Z == pytest.approx(plan["z_max_mm"], abs=1e-6), name


# ==========================================================================
# snap_clip -- the beam-bend rule
# ==========================================================================


def test_the_deflection_limit_is_the_documented_beam_formula():
    plan = maker_lib.snap_clip_plan(12.0, 2.0, 6.0, 0.5)
    material = maker_lib.material("PLA")
    expected = plan["taper_k"] * material["strain"] * 12.0 ** 2 / 2.0
    assert plan["max_deflection_mm"] == pytest.approx(expected)
    assert plan["deflection_rule"] == "y_max = K * strain_limit * L^2 / t"


def test_an_over_flexed_arm_is_clamped_rather_than_snapped():
    plan = maker_lib.snap_clip_plan(12.0, 2.0, 6.0, 5.0)
    assert plan["deflection_mm"] == pytest.approx(plan["max_deflection_mm"])
    assert plan["deflection_mm"] < 5.0
    assert any("deflection 5 ->" in note for note in plan["clamped"])
    # And at the clamp the arm is exactly at its allowable strain, not past it.
    assert plan["strain_at_deflection"] == pytest.approx(plan["strain_limit"], rel=1e-3)
    assert plan["strain_utilisation"] == pytest.approx(1.0, rel=1e-3)


def test_deflection_grows_with_the_square_of_the_arm_length():
    short = maker_lib.snap_clip_plan(10.0, 2.0, 6.0, 0.5)
    long_ = maker_lib.snap_clip_plan(20.0, 2.0, 6.0, 0.5)
    assert long_["max_deflection_mm"] == pytest.approx(
        4.0 * short["max_deflection_mm"], rel=1e-6
    )


def test_a_tapered_arm_bends_further_than_a_constant_one():
    straight = maker_lib.snap_clip_plan(14.0, 2.0, 6.0, 0.5, taper=1.0)
    tapered = maker_lib.snap_clip_plan(14.0, 2.0, 6.0, 0.5, taper=0.5)
    assert straight["taper_k"] == pytest.approx(0.67)
    assert tapered["taper_k"] == pytest.approx(1.09)
    assert tapered["max_deflection_mm"] > straight["max_deflection_mm"]


def test_petg_takes_nearly_twice_the_strain_pla_does():
    pla = maker_lib.snap_clip_plan(14.0, 2.0, 6.0, 0.5, material_name="PLA")
    petg = maker_lib.snap_clip_plan(14.0, 2.0, 6.0, 0.5, material_name="PETG")
    assert petg["max_deflection_mm"] > 1.5 * pla["max_deflection_mm"]
    with pytest.raises(PrintabilityError):
        maker_lib.snap_clip_plan(14.0, 2.0, 6.0, 0.2, material_name="unobtainium")


def test_an_arm_too_stiff_to_make_a_catch_refuses_with_the_length_it_needs():
    with pytest.raises(PrintabilityError) as excinfo:
        maker_lib.snap_clip_plan(4.0, 3.0, 6.0, 1.0)
    message = str(excinfo.value)
    assert "not a catch" in message
    assert "PETG" in message


def test_the_thickness_and_width_are_clamped_to_the_printer_minimums():
    plan = maker_lib.snap_clip_plan(20.0, 0.2, 0.3, 0.5)
    assert plan["root_thickness_mm"] == pytest.approx(MIN_WALL)
    assert plan["width_mm"] == pytest.approx(MIN_FEATURE)
    assert len(plan["clamped"]) >= 2


def test_a_steep_return_face_is_reported_as_a_permanent_snap():
    removable = maker_lib.snap_clip_plan(20.0, 2.0, 6.0, 0.5, return_angle_deg=45.0)
    assert removable["permanent"] is False
    assert removable["retention_force_n"] > removable["deflection_force_n"]
    permanent = maker_lib.snap_clip_plan(20.0, 2.0, 6.0, 0.5, return_angle_deg=88.0)
    assert permanent["permanent"] is True
    assert any("PERMANENT" in note for note in permanent["notes"])


def test_the_layer_direction_warning_is_always_there():
    plan = maker_lib.snap_clip_plan(20.0, 2.0, 6.0, 0.5)
    assert any("ALONG" in note for note in plan["notes"])


def test_the_clip_and_its_catch_are_built_from_the_same_numbers(kernel_available):
    rig = maker_lib.snap_clip(20.0, 2.0, 6.0, 0.5)
    plan = rig["plan"]
    clip = rig["clip"].bounding_box()
    catch = rig["catch"].bounding_box()
    assert clip.min.Z == pytest.approx(0.0, abs=1e-6)
    assert clip.max.Z == pytest.approx(plan["length_mm"], abs=1e-6)
    assert catch.size.Y == pytest.approx(plan["catch_length_mm"], abs=1e-3)
    assert plan["catch_depth_mm"] > plan["deflection_mm"]


# ==========================================================================
# battery_door
# ==========================================================================


def test_the_lip_is_clamped_up_to_whatever_the_fixing_actually_needs():
    magnet = maker_lib.battery_door_plan(30.0, 24.0, style="magnet", lip=1.0)
    assert magnet["lip_mm"] >= 6.0 + 2.0 * MIN_WALL
    assert any("lip 1 ->" in note for note in magnet["clamped"])

    screw = maker_lib.battery_door_plan(30.0, 24.0, style="screw", lip=1.0)
    assert screw["lip_mm"] >= 6.0 + 2.0 * MIN_WALL
    assert any("pan head" in note for note in screw["clamped"])


def test_the_door_is_thick_enough_to_hide_its_magnet_pocket():
    plan = maker_lib.battery_door_plan(30.0, 24.0, style="magnet", thickness=1.0)
    pocket_depth = plan["fixings"]["pocket"]["pocket_depth_mm"]
    assert plan["door_thickness_mm"] >= pocket_depth + MIN_WALL - 1e-9


def test_the_door_sits_in_a_rebate_a_slide_fit_bigger_than_itself():
    plan = maker_lib.battery_door_plan(30.0, 24.0)
    assert plan["rebate_length_mm"] == pytest.approx(
        plan["door_length_mm"] + 2.0 * SLIDE_FIT
    )
    assert plan["rebate_depth_mm"] == pytest.approx(plan["door_thickness_mm"])
    assert plan["clearance_source"] == "printer.tolerances.slide_fit"


def test_a_ledge_too_thin_for_its_magnet_refuses_through_forge_lib():
    with pytest.raises(PrintabilityError):
        maker_lib.battery_door_plan(30.0, 24.0, style="magnet", ledge_thickness=3.0)


def test_an_opening_nobody_could_reach_through_is_refused():
    with pytest.raises(PrintabilityError):
        maker_lib.battery_door_plan(0.5, 0.5)
    with pytest.raises(PrintabilityError):
        maker_lib.battery_door_plan(30.0, 24.0, style="hope")


def test_the_door_pieces_build_and_the_magnets_land_on_the_lip(kernel_available):
    rig = maker_lib.battery_door(30.0, 24.0, style="magnet")
    plan = rig["plan"]
    door = rig["door"].bounding_box()
    assert door.size.X == pytest.approx(plan["door_length_mm"], abs=1e-3)
    assert rig["frame_magnets"] is not None
    assert rig["bosses"] is None
    for x, y in plan["fixings"]["positions_mm"]:
        assert abs(x) < plan["door_length_mm"] / 2.0
        assert abs(y) < plan["door_width_mm"] / 2.0

    screwed = maker_lib.battery_door(30.0, 24.0, style="screw")
    assert screwed["bosses"] is not None
    assert screwed["frame_magnets"] is None


# ==========================================================================
# circuit_plan -- the resistor maths
# ==========================================================================


def test_e12_rounds_up_because_too_big_only_dims_the_led():
    assert wiring.preferred_resistor(200.0) == pytest.approx(220.0)
    assert wiring.preferred_resistor(220.0) == pytest.approx(220.0)
    assert wiring.preferred_resistor(221.0) == pytest.approx(270.0)
    assert wiring.preferred_resistor(50.0) == pytest.approx(56.0)
    assert wiring.preferred_resistor(1.0) == pytest.approx(1.0)
    assert wiring.preferred_resistor(0.0) == 0.0
    with pytest.raises(wiring.CircuitError):
        wiring.preferred_resistor(100.0, series="E96")


def test_the_litwick_default_needs_no_resistor_at_all():
    """CR2032 at 3 V, white LED at 3 V: there is nothing for a resistor to drop."""
    plan = wiring.circuit_plan()
    assert plan["led"]["name"] == "led_5mm"
    assert plan["led"]["color"] == "white"
    assert plan["led"]["forward_voltage_v"] == pytest.approx(3.0)
    assert plan["supply"]["voltage_v"] == pytest.approx(3.0)
    assert plan["headroom_v"] == pytest.approx(0.0)
    assert plan["verdict"] == "no resistor needed"
    assert plan["resistor_ohms"] == 0.0
    # The cell's own resistance is what limits it, off the FRESH voltage.
    assert plan["forward_current_ma"] == pytest.approx(
        1000.0 * (3.2 - 3.0) / 30.0, rel=1e-3
    )
    assert plan["runtime_hours"] > 10.0
    assert any("internal resistance" in note for note in plan["notes"])


def test_three_volts_and_a_red_led_makes_the_resistor_optional():
    plan = wiring.circuit_plan(color="red")
    assert plan["headroom_v"] == pytest.approx(1.0)
    assert plan["resistor_exact_ohms"] == pytest.approx(1.0 / 0.020)
    assert plan["resistor_ohms"] == pytest.approx(56.0)
    assert plan["verdict"] == "resistor optional"
    assert any("leave the resistor out" in note for note in plan["notes"])
    # And the gentler alternative for a cell you want to last.
    assert plan["resistor_gentle_current_ma"] == pytest.approx(3.0)
    assert plan["resistor_gentle_ohms"] > plan["resistor_ohms"]


def test_three_volts_and_a_blue_led_is_the_same_zero_headroom_edge():
    plan = wiring.circuit_plan(color="blue")
    assert plan["verdict"] == "no resistor needed"
    assert plan["resistor_ohms"] == 0.0


def test_six_volts_and_a_red_led_lands_in_the_180_to_220_ohm_band():
    plan = wiring.circuit_plan(color="red", cell="cr2032_cell", cells=2,
                               current_ma=20.0)
    assert plan["supply"]["voltage_v"] == pytest.approx(6.0)
    assert plan["resistor_exact_ohms"] == pytest.approx(200.0)
    assert 180.0 <= plan["resistor_ohms"] <= 220.0
    assert plan["verdict"] == "resistor required"


def test_two_aaas_make_the_resistor_compulsory_where_a_coin_cell_did_not():
    coin = wiring.circuit_plan(color="red", cell="cr2032_cell")
    alkaline = wiring.circuit_plan(color="red", cell="aaa_pair_box")
    assert coin["verdict"] == "resistor optional"
    assert alkaline["verdict"] == "resistor required"
    # Same voltage, same LED, same resistor -- five times the run time.
    assert alkaline["resistor_ohms"] == pytest.approx(coin["resistor_ohms"])
    assert alkaline["runtime_hours"] > coin["runtime_hours"]


def test_the_resistor_carries_a_power_rating_with_headroom():
    plan = wiring.circuit_plan(color="red", supply_v=12.0, cell=None, switch=None)
    assert plan["resistor_ohms"] > 0.0
    assert plan["resistor_power_w"] > 0.0
    assert plan["resistor_rating_w"] >= 2.0 * plan["resistor_power_w"]
    assert wiring.power_rating_for(0.0) == 0.125
    assert wiring.power_rating_for(0.3) == 1.0


def test_a_current_over_the_leds_maximum_is_refused():
    with pytest.raises(wiring.CircuitError):
        wiring.circuit_plan(color="red", current_ma=50.0)


def test_a_switch_that_cannot_carry_the_current_is_warned_about():
    plan = wiring.circuit_plan(color="red", cell="aaa_pair_box",
                               switch="tactile_6x6_latching", current_ma=20.0)
    # 20 mA is inside the 50 mA rating, so no warning.
    assert not plan["warnings"]
    hot = wiring.circuit_plan(color="red", supply_v=12.0, cell=None,
                              switch="tactile_6x6_latching", current_ma=20.0)
    assert hot["switch"]["max_current_ma"] == 50.0
    assert hot["resistor_ohms"] > 0.0


def test_the_series_order_is_one_loop_with_the_led_polarity_in_it():
    order = wiring.circuit_plan(color="red")["series_order"]
    assert order[0] == "cell +"
    assert order[-1] == "cell -"
    assert any("long leg" in item for item in order)
    assert any("resistor" in item for item in order)


# ==========================================================================
# wiring_steps -- the beginner instructions
# ==========================================================================


def test_the_wiring_steps_lead_with_polarity_and_end_with_test_before_glue():
    plan = wiring.circuit_plan()
    steps = wiring.wiring_steps(plan)
    text = " ".join(steps)
    assert len(steps) >= 8
    assert "LONGER leg is +" in text or "longer leg = +" in text
    assert "flat" in text                       # the cathode marker
    assert "TEST IT NOW" in text
    # The test step comes before the glue step, which is the whole point.
    test_at = next(i for i, s in enumerate(steps) if "TEST IT NOW" in s)
    glue_at = next(i for i, s in enumerate(steps) if "glue only what has to be" in s)
    assert test_at < glue_at
    assert any("Heat-shrink" in step or "electrical tape" in step for step in steps)


def test_a_resistorless_circuit_says_so_and_offers_the_alternative():
    steps = wiring.wiring_steps(wiring.circuit_plan())
    text = " ".join(steps)
    assert "no resistor in this circuit" in text
    assert "100 ohm" in text


def test_a_latching_circuit_tells_you_what_a_correct_second_press_looks_like():
    steps = wiring.wiring_steps(wiring.circuit_plan())
    assert any("go off and stay off" in step for step in steps)


def test_every_step_is_a_finished_sentence():
    for plan in (wiring.circuit_plan(),
                 wiring.circuit_plan(color="red"),
                 wiring.circuit_plan(color="red", cell="aaa_pair_box")):
        for step in wiring.wiring_steps(plan):
            assert step.strip().endswith((".", "!"))
            assert ".." not in step
            assert "  " not in step


def test_the_bill_of_materials_lists_the_resistor_only_when_there_is_one():
    with_resistor = wiring.bill_of_materials(wiring.circuit_plan(color="red"))
    without = wiring.bill_of_materials(wiring.circuit_plan())
    assert any("resistor" in row["item"] for row in with_resistor)
    assert not any("resistor" in row["item"] for row in without)
    for row in with_resistor:
        assert row["quantity"] >= 1 and row["note"]


def test_the_diagram_is_a_self_contained_svg_with_no_dependencies():
    svg = wiring.diagram_svg(wiring.circuit_plan())
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "http://www.w3.org/2000/svg" in svg
    assert "src=" not in svg and "href=" not in svg
    assert "LED" in svg and "CELL" in svg and "SWITCH" in svg


def test_assembly_steps_put_the_plunger_in_before_the_cap_goes_on():
    plan = maker_lib.plunger_plan(6.0, switch="tactile_6x6_latching")
    steps = maker_lib.assembly_steps(
        plunger_plan_=plan,
        circuit=wiring.circuit_plan(),
        door_plan=maker_lib.battery_door_plan(30.0, 24.0),
        pieces=("base", "lid", "plunger", "flame"),
    )
    text = " ".join(steps)
    inside_at = next(i for i, s in enumerate(steps) if "INSIDE" in s)
    cap_at = next(i for i, s in enumerate(steps) if "Press the cap" in s)
    assert inside_at < cap_at
    assert "TEST" in text


# ==========================================================================
# The sample, through the real endpoint
# ==========================================================================

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402

PUSH_LAMP_SMALL = {
    "body_height": 34.0, "body_radius": 23.5, "body_swell": 0.0, "wall": 1.2,
    "lid_thickness": 2.0, "stem_diameter": 4.0, "guide_length": 8.0,
    "led_variant": 0, "led_offset": 8.0, "flame_height": 24.0,
    "flame_radius": 11.0, "flame_gap": 1.0, "wire_channel": 2.0,
}

PUSH_LAMP_LARGE = {
    "body_height": 90.0, "body_radius": 40.0, "body_swell": 12.0, "wall": 5.0,
    "lid_thickness": 6.0, "stem_diameter": 12.0, "guide_length": 30.0,
    "led_variant": 2, "led_offset": 18.0, "flame_height": 70.0,
    "flame_radius": 25.0, "flame_gap": 8.0, "wire_channel": 6.0,
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
def push_lamp_source() -> str:
    return (SAMPLES / "push_lamp_core.py").read_text(encoding="utf-8")


def _statuses(body):
    return {check["name"]: check["status"] for check in body["checks"]}


@pytest.mark.parametrize(
    "label, overrides",
    [
        ("defaults", {}),
        ("small extreme", PUSH_LAMP_SMALL),
        ("large extreme", PUSH_LAMP_LARGE),
        ("momentary switch", {"switch_variant": 1}),
        ("3 mm LED", {"led_variant": 0, "led_offset": 9.0}),
    ],
)
@pytest.mark.parametrize("part", (0, 1, 2, 3))
def test_push_lamp_pieces_are_all_printable(
    client, kernel, push_lamp_source, label, overrides, part
):
    """Nothing fails. The base's wire tunnel warns on overhangs, and says why."""
    body = dict(overrides)
    body["part"] = part
    response = client.post(
        "/check", json={"script": push_lamp_source, "overrides": body}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    statuses = _statuses(payload)
    assert "fail" not in statuses.values(), (label, part, payload["checks"])
    assert statuses["min_wall"] == "pass", (label, part)
    assert statuses["watertight"] == "pass", (label, part)
    assert statuses["bed_fit"] == "pass", (label, part)
    # Only the base is allowed to warn, and only on overhangs.
    if part != 0:
        assert statuses["overhangs"] == "pass", (label, part)


def test_the_push_lamp_carries_its_own_circuit_and_assembly(push_lamp_source):
    """The script that builds the geometry also answers 'what do I solder?'."""
    from service import params  # noqa: PLC0415

    namespace = params.exec_script(push_lamp_source)
    notes = namespace["notes"]()
    assert notes["circuit"]["verdict"] == "no resistor needed"
    assert notes["circuit"]["resistor_ohms"] == 0.0
    assert len(notes["wiring_steps"]) >= 8
    assert notes["kinematics"]["end_stop_protects_switch"] is True
    assert notes["assembly_steps"]
    assert notes["diagram_svg"].startswith("<svg")
    assert any(row["item"].startswith("led_5mm") for row in notes["bill_of_materials"])


def test_the_push_lamp_refuses_a_body_too_narrow_for_the_holder(client, kernel,
                                                               push_lamp_source):
    """Design around the components: the bought part sets the minimum size."""
    response = client.post(
        "/check",
        json={
            "script": push_lamp_source,
            "overrides": {"part": 0, "body_radius": 18.0},
        },
    )
    assert response.status_code == 400
    message = response.json()["error"]
    assert "coin-cell holder" in message
    assert "body_radius" in message


def test_maker_lib_is_importable_from_a_part_script_like_forge_lib():
    """The install mechanism, which is what makes any of this usable."""
    from service import params  # noqa: PLC0415

    source = (
        "import maker_lib, components, wiring\n"
        "PARAMS = {'d': {'value': 6.0, 'unit': 'mm'}}\n"
        "CATALOG = maker_lib.catalog('switch')\n"
        "PLAN = maker_lib.plunger_plan(6.0)\n"
        "CIRCUIT = wiring.circuit_plan()\n"
        "TABLE = components.COMPONENTS\n"
        "def build(p):\n"
        "    return None\n"
    )
    namespace = params.exec_script(source)
    assert "tactile_6x6_latching" in namespace["CATALOG"]
    assert namespace["PLAN"]["mechanism"] == "plunger"
    assert namespace["CIRCUIT"]["verdict"] == "no resistor needed"
    assert "led_5mm" in namespace["TABLE"]


def test_a_script_that_never_mentions_maker_lib_still_runs():
    """The install is a convenience and must never be able to break a script."""
    from service import params  # noqa: PLC0415

    namespace = params.exec_script(
        "PARAMS = {'x': {'value': 1.0, 'unit': 'mm'}}\ndef build(p):\n    return None\n"
    )
    assert "maker_lib" in namespace       # bound, unused, harmless
    assert namespace["PARAMS"]["x"]["value"] == 1.0
