"""Mesh input: "fix this downloaded model".

Everything the service does past ``/generate`` -- the print checks, the cuts,
the joints, the plate -- is worth as much to a model somebody downloaded as it
is to a PARAMS script.  This module is the door those models come in through.

Two input forms, and both end up in the same place::

    {"mesh": {"vertices": [[x, y, z], ...], "faces": [[i, j, k], ...]}}
    {"file_path": "C:/downloads/thing.stl"}     # .stl | .3mf | .obj

Coordinates are **millimetres**, the same as everywhere else in the service.
The readers are deliberately small and explicit rather than a third-party mesh
library: the four formats we need are simple, and a downloaded file is exactly
the input where a surprising dependency failure is least welcome.

What happens on the way in
--------------------------
1. **Read** -- triangles out of the file, or the caller's own arrays.
2. **Triangulate** -- a face with more than three corners is fanned from its
   first vertex.  Quads are common in OBJ and legal in a caller's arrays.
3. **Validate** -- non-empty, every index in range, every coordinate finite.
   These are 400s: a malformed mesh is the caller's to fix.
4. **Weld** -- coincident vertices are merged with a proximity search, not a
   grid.  Binary STL stores float32, so the corner two triangles share can
   differ in the fifth decimal; grid welding would leave that model an open
   soup and every check downstream would be answering the wrong question.
5. **Orient** -- a closed mesh whose triangles wind the wrong way (a negative
   signed volume) has every triangle flipped.  Inward normals would invert the
   overhang report and hand OCC an inside-out solid.

Then the mesh is exactly the thing :mod:`checks` already works on, and
:func:`sew_to_solid` turns it into the B-Rep the segmenting machinery needs.

Nothing above :func:`sew_to_solid` imports build123d or OCP, so the loader can
be unit-tested without the kernel.
"""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ParamError, ServiceError

Vec3 = Tuple[float, float, float]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


#: Mesh file extensions we read.  The export side writes STL, STEP and 3MF;
#: STEP is a B-Rep and does not belong here, and OBJ is added because it is what
#: half the model sites hand you.
MESH_FORMATS: Dict[str, str] = {
    ".stl": "stl",
    ".3mf": "3mf",
    ".obj": "obj",
}

#: Vertices closer together than this are the same vertex.  Scale-relative,
#: because the noise being welded out is float32 rounding in the file: a 300 mm
#: model stored as float32 agrees with itself to about 3e-5 mm.  Clamped to
#: 1 micron so it can never merge two features a printer could tell apart --
#: a 0.4 mm nozzle is four hundred times coarser than the ceiling here.
WELD_RELATIVE = 1e-6
WELD_MIN_MM = 1e-6
WELD_MAX_MM = _env_float("FORGE_MESH_WELD_TOLERANCE", 1e-3)

#: Triangles a mesh may bring to :func:`sew_to_solid`.
#:
#: Sewing is one OCC face per triangle, and then the cut booleans run against
#: every one of those faces, so a dense mesh is expensive twice over.  Measured
#: end to end on this machine -- a 300 mm ring cut radially into four with
#: dovetails, which is the shape of job this endpoint exists for::
#:
#:      tris     sew      cut+joints   re-tessellate    ~total
#:       512    4.0 s        0.8 s          0.4 s         5 s
#:     3 072    4.5 s        5.2 s          2.1 s        12 s
#:    14 000    8.3 s       24.8 s         10.5 s        44 s
#:    39 200   19.5 s       80.8 s         29.6 s       130 s
#:
#: Sewing is roughly linear; the cut is slightly worse than linear.  The budget
#: this has to fit inside is ``FORGE_SEGMENT_TIMEOUT`` (300 s), and 60k
#: extrapolates to about 200 s of it -- enough headroom for a slower machine or
#: a part cut into more pieces, and a ceiling a Blender Decimate modifier can
#: bring a typical download under in one step.  ``FORGE_MESH_TRI_LIMIT``
#: overrides it for anyone happy to wait.
TRI_LIMIT = _env_int("FORGE_MESH_TRI_LIMIT", 60_000)

