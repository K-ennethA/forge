"""Phase 19: the plan contract, the diff law, the build specs and the extractor.

Five layers, cheapest first, the same shape as ``test_maker_lib.py``:

* the **appliance table** is plain data, so its sanity (realistic sizes, a note
  on every entry, an alias index with no collisions) is tested without touching
  a plan at all;
* the **schema** is tested by what it REFUSES, and every refusal is asserted to
  name the offending entry by id -- a message that says "invalid polygon" costs
  the artist a trip through JSON, and that trip is the thing this module exists
  to save;
* the **defaults** resolve, and the resolution is checked both ways: what came
  from the block, and what the block never touched;
* the **diff** is the incremental-regen law, so it is tested at its three
  awkward edges -- a defaults change rippling into a wall's resolved height, an
  opening change attributed to its wall, and an id never appearing in two
  lists;
* the **build specs** are asserted NUMERICALLY on a two-room plan with a door
  between the rooms, because "the cutout is in the right place" is a claim
  about four coordinates and nothing else.

Then the extractor (a 2-degree-wobbly rectangle has to come out square) and the
mask (a plan against itself is 1.0, which is the floor under every later IoU
claim).
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from service import appliance_dims, floorplan
from service.errors import ForgeError, ScriptError
from service.floorplan import FloorPlanError


# ==========================================================================
# Fixtures: one plan, two rooms, a door between them
# ==========================================================================


def two_room_plan():
    """4 x 3 m kitchen, 3 x 3 m living room, one shared wall with a door in it.

    The shared wall runs from (4000, 0) to (4000, 3000), so it is vertical,
    3000 mm long, and the door's centre at 1500 is exactly halfway up it.  Every
    number the build-spec tests assert comes out of those four coordinates and
    the defaults, so they can be worked out on paper.
    """
    return {
        "version": 1,
        "units": "mm",
        "scale": {"mm_per_px": 12.5, "calibrated_by": "the 820 mm front door"},
        "rooms": [
            {"id": "room-kitchen", "label": "kitchen",
             "polygon_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]},
            {"id": "room-living", "label": "living",
             "polygon_mm": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]},
        ],
        "walls": [
            {"id": "wall-south", "from_mm": [0, 0], "to_mm": [7000, 0]},
            {"id": "wall-mid", "from_mm": [4000, 0], "to_mm": [4000, 3000],
             "openings": [
                 {"id": "door-01", "kind": "door", "at_mm": 1500, "swing": "in"},
             ]},
        ],
        "labels": [
            # footprint_mm's [x, y] is the box's CENTRE, which is the add-on's
            # convention: this pair stands against the kitchen's north wall.
            {"id": "wd-01", "label": "washer/dryer",
             "footprint_mm": [900, 2400, 1372, 813]},
        ],
        "history": [{"rev": 1, "date": "2026-09-12", "note": "first pass"}],
    }


def minimal_plan():
    return {
        "version": 1, "units": "mm",
        "walls": [{"id": "wall-01", "from_mm": [0, 0], "to_mm": [3000, 0]}],
    }


# ==========================================================================
# The appliance table
# ==========================================================================


def test_the_floorplan_error_is_a_400_not_a_500():
    """A plan the artist can fix by editing it is never a service bug."""
    assert issubclass(FloorPlanError, ScriptError)
    assert issubclass(FloorPlanError, ForgeError)
    assert FloorPlanError("x").http_status == 400


@pytest.mark.parametrize("name", sorted(appliance_dims.APPLIANCES))
def test_every_appliance_carries_a_note_saying_what_its_number_is(name):
    entry = appliance_dims.APPLIANCES[name]
    assert entry["category"] in appliance_dims.CATEGORIES
    assert isinstance(entry["note"], str) and len(entry["note"]) > 20, name
    assert isinstance(entry["aliases"], tuple)


@pytest.mark.parametrize("name", sorted(appliance_dims.APPLIANCES))
def test_every_appliance_size_is_three_positive_realistic_millimetres(name):
    width, depth, height = appliance_dims.APPLIANCES[name]["size_mm"]
    for value in (width, depth, height):
        assert value > 0.0, name
        # Nothing in a house is under 5 cm or over 3 m in any direction.
        assert 50.0 <= value <= 3000.0, f"{name}: {value} mm is not a real size"


def test_the_appliance_alias_index_has_no_silent_collisions():
    """Two entries claiming one alias would make lookups depend on dict order."""
    claimed = {}
    for name, entry in appliance_dims.APPLIANCES.items():
        for alias in entry["aliases"]:
            key = appliance_dims._norm(alias)
            assert key not in claimed or claimed[key] == name, (
                f"{alias!r} is claimed by both {claimed.get(key)} and {name}"
            )
            claimed[key] = name


def test_the_lookup_fuzzy_matches_w_slash_d_to_the_washer_dryer_pair():
    found = appliance_dims.lookup("W/D")
    assert found is not None
    assert found["match"] == "washer/dryer"
    assert found["confidence"] >= 0.9
    assert found["size_mm"] == [1372.0, 813.0, 965.0]
    assert "27 in" in found["note"]


def test_the_lookup_returns_none_for_a_label_nobody_recognises():
    """None is a real answer: the caller then uses the footprint that was drawn."""
    assert appliance_dims.lookup("xyzzy") is None
    assert appliance_dims.lookup("") is None
    assert appliance_dims.lookup(None) is None
    assert appliance_dims.lookup(42) is None


def test_the_lookup_survives_a_typo_and_says_it_was_fuzzy():
    found = appliance_dims.lookup("dishwaher")
    assert found is not None and found["match"] == "dishwasher"
    assert found["how"] == "fuzzy"
    assert 0.7 <= found["confidence"] < 1.0


def test_the_lookup_prefers_the_most_specific_entry_the_words_allow():
    """'stacked washer/dryer' contains 'washer/dryer', and must not stop there."""
    found = appliance_dims.lookup("stacked washer/dryer")
    assert found["match"] == "washer/dryer stacked"
    assert found["size_mm"][2] > 1500.0  # it is the tall one


def test_the_lookup_reads_the_ordinary_spellings_a_plan_key_uses():
    for label, expected in (
        ("washer/dryer", "washer/dryer"),
        ("washing machine", "washer"),
        ("stove", "range"),
        ("refrigerator", "fridge"),
        ("queen bed", "bed queen"),
        ("toilet", "toilet"),
        ("wc", "toilet"),
        ("couch", "sofa"),
        ("washer/dryer here", "washer/dryer"),
    ):
        found = appliance_dims.lookup(label)
        assert found is not None, label
        assert found["match"] == expected, f"{label} -> {found['match']}"


def test_the_catalog_is_a_copy_so_a_caller_cannot_edit_the_table():
    catalog = appliance_dims.catalog()
    catalog["washer"]["size_mm"] = (1.0, 1.0, 1.0)
    assert appliance_dims.APPLIANCES["washer"]["size_mm"] == (686.0, 813.0, 965.0)
    assert set(appliance_dims.catalog("bath")) <= set(appliance_dims.names())


# ==========================================================================
# The schema: every refusal names the entry it is about
# ==========================================================================


def test_the_plan_without_a_version_is_refused_and_says_what_to_write():
    plan = minimal_plan()
    del plan["version"]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "version" in str(caught.value)
    assert '"version": 1' in str(caught.value)


def test_the_plan_in_the_wrong_units_is_refused():
    plan = minimal_plan()
    plan["units"] = "inches"
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "inches" in str(caught.value) and "millimetres" in str(caught.value)


def test_an_unknown_version_names_the_versions_this_forge_reads():
    plan = minimal_plan()
    plan["version"] = 7
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "7" in str(caught.value) and "1" in str(caught.value)


def test_an_entry_with_no_id_is_refused_rather_than_given_one():
    """The no-invented-ids law: a generated id changes next run and takes the
    artist's hand edits with it, so the refusal is the feature."""
    plan = minimal_plan()
    plan["rooms"] = [{"polygon_mm": [[0, 0], [1000, 0], [1000, 1000]]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "rooms[0]" in message
    assert "will not invent" in message


def test_a_duplicate_id_names_the_id_and_both_kinds_that_want_it():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wall-01", "label": "sofa",
                       "footprint_mm": [0, 0, 2134, 914]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'wall-01'" in message
    assert "wall" in message and "label" in message
    assert "FP:wall-01" in message


def test_an_id_that_cannot_name_a_blender_object_is_refused():
    plan = minimal_plan()
    plan["walls"][0]["id"] = "wall 01/kitchen"
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'wall 01/kitchen'" in str(caught.value)


def test_a_zero_length_wall_is_refused_and_names_the_wall_and_its_ends():
    plan = minimal_plan()
    plan["walls"][0]["to_mm"] = [0, 0]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'wall-01'" in message
    assert "two different ends" in message


def test_a_wall_with_an_infinite_coordinate_is_refused():
    plan = minimal_plan()
    plan["walls"][0]["to_mm"] = [float("inf"), 0]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'wall-01'" in str(caught.value)


def test_a_self_intersecting_room_is_refused_and_names_the_crossing_corners():
    plan = minimal_plan()
    plan["rooms"] = [{"id": "room-bowtie",
                      "polygon_mm": [[0, 0], [4000, 3000], [4000, 0], [0, 3000]]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'room-bowtie'" in message
    assert "crosses itself" in message
    assert "corner" in message


def test_a_room_with_no_area_is_refused():
    plan = minimal_plan()
    plan["rooms"] = [{"id": "room-flat",
                      "polygon_mm": [[0, 0], [1000, 0], [2000, 0]]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'room-flat'" in str(caught.value)
    assert "not a room" in str(caught.value)


def test_a_room_with_too_few_corners_is_refused():
    plan = minimal_plan()
    plan["rooms"] = [{"id": "room-thin", "polygon_mm": [[0, 0], [1000, 0]]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'room-thin'" in str(caught.value)


def test_a_room_may_repeat_its_first_corner_to_close_the_ring():
    plan = minimal_plan()
    plan["rooms"] = [{"id": "room-a",
                      "polygon_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000], [0, 0]]}]
    normalized = floorplan.validate_plan(plan)
    assert len(normalized["rooms"][0]["polygon_mm"]) == 4


def test_a_clockwise_room_is_normalised_counter_clockwise():
    """Two artists drawing the same room in opposite directions must diff equal."""
    ccw = minimal_plan()
    ccw["rooms"] = [{"id": "room-a",
                     "polygon_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}]
    cw = copy.deepcopy(ccw)
    cw["rooms"][0]["polygon_mm"] = list(reversed(cw["rooms"][0]["polygon_mm"]))
    left = floorplan.validate_plan(ccw)["rooms"][0]["polygon_mm"]
    right = floorplan.validate_plan(cw)["rooms"][0]["polygon_mm"]
    assert floorplan._signed_area(left) > 0.0
    assert floorplan._signed_area(right) > 0.0
    assert floorplan.diff_plans(ccw, cw)["unchanged"] == ["room-a", "wall-01"]


def test_an_opening_past_the_end_of_its_wall_is_refused_and_names_both():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-99", "kind": "door", "at_mm": 4200},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'door-99'" in message and "'wall-01'" in message
    assert "3000" in message


def test_an_opening_whose_width_runs_off_the_end_is_refused_with_the_span():
    """at_mm is the CENTRE, so a 820 door at 200 hangs 210 mm off the end."""
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-99", "kind": "door", "at_mm": 200, "width_mm": 820},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'door-99'" in message and "'wall-01'" in message
    assert "-210" in message


def test_an_opening_taller_than_its_wall_is_refused_when_defaults_resolve():
    plan = minimal_plan()
    plan["defaults"] = {"ceiling_mm": 1800}
    plan["walls"][0]["openings"] = [{"id": "door-01", "kind": "door", "at_mm": 1500}]
    floorplan.validate_plan(plan)  # the schema alone cannot see it
    with pytest.raises(FloorPlanError) as caught:
        floorplan.fill_defaults(plan)
    message = str(caught.value)
    assert "'door-01'" in message and "'wall-01'" in message
    assert "2040" in message and "1800" in message


def test_an_opening_may_be_placed_by_its_near_edge_instead_of_its_centre():
    """start_mm is the other honest reading of 'along wall', so it is supported
    and converted once the width is known -- never carried alongside at_mm."""
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-01", "kind": "door", "start_mm": 1000},
    ]
    door = floorplan.fill_defaults(plan)["walls"][0]["openings"][0]
    assert door["at_mm"] == pytest.approx(1000 + 820 / 2.0)
    assert "start_mm" not in door


def test_giving_both_at_mm_and_start_mm_is_refused_rather_than_averaged():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-01", "kind": "door", "at_mm": 1500, "start_mm": 1000},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'door-01'" in message and "half the" in message


def test_two_openings_that_overlap_are_refused_and_both_are_named():
    """The add-on refuses this; the service has to agree, or the artist gets the
    refusal after the approval gate instead of before it."""
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-a", "kind": "door", "at_mm": 1000},
        {"id": "door-b", "kind": "door", "at_mm": 1400},
    ]
    floorplan.validate_plan(plan)          # the widths are not known yet
    with pytest.raises(FloorPlanError) as caught:
        floorplan.fill_defaults(plan)
    message = str(caught.value)
    assert "'door-a'" in message and "'door-b'" in message and "'wall-01'" in message


def test_two_openings_that_merely_touch_are_allowed():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-a", "kind": "door", "at_mm": 500},
        {"id": "door-b", "kind": "door", "at_mm": 1320},
    ]
    assert len(floorplan.fill_defaults(plan)["walls"][0]["openings"]) == 2


def test_a_door_hinges_left_by_default_and_the_spec_says_which_jamb():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-a", "kind": "door", "at_mm": 1500, "swing": "in"},
        {"id": "door-b", "kind": "door", "at_mm": 2500, "swing": "in",
         "hinge": "right"},
    ]
    left, right = floorplan.component_build_specs(plan)[0]["openings"]
    assert left["hinge"] == "left"
    assert left["hinge_mm"] == pytest.approx([1500 - 410.0, 0.0])
    assert right["hinge"] == "right"
    assert right["hinge_mm"] == pytest.approx([2500 + 410.0, 0.0])


def test_only_a_door_may_hinge():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "win-01", "kind": "window", "at_mm": 1500, "hinge": "left"},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'win-01'" in str(caught.value) and "Only a door hinges" in str(caught.value)


def test_an_unknown_opening_kind_lists_the_kinds_forge_knows():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "hole-01", "kind": "portcullis", "at_mm": 1500},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'hole-01'" in message
    assert "door" in message and "window" in message and "gap" in message


def test_only_a_door_may_swing():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "win-01", "kind": "window", "at_mm": 1500, "swing": "in"},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'win-01'" in str(caught.value)
    assert "Only a door swings" in str(caught.value)


def test_a_label_with_a_zero_footprint_is_refused_and_names_it():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer",
                       "footprint_mm": [0, 0, 0, 813]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'wd-01'" in str(caught.value)
    assert "width" in str(caught.value)


def test_a_label_footprint_of_the_wrong_shape_says_what_the_four_numbers_are():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer",
                       "footprint_mm": [0, 0, 813]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'wd-01'" in str(caught.value)
    assert "[x, y, w, d]" in str(caught.value)


def test_a_mistyped_default_is_refused_because_it_would_build_the_wrong_house():
    plan = minimal_plan()
    plan["defaults"] = {"celing_mm": 2700}
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    message = str(caught.value)
    assert "'celing_mm'" in message
    assert "ceiling_mm" in message


def test_the_defaults_block_is_the_addons_plan_defaults_key_for_key():
    """The resolved plan this module produces is what build_floorplan eats. A
    default the service spells differently is one the add-on quietly replaces
    with its own, and the level comes out a different size than the report."""
    addon = {
        "ceiling_mm": 2400.0, "wall_mm": 100.0, "door_w_mm": 820.0,
        "door_h_mm": 2040.0, "window_w_mm": 1200.0, "window_h_mm": 1200.0,
        "sill_mm": 900.0, "label_h_mm": 850.0, "floor_mm": 50.0,
        "label_anchor": "center",
    }
    assert floorplan.DEFAULTS == addon


def test_an_unknown_label_anchor_default_is_refused():
    plan = minimal_plan()
    plan["defaults"] = {"label_anchor": "middle"}
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "label_anchor" in str(caught.value)


def test_validate_plan_never_mutates_what_it_was_given():
    plan = two_room_plan()
    before = copy.deepcopy(plan)
    floorplan.validate_plan(plan)
    floorplan.fill_defaults(plan)
    floorplan.component_build_specs(plan)
    floorplan.plan_mask(plan, cell_mm=200)
    assert plan == before


def test_an_entry_keeps_the_keys_the_schema_never_named():
    """A door's Phase 17 mechanism record rides along, and reaches the spec."""
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "door-01", "kind": "door", "at_mm": 1500,
         "mechanism": {"joint_type": "revolute", "range_deg": 90}},
    ]
    resolved = floorplan.fill_defaults(plan)
    assert resolved["walls"][0]["openings"][0]["mechanism"]["range_deg"] == 90
    spec = floorplan.component_build_specs(plan)[0]
    assert spec["openings"][0]["mechanism"]["joint_type"] == "revolute"


# ==========================================================================
# fill_defaults
# ==========================================================================


def test_the_defaults_resolve_into_every_entry_that_left_a_number_out():
    resolved = floorplan.fill_defaults(two_room_plan())
    wall = resolved["walls"][1]
    assert wall["thickness_mm"] == 100.0
    assert wall["height_mm"] == 2400.0
    assert resolved["provenance"]["wall-mid"]["from_defaults"] == [
        "height_mm", "thickness_mm",
    ]
    door = wall["openings"][0]
    assert door["width_mm"] == 820.0 and door["height_mm"] == 2040.0
    assert door["sill_mm"] == 0.0
    assert door["hinge"] == "left"
    assert resolved["provenance"]["door-01"]["from_defaults"] == [
        "height_mm", "sill_mm", "width_mm",
    ]


def test_a_resolved_entry_carries_no_provenance_the_addon_would_fingerprint():
    """The add-on hashes each entry's canonical JSON to decide what to rebuild,
    so a note about where a number came from has to live somewhere else."""
    resolved = floorplan.fill_defaults(two_room_plan())
    entries = (resolved["rooms"] + resolved["walls"] + resolved["labels"]
               + [o for w in resolved["walls"] for o in w["openings"]])
    for entry in entries:
        for key in floorplan.PROVENANCE_KEYS:
            assert key not in entry, f"{entry['id']} carries {key}"
    assert set(resolved["provenance"]) == {
        "room-kitchen", "room-living", "wall-south", "wall-mid", "door-01", "wd-01",
    }


def test_an_explicit_number_beats_the_default_and_says_so():
    plan = minimal_plan()
    plan["walls"][0]["thickness_mm"] = 250
    resolved = floorplan.fill_defaults(plan)
    assert resolved["walls"][0]["thickness_mm"] == 250.0
    assert resolved["provenance"]["wall-01"]["from_defaults"] == ["height_mm"]


def test_a_changed_default_changes_what_the_entries_resolve_to():
    plan = minimal_plan()
    plan["defaults"] = {"ceiling_mm": 3000, "wall_mm": 150}
    resolved = floorplan.fill_defaults(plan)
    assert resolved["walls"][0]["height_mm"] == 3000.0
    assert resolved["walls"][0]["thickness_mm"] == 150.0


def test_a_window_takes_the_window_defaults_and_a_gap_runs_floor_to_ceiling():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "win-01", "kind": "window", "at_mm": 1000},
        {"id": "gap-01", "kind": "gap", "at_mm": 2200},
    ]
    resolved = floorplan.fill_defaults(plan)
    window, gap = resolved["walls"][0]["openings"]
    assert window["id"] == "win-01"
    assert (window["width_mm"], window["height_mm"], window["sill_mm"]) == (1200.0, 1200.0, 900.0)
    assert gap["sill_mm"] == 0.0
    assert gap["height_mm"] == resolved["walls"][0]["height_mm"] == 2400.0


def test_a_fixture_height_comes_from_the_appliance_table_and_the_width_never_does():
    """The drawing is evidence; the library is a default for the one number a
    top-down drawing cannot contain."""
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "W/D",
                       "footprint_mm": [0, 0, 900, 700]}]
    resolved = floorplan.fill_defaults(plan)
    label = resolved["labels"][0]
    assert label["height_mm"] == 965.0
    assert resolved["provenance"]["wd-01"]["height_from"] == "appliance"
    assert resolved["provenance"]["wd-01"]["appliance_match"] == "washer/dryer"
    assert label["footprint_mm"][2:] == [900.0, 700.0]  # unchanged by the table


