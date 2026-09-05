"""Mesh input: the loader on its own, and the three endpoints end to end.

The loader half needs nothing but Python, so it runs everywhere.  The endpoint
half builds real solids and is skipped -- not failed -- when build123d is
missing, the same way the rest of the suite is.

The centrepiece is :func:`test_a_downloaded_ring_is_checked_and_cut`, which is
the whole feature in one test: a binary STL is written here in the test, exactly
as a download would arrive, and comes out the other end as four watertight
dovetailed segments that fit the bed.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from service import mesh_input  # noqa: E402
from service.errors import ParamError  # noqa: E402

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402

#: 300 mm across, 12 mm wall, 20 mm tall: too big for a 256 mm bed in every
#: orientation, ring-like enough to cut radially, thick enough to take dovetails.
RING_OUTER_MM = 150.0
RING_INNER_MM = 138.0
RING_HEIGHT_MM = 20.0


# --------------------------------------------------------------------------
# Test geometry
# --------------------------------------------------------------------------


def ring_mesh(
    outer: float = RING_OUTER_MM,
    inner: float = RING_INNER_MM,
    height: float = RING_HEIGHT_MM,
    sides: int = 64,
    flip: bool = False,
):
    """A closed faceted ring band: outer wall, inner wall, two end rings.

    Vertices are placed on half-step angles so the radial cut planes at 0, 90,
    180 and 270 degrees fall in the middle of a facet rather than exactly on a
    vertex -- the same thing a real download would do, and it keeps the booleans
    away from coincident-face cases that are their own separate bug hunt.
    """
    vertices = []
    for z in (0.0, height):
        for radius in (outer, inner):
            for index in range(sides):
                angle = 2.0 * math.pi * (index + 0.5) / sides
                vertices.append(
                    (radius * math.cos(angle), radius * math.sin(angle), z)
                )

    def out(level: int, index: int) -> int:
        return level * 2 * sides + (index % sides)

    def inn(level: int, index: int) -> int:
        return level * 2 * sides + sides + (index % sides)

    faces = []
    for i in range(sides):
        # Outer wall: normals point away from the axis.
        faces.append([out(0, i), out(0, i + 1), out(1, i + 1)])
        faces.append([out(0, i), out(1, i + 1), out(1, i)])
        # Inner wall: normals point at the axis, so the winding is reversed.
        faces.append([inn(0, i), inn(1, i + 1), inn(0, i + 1)])
        faces.append([inn(0, i), inn(1, i), inn(1, i + 1)])
        # Top and bottom annulus.
        faces.append([out(1, i), out(1, i + 1), inn(1, i + 1)])
        faces.append([out(1, i), inn(1, i + 1), inn(1, i)])
        faces.append([out(0, i), inn(0, i + 1), out(0, i + 1)])
        faces.append([out(0, i), inn(0, i), inn(0, i + 1)])

    if flip:
        faces = [[a, c, b] for a, b, c in faces]
    return vertices, faces


def write_binary_stl(path: Path, vertices, faces) -> Path:
    """Write the mesh the way a download would carry it: float32, no indices."""
    with path.open("wb") as handle:
        handle.write(b"forge test ring".ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(faces)))
        for a, b, c in faces:
            pa, pb, pc = vertices[a], vertices[b], vertices[c]
            ux, uy, uz = (pb[i] - pa[i] for i in range(3))
            vx, vy, vz = (pc[i] - pa[i] for i in range(3))
            nx, ny, nz = (
                uy * vz - uz * vy,
                uz * vx - ux * vz,
                ux * vy - uy * vx,
            )
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            handle.write(
                struct.pack(
                    "<12fH",
                    nx / length,
                    ny / length,
                    nz / length,
                    *pa[:3],
                    *pb[:3],
                    *pc[:3],
                    0,
                )
            )
    return path


def write_obj(path: Path, vertices, faces) -> Path:
    lines = ["# forge test", "o thing"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices]
    lines += [f"f {a + 1}//1 {b + 1}//1 {c + 1}//1" for a, b, c in faces]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


OPEN_BOX = {
    # A cube missing its lid: five faces, four open edges round the top.
    "vertices": [
        [0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
        [0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10],
    ],
    "faces": [
        [0, 2, 1], [0, 3, 2],          # bottom
        [0, 1, 5], [0, 5, 4],          # -Y
        [1, 2, 6], [1, 6, 5],          # +X
        [2, 3, 7], [2, 7, 6],          # +Y
        [3, 0, 4], [3, 4, 7],          # -X
    ],
}

CLOSED_CUBE = {
    "vertices": OPEN_BOX["vertices"],
    "faces": OPEN_BOX["faces"] + [[4, 5, 6], [4, 6, 7]],
}


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
def big_ring():
    return ring_mesh()


@pytest.fixture(scope="module")
def big_ring_stl(tmp_path_factory, big_ring):
    path = tmp_path_factory.mktemp("downloads") / "ring 300mm.stl"
    return write_binary_stl(path, *big_ring)


def named(checks, name):
    entry = next((c for c in checks if c["name"] == name), None)
    assert entry is not None, f"no {name} check in the response"
    return entry


# --------------------------------------------------------------------------
# The loader, on its own
# --------------------------------------------------------------------------


def test_an_inline_mesh_comes_through_unchanged():
    loaded = mesh_input.load_mesh_input({"mesh": CLOSED_CUBE})
    assert len(loaded["vertices"]) == 8
    assert len(loaded["faces"]) == 12
    info = loaded["info"]
    assert info["source"] == "mesh"
    assert info["format"] == "inline"
    assert info["input_vertex_count"] == 8
    assert info["merged_vertices"] == 0
    assert info["winding_flipped"] is False
    assert info["signed_volume_mm3"] == pytest.approx(1000.0)


def test_polygons_are_fanned_into_triangles():
    quads = {
        "vertices": CLOSED_CUBE["vertices"],
        "faces": [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5],
                  [2, 3, 7, 6], [3, 0, 4, 7]],
    }
    loaded = mesh_input.load_mesh_input({"mesh": quads})
    assert len(loaded["faces"]) == 12
    assert all(len(face) == 3 for face in loaded["faces"])
    assert loaded["info"]["polygons_triangulated"] == 6


def test_an_inside_out_closed_mesh_is_turned_the_right_way_out():
    flipped = {
        "vertices": CLOSED_CUBE["vertices"],
        "faces": [[a, c, b] for a, b, c in CLOSED_CUBE["faces"]],
    }
    loaded = mesh_input.load_mesh_input({"mesh": flipped})
    assert loaded["info"]["winding_flipped"] is True
    assert loaded["info"]["signed_volume_mm3"] == pytest.approx(1000.0)
    assert mesh_input.signed_volume(loaded["vertices"], loaded["faces"]) > 0


def test_a_face_index_out_of_range_is_a_param_error():
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input(
            {"mesh": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                      "faces": [[0, 1, 7]]}}
        )
    assert "7" in str(caught.value) and "3 vertices" in str(caught.value)


def test_a_non_finite_coordinate_is_a_param_error():
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input(
            {"mesh": {"vertices": [[0, 0, 0], [float("nan"), 0, 0], [0, 1, 0]],
                      "faces": [[0, 1, 2]]}}
        )
    assert "not finite" in str(caught.value)


def test_empty_input_is_a_param_error():
    with pytest.raises(ParamError):
        mesh_input.load_mesh_input({"mesh": {"vertices": [], "faces": []}})


def test_both_input_forms_at_once_is_a_param_error():
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input(
            {"mesh": CLOSED_CUBE, "file_path": "C:\\thing.stl"}
        )
    assert "not both" in str(caught.value)


def test_no_input_at_all_says_what_to_send():
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input({})
    message = str(caught.value)
    assert "file_path" in message and "vertices" in message


def test_a_relative_file_path_is_refused():
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input({"file_path": "thing.stl"})
    assert "absolute" in str(caught.value)


def test_an_unsupported_extension_names_the_ones_that_work(tmp_path):
    target = tmp_path / "thing.step"
    target.write_text("nope", encoding="utf-8")
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input({"file_path": str(target)})
    message = str(caught.value)
    assert ".stl" in message and ".3mf" in message and ".obj" in message


def test_a_missing_file_is_a_param_error(tmp_path):
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input({"file_path": str(tmp_path / "gone.stl")})
    assert "no such file" in str(caught.value)


# --------------------------------------------------------------------------
# The file readers
# --------------------------------------------------------------------------


def test_a_binary_stl_survives_float32_and_comes_back_watertight(tmp_path, big_ring):
    """The whole reason the weld is a proximity search and not a grid.

    Binary STL has no vertex indices and stores float32, so the eight copies of
    a corner in the file differ in the fifth decimal.  Welded properly the ring
    is closed; welded on a grid it would be an open soup of 3072 loose corners
    and every check downstream would answer the wrong question.
    """
    path = write_binary_stl(tmp_path / "ring.stl", *big_ring)
    loaded = mesh_input.load_mesh_input({"file_path": str(path)})

    info = loaded["info"]
    assert info["format"] == "stl"
    assert info["variant"] == "binary"
    assert info["source"] == "file_path"
    assert info["input_vertex_count"] == 3 * len(big_ring[1])
    assert len(loaded["vertices"]) == len(big_ring[0])
    assert info["merged_vertices"] == info["input_vertex_count"] - len(big_ring[0])

    from service.runner import mesh_edge_report

    report = mesh_edge_report(loaded["faces"])
    assert report["closed"] and report["oriented"]


def test_an_ascii_stl_reads_the_same_geometry(tmp_path):
    lines = ["solid cube"]
    vertices = CLOSED_CUBE["vertices"]
    for a, b, c in CLOSED_CUBE["faces"]:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for index in (a, b, c):
            x, y, z = vertices[index]
            lines.append(f"      vertex {x} {y} {z}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid cube")
    path = tmp_path / "cube.stl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    loaded = mesh_input.load_mesh_input({"file_path": str(path)})
    assert loaded["info"]["variant"] == "ascii"
    assert len(loaded["vertices"]) == 8
    assert len(loaded["faces"]) == 12
    assert loaded["info"]["signed_volume_mm3"] == pytest.approx(1000.0)


def test_obj_indices_may_be_negative_or_carry_slashes(tmp_path):
    path = tmp_path / "tri.obj"
    path.write_text(
        "v 0 0 0\nv 10 0 0\nv 0 10 0\nv 0 0 10\n"
        "f -4 -3 -2\n"          # negative, relative to the end
        "f 1/1/1 2/2/2 4/4/4\n"  # positive with texture/normal suffixes
        "f 1 4 3\nf 2 3 4\n",
        encoding="utf-8",
    )
    loaded = mesh_input.load_mesh_input({"file_path": str(path)})
    assert len(loaded["vertices"]) == 4
    assert len(loaded["faces"]) == 4
    assert loaded["info"]["format"] == "obj"


def test_a_truncated_stl_is_a_clear_400(tmp_path):
    path = tmp_path / "broken.stl"
    path.write_bytes(b"x" * 80 + struct.pack("<I", 900) + b"y" * 40)
    with pytest.raises(ParamError) as caught:
        mesh_input.load_mesh_input({"file_path": str(path)})
    assert "not a readable STL" in str(caught.value)


# --------------------------------------------------------------------------
# Welding, ceilings and refusals
# --------------------------------------------------------------------------


def test_the_weld_tolerance_is_scale_relative_and_capped():
    small = mesh_input.weld_tolerance_for([(0, 0, 0), (1, 1, 1)])
    big = mesh_input.weld_tolerance_for([(0, 0, 0), (300, 300, 20)])
    huge = mesh_input.weld_tolerance_for([(0, 0, 0), (100000, 0, 0)])
    assert small < big < mesh_input.WELD_MAX_MM
    assert huge == mesh_input.WELD_MAX_MM
    # Never coarse enough to merge two things a 0.4 mm nozzle could tell apart.
    assert mesh_input.WELD_MAX_MM <= 0.001


def test_welding_merges_points_across_a_cell_boundary():
    """A grid weld splits these two; a proximity weld does not.

    These two points are 2e-7 apart -- fifty times closer than the tolerance --
    but they sit either side of a bucket wall.  Grid welding would keep them
    apart and leave a hole in the mesh; the neighbour probe finds them.
    """
    tolerance = 1e-5
    cell = tolerance * 8.0
    boundary = 12500 * cell
    vertices = [(boundary - 1e-7, 0.0, 0.0), (boundary + 1e-7, 0.0, 0.0),
                (0.0, 1.0, 0.0)]
    assert math.floor(vertices[0][0] / cell) != math.floor(vertices[1][0] / cell)

    welded, faces, dropped = mesh_input.weld_nearby(vertices, [(0, 1, 2)], tolerance)
    assert len(welded) == 2
    assert faces == []  # the triangle collapsed, which is the honest answer
    assert dropped == 1


def test_the_triangle_ceiling_says_how_to_get_under_it():
    assert mesh_input.check_triangle_ceiling(10, limit=100) == 100
    with pytest.raises(ParamError) as caught:
        mesh_input.check_triangle_ceiling(500, limit=100)
    message = str(caught.value)
    assert "500 triangles" in message
    assert "Decimate" in message
    assert "FORGE_MESH_TRI_LIMIT" in message


def test_the_repair_message_is_the_one_the_contract_promised():
    assert mesh_input.REPAIR_FIRST_MESSAGE == (
        "repair it first - in Blender: select it, ask the assistant to voxel "
        "remesh it, or Forge panel -> Remesh"
    )


# --------------------------------------------------------------------------
# /check_mesh
# --------------------------------------------------------------------------


def test_check_mesh_agrees_with_check_on_the_same_geometry(
    client, kernel, ring_band_source
):
    """Parity: the mesh path must not be a second opinion.

    ``/check`` tessellates the solid it built; ``/check_mesh`` is handed those
    very triangles.  Every verdict has to match, and the only stats that may
    differ are the ones that need a B-Rep.
    """
    generated = client.post("/generate", json={"script": ring_band_source})
    assert generated.status_code == 200, generated.text
    mesh = generated.json()["mesh"]

    from_script = client.post("/check", json={"script": ring_band_source})
    from_mesh = client.post("/check_mesh", json={"mesh": mesh})
    assert from_script.status_code == 200, from_script.text
    assert from_mesh.status_code == 200, from_mesh.text
    script_body, mesh_body = from_script.json(), from_mesh.json()

    assert [c["name"] for c in mesh_body["checks"]] == [
        "bed_fit",
        "min_wall",
        "overhangs",
        "watertight",
    ]
    assert {c["name"]: c["status"] for c in mesh_body["checks"]} == {
        c["name"]: c["status"] for c in script_body["checks"]
    }
    assert mesh_body["overall"] == script_body["overall"] == "pass"
    assert mesh_body["printer"] == script_body["printer"]

    # No script, so no PARAMS; no B-Rep, so no OpenCascade validity verdict.
    assert mesh_body["params"] is None
    assert mesh_body["stats"]["solid_is_valid"] is None
    assert mesh_body["stats"]["bounding_box_source"] == "mesh"
    assert mesh_body["stats"]["watertight"] is True
    assert mesh_body["stats"]["face_count"] == script_body["stats"]["face_count"]
    # The bounding box is the one number that legitimately differs: /check
    # measures the exact B-Rep, /check_mesh measures the triangles, and a
    # tessellated circle sits inside its true one by up to the 0.05 mm
    # tessellation tolerance.  Well under anything a printer resolves.
    assert mesh_body["stats"]["bounding_box_mm"] == pytest.approx(
        script_body["stats"]["bounding_box_mm"], abs=0.05
    )
    assert mesh_body["mesh_input"]["source"] == "mesh"
    assert "check_ms" in mesh_body["timings"]


def test_check_mesh_reads_the_stl_we_exported(
    client, kernel, ring_band_source, tmp_path
):
    target = tmp_path / "band exports" / "band.stl"
    written = client.post(
        "/export",
        json={"script": ring_band_source, "format": "stl", "path": str(target)},
    )
    assert written.status_code == 200, written.text

    response = client.post("/check_mesh", json={"file_path": written.json()["path"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall"] == "pass"
    assert named(body["checks"], "watertight")["status"] == "pass"
    assert body["mesh_input"]["format"] == "stl"
    assert body["mesh_input"]["path"] == written.json()["path"]
    assert body["mesh_input"]["file_bytes"] > 0


def test_check_mesh_reads_a_3mf(client, kernel, ring_band_source, tmp_path):
    target = tmp_path / "band.3mf"
    written = client.post(
        "/export",
        json={"script": ring_band_source, "format": "3mf", "path": str(target)},
    )
    assert written.status_code == 200, written.text

    response = client.post("/check_mesh", json={"file_path": written.json()["path"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mesh_input"]["format"] == "3mf"
    assert body["mesh_input"]["unit_scale_mm"] == 1.0
    assert body["mesh_input"]["meshes"] == 1
    assert body["stats"]["watertight"] is True
    assert body["overall"] == "pass"


def test_a_3mf_in_metres_arrives_in_millimetres(kernel, tmp_path):
    """3MF carries its unit, and downloads are often in metres.

    lib3mf answers ``GetUnit()`` with a bare enum value, so reading the unit off
    ``str()`` would quietly treat a 10 m model as 10 mm.  This is that bug's
    tripwire.
    """
    from build123d import Box, Mesher, Unit

    mesher = Mesher(unit=Unit.M)
    mesher.add_shape(Box(10, 10, 10))
    path = tmp_path / "metres.3mf"
    mesher.write(str(path))

    loaded = mesh_input.load_mesh_input({"file_path": str(path)})
    assert loaded["info"]["unit"] == "Meter"
    assert loaded["info"]["unit_scale_mm"] == 1000.0
    xs = [v[0] for v in loaded["vertices"]]
    assert max(xs) - min(xs) == pytest.approx(10_000.0)


def test_check_mesh_reads_an_obj(client, kernel, ring_band_source, tmp_path):
    generated = client.post("/generate", json={"script": ring_band_source}).json()
    path = write_obj(
        tmp_path / "band.obj", generated["mesh"]["vertices"], generated["mesh"]["faces"]
    )

    response = client.post("/check_mesh", json={"file_path": str(path)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mesh_input"]["format"] == "obj"
    assert body["stats"]["watertight"] is True
    assert body["overall"] == "pass"


def test_check_mesh_reports_an_open_mesh_rather_than_refusing_it(client, kernel):
    """Checking is diagnosis: a broken mesh gets an answer, not a 400."""
    response = client.post("/check_mesh", json={"mesh": OPEN_BOX})
    assert response.status_code == 200, response.text
    body = response.json()
    watertight = named(body["checks"], "watertight")
    assert watertight["status"] == "fail"
    assert watertight["data"]["solid_is_valid"] is None
    assert watertight["data"]["boundary_edges"] == 4
    assert body["overall"] == "fail"


def test_check_mesh_needs_exactly_one_input_form(client, kernel):
    empty = client.post("/check_mesh", json={})
    assert empty.status_code == 400
    assert "file_path" in empty.json()["error"]

    both = client.post(
        "/check_mesh", json={"mesh": CLOSED_CUBE, "file_path": "C:\\a.stl"}
    )
    assert both.status_code == 400
    assert "not both" in both.json()["error"]


# --------------------------------------------------------------------------
# /segment_mesh -- the whole point
# --------------------------------------------------------------------------


def test_a_downloaded_ring_is_checked_and_cut(client, kernel, big_ring_stl):
    """The feature, end to end, from a file that could have come off the web.

    Check it: too big for the bed, and the answer is "cut it into four arcs".
    Then cut it exactly that way and get four watertight segments that fit.
    """
    checked = client.post("/check_mesh", json={"file_path": str(big_ring_stl)})
    assert checked.status_code == 200, checked.text
    report = checked.json()

    bed_fit = named(report["checks"], "bed_fit")
    assert bed_fit["status"] == "fail"
    assert report["overall"] == "fail"
    suggestion = bed_fit["data"]["suggested_segmentation"]
    assert suggestion["kind"] == "radial"
    assert suggestion["feasible"] is True
    assert suggestion["mode"] == {"radial": 4}
    assert suggestion["metrics"]["ring_like"] is True
    # The mesh itself is fine -- it is only the size that is wrong.
    assert named(report["checks"], "watertight")["status"] == "pass"

    cut = client.post(
        "/segment_mesh",
        json={
            "file_path": str(big_ring_stl),
            # Straight from the check above: bed_fit's suggestion is a valid mode.
            "mode": suggestion["mode"],
            "joint": {"type": "dovetail"},
            "include_mesh": False,
        },
    )
    assert cut.status_code == 200, cut.text
    body = cut.json()

    assert body["mode"] == {"kind": "radial", "count": 4, "start_angle_deg": 0.0}
    assert body["joint"]["type"] == "dovetail"
    assert len(body["cuts"]) == 4
    assert body["params"] is None

    segments = [s for s in body["segments"] if s["kind"] == "segment"]
    assert len(segments) == 4
    for segment in segments:
        assert segment["stats"]["watertight"] is True, segment["name"]
        assert segment["stats"]["solid_is_valid"] is True
        width, depth, height = segment["oriented_bbox_mm"]
        assert max(width, depth) < 246.0, segment["name"]
        assert height == pytest.approx(RING_HEIGHT_MM, abs=0.5)

    # ...and all four go on one plate.
    assert body["plate"]["fits"] is True
    assert len(body["plate"]["items"]) == len(body["segments"])

    sewing = body["sewing"]
    assert sewing["faces_sewn"] == body["mesh_input"]["face_count"]
    assert sewing["faces_skipped"] == 0
    assert sewing["shells"] == 1
    assert sewing["sew_tolerance_mm"] > 0
    assert body["mesh_input"]["triangle_limit"] == mesh_input.TRI_LIMIT
    assert "sew_ms" in body["timings"]


def test_segment_mesh_takes_the_same_geometry_inline(client, kernel, big_ring):
    """The two input forms are one code path: same cut, same verdicts."""
    vertices, faces = big_ring
    response = client.post(
        "/segment_mesh",
        json={
            "mesh": {"vertices": [list(v) for v in vertices], "faces": faces},
            "mode": {"radial": 4},
            "joint": {"type": "none"},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mesh_input"]["source"] == "mesh"
    assert len(body["segments"]) == 4
    assert all(s["stats"]["watertight"] for s in body["segments"])
    assert body["cuts"][0]["joint"]["type"] == "none"


def test_segment_mesh_refuses_a_mesh_with_holes_in_it(client, kernel):
    """The one refusal a beginner will actually hit, in words they can act on."""
    response = client.post(
        "/segment_mesh", json={"mesh": OPEN_BOX, "mode": {"planar": [5.0]}}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert mesh_input.REPAIR_FIRST_MESSAGE in error
    assert "holes in it" in error
    assert "4 open edges" in error
    # No jargon the artist has to look up.
    assert "BRep" not in error and "manifold edges" in error


def test_segment_mesh_refuses_a_mesh_denser_than_the_ceiling(client, kernel):
    response = client.post(
        "/segment_mesh",
        json={"mesh": CLOSED_CUBE, "mode": {"planar": [5.0]}, "tri_limit": 4},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "12 triangles, over the 4" in error
    assert "Decimate" in error


def test_the_ceiling_defaults_to_the_measured_number(monkeypatch):
    """60k is where a sew-plus-cut still lands inside FORGE_SEGMENT_TIMEOUT."""
    assert mesh_input.TRI_LIMIT == 60_000

    # The default is read from the environment at import, so re-importing with
    # the variable set is what proves it is tunable without touching code.
    import importlib

    monkeypatch.setenv("FORGE_MESH_TRI_LIMIT", "1234")
    try:
        reloaded = importlib.reload(mesh_input)
        assert reloaded.TRI_LIMIT == 1234
    finally:
        monkeypatch.delenv("FORGE_MESH_TRI_LIMIT", raising=False)
        importlib.reload(mesh_input)
    assert mesh_input.TRI_LIMIT == 60_000


def test_segment_mesh_still_refuses_a_bad_joint_before_it_sews(client, kernel, big_ring):
    """Validation that costs nothing must not wait behind work that costs a lot."""
    vertices, faces = big_ring
    response = client.post(
        "/segment_mesh",
        json={
            "mesh": {"vertices": [list(v) for v in vertices], "faces": faces},
            "mode": {"radial": 4},
            "joint": {"type": "glue"},
        },
    )
    assert response.status_code == 400
    assert "joint.type" in response.json()["error"]


# --------------------------------------------------------------------------
# /export_segments_mesh
# --------------------------------------------------------------------------


def test_export_segments_mesh_writes_one_file_each_plus_a_plate(
    client, kernel, big_ring_stl, tmp_path
):
    target = tmp_path / "ring out"  # a space, on purpose: Windows is the platform
    response = client.post(
        "/export_segments_mesh",
        json={
            "file_path": str(big_ring_stl),
            "mode": {"radial": 4},
            "joint": {"type": "dovetail"},
            "directory": str(target),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["files"]) == len(body["plate"]["items"]) == 4
    for entry in body["files"]:
        path = Path(entry["path"])
        assert path.parent == target
        # The default stem is "model", not "part": there is no script to name it.
        assert path.name.startswith("model_segment_")
        assert path.suffix == ".stl"
        data = path.read_bytes()
        (triangles,) = struct.unpack("<I", data[80:84])
        assert triangles > 0
        assert len(data) == 84 + 50 * triangles
        assert entry["stats"]["watertight"] is True

    plate = Path(body["plate_path"])
    assert plate.name == "model_plate.3mf"
    assert plate.exists() and plate.stat().st_size > 0
    assert body["mesh_input"]["source"] == "file_path"
    assert "export_ms" in body["timings"]


def test_export_segments_mesh_rejects_a_relative_directory(client, kernel):
    response = client.post(
        "/export_segments_mesh",
        json={"mesh": CLOSED_CUBE, "mode": {"planar": [5.0]}, "directory": "out"},
    )
    assert response.status_code == 400
    assert "absolute" in response.json()["error"]
