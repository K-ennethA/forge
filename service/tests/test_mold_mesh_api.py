"""Phase 12 end to end: /mold_mesh, /export_mold_mesh and ``mode: "master_box"``.

The centrepiece is :func:`test_a_generated_part_molds_the_same_from_its_own_mesh`:
a part is built by ``/generate``, its triangles are handed straight back to
``/mold_mesh``, and the mold that comes out is the same *class* of thing
``/mold`` makes from the script -- two watertight halves, keys on one and
sockets in the other, a spout, a parting plane in the same place.  Sewing a mesh
and building a solid are different roads to the same mold, and this is the test
that says so.

These build real solids, so they skip themselves when build123d is missing.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.casting import (  # noqa: E402
    master_box_instructions,
    normalize_master_box_options,
    normalize_mode,
    printed_negative_instructions,
)
from service.errors import ParamError  # noqa: E402
from service.main import app  # noqa: E402
from service.mesh_input import REPAIR_FIRST_MESSAGE  # noqa: E402
from service.printer import normalize_printer  # noqa: E402

#: A dome on the ground: no undercut anywhere, cheap to sew, and the same shape
#: ``test_mold_api`` uses, so a difference between the two paths shows up as a
#: difference in the numbers rather than in the shape.
SPHERE = """
from build123d import *

PARAMS = {"radius": {"value": 10.0, "unit": "mm", "min": 2.0, "max": 60.0}}

def build(p):
    return Pos(0, 0, p["radius"]) * Sphere(p["radius"])
"""

#: A waist between two drums: whichever way a two-piece mold opens, the far
#: drum is a wall it cannot climb.  This is the shape master_box exists for.
SPOOL = """
from build123d import *

PARAMS = {"scale": {"value": 1.0, "unit": "ratio", "min": 0.2, "max": 4.0}}

def build(p):
    s = p["scale"]
    lower = Pos(0, 0, 4 * s) * Cylinder(10 * s, 8 * s)
    waist = Pos(0, 0, 14 * s) * Cylinder(3 * s, 12 * s)
    upper = Pos(0, 0, 24 * s) * Cylinder(8 * s, 8 * s)
    return lower + waist + upper
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def kernel(client):
    payload = client.get("/health").json()
    if payload.get("status") != "ok":
        pytest.skip(f"build123d unavailable: {payload.get('error')}")
    return payload


@pytest.fixture(scope="module")
def sphere_mesh(client, kernel):
    """A real ``/generate`` mesh, which is what a caller would actually send.

    Deliberately coarse.  Sewing builds one OpenCascade face per triangle and
    every mold boolean then runs against all of them: the same sphere at the
    default deflection is 2020 triangles and 91 seconds, at this one it is 224
    and 8.  The code path is identical either way, and a suite that runs is
    worth more than a suite with rounder spheres in it.
    """
    response = client.post(
        "/generate",
        json={"script": SPHERE, "tolerance": 0.3, "angular_tolerance": 0.6},
    )
    assert response.status_code == 200, response.text
    return response.json()["mesh"]


@pytest.fixture(scope="module")
def mesh_mold(client, kernel, sphere_mesh):
    """One ``/mold_mesh`` of that mesh; several tests read the same answer."""
    response = client.post(
        "/mold_mesh", json={"mesh": sphere_mesh, "include_mesh": False}
    )
    assert response.status_code == 200, response.text
    return response.json()


def write_binary_stl(path: Path, vertices, faces) -> Path:
    """The same file a download would be: binary STL, float32, no shared vertices."""
    with open(path, "wb") as handle:
        handle.write(b"\0" * 80)
        handle.write(struct.pack("<I", len(faces)))
        for tri in faces:
            a, b, c = (vertices[i] for i in tri)
            ux, uy, uz = (b[i] - a[i] for i in range(3))
            vx, vy, vz = (c[i] - a[i] for i in range(3))
            nx, ny, nz = (
                uy * vz - uz * vy,
                uz * vx - ux * vz,
                ux * vy - uy * vx,
            )
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            handle.write(struct.pack("<3f", nx / length, ny / length, nz / length))
            for point in (a, b, c):
                handle.write(struct.pack("<3f", *[float(v) for v in point]))
            handle.write(struct.pack("<H", 0))
    return path