def test_an_unrecognised_fixture_falls_back_to_the_plan_default_height():
    plan = minimal_plan()
    plan["labels"] = [{"id": "thing-01", "label": "grandmother's sideboard",
                       "footprint_mm": [0, 0, 1500, 500]}]
    resolved = floorplan.fill_defaults(plan)
    assert resolved["labels"][0]["height_mm"] == floorplan.DEFAULTS["label_h_mm"]
    record = resolved["provenance"]["thing-01"]
    assert record["height_from"] == "default"
    assert record["appliance_match"] is None
    assert record["from_defaults"] == ["anchor", "height_mm"]


def test_an_explicit_fixture_height_beats_the_appliance_table():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer",
                       "footprint_mm": [0, 0, 1372, 813], "height_mm": 1100}]
    resolved = floorplan.fill_defaults(plan)
    assert resolved["labels"][0]["height_mm"] == 1100.0
    assert resolved["provenance"]["wd-01"]["height_from"] == "plan"


def test_openings_are_sorted_along_the_wall_so_two_orderings_are_one_plan():
    plan = minimal_plan()
    plan["walls"][0]["openings"] = [
        {"id": "win-b", "kind": "window", "at_mm": 2200},
        {"id": "win-a", "kind": "window", "at_mm": 800},
    ]
    other = copy.deepcopy(plan)
    other["walls"][0]["openings"].reverse()
    ids = [o["id"] for o in floorplan.validate_plan(plan)["walls"][0]["openings"]]
    assert ids == ["win-a", "win-b"]
    assert floorplan.diff_plans(plan, other)["changed"] == []


