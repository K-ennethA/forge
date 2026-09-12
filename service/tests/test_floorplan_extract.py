"""Reading a drawing -- the layer that exists because one was read by eye.

The failure this suite guards is specific and it really happened: given a drawn
floor plan, the assistant looked at the bitmap and typed out coordinates.  The
level came out with the wrong footprint, the rooms in the wrong places, and a
**diagonal wall that exists nowhere in the drawing**.  So the assertions here
are about arithmetic, not about taste:

* every wall is axis-aligned, in every test, because a diagonal is supposed to
  be impossible by construction;
* the ids are the same across two runs of the same image, because an id names a
  Blender object and a changed id deletes the artist's work;
* the plan passes ``validate_plan``, because anything else is a refusal the
  artist sees instead of a level;
* three pixels of jitter in a hand-placed block come out as one clean grid.

Every fixture is SYNTHESISED here with numpy and Pillow -- no PNGs on disk, so
there is nothing to go stale and the drawing that failed a test is readable in
the test that failed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np
import pytest

from service import floorplan
from service.floorplan import FloorPlanError
from service.floorplan_extract import extract_floorplan, mm_per_px_from

Image = pytest.importorskip("PIL.Image")


# ==========================================================================
# The drawing, synthesised -- the owner's style, block for block
# ==========================================================================

WHITE = (255, 255, 255)
GREY = (200, 200, 200)
GREY_2 = (168, 168, 168)
YARD = (222, 222, 222)
BLACK = (20, 20, 20)

BLUE = (66, 133, 244)    # house door
GREEN = (52, 168, 83)    # open doorway
RED = (219, 68, 55)      # room door


def canvas(width: int = 600, height: int = 500) -> np.ndarray:
    """A white sheet, exactly as a design tool would export one."""
    return np.full((height, width, 3), WHITE, dtype=np.uint8)


def block(sheet: np.ndarray, x0: int, y0: int, x1: int, y1: int,
          color: Sequence[int]) -> None:
    """A flat axis-aligned rectangle, pixel-clean: [x0, x1) x [y0, y1)."""
    sheet[y0:y1, x0:x1] = np.asarray(color, dtype=np.uint8)


def text_noise(sheet: np.ndarray, x: int, y: int, letters: int = 4) -> None:
    """Thin dark strokes inside a fill -- a room name, as far as pixels care.

    Deliberately drawn as bars 2 px wide with 1 px gaps, which is what kills a
    naive connected-components pass: the fill it sits in stops being one blob.
    """
    for index in range(letters):
        left = x + index * 5
        sheet[y:y + 9, left:left + 2] = np.asarray(BLACK, dtype=np.uint8)
        sheet[y + 4:y + 6, left:left + 4] = np.asarray(BLACK, dtype=np.uint8)


def save(sheet: np.ndarray, tmp_path, name: str = "plan.png") -> str:
    path = tmp_path / name
    Image.fromarray(sheet, mode="RGB").save(str(path))
    return str(path)


#: The replica, on a 600 x 500 sheet with a 20 px grid and a 20 px wall gap.
#: Two rooms of different sizes sharing a wall, a wide room under both of them,
#: a DETACHED garage, a yard (the outdoor area), three colours of door strip,
#: a legend block of swatches, and text in every room.
#:
#:   r1  x  40..180  y  40..140   the small room
#:   r2  x 200..380  y  40..140   the big room beside it  (20 px wall between)
#:   r3  x  40..380  y 160..280   the wide room under both (20 px wall above)
#:   r4  x  40..180  y 360..460   the garage, 80 px clear of the house
#:   r5  x 440..580  y 360..460   the yard, sharing a wall with nothing
#:
#: Reading order -- top to bottom, then left to right -- makes those exactly
#: r1..r5, which is the order an artist would list them in.
WALL_GAP = 20
GRID = 20


def owner_drawing(jitter: Sequence[int] = ()) -> np.ndarray:
    """The representative drawing.  *jitter* nudges each block's edges."""
    sheet = canvas()
    offsets = list(jitter) + [0] * 32

    def shove(index: int) -> int:
        return int(offsets[index])

    # Two rooms across the top, sharing a vertical wall of white.
    block(sheet, 40 + shove(0), 40 + shove(1), 180 + shove(2), 140 + shove(3), GREY)
    block(sheet, 200 + shove(4), 40 + shove(5), 380 + shove(6), 140 + shove(7), GREY_2)
    # One wide room below them, sharing a horizontal wall with both.
    block(sheet, 40 + shove(8), 160 + shove(9), 380 + shove(10), 280 + shove(11), GREY)
    # A detached garage: no shared wall anywhere.
    block(sheet, 40, 360, 180, 460, GREY_2)
    # The outdoor area, off on its own.
    block(sheet, 440, 360, 580, 460, YARD)

    # Door strips, each drawn ACROSS its wall so it touches both sides.
    # red room door in the vertical wall between the top two rooms
    block(sheet, 180, 60, 200, 100, RED)
    # green open doorway from the small room down into the wide room
    block(sheet, 80, 140, 140, 160, GREEN)
    # red room door from the big room down into the wide room
    block(sheet, 260, 140, 320, 160, RED)
    # blue house door on the wide room's south wall, drawn HALF OVER the fill --
    # which is how a door gets drawn, and which would otherwise take a bite out
    # of the room's outline.
    block(sheet, 160, 270, 220, 290, BLUE)

    # Text in every room -- noise, and the naming pass's whole job.
    text_noise(sheet, 70, 80)
    text_noise(sheet, 240, 80)
    text_noise(sheet, 150, 210)
    text_noise(sheet, 70, 390)
    text_noise(sheet, 470, 390)

    # The legend: three swatches floating on white, touching no room at all.
    block(sheet, 480, 60, 500, 76, BLUE)
    block(sheet, 480, 100, 500, 116, GREEN)
    block(sheet, 480, 140, 500, 156, RED)
    text_noise(sheet, 510, 60, letters=3)
    text_noise(sheet, 510, 100, letters=3)
    text_noise(sheet, 510, 140, letters=3)
    return sheet