def open_box_mesh(size=(20.0, 20.0, 20.0)):
    """A box with its lid missing: the canonical "repair this first" mesh."""
    sx, sy, sz = size
    vertices = [
        (0, 0, 0), (sx, 0, 0), (sx, sy, 0), (0, sy, 0),
        (0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz),
    ]
    faces = [
        [0, 3, 2], [0, 2, 1],
        [0, 1, 5], [0, 5, 4],
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],
    ]  # no top
    return {"vertices": vertices, "faces": faces}


# --------------------------------------------------------------------------
# /mold_mesh: the same mold, from triangles
# --------------------------------------------------------------------------


def test_a_generated_part_molds_the_same_from_its_own_mesh(client, kernel, mesh_mold):
    from_script = client.post(
        "/mold", json={"script": SPHERE, "include_mesh": False}
    )
    assert from_script.status_code == 200, from_script.text

    script, mesh = from_script.json(), mesh_mold

    # Same two halves, both watertight, both really solids.
    assert [half["name"] for half in mesh["halves"]] == ["mold_top", "mold_bottom"]
    for half in mesh["halves"]:
        assert half["stats"]["watertight"] is True, half["name"]
        assert half["stats"]["boundary_edges"] == 0
        assert half["stats"]["nonmanifold_edges"] == 0
        assert half["stats"]["solid_is_valid"] is True
        assert half["volume_mm3"] > 0.0

    # Same mold: the plane lands in the same place and the keys, spout and
    # vents are the same features, not a parallel implementation of them.
    assert mesh["parting_source"] == "auto"
    assert mesh["parting_z_mm"] == pytest.approx(script["parting_z_mm"], abs=0.5)
    assert mesh["registration_keys"]["count"] == 4
    assert mesh["registration_keys"]["male_half"] == "mold_top"
    assert mesh["registration_keys"]["female_half"] == "mold_bottom"
    assert mesh["spout"] is not None and mesh["spout"]["diameter_mm"] > 0.0
    assert mesh["box"]["shell_mm"] == script["box"]["shell_mm"]
    assert mesh["mode"] == "printed_negative"


def test_the_mesh_input_block_says_what_arrived_and_what_was_repaired(
    mesh_mold, sphere_mesh
):
    payload = mesh_mold

    info = payload["mesh_input"]
    assert info["source"] == "mesh"
    assert info["face_count"] == len(sphere_mesh["faces"])
    assert info["winding_flipped"] is False
    assert info["signed_volume_mm3"] > 0.0
    assert payload["sewing"]["faces_sewn"] == len(sphere_mesh["faces"])
    assert payload["sewing"]["faces_skipped"] == 0
    assert payload["sewing"]["shells"] == 1

    # There is no PARAMS schema behind a mesh, exactly as with /segment_mesh.
    assert payload["params"] is None
    assert payload["timings"]["load_ms"] >= 0.0
    assert payload["timings"]["sew_ms"] >= 0.0