def test_resolving_a_resolved_plan_changes_nothing_but_its_provenance():
    """Idempotency, with the one honest caveat: nothing is taken from the
    defaults the second time round, because nothing is missing any more."""
    once = floorplan.fill_defaults(two_room_plan())
    twice = floorplan.fill_defaults(once)
    for wall_a, wall_b in zip(once["walls"], twice["walls"]):
        assert wall_a["thickness_mm"] == wall_b["thickness_mm"]
        assert wall_a["height_mm"] == wall_b["height_mm"]
    assert once["provenance"]["wall-mid"]["from_defaults"]
    assert twice["provenance"]["wall-mid"]["from_defaults"] == []
    assert floorplan.diff_plans(once, twice)["changed"] == []


# ==========================================================================
# diff_plans -- the incremental-regen law
# ==========================================================================


def test_the_diff_of_a_plan_against_itself_is_all_unchanged():
    plan = two_room_plan()
    diff = floorplan.diff_plans(plan, plan)
    assert set(diff) == {"added", "removed", "changed", "unchanged"}
    assert diff["added"] == [] and diff["removed"] == [] and diff["changed"] == []
    assert diff["unchanged"] == [
        "door-01", "room-kitchen", "room-living", "wall-mid", "wall-south", "wd-01",
    ]


def test_the_diff_reports_an_added_entry_and_leaves_its_neighbours_alone():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"].append({"id": "wall-north", "from_mm": [0, 3000], "to_mm": [7000, 3000]})
    diff = floorplan.diff_plans(old, new)
    assert diff["added"] == ["wall-north"]
    assert diff["changed"] == [] and diff["removed"] == []
    assert "wall-south" in diff["unchanged"]


def test_the_diff_reports_a_removed_entry_by_id():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["labels"] = []
    diff = floorplan.diff_plans(old, new)
    assert diff["removed"] == ["wd-01"]
    assert diff["added"] == [] and diff["changed"] == []


def test_moving_one_wall_marks_that_wall_and_nothing_else():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][0]["to_mm"] = [7500, 0]
    diff = floorplan.diff_plans(old, new)
    assert diff["changed"] == ["wall-south"]
    assert "wall-mid" in diff["unchanged"] and "door-01" in diff["unchanged"]


def test_a_defaults_change_ripples_into_every_wall_that_was_taking_it():
    """The whole reason the diff compares RESOLVED values: raising the ceiling
    changes nothing in the walls block and everything about the walls."""
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][0]["height_mm"] = 2400          # this one now says it itself
    new["defaults"] = {"ceiling_mm": 2700}
    diff = floorplan.diff_plans(old, new)
    assert "wall-mid" in diff["changed"]         # resolved 2400 -> 2700
    assert "wall-south" in diff["unchanged"]     # pinned at 2400, untouched
    assert diff["added"] == [] and diff["removed"] == []