#: Sewing tolerance, in millimetres.  The mesh reaching the sewer has already
#: been welded, so vertices two triangles share are bit-identical and the sewer
#: only has to not be *stricter* than that.  It is deliberately the same number
#: as the weld: one tolerance to reason about, and both are far below anything
#: a printer resolves.  ``FORGE_MESH_SEW_TOLERANCE`` overrides it absolutely.
SEW_TOLERANCE_ENV = "FORGE_MESH_SEW_TOLERANCE"

#: The one message a beginner needs when their download is broken.  Repairing a
#: mesh is not this service's job -- Blender already has the tool -- so the
#: refusal says where the tool is instead of what a manifold edge is.
REPAIR_FIRST_MESSAGE = (
    "repair it first - in Blender: select it, ask the assistant to voxel remesh "
    "it, or Forge panel -> Remesh"
)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def resolve_mesh_path(path: Any) -> Path:
    """Validate ``file_path`` into an existing, absolute, readable mesh file."""
    if not isinstance(path, str) or not path.strip():
        raise ParamError("file_path must be an absolute path to an .stl, .3mf or .obj")

    candidate = Path(path.strip())
    if not candidate.is_absolute():
        raise ParamError(
            f"file_path must be absolute, got {path!r}; the service does not guess a "
            "working directory"
        )
    if candidate.is_dir():
        raise ParamError(f"file_path {str(candidate)!r} is a directory, not a file")
    if not candidate.exists():
        raise ParamError(f"no such file: {str(candidate)!r}")

    suffix = candidate.suffix.lower()
    if suffix not in MESH_FORMATS:
        raise ParamError(
            f"unsupported mesh format {suffix or '(no extension)'!r} for "
            f"{str(candidate)!r}; use one of "
            f"{', '.join(sorted(MESH_FORMATS))} (STEP is a B-Rep -- use /check "
            "and /segment with the script that made it)"
        )
    return candidate


def read_mesh_file(path: Path) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    """Read one mesh file into ``(vertices, polygons, info)``.

    ``polygons`` may contain faces with more than three corners; they are
    triangulated later, in one place, for every input form.
    """
    fmt = MESH_FORMATS[path.suffix.lower()]
    if fmt == "stl":
        return read_stl(path)
    if fmt == "obj":
        return read_obj(path)
    return read_3mf(path)


def read_stl(path: Path) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    """Binary or ASCII STL.  The header's triangle count decides which."""
    data = path.read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:
            return _read_stl_binary(data, count)

    # Not a well-formed binary file.  ASCII STL starts with "solid", but so does
    # a binary file whose header happens to say so -- which is why the size test
    # above runs first.
    text = data.decode("utf-8", "replace")
    if "facet" not in text and not text.lstrip().lower().startswith("solid"):
        raise ParamError(
            f"{str(path)!r} is not a readable STL: the binary triangle count does "
            "not match the file size and there is no ASCII facet in it"
        )
    return _read_stl_ascii(text)


def _read_stl_binary(
    data: bytes, count: int
) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    vertices: List[Vec3] = []
    faces: List[List[int]] = []
    offset = 84
    for index in range(count):
        values = struct.unpack_from("<12f", data, offset)
        offset += 50
        base = 3 * index
        vertices.append((values[3], values[4], values[5]))
        vertices.append((values[6], values[7], values[8]))
        vertices.append((values[9], values[10], values[11]))
        faces.append([base, base + 1, base + 2])
    return vertices, faces, {"format": "stl", "variant": "binary"}


def _read_stl_ascii(text: str) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    vertices: List[Vec3] = []
    faces: List[List[int]] = []
    loop: List[int] = []

    for number, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        keyword = parts[0].lower()
        if keyword == "vertex":
            if len(parts) < 4:
                raise ParamError(f"ASCII STL line {number}: a vertex needs three numbers")
            try:
                point = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise ParamError(
                    f"ASCII STL line {number}: {line.strip()!r} is not a vertex"
                ) from exc
            loop.append(len(vertices))
            vertices.append(point)
        elif keyword == "endloop":
            # Facets are triangles by the spec, but a loop with more corners is
            # fanned rather than refused: the file is still describing a surface.
            if len(loop) >= 3:
                faces.append(list(loop))
            loop = []
        elif keyword == "outer":
            loop = []

    if not faces:
        raise ParamError("the ASCII STL has no facets in it")
    return vertices, faces, {"format": "stl", "variant": "ascii"}