def test_a_mold_from_a_file_on_disk_is_the_same_mold(
    client, kernel, sphere_mesh, tmp_path
):
    path = write_binary_stl(
        tmp_path / "sphere.stl", sphere_mesh["vertices"], sphere_mesh["faces"]
    )
    response = client.post(
        "/mold_mesh", json={"file_path": str(path), "include_mesh": False}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mesh_input"]["source"] == "file_path"
    assert payload["mesh_input"]["path"] == str(path)
    assert payload["mesh_input"]["merged_vertices"] > 0  # binary STL never shares one
    for half in payload["halves"]:
        assert half["stats"]["watertight"] is True


def test_a_mesh_with_holes_in_it_is_refused_before_any_kernel_work(client):
    response = client.post("/mold_mesh", json={"mesh": open_box_mesh()})
    assert response.status_code == 400, response.text
    message = response.json()["error"]
    assert "holes in it" in message
    # The same repair-first words /segment_mesh uses, because it is the same
    # problem and the caller should not have to learn two vocabularies.
    assert REPAIR_FIRST_MESSAGE[:40] in message


def test_a_mesh_over_the_triangle_ceiling_is_refused_with_the_number(
    client, kernel, sphere_mesh
):
    response = client.post(
        "/mold_mesh", json={"mesh": sphere_mesh, "tri_limit": 4}
    )
    assert response.status_code == 400, response.text
    assert "triangles" in response.json()["error"]
    assert "Decimate" in response.json()["error"]


def test_export_mold_mesh_writes_one_file_per_piece(
    client, kernel, sphere_mesh, tmp_path
):
    response = client.post(
        "/export_mold_mesh",
        json={
            "mesh": sphere_mesh,
            "directory": str(tmp_path),
            "basename": "dome",
            "format": "stl",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    names = [entry["name"] for entry in payload["files"]]
    assert names == ["mold_top", "mold_bottom"]
    for entry in payload["files"]:
        written = Path(entry["path"])
        assert written.exists() and written.stat().st_size > 0
        assert written.name.startswith("dome_")
    assert payload["mesh_input"]["source"] == "mesh"
    assert payload["instructions"]


# --------------------------------------------------------------------------
# Undercut analysis on the response
# --------------------------------------------------------------------------


def test_every_mold_response_carries_an_undercut_report(client, kernel):
    response = client.post("/mold", json={"script": SPHERE, "include_mesh": False})
    assert response.status_code == 200, response.text
    payload = response.json()

    undercuts = payload["undercuts"]
    assert set(undercuts["halves"]) == {"mold_top", "mold_bottom"}
    assert undercuts["severity"] in ("none", "mild", "severe")
    assert undercuts["parting_z_mm"] == pytest.approx(payload["parting_z_mm"])
    assert undercuts["halves"]["mold_top"]["draw_direction"] == [0.0, 0.0, 1.0]
    assert undercuts["halves"]["mold_bottom"]["draw_direction"] == [0.0, 0.0, -1.0]
    assert "approximation" in undercuts["criterion"]

    # A ball parted at its equator is the easy case, and it should say so.
    assert undercuts["severity"] != "severe"
    assert payload["recommendation"] is None


def test_a_spool_is_severe_and_the_response_says_to_use_master_box(client, kernel):
    response = client.post("/mold", json={"script": SPOOL, "include_mesh": False})
    assert response.status_code == 200, response.text
    payload = response.json()

    undercuts = payload["undercuts"]
    assert undercuts["severity"] == "severe"
    assert undercuts["verdict"] == "severe -- a rigid mold cannot release this"
    assert undercuts["recommend_master_box"] is True

    recommendation = payload["recommendation"]
    assert recommendation and "master_box" in recommendation
    assert "silicone" in recommendation

    severe = [
        name
        for name, half in undercuts["halves"].items()
        if half["severity"] == "severe"
    ]
    assert severe, "something has to be the half that cannot come off"
    for name in severe:
        half = undercuts["halves"][name]
        assert half["examples"], "a severe verdict has to say where"
        for example in half["examples"]:
            assert len(example["position_mm"]) == 3
            assert example["depth_mm"] >= 0.0


def test_the_undercut_knobs_reach_the_worker(client, kernel):
    quiet = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "include_mesh": False,
            "undercut_threshold_deg": 40.0,
            "undercut_examples": 0,
        },
    )
    assert quiet.status_code == 200, quiet.text
    payload = quiet.json()
    assert payload["undercuts"]["criterion"]["threshold_deg"] == 40.0
    for half in payload["undercuts"]["halves"].values():
        assert half["examples"] == []


# --------------------------------------------------------------------------
# master_box mode
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def master_box(client, kernel):
    response = client.post(
        "/mold",
        json={"script": SPOOL, "mode": "master_box", "include_mesh": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_master_box_returns_the_master_and_a_box_and_touches_neither(
    client, kernel, master_box
):
    assert master_box["mode"] == "master_box"
    assert [piece["name"] for piece in master_box["pieces"]] == ["master", "box"]
    assert "halves" not in master_box

    for piece in master_box["pieces"]:
        stats = piece["stats"]
        assert stats["watertight"] is True, piece["name"]
        assert stats["boundary_edges"] == 0
        assert stats["nonmanifold_edges"] == 0
        assert piece["volume_mm3"] > 0.0

    # The master is the part, unchanged: same bounding box the part reports.
    master = next(p for p in master_box["pieces"] if p["name"] == "master")
    assert master["stats"]["bounding_box_mm"] == pytest.approx(
        master_box["stats_part"]["bounding_box_mm"], abs=1e-6
    )


def test_the_box_is_the_master_plus_margin_plus_wall(client, kernel, master_box):
    geometry = master_box["master_box"]
    size = master_box["stats_part"]["bounding_box_mm"]
    margin = geometry["margin_mm"]
    wall = geometry["wall_mm"]

    assert margin == 10.0  # the documented default
    assert wall >= 3.0
    interior = geometry["interior_size_mm"]
    assert interior[0] == pytest.approx(size[0] + 2 * margin, abs=1e-6)
    assert interior[1] == pytest.approx(size[1] + 2 * margin, abs=1e-6)
    # Height: the pad under the master, the master, then the pour clearance.
    assert interior[2] == pytest.approx(
        size[2] + geometry["platform_mm"] + geometry["pour_clearance_mm"], abs=1e-6
    )
    assert geometry["pour_clearance_mm"] == 15.0
    assert geometry["open_top"] is True

    outer = geometry["outer_size_mm"]
    assert outer[0] == pytest.approx(interior[0] + 2 * wall, abs=1e-6)
    assert outer[1] == pytest.approx(interior[1] + 2 * wall, abs=1e-6)


def test_the_floor_carries_a_pad_the_master_is_glued_to(master_box):
    pad = master_box["master_box"]["platform"]
    assert pad is not None
    assert pad["height_mm"] == 3.0
    # Wide at the bottom, narrow at the top: in the cured silicone that is a
    # funnel, which is how the resin gets in.
    assert pad["bottom_diameter_mm"] > pad["top_diameter_mm"] > 0.0
    assert pad["top_z_mm"] == pytest.approx(
        master_box["stats_part"]["bounding_box_min_mm"][2]
    )
    assert "pour hole" in pad["role"]


def test_a_corner_pour_funnel_is_there_and_can_be_turned_off(client, kernel, master_box):
    funnels = master_box["master_box"]["funnels"]
    assert funnels["count"] == 1
    assert funnels["throat_diameter_mm"] > 0.0
    assert funnels["draft_deg"] > 0.0
    x, y = funnels["positions_mm"][0]
    interior_low = master_box["master_box"]["interior_min_mm"]
    assert (x, y) == pytest.approx((interior_low[0], interior_low[1]))

    bare = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "funnels": 0,
            "include_mesh": False,
        },
    )
    assert bare.status_code == 200, bare.text
    assert bare.json()["master_box"]["funnels"]["count"] == 0
    for piece in bare.json()["pieces"]:
        assert piece["stats"]["watertight"] is True


def test_a_split_box_comes_in_two_watertight_halves_that_mate(client, kernel):
    response = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert [piece["name"] for piece in payload["pieces"]] == [
        "master",
        "box_a",
        "box_b",
    ]
    for piece in payload["pieces"]:
        assert piece["stats"]["watertight"] is True, piece["name"]
        assert piece["stats"]["solid_is_valid"] is True, piece["name"]

    keys = payload["master_box"]["registration_keys"]
    assert keys["count"] == 4
    assert keys["male_piece"] == "box_a"
    assert keys["female_piece"] == "box_b"
    assert keys["tolerance_mm"] > 0.0
    # Every key sits on the split plane, which is the middle of the box in X.
    outer_low = payload["master_box"]["outer_min_mm"]
    outer_high = payload["master_box"]["outer_max_mm"]
    split_x = (outer_low[0] + outer_high[0]) / 2.0
    for x, _y, _z in keys["positions_mm"]:
        assert x == pytest.approx(split_x)

    # The male bosses are solid material, the sockets are the same sphere grown
    # by the printer's press fit: a half with keys is bigger than one without.
    plain = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "registration_keys": 0,
            "include_mesh": False,
        },
    ).json()
    keyed_a = next(p for p in payload["pieces"] if p["name"] == "box_a")
    plain_a = next(p for p in plain["pieces"] if p["name"] == "box_a")
    keyed_b = next(p for p in payload["pieces"] if p["name"] == "box_b")
    plain_b = next(p for p in plain["pieces"] if p["name"] == "box_b")
    assert keyed_a["volume_mm3"] > plain_a["volume_mm3"]
    assert keyed_b["volume_mm3"] < plain_b["volume_mm3"]
    assert plain["master_box"]["registration_keys"]["count"] == 0