def test_a_defaults_change_that_alters_nothing_resolved_marks_nothing():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["defaults"] = {"sill_mm": 1000}          # there are no windows
    assert floorplan.diff_plans(old, new)["changed"] == []


def test_dropping_an_explicit_value_equal_to_the_default_is_not_a_change():
    """Provenance moved; the wall did not. Rebuilding it would risk a hand edit
    for no reason at all."""
    old = minimal_plan()
    old["walls"][0]["thickness_mm"] = floorplan.DEFAULTS["wall_mm"]
    new = minimal_plan()
    assert floorplan.diff_plans(old, new)["unchanged"] == ["wall-01"]


def test_moving_a_door_marks_the_door_and_its_wall():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][1]["openings"][0]["at_mm"] = 900
    diff = floorplan.diff_plans(old, new)
    assert diff["changed"] == ["door-01", "wall-mid"]
    assert "wall-south" in diff["unchanged"]


def test_adding_a_door_marks_the_door_added_and_its_wall_changed():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][0]["openings"] = [{"id": "door-front", "kind": "door", "at_mm": 1000}]
    diff = floorplan.diff_plans(old, new)
    assert diff["added"] == ["door-front"]
    assert diff["changed"] == ["wall-south"]


def test_deleting_a_door_marks_the_door_removed_and_its_wall_changed():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][1]["openings"] = []
    diff = floorplan.diff_plans(old, new)
    assert diff["removed"] == ["door-01"]
    assert diff["changed"] == ["wall-mid"]


def test_a_door_that_moves_to_another_wall_marks_both_walls():
    old = two_room_plan()
    new = copy.deepcopy(old)
    door = new["walls"][1]["openings"].pop()
    new["walls"][0]["openings"] = [door]
    diff = floorplan.diff_plans(old, new)
    assert diff["changed"] == ["door-01", "wall-mid", "wall-south"]


def test_a_brand_new_wall_full_of_doors_is_added_and_never_also_changed():
    """An id lands in exactly one list, so the add-on never builds it twice."""
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"].append({
        "id": "wall-north", "from_mm": [0, 3000], "to_mm": [7000, 3000],
        "openings": [{"id": "door-back", "kind": "door", "at_mm": 2000}],
    })
    diff = floorplan.diff_plans(old, new)
    assert sorted(diff["added"]) == ["door-back", "wall-north"]
    assert diff["changed"] == []


def test_a_deleted_wall_takes_its_doors_with_it_and_is_not_marked_changed():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"] = [new["walls"][0]]
    diff = floorplan.diff_plans(old, new)
    assert sorted(diff["removed"]) == ["door-01", "wall-mid"]
    assert diff["changed"] == []


def test_every_id_appears_in_exactly_one_of_the_four_lists():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][1]["openings"][0]["at_mm"] = 900
    new["labels"] = []
    new["rooms"].append({"id": "room-hall",
                         "polygon_mm": [[0, 3000], [7000, 3000], [7000, 4000], [0, 4000]]})
    diff = floorplan.diff_plans(old, new)
    seen = diff["added"] + diff["removed"] + diff["changed"] + diff["unchanged"]
    assert len(seen) == len(set(seen))
    assert set(seen) == {"room-kitchen", "room-living", "room-hall", "wall-south",
                         "wall-mid", "door-01", "wd-01"}


def test_an_edit_to_a_passthrough_record_is_a_change_like_any_other():
    old = minimal_plan()
    old["walls"][0]["openings"] = [
        {"id": "door-01", "kind": "door", "at_mm": 1500,
         "mechanism": {"joint_type": "revolute", "range_deg": 90}},
    ]
    new = copy.deepcopy(old)
    new["walls"][0]["openings"][0]["mechanism"]["range_deg"] = 110
    diff = floorplan.diff_plans(old, new)
    assert diff["changed"] == ["door-01", "wall-01"]


def test_a_plan_that_cannot_be_resolved_is_refused_by_the_diff_too():
    good = minimal_plan()
    bad = minimal_plan()
    bad["walls"][0]["openings"] = [{"id": "door-01", "kind": "door", "at_mm": 99000}]
    with pytest.raises(FloorPlanError):
        floorplan.diff_plans(good, bad)


# ==========================================================================
# component_build_specs -- the numbers, on paper
# ==========================================================================


def test_the_build_specs_come_out_walls_then_floors_then_fixtures():
    specs = floorplan.component_build_specs(two_room_plan())
    assert [(s["kind"], s["id"]) for s in specs] == [
        ("wall", "wall-south"), ("wall", "wall-mid"),
        ("floor", "room-kitchen"), ("floor", "room-living"),
        ("fixture", "wd-01"),
    ]
    # Deterministic: the same plan twice is the same list.
    assert specs == floorplan.component_build_specs(two_room_plan())


def test_every_spec_id_is_a_plan_id_so_the_diff_addresses_the_build():
    plan = two_room_plan()
    specs = floorplan.component_build_specs(plan)
    diff = floorplan.diff_plans(plan, plan)
    known = set(diff["unchanged"])
    assert {spec["id"] for spec in specs} <= known


def test_the_shared_wall_spec_is_the_rectangle_the_two_rooms_share():
    specs = {s["id"]: s for s in floorplan.component_build_specs(two_room_plan())}
    wall = specs["wall-mid"]
    assert wall["centerline_mm"] == [[4000.0, 0.0], [4000.0, 3000.0]]
    assert wall["length_mm"] == pytest.approx(3000.0)
    assert wall["angle_deg"] == pytest.approx(90.0)
    assert wall["thickness_mm"] == 100.0 and wall["height_mm"] == 2400.0
    assert wall["z_min_mm"] == 0.0 and wall["z_max_mm"] == 2400.0
    assert wall["center_mm"] == pytest.approx([4000.0, 1500.0, 1200.0])
    xs = sorted({round(x, 6) for x, _y in wall["footprint_mm"]})
    ys = sorted({round(y, 6) for _x, y in wall["footprint_mm"]})
    assert xs == [3950.0, 4050.0]        # 100 mm thick, centred on x = 4000
    assert ys == [0.0, 3000.0]


def test_the_door_cutout_lands_exactly_where_the_arithmetic_says():
    """The door is 820 wide at 1500 along a vertical wall from (4000, 0), so its
    centre is (4000, 1500), it spans y 1090..1910, and it is cut 1 mm proud of
    the wall on each side: x 3949..4051."""
    specs = {s["id"]: s for s in floorplan.component_build_specs(two_room_plan())}
    door = specs["wall-mid"]["openings"][0]
    assert door["id"] == "door-01" and door["kind"] == "door" and door["swing"] == "in"

    cutout = door["cutout_mm"]
    assert cutout["center_mm"] == pytest.approx([4000.0, 1500.0, 1020.0])
    assert cutout["size_mm"] == pytest.approx([820.0, 102.0, 2040.0])
    assert cutout["angle_deg"] == pytest.approx(90.0)
    assert cutout["z_min_mm"] == 0.0 and cutout["z_max_mm"] == pytest.approx(2040.0)
    assert cutout["overshoot_mm"] == floorplan.CUTOUT_OVERSHOOT_MM

    xs = sorted({round(x, 6) for x, _y in cutout["corners_mm"]})
    ys = sorted({round(y, 6) for _x, y in cutout["corners_mm"]})
    assert xs == [3949.0, 4051.0]        # thickness 100 + 1 mm each side
    assert ys == [1090.0, 1910.0]        # 1500 +/- 410

    # The jambs are the door's two edges on the centreline, hinge at the near one.
    flat = [value for jamb in door["jambs_mm"] for value in jamb]
    assert flat == pytest.approx([4000.0, 1090.0, 4000.0, 1910.0])
    assert door["hinge_mm"] == pytest.approx([4000.0, 1090.0])


def test_the_cutout_is_wider_than_the_wall_so_the_boolean_has_no_coincident_face():
    specs = {s["id"]: s for s in floorplan.component_build_specs(two_room_plan())}
    wall = specs["wall-mid"]
    cutout = wall["openings"][0]["cutout_mm"]
    assert cutout["size_mm"][1] == wall["thickness_mm"] + 2 * floorplan.CUTOUT_OVERSHOOT_MM