def read_obj(path: Path) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    """Minimal OBJ: ``v`` and ``f``.

    Normals, texture coordinates, materials, groups and smoothing are skipped --
    a print has no use for any of them.  ``f`` indices are 1-based, may be
    negative (relative to the end), and may carry ``/`` suffixes.
    """
    vertices: List[Vec3] = []
    faces: List[List[int]] = []
    objects = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            parts = line.split()
            if not parts:
                continue
            keyword = parts[0]
            if keyword == "v":
                if len(parts) < 4:
                    raise ParamError(f"OBJ line {number}: 'v' needs three coordinates")
                try:
                    vertices.append(
                        (float(parts[1]), float(parts[2]), float(parts[3]))
                    )
                except ValueError as exc:
                    raise ParamError(
                        f"OBJ line {number}: {line.strip()!r} is not a vertex"
                    ) from exc
            elif keyword == "f":
                corners: List[int] = []
                for token in parts[1:]:
                    head = token.split("/", 1)[0]
                    try:
                        raw = int(head)
                    except ValueError as exc:
                        raise ParamError(
                            f"OBJ line {number}: {token!r} is not a face index"
                        ) from exc
                    if raw == 0:
                        raise ParamError(f"OBJ line {number}: face index 0 is not legal")
                    corners.append(raw - 1 if raw > 0 else len(vertices) + raw)
                if len(corners) >= 3:
                    faces.append(corners)
            elif keyword == "o":
                objects += 1

    if not vertices:
        raise ParamError("the OBJ has no vertices in it")
    if not faces:
        raise ParamError(
            "the OBJ has no faces in it; a point cloud cannot be checked or cut"
        )
    return vertices, faces, {"format": "obj", "objects": objects}


#: 3MF model units -> millimetres.  A downloaded 3MF is often in metres.
_3MF_UNIT_MM = {
    "MicroMeter": 0.001,
    "MilliMeter": 1.0,
    "CentiMeter": 10.0,
    "Inch": 25.4,
    "Foot": 304.8,
    "Meter": 1000.0,
}