LEGEND = {"#4285f4": "house_door", "#34a853": "doorway", "#db4437": "room_door"}


@pytest.fixture
def drawing(tmp_path) -> str:
    return save(owner_drawing(), tmp_path)


@pytest.fixture
def read(drawing) -> Dict[str, Any]:
    return extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)


def ids(entries: Sequence[Any]) -> List[str]:
    return [str(entry["id"]) for entry in entries]


# ==========================================================================
# The replica: counts, shapes, adjacency
# ==========================================================================


def test_finds_every_room_and_nothing_else(read):
    assert len(read["plan"]["rooms"]) == 5
    assert ids(read["plan"]["rooms"]) == [
        "room-r1", "room-r2", "room-r3", "room-r4", "room-r5",
    ]


def test_reading_order_is_top_to_bottom_then_left_to_right(read):
    tops = [region["bbox_px"][1] for region in read["regions"]]
    assert tops == sorted(tops)
    # r1 and r2 start on the same row, so they are ordered left to right
    assert tops[0] == tops[1]
    assert read["regions"][0]["bbox_px"][0] < read["regions"][1]["bbox_px"][0]
    # r4 (the garage) and r5 (the yard) likewise, further down the sheet
    assert tops[3] == tops[4]
    assert read["regions"][3]["bbox_px"][0] < read["regions"][4]["bbox_px"][0]


def test_text_inside_a_room_does_not_split_it(read):
    """Four bars of black inside a fill are a name, not four rooms."""
    assert len(read["plan"]["rooms"]) == 5
    for region in read["regions"]:
        assert region["corners"] == 4, region["confidence_reasons"]