def test_a_cutout_on_a_diagonal_wall_stays_square_to_that_wall():
    plan = minimal_plan()
    plan["walls"] = [{"id": "wall-diag", "from_mm": [0, 0], "to_mm": [3000, 3000],
                      "openings": [{"id": "door-01", "kind": "door",
                                    "at_mm": math.hypot(3000, 3000) / 2.0}]}]
    cutout = floorplan.component_build_specs(plan)[0]["openings"][0]["cutout_mm"]
    assert cutout["angle_deg"] == pytest.approx(45.0)
    assert cutout["center_mm"][:2] == pytest.approx([1500.0, 1500.0])
    # The cutout's long axis runs along the wall, so its corners are 45 degrees off.
    first, second = cutout["corners_mm"][0], cutout["corners_mm"][1]
    along = math.hypot(first[0] - second[0], first[1] - second[1])
    assert along == pytest.approx(820.0)


def test_walls_that_share_a_corner_grow_into_it_and_free_ends_do_not():
    """A butt-ended box leaves a notch at every corner; a free end that grew
    would stick out past where it was drawn. Both rules at once."""
    plan = minimal_plan()
    plan["walls"] = [
        {"id": "wall-a", "from_mm": [0, 0], "to_mm": [4000, 0]},
        {"id": "wall-b", "from_mm": [4000, 0], "to_mm": [4000, 3000]},
    ]
    specs = {s["id"]: s for s in floorplan.component_build_specs(plan)}
    assert specs["wall-a"]["extended_mm"] == [0.0, 50.0]   # free end, shared end
    assert specs["wall-b"]["extended_mm"] == [50.0, 0.0]
    assert specs["wall-a"]["size_mm"][0] == pytest.approx(4050.0)


def test_a_floor_slab_hangs_below_zero_and_carries_the_room_polygon():
    specs = {s["id"]: s for s in floorplan.component_build_specs(two_room_plan())}
    floor = specs["room-kitchen"]
    assert floor["kind"] == "floor" and floor["label"] == "kitchen"
    assert floor["thickness_mm"] == floorplan.DEFAULTS["floor_mm"] == 50.0
    assert floor["z_min_mm"] == -50.0 and floor["z_max_mm"] == 0.0
    assert floor["area_mm2"] == pytest.approx(4000.0 * 3000.0)
    assert floor["centroid_mm"] == pytest.approx([2000.0, 1500.0])


def test_a_room_can_refuse_its_floor_slab():
    plan = two_room_plan()
    plan["rooms"][0]["floor"] = False
    kinds = [(s["kind"], s["id"]) for s in floorplan.component_build_specs(plan)]
    assert ("floor", "room-kitchen") not in kinds
    assert ("floor", "room-living") in kinds


def test_the_fixture_spec_is_a_box_standing_on_the_floor_at_its_real_height():
    specs = {s["id"]: s for s in floorplan.component_build_specs(two_room_plan())}
    fixture = specs["wd-01"]
    assert fixture["kind"] == "fixture" and fixture["label"] == "washer/dryer"
    assert fixture["size_mm"] == pytest.approx([1372.0, 813.0, 965.0])
    assert fixture["anchor"] == "center"
    assert fixture["center_mm"] == pytest.approx([900.0, 2400.0, 482.5])
    assert fixture["z_min_mm"] == 0.0 and fixture["z_max_mm"] == pytest.approx(965.0)
    assert fixture["appliance"]["match"] == "washer/dryer"
    assert fixture["size_note"] is None          # drawn at the library size
    assert fixture["source"] == "user"
    xs = sorted({round(x, 6) for x, _y in fixture["corners_mm"]})
    ys = sorted({round(y, 6) for _x, y in fixture["corners_mm"]})
    assert xs == [214.0, 1586.0]                 # 900 +/- 686
    assert ys == [1993.5, 2806.5]                # 2400 +/- 406.5


def test_a_corner_anchored_fixture_hangs_off_its_x_y_instead_of_straddling_it():
    """Both readings of [x, y, w, d] are real; the plan says which, and getting
    it wrong puts every fixture half its own size away from the drawing."""
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer", "anchor": "corner",
                       "footprint_mm": [0, 0, 1372, 813]}]
    corner = floorplan.component_build_specs(plan)[-1]
    assert corner["center_mm"][:2] == pytest.approx([686.0, 406.5])

    plan["labels"][0].pop("anchor")
    plan["defaults"] = {"label_anchor": "corner"}
    from_defaults = floorplan.component_build_specs(plan)[-1]
    assert from_defaults["center_mm"][:2] == pytest.approx([686.0, 406.5])

    plan["defaults"] = {}
    centred = floorplan.component_build_specs(plan)[-1]
    assert centred["center_mm"][:2] == pytest.approx([0.0, 0.0])


def test_an_unknown_anchor_is_refused_and_names_the_label():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer", "anchor": "middle",
                       "footprint_mm": [0, 0, 1372, 813]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.validate_plan(plan)
    assert "'wd-01'" in str(caught.value) and "'middle'" in str(caught.value)


def test_a_fixture_drawn_at_the_wrong_size_keeps_its_size_and_gets_a_note():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer",
                       "footprint_mm": [0, 0, 500, 600]}]
    fixture = floorplan.component_build_specs(plan)[-1]
    assert fixture["size_mm"][:2] == pytest.approx([500.0, 600.0])
    assert "you drew 500 x 600" in fixture["size_note"]
    assert "1372" in fixture["size_note"]