def test_a_funnel_that_would_reach_across_the_split_is_shrunk_or_left_off(
    client, kernel
):
    # A thick wall pushes the collar's flare out toward the middle of the box.
    # There is still room for a funnel, so it keeps one -- shorter.
    shorter = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "wall_mm": 5.0,
            "floor_mm": 5.0,
            "include_mesh": False,
        },
    )
    assert shorter.status_code == 200, shorter.text
    funnels = shorter.json()["master_box"]["funnels"]
    assert funnels["count"] == 1
    assert funnels["note"] is None
    assert 0.0 < funnels["height_mm"] < 8.0
    for piece in shorter.json()["pieces"]:
        assert piece["stats"]["watertight"] is True, piece["name"]

    # One more millimetre of wall and the collar is squeezed below half its
    # design height: a stub silicone pours straight over, and it would still
    # reach across the split. The funnel gives way -- and the response says so,
    # and which knob to turn.
    response = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "wall_mm": 6.0,
            "floor_mm": 6.0,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    dropped = payload["master_box"]["funnels"]
    assert dropped["requested"] == 1
    assert dropped["count"] == 0
    assert dropped["positions_mm"] == []
    assert dropped["note"] and "loose chip" in dropped["note"]
    assert "margin_mm" in dropped["note"]
    for piece in payload["pieces"]:
        assert piece["stats"]["watertight"] is True, piece["name"]