def test_a_door_drawn_over_a_room_does_not_dent_it(read):
    """The blue strip covers 10 px of r3's fill. A bite is not a bay window."""
    wide = next(region for region in read["regions"] if region["id"] == "room-r3")
    assert wide["corners"] == 4
    assert wide["size_mm"] == [3400.0, 1200.0]
    assert any("given back to the room" in note for note in read["report"]["notes"])


def test_the_door_drawn_over_the_room_is_still_a_door(read):
    """Healing the fill must not cost the opening that caused it."""
    house = [entry for entry in read["report"]["openings"]
             if entry["role"] == "house_door"]
    assert len(house) == 1
    assert house[0]["width_mm"] == 600.0


def test_every_room_is_a_clean_rectangle_on_the_grid(read):
    for region in read["regions"]:
        assert region["fidelity"] == pytest.approx(1.0, abs=0.02)
        assert region["confidence"] >= 0.95


def test_room_sizes_are_the_pixels_that_were_drawn(read):
    """r1 is 140 x 100 px at 10 mm/px, so 1400 x 1000 mm. Measured, not guessed."""
    sizes = {region["id"]: region["size_mm"] for region in read["regions"]}
    assert sizes["room-r1"] == [1400.0, 1000.0]
    assert sizes["room-r2"] == [1800.0, 1000.0]
    assert sizes["room-r3"] == [3400.0, 1200.0]
    assert sizes["room-r4"] == [1400.0, 1000.0]


def test_polygon_is_rectilinear_every_corner(read):
    for room in read["plan"]["rooms"]:
        polygon = room["polygon_mm"]
        assert len(polygon) >= 4
        for index in range(len(polygon)):
            x0, y0 = polygon[index]
            x1, y1 = polygon[(index + 1) % len(polygon)]
            assert abs(x1 - x0) < 1e-6 or abs(y1 - y0) < 1e-6, (room["id"], index)


# ==========================================================================
# Walls -- the thing the eyeballed plan got wrong
# ==========================================================================


def test_no_wall_is_ever_diagonal(read):
    for wall in read["plan"]["walls"]:
        dx = wall["to_mm"][0] - wall["from_mm"][0]
        dy = wall["to_mm"][1] - wall["from_mm"][1]
        assert abs(dx) < 1e-6 or abs(dy) < 1e-6, wall["id"]


def test_validate_plan_reports_no_off_axis_wall(read):
    assert floorplan.plan_warnings(read["plan"]) == []
    assert read["report"]["warnings"] == []


def test_shared_walls_name_the_two_rooms_that_share_them(read):
    shared = [wall["id"] for wall in read["plan"]["walls"] if "-out-" not in wall["id"]]
    # r1 | r2 across the top, and both of them onto the wide room below
    assert sorted(shared) == ["wall-r1-r2", "wall-r1-r3", "wall-r2-r3"]


def test_wide_white_is_open_space_and_not_a_wall(read):
    """80 px of white is wider than this drawing's 20 px walls, so it is a gap."""
    shared = [wall["id"] for wall in read["plan"]["walls"] if "-out-" not in wall["id"]]
    assert not any("r4" in wall or "r5" in wall for wall in shared)
    assert any("r3 and r4" in sentence and "open space" in sentence
               for sentence in read["report"]["ambiguous"])


def test_the_detached_garage_has_only_outside_walls(read):
    garage = [wall for wall in read["plan"]["walls"] if wall["id"].startswith("wall-r4")]
    assert {wall["id"] for wall in garage} == {
        "wall-r4-out-n", "wall-r4-out-s", "wall-r4-out-e", "wall-r4-out-w",
    }


def test_wall_thickness_is_the_gap_that_was_drawn(read):
    wall = next(w for w in read["plan"]["walls"] if w["id"] == "wall-r1-r2")
    assert wall["thickness_mm"] == WALL_GAP * 10.0