def test_a_rotated_fixture_turns_about_its_own_centre():
    plan = minimal_plan()
    plan["labels"] = [{"id": "bed-01", "label": "bed queen", "anchor": "corner",
                       "footprint_mm": [0, 0, 1524, 2032], "rotation_deg": 90}]
    fixture = floorplan.component_build_specs(plan)[-1]
    assert fixture["center_mm"][:2] == pytest.approx([762.0, 1016.0])
    xs = [x for x, _y in fixture["corners_mm"]]
    ys = [_y for _x, _y in fixture["corners_mm"]]
    assert max(xs) - min(xs) == pytest.approx(2032.0)   # swapped by the turn
    assert max(ys) - min(ys) == pytest.approx(1524.0)
    assert fixture["center_mm"][:2] == pytest.approx(
        [(max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0]
    )


def test_a_negative_rotation_is_normalised_to_the_same_turn_going_forward():
    plan = minimal_plan()
    plan["labels"] = [{"id": "desk-01", "label": "desk",
                       "footprint_mm": [0, 0, 1219, 610], "rotation_deg": -90}]
    assert floorplan.validate_plan(plan)["labels"][0]["rotation_deg"] == 270.0


# ==========================================================================
# snap_segments -- the deterministic half of extraction
# ==========================================================================


def wobbly_rectangle(degrees=2.0, width=4000.0, height=3000.0):
    """A 4 x 3 m rectangle drawn 2 degrees off square, as four segments."""
    radians = math.radians(degrees)
    corners = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    turned = [
        [x * math.cos(radians) - y * math.sin(radians),
         x * math.sin(radians) + y * math.cos(radians)]
        for x, y in corners
    ]
    return [{"id": f"seg-{i}", "from_px": turned[i], "to_px": turned[(i + 1) % 4]}
            for i in range(4)]


def test_a_two_degree_wobbly_rectangle_comes_out_square():
    result = floorplan.snap_segments(wobbly_rectangle())
    segments = result["segments"]
    assert len(segments) == 4
    for segment in segments:
        assert segment["axis"] in ("x", "y"), segment
        assert round(segment["angle_deg"] % 90.0, 6) == 0.0


def test_the_snap_reports_every_degree_it_took_out():
    report = floorplan.snap_segments(wobbly_rectangle())["report"]
    assert len(report["angle_snapped"]) == 4
    for entry in report["angle_snapped"]:
        assert entry["delta_deg"] == pytest.approx(2.0, abs=1e-6)
        assert round(entry["now_deg"] % 90.0, 6) == 0.0
    assert report["axis_aligned"] == 4
    assert "straightened onto an axis" in report["summary"]


def test_the_snapped_rectangle_actually_closes_at_its_corners():
    """Straightening four sides about their midpoints opens four gaps; the weld
    pass is what makes the result a rectangle rather than a pinwheel."""
    result = floorplan.snap_segments(wobbly_rectangle())
    ends = []
    for segment in result["segments"]:
        ends.append(tuple(round(v, 3) for v in segment["from_mm"]))
        ends.append(tuple(round(v, 3) for v in segment["to_mm"]))
    assert len(set(ends)) == 4               # four corners, each used twice
    for corner in set(ends):
        assert ends.count(corner) == 2
    assert result["report"]["welded"]


def test_the_snap_keeps_the_rectangle_the_size_it_was_drawn():
    result = floorplan.snap_segments(wobbly_rectangle())
    lengths = sorted(round(s["length_mm"]) for s in result["segments"])
    assert lengths[0] == pytest.approx(2999, abs=5)
    assert lengths[-1] == pytest.approx(3999, abs=5)
    assert result["report"]["max_move_mm"] < 150.0


def test_a_wall_at_forty_five_degrees_is_left_alone_by_the_default_snap():
    segments = [{"id": "diag", "from_px": [0, 0], "to_px": [3000, 3000]}]
    result = floorplan.snap_segments(segments)
    assert result["report"]["angle_snapped"] == []
    assert result["segments"][0]["angle_deg"] == pytest.approx(45.0)
    assert result["segments"][0]["axis"] is None


def test_the_snap_converts_pixels_to_millimetres_with_the_calibration():
    segments = [{"id": "wall", "from_px": [0, 0], "to_px": [400, 0]}]
    result = floorplan.snap_segments(segments, mm_per_px=12.5)
    assert result["segments"][0]["length_mm"] == pytest.approx(5000.0)
    assert result["report"]["mm_per_px"] == 12.5


def test_a_segment_already_in_millimetres_is_not_scaled_twice():
    segments = [{"id": "wall", "from_mm": [0, 0], "to_mm": [5000, 0]}]
    result = floorplan.snap_segments(segments, mm_per_px=12.5)
    assert result["segments"][0]["length_mm"] == pytest.approx(5000.0)


def test_mixing_pixels_and_millimetres_in_one_segment_is_refused():
    segments = [{"id": "wall", "from_px": [0, 0], "to_mm": [5000, 0]}]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.snap_segments(segments)
    assert "scaled twice" in str(caught.value)


def test_a_broken_wall_is_joined_back_into_one_run_and_the_join_is_reported():
    segments = [
        {"id": "a", "from_px": [0, 0], "to_px": [2000, 0]},
        {"id": "b", "from_px": [2010, 0], "to_px": [4000, 0]},
    ]
    result = floorplan.snap_segments(segments)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["length_mm"] == pytest.approx(4000.0)
    assert result["report"]["joined"][0]["absorbed"] == ["b"]


def test_a_doorway_gap_is_far_too_wide_to_be_joined_across():
    segments = [
        {"id": "a", "from_px": [0, 0], "to_px": [1500, 0]},
        {"id": "b", "from_px": [2320, 0], "to_px": [4000, 0]},   # an 820 mm door
    ]
    result = floorplan.snap_segments(segments)
    assert len(result["segments"]) == 2
    assert result["report"]["joined"] == []


def test_the_join_can_be_switched_off():
    segments = [
        {"id": "a", "from_px": [0, 0], "to_px": [2000, 0]},
        {"id": "b", "from_px": [2010, 0], "to_px": [4000, 0]},
    ]
    result = floorplan.snap_segments(segments, join_collinear=False)
    assert len(result["segments"]) == 2


def test_a_tick_mark_is_dropped_and_says_why():
    segments = [
        {"id": "wall", "from_px": [0, 0], "to_px": [3000, 0]},
        {"id": "tick", "from_px": [1000, 500], "to_px": [1002, 500]},
    ]
    result = floorplan.snap_segments(segments, merge_mm=10.0)
    assert [s["id"] for s in result["segments"]] == ["wall"]
    assert result["report"]["dropped"][0]["id"] == "tick"
    assert "min_length_mm" in result["report"]["dropped"][0]["reason"]


def test_a_segment_with_no_id_is_numbered_and_the_report_says_which():
    segments = [{"from_px": [0, 0], "to_px": [3000, 0]}]
    result = floorplan.snap_segments(segments)
    assert result["segments"][0]["id"] == "seg-01"
    assert result["report"]["ids_assigned"] == ["seg-01"]


def test_two_segments_cannot_share_an_id():
    segments = [
        {"id": "a", "from_px": [0, 0], "to_px": [3000, 0]},
        {"id": "a", "from_px": [0, 500], "to_px": [3000, 500]},
    ]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.snap_segments(segments)
    assert "'a'" in str(caught.value)


def test_the_snap_is_deterministic():
    first = floorplan.snap_segments(wobbly_rectangle())
    second = floorplan.snap_segments(wobbly_rectangle())
    assert first == second


def test_a_wobble_past_the_tolerance_is_left_crooked_rather_than_forced():
    result = floorplan.snap_segments(wobbly_rectangle(degrees=20.0))
    assert result["report"]["angle_snapped"] == []
    assert result["report"]["axis_aligned"] == 0


# ==========================================================================
# plan_mask -- the plan as a picture
# ==========================================================================


def test_a_plan_mask_against_itself_is_exactly_one():
    """The floor under every later IoU claim: if this is not 1.0, no number
    measured against a render means anything."""
    plan = two_room_plan()
    first = floorplan.plan_mask(plan, cell_mm=100.0)
    second = floorplan.plan_mask(plan, cell_mm=100.0)
    assert floorplan.mask_iou(first, second) == 1.0
    assert floorplan.mask_iou(first["mask"], second["mask"]) == 1.0


def test_the_mask_is_a_numpy_bool_grid_with_its_own_transform():
    result = floorplan.plan_mask(two_room_plan(), cell_mm=100.0)
    mask = result["mask"]
    assert isinstance(mask, np.ndarray) and mask.dtype == np.bool_
    assert list(mask.shape) == result["shape"]
    assert result["cells_filled"] == int(mask.sum()) > 0
    assert result["area_mm2"] == pytest.approx(result["cells_filled"] * 100.0 * 100.0)
    assert result["origin_mm"][0] % 100.0 == 0.0


def test_the_mask_covers_the_walls_and_the_fixtures_but_not_the_floors():
    """A floor slab fills a room completely; an IoU against a filled rectangle
    measures the rectangle, not the build."""
    result = floorplan.plan_mask(two_room_plan(), cell_mm=100.0)
    assert set(result["components"]) == {"wall-south", "wall-mid", "wd-01"}
    assert all(count > 0 for count in result["components"].values())


def test_the_fixture_footprint_is_about_the_area_it_was_drawn_at():
    plan = minimal_plan()
    plan["labels"] = [{"id": "wd-01", "label": "washer/dryer",
                       "footprint_mm": [1000, 1000, 1372, 813]}]
    result = floorplan.plan_mask(plan, cell_mm=25.0)
    drawn = 1372.0 * 813.0
    assert result["components"]["wd-01"] * 25.0 * 25.0 == pytest.approx(drawn, rel=0.05)


def test_a_full_height_gap_is_a_hole_in_the_mask_and_a_door_is_not():
    """Seen from above, a doorway with a header over it is still a solid wall."""
    with_door = minimal_plan()
    with_door["walls"][0]["openings"] = [
        {"id": "door-01", "kind": "door", "at_mm": 1500},
    ]
    with_gap = minimal_plan()
    with_gap["walls"][0]["openings"] = [
        {"id": "gap-01", "kind": "gap", "at_mm": 1500},
    ]
    plain = floorplan.plan_mask(minimal_plan(), cell_mm=25.0)
    door = floorplan.plan_mask(with_door, cell_mm=25.0)
    gap = floorplan.plan_mask(with_gap, cell_mm=25.0)
    assert door["components"]["wall-01"] == plain["components"]["wall-01"]
    assert gap["components"]["wall-01"] < plain["components"]["wall-01"]


def test_moving_a_wall_moves_the_mask_so_the_iou_falls():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["walls"][1]["from_mm"] = [5000, 0]
    new["walls"][1]["to_mm"] = [5000, 3000]
    bounds = floorplan.plan_bounds(old)
    before = floorplan.plan_mask(old, cell_mm=50.0, bounds_mm=bounds)
    after = floorplan.plan_mask(new, cell_mm=50.0, bounds_mm=bounds)
    iou = floorplan.mask_iou(before, after)
    assert 0.0 < iou < 1.0


def test_two_masks_on_different_grids_are_refused_rather_than_compared():
    plan = two_room_plan()
    coarse = floorplan.plan_mask(plan, cell_mm=100.0)
    fine = floorplan.plan_mask(plan, cell_mm=50.0)
    with pytest.raises(FloorPlanError) as caught:
        floorplan.mask_iou(coarse, fine)
    assert "different grids" in str(caught.value)


def test_a_silly_cell_size_is_refused_and_names_the_number_to_change():
    with pytest.raises(FloorPlanError) as caught:
        floorplan.plan_mask(two_room_plan(), cell_mm=0.05)
    assert "cell_mm" in str(caught.value)


def test_an_empty_plan_has_nothing_to_measure_and_says_so():
    plan = {"version": 1, "units": "mm"}
    with pytest.raises(FloorPlanError) as caught:
        floorplan.plan_bounds(plan)
    assert "nothing to measure" in str(caught.value)


def test_shared_bounds_let_two_plans_be_compared_cell_for_cell():
    old = two_room_plan()
    new = copy.deepcopy(old)
    new["labels"] = []
    bounds = floorplan.plan_bounds(old)
    before = floorplan.plan_mask(old, cell_mm=50.0, bounds_mm=bounds)
    after = floorplan.plan_mask(new, cell_mm=50.0, bounds_mm=bounds)
    assert before["shape"] == after["shape"]
    assert floorplan.mask_iou(before, after) < 1.0


# ==========================================================================
# absorb_reconcile -- the scene's hand edits, folded back into the plan
# ==========================================================================
#
# The failure this exists for, in the owner's words: *"i deleted the wall
# because there isnt a wall there, i'd like to play with things to determine
# optimal layout, having it undone doesnt make sense."*  `build_floorplan` put
# FP:wall-living-kitchen back twice.  So the tests below are mostly about what
# is NOT done: a default is not turned into an explicit number by a move that
# did not touch it, a wall is not shortened out from under its door, a plan is
# not emptied because somebody reconciled against the wrong collection, and a
# deletion is not argued with.
#
# The add-on measures and this half decides, so every fixture here is a report
# shaped exactly as `reconcile_floorplan` returns one -- millimetres, and no
# opinions in it.


def absorb_plan():
    """One room, two walls (one with a door), one fixture. Every optional
    number left out, so the defaults are live and can be seen to stay that way."""
    return {
        "version": 1, "units": "mm",
        "rooms": [{"id": "room-k", "label": "kitchen",
                   "polygon_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
        "walls": [
            {"id": "wall-01", "from_mm": [0, 0], "to_mm": [4000, 0],
             "openings": [{"id": "door-01", "kind": "door", "at_mm": 2000}]},
            {"id": "wall-02", "from_mm": [4000, 0], "to_mm": [4000, 3000]},
        ],
        "labels": [{"id": "wd-01", "label": "washer/dryer",
                    "footprint_mm": [600, 400, 700, 700], "height_mm": 900}],
    }


def wall_measurement(**overrides):
    """`wall-01` measured where the plan puts it, unless told otherwise."""
    measured = {"from_mm": [0.0, 0.0], "to_mm": [4000.0, 0.0],
                "thickness_mm": 100.0, "height_mm": 2400.0, "base_z_mm": 0.0}
    measured.update(overrides)
    was = {"from_mm": [0.0, 0.0], "to_mm": [4000.0, 0.0],
           "thickness_mm": 100.0, "height_mm": 2400.0, "base_z_mm": 0.0}
    return {"id": "wall-01", "kind": "wall", "object": "FP:wall-01",
            "measured": measured, "was": was, "changed": ["from_mm", "to_mm"],
            "moved_mm": 0.0, "mesh": "intact", "confidence": "measured"}


def fixture_measurement(**overrides):
    measured = {"centre_mm": [600.0, 400.0], "size_mm": [700.0, 700.0],
                "height_mm": 900.0, "rotation_deg": 0.0, "base_z_mm": 0.0}
    measured.update(overrides)
    return {"id": "wd-01", "kind": "label", "object": "FP:wd-01",
            "measured": measured, "changed": ["centre_mm"], "moved_mm": 0.0,
            "mesh": "intact", "confidence": "measured"}


def report(**overrides):
    body = {"collection": "Floorplan", "clean": [], "moved": [], "resized": [],
            "stale": [], "deleted_in_scene": [], "candidates": [],
            "unabsorbable": []}
    body.update(overrides)
    return body


def test_absorb_applies_a_move_to_the_wall_that_moved():
    """The whole feature in one assertion: they dragged it, the plan says so."""
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    moved["moved_mm"] = 500.0
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))

    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert wall["from_mm"] == [0.0, 500.0]
    assert wall["to_mm"] == [4000.0, 500.0]
    assert [record["id"] for record in out["applied"]] == ["wall-01"]
    assert "500" in out["applied"][0]["what"]
    assert out["applied"][0]["fields"] == ["from_mm", "to_mm"]


def test_a_move_leaves_every_other_wall_exactly_as_it_was():
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    before = floorplan.validate_plan(absorb_plan())
    after = out["plan"]
    assert after["walls"][1] == before["walls"][1]
    assert after["rooms"] == before["rooms"]
    assert after["labels"] == before["labels"]


def test_a_move_that_did_not_touch_the_thickness_leaves_the_DEFAULT_live():
    """The subtle one, and the reason a field is written only when it changed.

    Copying a measured 100 mm thickness onto a wall that never changed would
    turn a live default into an explicit number, and raising
    `defaults.wall_mm` next week would then move nothing.
    """
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert "thickness_mm" not in wall
    assert "height_mm" not in wall


def test_a_resize_writes_the_thickness_and_the_height():
    resized = wall_measurement(thickness_mm=150.0, height_mm=2700.0)
    resized["changed"] = ["thickness_mm", "height_mm"]
    out = floorplan.absorb_reconcile(absorb_plan(), report(resized=[resized]))
    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert wall["thickness_mm"] == 150.0
    assert wall["height_mm"] == 2700.0
    assert "150" in out["applied"][0]["what"]


def test_an_opening_is_re_anchored_proportionally_when_its_wall_changes_length():
    """A door halfway along a wall is halfway along the new one."""
    longer = wall_measurement(to_mm=[8000.0, 0.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[longer]))
    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert wall["openings"][0]["at_mm"] == 4000.0  # 2000/4000 -> 4000/8000
    assert any("re-anchored" in record["what"] for record in out["applied"])


def test_an_opening_that_no_longer_fits_stops_its_whole_wall_with_a_sentence():
    """Absorbing the wall and leaving the door hanging off the end would write
    a plan that refuses to validate -- for an edit made with a mouse."""
    short = wall_measurement(to_mm=[700.0, 0.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(resized=[short]))

    assert out["applied"] == []
    why = next(item["why"] for item in out["skipped"] if item["id"] == "wall-01")
    assert "door-01" in why and "820" in why and "700" in why
    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert wall["to_mm"] == [4000.0, 0.0]          # not applied, not half-applied
    assert wall["openings"][0]["at_mm"] == 2000.0


def test_a_deletion_is_ABSORBED_rather_than_queried():
    """The incident: they deleted the wall because there is no wall there."""
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(deleted_in_scene=[{"id": "wall-02", "kind": "wall"}]),
    )
    assert [w["id"] for w in out["plan"]["walls"]] == ["wall-01"]
    assert out["deleted"] == ["wall-02"]
    record = next(r for r in out["applied"] if r["id"] == "wall-02")
    assert "absorbed your deletion" in record["what"]
    assert any("arguing" in note for note in out["notes"])


def test_a_deleted_wall_takes_its_openings_out_with_it():
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(deleted_in_scene=[{"id": "wall-01", "kind": "wall"}]),
    )
    assert [w["id"] for w in out["plan"]["walls"]] == ["wall-02"]
    record = next(r for r in out["applied"] if r["id"] == "wall-01")
    assert record["openings"] == ["door-01"]
    assert "door-01" in record["what"]
    # and the plan still validates with the door gone
    assert floorplan.diff_plans(absorb_plan(), out["plan"])["removed"] == [
        "door-01", "wall-01"
    ]