def test_four_funnels_on_a_split_box_all_survive_when_they_fit(client, kernel):
    response = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "funnels": 4,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["master_box"]["funnels"]["count"] == 4
    assert payload["master_box"]["funnels"]["note"] is None
    for piece in payload["pieces"]:
        assert piece["stats"]["watertight"] is True, piece["name"]


def test_the_box_is_checked_against_the_bed_like_everything_else(client, kernel):
    response = client.post(
        "/mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "margin_mm": 120.0,
            "include_mesh": False,
        },
    )
    assert response.status_code == 400, response.text
    message = response.json()["error"]
    assert "usable bed" in message
    assert "margin_mm" in message


def test_master_box_still_reports_what_a_two_piece_mold_would_have_fought(
    client, kernel, master_box
):
    undercuts = master_box["undercuts"]
    assert undercuts["severity"] == "severe"
    assert undercuts["recommend_master_box"] is True
    # ... and the mode it recommends is the mode we are already in, which is
    # the point: the report is the justification for the box.
    assert master_box["mode"] == "master_box"
    assert master_box["parting_source"] == "auto"


def test_export_master_box_writes_the_master_and_the_box(
    client, kernel, tmp_path
):
    response = client.post(
        "/export_mold",
        json={
            "script": SPHERE,
            "mode": "master_box",
            "split": True,
            "directory": str(tmp_path),
            "basename": "figure",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    names = [entry["name"] for entry in payload["files"]]
    assert names == ["master", "box_a", "box_b"]
    for entry in payload["files"]:
        written = Path(entry["path"])
        assert written.exists() and written.stat().st_size > 0
    assert payload["instructions"]
    assert "halves" not in payload


def test_master_box_works_from_a_mesh_too(client, kernel, sphere_mesh):
    response = client.post(
        "/mold_mesh",
        json={"mesh": sphere_mesh, "mode": "master_box", "include_mesh": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [piece["name"] for piece in payload["pieces"]] == ["master", "box"]
    for piece in payload["pieces"]:
        assert piece["stats"]["watertight"] is True
    assert payload["mesh_input"]["source"] == "mesh"
    assert payload["instructions"]


# --------------------------------------------------------------------------
# Instructions
# --------------------------------------------------------------------------


def test_the_master_box_instructions_are_a_workflow_a_beginner_can_follow(master_box):
    steps = master_box["instructions"]
    assert len(steps) >= 8
    assert all(isinstance(step, str) and len(step) > 20 for step in steps)

    joined = " ".join(steps).lower()
    for beat in ("print", "glue", "mix", "pour", "cure", "flex", "resin"):
        assert beat in joined, beat
    # The numbers the box was actually built with travel into the words.
    assert "15 mm above the highest point" in " ".join(steps)
    assert str(master_box["master_box"]["silicone_volume_ml"]) in " ".join(steps)


def test_the_printed_negative_instructions_talk_about_the_printed_mold(client, kernel):
    payload = client.post(
        "/mold", json={"script": SPHERE, "include_mesh": False}
    ).json()
    steps = payload["instructions"]
    assert len(steps) >= 6
    joined = " ".join(steps).lower()
    assert "mold_top" in joined and "mold_bottom" in joined
    assert "release" in joined
    assert "spout" in joined


def test_the_instruction_builders_bend_to_the_mold_they_are_given():
    keyless = printed_negative_instructions(
        {"registration_keys": {"count": 0}, "vents": {"count": 0}, "spout": None}
    )
    assert any("line the edges up by eye" in step for step in keyless)
    assert not any("spout" in step for step in keyless[:6])

    padless = master_box_instructions(
        {
            "pour_clearance_mm": 0.0,
            "split": False,
            "platform_mm": 0.0,
            "funnels": {"count": 0},
        }
    )
    assert any("flat onto the middle of the box floor" in step for step in padless)
    assert any("Pour into one corner" in step for step in padless)
    assert any("Check the box for print gaps" in step for step in padless)

    keyed = master_box_instructions(
        {"split": True, "funnels": {"count": 1}, "registration_keys": {"count": 4}}
    )
    assert any("the 4 bumps on box_a drop into the 4 dimples" in s for s in keyed)
    keyless = master_box_instructions(
        {"split": True, "funnels": {"count": 1}, "registration_keys": {"count": 0}}
    )
    assert any("line the two halves up by their edges" in s for s in keyless)


# --------------------------------------------------------------------------
# Option validation (no kernel needed)
# --------------------------------------------------------------------------


def test_mode_is_one_of_two_words():
    assert normalize_mode(None) == "printed_negative"
    assert normalize_mode("master_box") == "master_box"
    assert normalize_mode(" Master_Box ") == "master_box"
    with pytest.raises(ParamError) as excinfo:
        normalize_mode("silicone")
    assert "printed_negative" in str(excinfo.value)


def test_the_pour_box_defaults_are_the_documented_ones():
    options = normalize_master_box_options({}, normalize_printer(None))
    assert options == {
        "margin_mm": 10.0,
        "wall_mm": 3.0,
        "floor_mm": 3.0,
        "pour_clearance_mm": 15.0,
        "platform_mm": 3.0,
        "funnels": 1,
        "split": False,
        "registration_keys": 4,
    }


def test_the_wall_is_clamped_to_what_the_printer_can_actually_print():
    chunky = normalize_printer({"min_wall_thickness": 2.5, "min_feature_size": 2.0})
    assert normalize_master_box_options({}, chunky)["wall_mm"] == 5.0

    with pytest.raises(ParamError) as excinfo:
        normalize_master_box_options({"wall_mm": 0.5}, chunky)
    assert "wall_mm" in str(excinfo.value)


def test_a_nested_master_box_object_wins_over_the_flat_fields():
    printer = normalize_printer(None)
    options = normalize_master_box_options(
        {"margin_mm": 99.0, "master_box": {"margin_mm": 6.0, "split": True}}, printer
    )
    assert options["margin_mm"] == 6.0
    assert options["split"] is True


def test_the_pour_box_numbers_are_all_validated():
    printer = normalize_printer(None)
    for field, value in (
        ("margin_mm", -1.0),
        ("pour_clearance_mm", 1000.0),
        ("platform_mm", "tall"),
        ("funnels", 9),
        ("split", "yes"),
    ):
        with pytest.raises(ParamError):
            normalize_master_box_options({field: value}, printer)