def read_3mf(path: Path) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    """3MF through lib3mf -- the same library the export side writes with.

    Every mesh object in the model is concatenated.  Build-item transforms are
    *not* applied (build123d's own reader does not apply them either), so a 3MF
    that positions one mesh several times comes in once, in its own
    coordinates.  Model units are honoured: metres become millimetres here.
    """
    try:
        import lib3mf  # noqa: PLC0415 - only the 3MF path pays for it
        from lib3mf import Lib3MF  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(
            f"lib3mf is not importable, so 3MF input is unavailable ({exc}); "
            "export the model as STL instead"
        ) from exc

    try:
        wrapper = Lib3MF.Wrapper(os.path.join(os.path.dirname(lib3mf.__file__), "lib3mf"))
        model = wrapper.CreateModel()
        reader = model.QueryReader("3mf")
        reader.ReadFromFile(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParamError(f"lib3mf could not read {str(path)!r}: {exc}") from exc

    # GetUnit() answers with the raw enum value, not a name -- lib3mf's
    # ModelUnit is an IntEnum, so 5 prints as "5" and a str() lookup would
    # silently read every metre as a millimetre.  Resolve the member.
    raw_unit = model.GetUnit()
    try:
        unit_name = Lib3MF.ModelUnit(raw_unit).name
    except Exception:  # noqa: BLE001 - an unknown unit is not a broken file
        unit_name = str(raw_unit)
    scale = _3MF_UNIT_MM.get(unit_name, 1.0)

    vertices: List[Vec3] = []
    faces: List[List[int]] = []
    meshes = 0

    iterator = model.GetMeshObjects()
    while iterator.MoveNext():
        mesh = iterator.GetCurrentMeshObject()
        meshes += 1
        offset = len(vertices)
        for position in mesh.GetVertices():
            x, y, z = position.Coordinates[0:3]
            vertices.append((x * scale, y * scale, z * scale))
        for index in range(mesh.GetTriangleCount()):
            a, b, c = mesh.GetTriangle(index).Indices[0:3]
            faces.append([offset + a, offset + b, offset + c])

    if not faces:
        raise ParamError(f"{str(path)!r} has no mesh objects with triangles in it")
    return (
        vertices,
        faces,
        {"format": "3mf", "meshes": meshes, "unit": unit_name, "unit_scale_mm": scale},
    )


# --------------------------------------------------------------------------
# Validation, triangulation, welding
# --------------------------------------------------------------------------


def _read_inline(mesh: Any) -> Tuple[List[Vec3], List[List[int]], Dict[str, Any]]:
    """Validate a caller's own ``{"vertices", "faces"}`` arrays."""
    if not isinstance(mesh, Mapping):
        raise ParamError(
            f"mesh must be an object with 'vertices' and 'faces', got "
            f"{type(mesh).__name__}"
        )
    raw_vertices = mesh.get("vertices")
    raw_faces = mesh.get("faces")
    if not isinstance(raw_vertices, (list, tuple)):
        raise ParamError("mesh.vertices must be a list of [x, y, z] points in mm")
    if not isinstance(raw_faces, (list, tuple)):
        raise ParamError("mesh.faces must be a list of index lists")
    if not raw_vertices:
        raise ParamError("mesh.vertices is empty; there is no geometry to work on")
    if not raw_faces:
        raise ParamError("mesh.faces is empty; a point cloud cannot be checked or cut")

    vertices: List[Vec3] = []
    for index, point in enumerate(raw_vertices):
        if isinstance(point, (str, bytes)) or not isinstance(point, (list, tuple)):
            raise ParamError(
                f"mesh.vertices[{index}] must be [x, y, z], got {type(point).__name__}"
            )
        if len(point) < 3:
            raise ParamError(f"mesh.vertices[{index}] needs three coordinates")
        try:
            vertices.append((float(point[0]), float(point[1]), float(point[2])))
        except (TypeError, ValueError) as exc:
            raise ParamError(
                f"mesh.vertices[{index}] is not three numbers: {point!r}"
            ) from exc

    faces: List[List[int]] = []
    for index, face in enumerate(raw_faces):
        if isinstance(face, (str, bytes)) or not isinstance(face, (list, tuple)):
            raise ParamError(
                f"mesh.faces[{index}] must be a list of vertex indices, got "
                f"{type(face).__name__}"
            )
        if len(face) < 3:
            raise ParamError(
                f"mesh.faces[{index}] has {len(face)} corners; a face needs at least 3"
            )
        corners: List[int] = []
        for corner in face:
            if isinstance(corner, bool) or not isinstance(corner, int):
                raise ParamError(
                    f"mesh.faces[{index}] contains {corner!r}, which is not an "
                    "integer vertex index"
                )
            corners.append(int(corner))
        faces.append(corners)

    return vertices, faces, {"format": "inline"}


def _check_finite(vertices: Sequence[Vec3]) -> None:
    for index, (x, y, z) in enumerate(vertices):
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise ParamError(
                f"vertex {index} is not finite ({x}, {y}, {z}); the mesh has NaN or "
                "infinite coordinates in it"
            )


def triangulate(
    faces: Sequence[Sequence[int]], vertex_count: int
) -> Tuple[List[Tuple[int, int, int]], int, int]:
    """Fan every polygon into triangles, checking indices as we go.

    Returns ``(triangles, polygons_triangulated, degenerate_dropped)``.  A
    triangle that repeats an index carries no area and no normal, so it is
    dropped rather than passed on to arithmetic that would divide by its length.
    """
    triangles: List[Tuple[int, int, int]] = []
    polygons = 0
    degenerate = 0

    for index, face in enumerate(faces):
        for corner in face:
            if not 0 <= corner < vertex_count:
                raise ParamError(
                    f"face {index} refers to vertex {corner}, but the mesh has "
                    f"{vertex_count} vertices (indices are 0-based)"
                )
        if len(face) > 3:
            polygons += 1
        anchor = face[0]
        for offset in range(1, len(face) - 1):
            a, b, c = anchor, face[offset], face[offset + 1]
            if a == b or b == c or a == c:
                degenerate += 1
                continue
            triangles.append((int(a), int(b), int(c)))

    return triangles, polygons, degenerate


def weld_tolerance_for(vertices: Sequence[Vec3]) -> float:
    """Scale-relative weld tolerance for this mesh, in millimetres."""
    if not vertices:
        return WELD_MIN_MM
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    diagonal = math.sqrt(
        (max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2
    )
    return min(max(diagonal * WELD_RELATIVE, WELD_MIN_MM), WELD_MAX_MM)


def weld_nearby(
    vertices: Sequence[Vec3],
    triangles: Sequence[Sequence[int]],
    tolerance: float,
) -> Tuple[List[List[float]], List[List[int]], int]:
    """Merge vertices within *tolerance* of each other.

    A proximity weld, not the grid weld :func:`runner.weld_vertices` uses.  The
    grid is right for OCC output, where two faces' triangulations of a shared
    edge agree to a nanometre; it is wrong for a downloaded file, where the two
    copies of a corner differ by float32 rounding and can straddle a grid line.
    Two points that differ by less than the tolerance land in the same bucket
    here whatever side of a cell boundary they sit on.

    Returns ``(vertices, triangles, degenerate_dropped)``.
    """
    tolerance = max(float(tolerance), 0.0)
    # Cells much bigger than the tolerance so the usual vertex probes exactly
    # one bucket; only points within a tolerance of a cell wall look sideways.
    cell = max(tolerance * 8.0, 1e-12)

    buckets: Dict[Tuple[int, int, int], List[int]] = {}
    welded: List[List[float]] = []
    remap: List[int] = [0] * len(vertices)
    floor = math.floor

    for old_index, (x, y, z) in enumerate(vertices):
        cx, cy, cz = int(floor(x / cell)), int(floor(y / cell)), int(floor(z / cell))
        found: Optional[int] = None
        for kx in _neighbours(x, cx, cell, tolerance):
            for ky in _neighbours(y, cy, cell, tolerance):
                for kz in _neighbours(z, cz, cell, tolerance):
                    bucket = buckets.get((kx, ky, kz))
                    if not bucket:
                        continue
                    for candidate in bucket:
                        point = welded[candidate]
                        if (
                            abs(point[0] - x) <= tolerance
                            and abs(point[1] - y) <= tolerance
                            and abs(point[2] - z) <= tolerance
                        ):
                            found = candidate
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                break

        if found is None:
            found = len(welded)
            welded.append([x, y, z])
            buckets.setdefault((cx, cy, cz), []).append(found)
        remap[old_index] = found

    out: List[List[int]] = []
    degenerate = 0
    for tri in triangles:
        a, b, c = remap[tri[0]], remap[tri[1]], remap[tri[2]]
        if a == b or b == c or a == c:
            degenerate += 1
            continue
        out.append([a, b, c])

    return welded, out, degenerate


def _neighbours(value: float, cell_index: int, cell: float, tolerance: float):
    """Cell indices along one axis that could hold a vertex within *tolerance*."""
    yield cell_index
    if tolerance <= 0.0:
        return
    low = cell_index * cell
    if value - low < tolerance:
        yield cell_index - 1
    if (low + cell) - value < tolerance:
        yield cell_index + 1


def signed_volume(
    vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]
) -> float:
    """Signed volume by the divergence theorem; negative means inward normals."""
    total = 0.0
    for a, b, c in triangles:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        total += (
            pa[0] * (pb[1] * pc[2] - pb[2] * pc[1])
            - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
            + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0])
        )
    return total / 6.0