def test_a_deleted_room_drops_its_slab_entry():
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(deleted_in_scene=[{"id": "room-k", "kind": "room"}]),
    )
    assert out["plan"]["rooms"] == []
    assert out["deleted"] == ["room-k"]


def test_confirm_deletions_asks_instead_of_absorbing():
    """The opt-in for a cautious caller. Deliberately not the default."""
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(deleted_in_scene=[{"id": "wall-02", "kind": "wall"}]),
        confirm_deletions=True,
    )
    assert [w["id"] for w in out["plan"]["walls"]] == ["wall-01", "wall-02"]
    assert out["deleted"] == []
    why = next(item["why"] for item in out["skipped"] if item["id"] == "wall-02")
    assert "say the word" in why


def test_deletions_that_would_empty_the_plan_are_refused_as_a_wrong_collection():
    """Reconciling against the collection the level is NOT in must not demolish
    the plan -- the likeliest cause of 'everything is missing' is a typo."""
    everything = [{"id": ident, "kind": kind} for ident, kind in
                  (("room-k", "room"), ("wall-01", "wall"),
                   ("wall-02", "wall"), ("wd-01", "label"))]
    out = floorplan.absorb_reconcile(absorb_plan(),
                                     report(deleted_in_scene=everything))
    assert out["deleted"] == []
    assert len(out["skipped"]) == 4
    assert all("collection" in item["why"] for item in out["skipped"])
    assert len(out["plan"]["walls"]) == 2