def test_wall_gap_is_measured_not_assumed(read):
    assert read["report"]["wall_gap_px"] == float(WALL_GAP)
    assert read["report"]["wall_gap_limit_px"] > WALL_GAP


def test_walls_always_run_west_to_east_or_south_to_north(read):
    """A fixed direction is what makes at_mm mean the same thing twice."""
    for wall in read["plan"]["walls"]:
        start, end = wall["from_mm"], wall["to_mm"]
        assert (start[0], start[1]) < (end[0], end[1]), wall["id"]


def test_exterior_walls_take_compass_sides(read):
    outside = [w["id"] for w in read["plan"]["walls"] if "-out-" in w["id"]]
    assert "wall-r1-out-w" in outside
    assert "wall-r1-out-n" in outside
    assert all(wall.rsplit("-", 1)[-1][0] in "nsew" for wall in outside)


# ==========================================================================
# Doors
# ==========================================================================


def test_every_strip_became_an_opening(read):
    assert read["report"]["counts"]["openings"] == 4
    assert read["report"]["counts"]["unassigned_strips"] == 0


def test_door_kinds_come_from_the_colour_key(read):
    kinds = {entry["id"]: (entry["kind"], entry["role"])
             for entry in read["report"]["openings"]}
    roles = sorted(role for _kind, role in kinds.values())
    assert roles == ["doorway", "house_door", "room_door", "room_door"]
    assert [kind for kind, role in kinds.values() if role == "doorway"] == ["gap"]
    assert all(kind == "door" for kind, role in kinds.values()
               if role in ("house_door", "room_door"))


def test_the_house_door_lands_on_an_outside_wall(read):
    house = next(entry for entry in read["report"]["openings"]
                 if entry["role"] == "house_door")
    assert house["wall"] == "wall-r3-out-s"
    assert "-out-" in house["wall"]


def test_the_room_door_lands_on_the_wall_between_its_two_rooms(read):
    door = next(entry for entry in read["report"]["openings"]
                if entry["wall"] == "wall-r1-r2")
    assert door["kind"] == "door"
    # the strip spans y 60..100 px, so its centre is y = 80 px on a 500 px
    # sheet: 4200 mm up the plan. The wall runs south to north from 3600 mm.
    assert door["at_mm"] == 600.0


def test_opening_width_is_the_strip_that_was_drawn(read):
    door = next(entry for entry in read["report"]["openings"]
                if entry["wall"] == "wall-r1-r2")
    assert door["width_mm"] == 400.0  # 40 px at 10 mm/px
    doorway = next(entry for entry in read["report"]["openings"]
                   if entry["role"] == "doorway")
    assert doorway["width_mm"] == 600.0  # 60 px


def test_at_mm_is_within_a_pixel_of_the_strips_centre(read):
    """Measured against the drawing, not against a default door width."""
    expected = {
        # the blue house door spans x 160..220 px, so its centre is x = 190 px;
        # wall-r3-out-s starts at the room's west edge, x = 40 px.
        "house_door": (190 - 40) * 10.0,
        # the green doorway spans x 80..140 px, centre 110 px, on a wall that
        # starts at x = 40 px as well.
        "doorway": (110 - 40) * 10.0,
    }
    for entry in read["report"]["openings"]:
        if entry["role"] in expected:
            assert entry["at_mm"] == expected[entry["role"]], entry["id"]


def test_openings_ride_on_their_wall_in_the_plan(read):
    walls = {wall["id"]: wall for wall in read["plan"]["walls"]}
    carried = sum(len(wall["openings"]) for wall in walls.values())
    assert carried == 4
    door = walls["wall-r1-r2"]["openings"][0]
    assert door["id"] == "open-r1-r2-1"
    assert door["kind"] == "door"


def test_no_swing_is_invented(read):
    """A flat strip says nothing about which way a door opens, so nothing is said."""
    for wall in read["plan"]["walls"]:
        for opening in wall["openings"]:
            assert "swing" not in opening
            assert "hinge" not in opening