# --------------------------------------------------------------------------
# The loader
# --------------------------------------------------------------------------


def load_mesh_input(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn a request body into ``{"vertices", "faces", "info"}``.

    Exactly one of ``mesh`` and ``file_path`` must be present.  ``info`` is the
    report that goes back to the caller: where the mesh came from, what it
    looked like before and after welding, and what had to be repaired to make
    it usable.
    """
    inline = spec.get("mesh")
    file_path = spec.get("file_path")

    if inline is not None and file_path is not None:
        raise ParamError(
            "give either 'mesh' (vertices and faces) or 'file_path' (an .stl, "
            ".3mf or .obj), not both"
        )
    if inline is None and file_path is None:
        raise ParamError(
            "no mesh in the request: give 'mesh' as {\"vertices\": [[x, y, z], ...], "
            "\"faces\": [[i, j, k], ...]} in millimetres, or 'file_path' as an "
            "absolute path to an .stl, .3mf or .obj"
        )

    if inline is not None:
        vertices, polygons, info = _read_inline(inline)
        info["source"] = "mesh"
    else:
        path = resolve_mesh_path(file_path)
        vertices, polygons, info = read_mesh_file(path)
        info["source"] = "file_path"
        info["path"] = str(path)
        info["file_bytes"] = path.stat().st_size

    if not vertices:
        raise ParamError("the mesh has no vertices in it")
    _check_finite(vertices)

    info["input_vertex_count"] = len(vertices)
    info["input_face_count"] = len(polygons)

    triangles, fanned, degenerate = triangulate(polygons, len(vertices))
    if not triangles:
        raise ParamError(
            "the mesh has no usable triangles; every face was degenerate "
            "(repeated vertex indices)"
        )
    info["polygons_triangulated"] = fanned

    override = spec.get("weld_tolerance_mm")
    if override is not None:
        if isinstance(override, bool) or not isinstance(override, (int, float)):
            raise ParamError(
                f"weld_tolerance_mm must be a number, got {override!r}"
            )
        tolerance = float(override)
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ParamError(
                f"weld_tolerance_mm must be finite and not negative, got {override!r}"
            )
    else:
        tolerance = weld_tolerance_for(vertices)

    welded, faces, welded_degenerate = weld_nearby(vertices, triangles, tolerance)
    if not faces:
        raise ParamError(
            "every triangle collapsed when coincident vertices were merged; the "
            "mesh is flat or the weld tolerance is far too large"
        )

    info["weld_tolerance_mm"] = tolerance
    info["merged_vertices"] = len(vertices) - len(welded)
    info["degenerate_faces_dropped"] = degenerate + welded_degenerate
    info["vertex_count"] = len(welded)
    info["face_count"] = len(faces)

    # An inside-out closed mesh is common in downloads and silently poisons
    # everything downstream: overhangs would be measured off inward normals and
    # OCC would sew a solid whose "inside" is the rest of the universe.
    volume = signed_volume(welded, faces)
    flipped = False
    if volume < 0.0:
        faces = [[tri[0], tri[2], tri[1]] for tri in faces]
        volume = -volume
        flipped = True
    info["winding_flipped"] = flipped
    info["signed_volume_mm3"] = round(volume, 6)

    return {"vertices": welded, "faces": faces, "info": info}


def check_triangle_ceiling(face_count: int, limit: Optional[int] = None) -> int:
    """Refuse a mesh too dense to sew and boolean in a sensible time."""
    ceiling = int(limit) if limit else TRI_LIMIT
    if ceiling > 0 and face_count > ceiling:
        raise ParamError(
            f"this mesh has {face_count} triangles, over the {ceiling} the service "
            "will sew into a solid. Segmenting builds one OpenCascade face per "
            "triangle and then cuts against every one of them, so a dense mesh "
            "takes minutes rather than seconds. Decimate it first - in Blender: "
            "select it, ask the assistant to decimate it, or add a Decimate "
            "modifier and set the ratio until the triangle count is under "
            f"{ceiling} - then try again. Raise FORGE_MESH_TRI_LIMIT if you would "
            "rather wait."
        )
    return ceiling


def require_watertight(stats: Mapping[str, Any]) -> None:
    """Refuse a mesh that is not closed, in words a beginner can act on."""
    if stats.get("watertight"):
        return
    problems = []
    boundary = stats.get("boundary_edges")
    nonmanifold = stats.get("nonmanifold_edges")
    if not stats.get("mesh_is_closed"):
        problems.append(f"{boundary} open edges and {nonmanifold} non-manifold edges")
    if not stats.get("mesh_is_oriented"):
        problems.append("triangles wound inconsistently")
    detail = "; ".join(problems) or "it is not a closed surface"
    raise ParamError(
        f"this mesh has holes in it ({detail}), so it cannot be cut into printable "
        f"segments - {REPAIR_FIRST_MESSAGE}. Then send it back here."
    )


# --------------------------------------------------------------------------
# Sewing: mesh -> B-Rep solid
# --------------------------------------------------------------------------


def sew_tolerance_for(vertices: Sequence[Sequence[float]], override: Any = None) -> float:
    """The tolerance :func:`sew_to_solid` hands OCC, in millimetres."""
    if override is not None:
        if isinstance(override, bool) or not isinstance(override, (int, float)):
            raise ParamError(f"sew_tolerance_mm must be a number, got {override!r}")
        value = float(override)
        if not math.isfinite(value) or value <= 0.0:
            raise ParamError(
                f"sew_tolerance_mm must be a finite positive number, got {override!r}"
            )
        return value
    env = os.environ.get(SEW_TOLERANCE_ENV)
    if env:
        try:
            value = float(env)
            if math.isfinite(value) and value > 0.0:
                return value
        except (TypeError, ValueError):
            pass
    return max(weld_tolerance_for([tuple(v[:3]) for v in vertices]), WELD_MIN_MM)


def sew_to_solid(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    tolerance: float,
) -> Any:
    """Sew a watertight triangle mesh into a build123d ``Solid``.

    One planar OCC face per triangle, sewn into shells, and the shells assembled
    into a solid -- the outermost shell is the skin and any others are voids,
    which is what makes a hollow model come through hollow.  This is the same
    construction build123d's own 3MF reader uses; it is spelled out here because
    the tolerance, the winding and the failure messages all have to be ours.

    The result is an ordinary build123d solid, so every cut, joint, plate and
    export past this point is the code the PARAMS path already uses.
    """
    from OCP.BRepBuilderAPI import (  # noqa: PLC0415 - kernel imports stay in the worker
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_Sewing,
    )
    from OCP.gp import gp_Pnt  # noqa: PLC0415
    from OCP.TopAbs import TopAbs_SHELL  # noqa: PLC0415
    from OCP.TopExp import TopExp_Explorer  # noqa: PLC0415
    from OCP.TopoDS import TopoDS, TopoDS_Shell  # noqa: PLC0415

    from build123d import Shell, Solid  # noqa: PLC0415

    points = [gp_Pnt(float(v[0]), float(v[1]), float(v[2])) for v in vertices]

    sewing = BRepBuilderAPI_Sewing(float(tolerance))
    added = 0
    skipped = 0
    for tri in triangles:
        a, b, c = points[tri[0]], points[tri[1]], points[tri[2]]
        try:
            polygon = BRepBuilderAPI_MakePolygon(a, b, c, Close=True)
            face = BRepBuilderAPI_MakeFace(polygon.Wire())
            if not face.IsDone():
                skipped += 1
                continue
            sewing.Add(face.Face())
            added += 1
        except Exception:  # noqa: BLE001 - one bad triangle is not a bad mesh
            skipped += 1

    if added == 0:
        raise ParamError(
            "not one triangle in this mesh could be turned into a surface; the "
            "coordinates are degenerate"
        )

    sewing.Perform()
    sewed = sewing.SewedShape()

    shells = []
    explorer = TopExp_Explorer(sewed, TopAbs_SHELL)
    while explorer.More():
        shells.append(Shell(TopoDS.Shell_s(explorer.Current())))
        explorer.Next()
    if not shells:
        if isinstance(sewed, TopoDS_Shell):  # pragma: no cover - defensive
            shells = [Shell(sewed)]
    if not shells:
        raise ParamError(
            "OpenCascade could not sew this mesh into a closed surface even though "
            f"the triangles look closed - {REPAIR_FIRST_MESSAGE}"
        )

    outer = max(shells, key=lambda s: math.prod(s.bounding_box().size))
    builder = BRepBuilderAPI_MakeSolid(outer.wrapped)
    for shell in shells:
        if shell is not outer:
            builder.Add(shell.wrapped)
    if not builder.IsDone():
        raise ParamError(
            "OpenCascade could not close this mesh into a solid - "
            f"{REPAIR_FIRST_MESSAGE}"
        )

    solid = Solid(builder.Solid())
    return solid, {
        "faces_sewn": added,
        "faces_skipped": skipped,
        "shells": len(shells),
        "voids": len(shells) - 1,
        "sew_tolerance_mm": float(tolerance),
    }


__all__ = [
    "MESH_FORMATS",
    "REPAIR_FIRST_MESSAGE",
    "TRI_LIMIT",
    "check_triangle_ceiling",
    "load_mesh_input",
    "read_3mf",
    "read_mesh_file",
    "read_obj",
    "read_stl",
    "require_watertight",
    "resolve_mesh_path",
    "sew_to_solid",
    "sew_tolerance_for",
    "signed_volume",
    "triangulate",
    "weld_nearby",
    "weld_tolerance_for",
]