def test_a_fixture_absorbs_its_footprint_its_spin_and_its_height():
    resized = fixture_measurement(centre_mm=[1200.0, 900.0],
                                  size_mm=[1400.0, 700.0],
                                  rotation_deg=90.0, height_mm=1000.0)
    out = floorplan.absorb_reconcile(absorb_plan(), report(resized=[resized]))
    fixture = out["plan"]["labels"][0]
    assert fixture["footprint_mm"] == [1200.0, 900.0, 1400.0, 700.0]
    assert fixture["rotation_deg"] == 90.0
    assert fixture["height_mm"] == 1000.0


def test_a_corner_anchored_fixture_keeps_its_corner_convention():
    """`footprint_mm`'s [x, y] means what the entry says it means; a measured
    CENTRE written into a corner-anchored entry would move the box by half of
    itself."""
    plan = absorb_plan()
    plan["labels"][0]["anchor"] = "corner"
    plan["labels"][0]["footprint_mm"] = [600, 400, 700, 700]
    moved = fixture_measurement(centre_mm=[2000.0, 2000.0])
    out = floorplan.absorb_reconcile(plan, report(moved=[moved]))
    assert out["plan"]["labels"][0]["footprint_mm"] == [1650.0, 1650.0, 700.0, 700.0]


def test_a_room_that_was_only_MOVED_shifts_every_corner_of_its_polygon():
    moved = {"id": "room-k", "kind": "room", "object": "FP:room-k",
             "moved_mm": 1000.0, "changed": ["centre_mm"],
             "measured": {"outline_bbox_mm": {"min": [1000.0, 0.0],
                                              "max": [5000.0, 3000.0],
                                              "size": [4000.0, 3000.0]},
                          "centre_mm": [3000.0, 1500.0],
                          "thickness_mm": 50.0, "rotation_deg": 0.0,
                          "top_z_mm": 0.0}}
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    assert out["plan"]["rooms"][0]["polygon_mm"] == [
        [1000.0, 0.0], [5000.0, 0.0], [5000.0, 3000.0], [1000.0, 3000.0]
    ]
    assert "every corner" in out["applied"][0]["what"]


def test_a_room_that_was_also_resized_is_refused_naming_the_polygon():
    """A bounding box cannot say which corners moved, so it does not pretend to."""
    stretched = {"id": "room-k", "kind": "room",
                 "measured": {"outline_bbox_mm": {"min": [0.0, 0.0],
                                                  "max": [6000.0, 3000.0],
                                                  "size": [6000.0, 3000.0]},
                              "centre_mm": [3000.0, 1500.0],
                              "thickness_mm": 50.0, "rotation_deg": 0.0,
                              "top_z_mm": 0.0}}
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[stretched]))
    assert out["applied"] == []
    why = next(item["why"] for item in out["skipped"] if item["id"] == "room-k")
    assert "polygon_mm" in why


def test_an_unabsorbable_object_carries_the_add_ons_own_reason_through():
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(unabsorbable=[{"id": "wd-01", "object": "FP:wd-01",
                              "why": "it has been SCULPTED rather than resized"}]),
    )
    why = next(item["why"] for item in out["skipped"] if item["id"] == "wd-01")
    assert "SCULPTED" in why


def test_a_candidate_box_is_never_added_on_its_own():
    """A new fixture needs an id and a label, and both come from a person."""
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(candidates=[{"id": "sofa", "object": "FP:sofa",
                            "suggested": {"footprint_mm": [2000, 2000, 1800, 900]}}]),
    )
    assert len(out["plan"]["labels"]) == 1
    why = next(item["why"] for item in out["skipped"] if item["id"] == "sofa")
    assert "id and a label" in why


def test_skip_ids_lets_the_callers_own_plan_edit_win():
    """`floorplan_build` absorbs the scene first and applies the edit second, so
    an id in both places belongs to the edit -- and the skip says so."""
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]),
                                     skip_ids=["wall-01"])
    wall = next(w for w in out["plan"]["walls"] if w["id"] == "wall-01")
    assert wall["from_mm"] == [0.0, 0.0]
    why = next(item["why"] for item in out["skipped"] if item["id"] == "wall-01")
    assert "wins over the scene" in why


def test_skip_ids_protects_a_deletion_too():
    out = floorplan.absorb_reconcile(
        absorb_plan(),
        report(deleted_in_scene=[{"id": "wall-02", "kind": "wall"}]),
        skip_ids=["wall-02"],
    )
    assert [w["id"] for w in out["plan"]["walls"]] == ["wall-01", "wall-02"]
    assert out["deleted"] == []


def test_a_measurement_for_an_id_the_plan_does_not_have_is_a_skip():
    stray = wall_measurement()
    stray["id"] = "wall-99"
    stray["measured"]["from_mm"] = [0.0, 900.0]
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[stray]))
    assert out["applied"] == []
    assert "no entry with that id" in out["skipped"][0]["why"]


def test_a_report_that_measures_nothing_changes_nothing_and_says_so():
    out = floorplan.absorb_reconcile(absorb_plan(), report(clean=["wall-01"]))
    assert out["applied"] == []
    assert out["plan"] == floorplan.validate_plan(absorb_plan())
    assert any("already says what the scene shows" in note for note in out["notes"])


def test_the_input_plan_is_never_mutated():
    plan = absorb_plan()
    before = copy.deepcopy(plan)
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    floorplan.absorb_reconcile(plan, report(
        moved=[moved], deleted_in_scene=[{"id": "wall-02", "kind": "wall"}]))
    assert plan == before


def test_absorbing_is_deterministic():
    """No AI anywhere near this, and the test says so in the only way it can."""
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    body = report(moved=[moved], resized=[fixture_measurement(size_mm=[900.0, 700.0])],
                  deleted_in_scene=[{"id": "wall-02", "kind": "wall"}])
    first = floorplan.absorb_reconcile(absorb_plan(), body)
    second = floorplan.absorb_reconcile(absorb_plan(), body)
    assert first == second


def test_the_absorbed_plan_is_a_normalised_plan_that_still_validates():
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    assert floorplan.validate_plan(out["plan"]) == out["plan"]
    assert floorplan.fill_defaults(out["plan"])["walls"][0]["height_mm"] == 2400.0


def test_absorbing_a_move_makes_the_plan_and_the_scene_agree_on_exactly_one_id():
    """What the diff quote after an absorb has to be able to say."""
    moved = wall_measurement(from_mm=[0.0, 500.0], to_mm=[4000.0, 500.0])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    summary = floorplan.diff_plans(absorb_plan(), out["plan"])
    assert summary["changed"] == ["wall-01"]
    assert summary["added"] == [] and summary["removed"] == []


def test_a_report_that_is_not_a_report_is_refused_by_type():
    with pytest.raises(FloorPlanError) as caught:
        floorplan.absorb_reconcile(absorb_plan(), ["wall-01"])
    assert "reconcile_floorplan returned" in str(caught.value)


def test_a_broken_plan_is_refused_before_anything_is_absorbed():
    broken = absorb_plan()
    broken["walls"][0]["to_mm"] = [0, 0]
    with pytest.raises(FloorPlanError) as caught:
        floorplan.absorb_reconcile(broken, report())
    assert "wall-01" in str(caught.value)


def test_the_absorb_tolerance_is_the_add_ons_number():
    """Half a millimetre, the same number `KEEP_TOL_MM` uses in the add-on.

    Two different tolerances would mean a difference the add-on calls float
    noise and this module calls an edit -- which would write an explicit value
    onto an entry that never moved.
    """
    assert floorplan.ABSORB_TOL_MM == 0.5
    assert floorplan.ABSORB_TOL_DEG == 0.05


def test_a_sub_tolerance_wobble_is_not_an_edit():
    moved = wall_measurement(from_mm=[0.0, 0.2], to_mm=[4000.0, 0.2])
    out = floorplan.absorb_reconcile(absorb_plan(), report(moved=[moved]))
    assert out["applied"] == []
    assert out["plan"]["walls"][0]["from_mm"] == [0.0, 0.0]