# ==========================================================================
# The grid
# ==========================================================================


def test_grid_pitch_is_detected(read):
    grid = read["report"]["grid"]
    assert grid["source"] == "detected"
    assert grid["pitch_px"] is not None
    assert grid["worst_snap_px"] <= 3.5


def test_three_pixels_of_jitter_still_snaps_to_the_grid(tmp_path):
    """A hand-placed block in a design tool is a few pixels off. Fix it silently."""
    jitter = [3, -2, 2, 3, -3, 2, 1, -2, 2, 3, -3, 1]
    wobbly = save(owner_drawing(jitter=jitter), tmp_path, "wobbly.png")
    clean = extract_floorplan(save(owner_drawing(), tmp_path, "clean.png"),
                              legend=LEGEND, mm_per_px=10.0, grid_px=20.0)
    read = extract_floorplan(wobbly, legend=LEGEND, mm_per_px=10.0, grid_px=20.0)

    assert ids(read["plan"]["rooms"]) == ids(clean["plan"]["rooms"])
    assert ids(read["plan"]["walls"]) == ids(clean["plan"]["walls"])

    # Every room comes out the size it was drawn, to the millimetre: the jitter
    # is gone, not averaged in.
    sizes = {region["id"]: region["size_mm"] for region in read["regions"]}
    for region in clean["regions"]:
        assert sizes[region["id"]] == region["size_mm"], region["id"]

    # And every corner is within a couple of pixels of the clean drawing's --
    # the whole lattice may sit a pixel over, but nothing wobbles inside it.
    for wobble, tidy in zip(read["plan"]["rooms"], clean["plan"]["rooms"]):
        assert len(wobble["polygon_mm"]) == len(tidy["polygon_mm"])
        for (x0, y0), (x1, y1) in zip(wobble["polygon_mm"], tidy["polygon_mm"]):
            assert x0 == pytest.approx(x1, abs=20.0), wobble["id"]
            assert y0 == pytest.approx(y1, abs=20.0), wobble["id"]

    for wall in read["plan"]["walls"]:
        dx = wall["to_mm"][0] - wall["from_mm"][0]
        dy = wall["to_mm"][1] - wall["from_mm"][1]
        assert abs(dx) < 1e-6 or abs(dy) < 1e-6


def test_a_grid_pitch_that_does_not_fit_is_a_warning_not_a_silent_snap(drawing):
    read = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0, grid_px=37.0)
    assert any("does not fit" in sentence for sentence in read["report"]["warnings"])


def test_grid_px_must_be_a_real_pitch(drawing):
    with pytest.raises(FloorPlanError) as excinfo:
        extract_floorplan(drawing, grid_px=1.0)
    assert "snaps nothing" in str(excinfo.value)


# ==========================================================================
# The legend
# ==========================================================================


def test_legend_swatches_are_found_geometrically(drawing):
    read = extract_floorplan(drawing, mm_per_px=10.0)
    swatches = read["report"]["legend"]["swatches"]
    assert len(swatches) == 3
    assert {swatch["hex"] for swatch in swatches} == {"#4285f4", "#34a853", "#db4437"}


def test_auto_legend_assumes_roles_from_hue_and_says_so(drawing):
    read = extract_floorplan(drawing, mm_per_px=10.0)
    report = read["report"]["legend"]
    assert report["source"] == "assumed"
    assert report["roles"]["#4285f4"] == "house_door"
    assert report["roles"]["#34a853"] == "doorway"
    assert report["roles"]["#db4437"] == "room_door"
    assert len(report["assumed"]) == 3
    assert all("ASSUMED" in sentence for sentence in report["assumed"])
    assert any("swatch of it" in sentence for sentence in report["assumed"])


def test_auto_legend_reaches_the_same_geometry_as_a_given_one(drawing, read):
    auto = extract_floorplan(drawing, mm_per_px=10.0)
    assert ids(auto["plan"]["walls"]) == ids(read["plan"]["walls"])
    assert [entry["kind"] for entry in auto["report"]["openings"]] == \
           [entry["kind"] for entry in read["report"]["openings"]]


def test_a_given_legend_overrides_the_hue_guess(drawing):
    read = extract_floorplan(drawing, legend={"#4285f4": "window"}, mm_per_px=10.0)
    kinds = {entry["color"]: entry["kind"] for entry in read["report"]["openings"]}
    assert kinds["#4285f4"] == "window"
    assert read["report"]["legend"]["source"] == "mixed"


def test_a_nonsense_legend_entry_is_reported_not_obeyed(drawing):
    read = extract_floorplan(drawing, legend={"#4285f4": "trapdoor"}, mm_per_px=10.0)
    assert any("trapdoor" in sentence for sentence in read["report"]["ambiguous"])
    kinds = {entry["color"]: entry["role"] for entry in read["report"]["openings"]}
    assert kinds["#4285f4"] == "house_door"  # fell back to the hue assumption


def test_a_swatch_is_never_mistaken_for_a_door(read):
    """Three swatches in the corner, and not one of them became an opening."""
    assert read["report"]["counts"]["legend_swatches"] == 3
    assert read["report"]["counts"]["openings"] == 4


# ==========================================================================
# Scale and the ONE question
# ==========================================================================


def test_an_unscaled_read_asks_for_one_dimension(drawing):
    read = extract_floorplan(drawing, legend=LEGEND)
    assert read["report"]["scale"]["calibrated"] is False
    question = read["report"]["calibration_question"]
    assert question and "ONE real dimension" in question
    assert "pixels" in read["report"]["scale"]["units_are"]


def test_an_unscaled_plan_still_validates_and_is_marked(drawing):
    read = extract_floorplan(drawing, legend=LEGEND)
    floorplan.validate_plan(read["plan"])
    assert read["plan"]["units"] == "mm"
    assert "NOT CALIBRATED" in read["plan"]["scale"]["calibrated_by"]


def test_a_scaled_read_asks_nothing(read):
    assert read["report"]["calibration_question"] is None
    assert read["report"]["scale"]["calibrated"] is True
    assert read["plan"]["scale"]["mm_per_px"] == 10.0


def test_calibration_hints_carry_pixel_lengths(drawing):
    read = extract_floorplan(drawing, legend=LEGEND)
    hints = read["report"]["calibration_hints"]
    assert hints
    house = next(hint for hint in hints if "house_door" in hint["what"])
    assert house["length_px"] == pytest.approx(60.0, abs=1.0)
    assert mm_per_px_from(820.0, house["length_px"]) == pytest.approx(13.67, abs=0.1)


def test_scaling_multiplies_every_coordinate(drawing):
    one = extract_floorplan(drawing, legend=LEGEND, mm_per_px=1.0)
    ten = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)
    for small, large in zip(one["plan"]["rooms"], ten["plan"]["rooms"]):
        for (x0, y0), (x1, y1) in zip(small["polygon_mm"], large["polygon_mm"]):
            assert x1 == pytest.approx(x0 * 10.0)
            assert y1 == pytest.approx(y0 * 10.0)


# ==========================================================================
# The plan contract
# ==========================================================================


def test_the_plan_passes_validate_plan(read):
    normalised = floorplan.validate_plan(read["plan"])
    assert normalised["version"] == floorplan.PLAN_VERSION
    assert normalised["units"] == "mm"


def test_the_plan_resolves_and_builds_specs(read):
    resolved = floorplan.fill_defaults(read["plan"])
    specs = floorplan.component_build_specs(resolved)
    assert [spec["kind"] for spec in specs].count("wall") == len(read["plan"]["walls"])


def test_ids_are_identical_across_two_runs(drawing):
    first = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)
    second = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)
    assert ids(first["plan"]["rooms"]) == ids(second["plan"]["rooms"])
    assert ids(first["plan"]["walls"]) == ids(second["plan"]["walls"])
    assert first["plan"]["rooms"] == second["plan"]["rooms"]
    assert first["plan"]["walls"] == second["plan"]["walls"]


def test_re_extraction_diffs_to_nothing(drawing):
    """The incremental law, from the extractor's side: read it twice, rebuild none."""
    first = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)["plan"]
    second = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)["plan"]
    summary = floorplan.diff_plans(first, second)
    assert summary["added"] == []
    assert summary["removed"] == []
    assert summary["changed"] == []
    assert summary["unchanged"]


def test_no_fixtures_are_invented(read):
    """The washer is a word in the drawing; a word is not a footprint."""
    assert read["plan"]["labels"] == []


def test_the_plan_records_where_it_came_from(read):
    note = read["plan"]["history"][0]["note"]
    assert "floorplan_extract" in note
    assert "plan.png" in note


def test_history_carries_no_clock(drawing):
    """A timestamp would make two reads of one drawing two different plans."""
    first = extract_floorplan(drawing, legend=LEGEND, mm_per_px=10.0)
    assert "date" not in first["plan"]["history"][0]


# ==========================================================================
# Regions: the naming handoff
# ==========================================================================


def test_every_region_carries_a_crop_to_look_at(read):
    for region in read["regions"]:
        crop = region["crop"]["box_px"]
        x0, y0, x1, y1 = crop
        assert x1 > x0 and y1 > y0
        assert x0 <= region["bbox_px"][0] and y0 <= region["bbox_px"][1]
        assert x1 >= region["bbox_px"][2] and y1 >= region["bbox_px"][3]
        assert "LABEL" in region["crop"]["why"]


def test_regions_start_unnamed(read):
    assert all(region["label"] is None for region in read["regions"])
    assert all("label" not in room for room in read["plan"]["rooms"])


def test_crops_stay_inside_the_image(read):
    width, height = read["report"]["image_size_px"]
    for region in read["regions"]:
        x0, y0, x1, y1 = region["crop"]["box_px"]
        assert 0 <= x0 < x1 <= width
        assert 0 <= y0 < y1 <= height


def test_the_naming_rule_is_stated(read):
    assert "Never change an id" in read["report"]["naming"]


# ==========================================================================
# The mask
# ==========================================================================


def test_mask_is_on_plan_masks_contract(read):
    mask = read["mask"]
    for key in ("mask", "cell_mm", "origin_mm", "bounds_mm", "shape",
                "cells_filled", "area_mm2", "components", "rows_are"):
        assert key in mask
    assert mask["shape"] == list(mask["mask"].shape)
    assert mask["cell_mm"] == 10.0
    assert mask["bounds_mm"] == [0.0, 0.0, 6000.0, 5000.0]


def test_mask_self_iou_is_one(read):
    assert floorplan.mask_iou(read["mask"], read["mask"]) == 1.0


def test_mask_rows_ascend_in_y(read):
    """Row 0 is the lowest y -- the garage is at the bottom of the drawing."""
    mask = read["mask"]["mask"]
    # the garage sits at pixel rows 360..460 of a 500 px sheet, which is rows
    # 40..140 once the grid is the plan's, counting up from the bottom
    assert mask[40:140, 40:180].all()
    assert not mask[0:39, :].any()


def test_mask_iou_against_the_rooms_it_produced(read):
    """The accuracy number: how much of the drawn fill the rooms actually cover."""
    assert read["report"]["fidelity"]["rooms_vs_fill_iou"] > 0.97


def test_mask_shares_a_grid_with_plan_mask(read):
    """One call, no coordinate argument -- which is the point of the contract."""
    built = floorplan.plan_mask(read["plan"], cell_mm=read["mask"]["cell_mm"],
                                bounds_mm=read["mask"]["bounds_mm"])
    assert built["shape"] == read["mask"]["shape"]
    assert built["origin_mm"] == read["mask"]["origin_mm"]
    value = floorplan.mask_iou(built, read["mask"])
    assert 0.0 <= value <= 1.0


# ==========================================================================
# Refusals and honesty
# ==========================================================================


def test_a_blank_sheet_is_refused_with_what_it_saw(tmp_path):
    path = save(canvas(), tmp_path, "blank.png")
    with pytest.raises(FloorPlanError) as excinfo:
        extract_floorplan(path)
    message = str(excinfo.value)
    assert "No room fills" in message
    assert "do NOT type coordinates off the picture" in message


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(FloorPlanError) as excinfo:
        extract_floorplan(str(tmp_path / "nope.png"))
    assert "No image at" in str(excinfo.value)


def test_no_image_at_all_is_refused(tmp_path):
    with pytest.raises(FloorPlanError):
        extract_floorplan("")


def test_a_drawing_of_only_marks_is_refused(tmp_path):
    sheet = canvas()
    for index in range(8):
        block(sheet, 20 + index * 30, 20, 36 + index * 30, 36, GREY)
    path = save(sheet, tmp_path, "marks.png")
    with pytest.raises(FloorPlanError) as excinfo:
        extract_floorplan(path)
    assert "marks rather than rooms" in str(excinfo.value)


def test_the_report_says_what_it_is(read):
    honesty = read["report"]["honesty"]
    assert "MEASUREMENT of the drawing" in honesty
    assert "what anything is CALLED" in honesty


def test_the_report_names_every_colour_and_what_it_was_read_as(read):
    reads = {entry["hex"]: entry["read_as"] for entry in read["report"]["colors"]}
    assert reads["#ffffff"] == "background"
    assert reads["#c8c8c8"] == "room"
    assert reads["#4285f4"] == "opening"
    assert any(kind == "text" for kind in reads.values())


def test_absorbed_text_is_reported(read):
    assert any("absorbed into the fill" in note for note in read["report"]["notes"])


# ==========================================================================
# validate_plan's new warning (service/floorplan.py)
# ==========================================================================


def diagonal_plan() -> Dict[str, Any]:
    return {
        "version": 1, "units": "mm",
        "rooms": [],
        "walls": [
            {"id": "wall-square", "from_mm": [0, 0], "to_mm": [3000, 0]},
            {"id": "wall-slanted", "from_mm": [0, 0], "to_mm": [3000, 917]},
        ],
    }


def test_an_off_axis_wall_is_a_warning_and_not_a_refusal():
    collected: List[str] = []
    normalised = floorplan.validate_plan(diagonal_plan(), warnings=collected)
    assert len(normalised["walls"]) == 2
    assert len(collected) == 1
    assert "wall-slanted" in collected[0]
    assert "17 degrees off axis" in collected[0]
    assert "rectilinear" in collected[0]


def test_the_warning_names_the_wall_and_asks(read):
    sentences = floorplan.plan_warnings(diagonal_plan())
    assert sentences and sentences[0].endswith("is that intended?")


def test_a_square_plan_warns_about_nothing(read):
    assert floorplan.plan_warnings(read["plan"]) == []


def test_validate_plan_without_a_warnings_list_is_unchanged():
    """The default call site is byte-identical to what every caller had before."""
    plan = diagonal_plan()
    collected: List[str] = []
    assert floorplan.validate_plan(plan) == floorplan.validate_plan(
        plan, warnings=collected)
    assert collected


def test_warnings_never_land_in_the_document(read):
    """A note inside the file is a note the add-on would have to fingerprint."""
    assert "warnings" not in read["plan"]
    normalised = floorplan.validate_plan(diagonal_plan(), warnings=[])
    assert "warnings" not in normalised


def test_plan_warnings_tolerates_a_half_built_plan():
    assert floorplan.plan_warnings({"walls": [{"id": "x"}, "nonsense", None]}) == []
    assert floorplan.plan_warnings(None) == []
