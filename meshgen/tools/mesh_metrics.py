"""Deterministic geometric scoring of a generated ``.glb``.

Every number here is computed from vertices and triangles alone.  No VLM, no
render comparison by eye, no "looks better" — the whole point of the A/B tuning
harness is that a setting change has to *move a number* before it changes a
default, and that whoever reads meshgen/README.md later can re-run it and get
the same number.

What is measured, and which setting each one is there to catch:

``silhouette_iou``   the mesh's outline against the input image's exact mask,
                     through the identical camera.  Catches anything that eats
                     volume — ``smooth_iters`` shrinking the object, or
                     ``project_back`` pulling it back onto the true surface.
``edge_sharpness``   of the edge length that is a *crease at all* (dihedral
                     above 5°), the fraction that is still a real crease (60°+)
                     rather than melted into a 5–60° ramp.  A cube scores 1.0;
                     a cube with its corners rounded off scores near 0.  This is
                     the number ``smooth_iters`` and ``qef`` move.
``crease_normal_p95`` 95th-percentile angle between a shading normal and its
                     own face's geometric normal, over faces that touch a real
                     crease.  ``crease_angle`` moves only this: it splits
                     vertices, so it changes shading, never geometry — the
                     silhouette and dihedral numbers are blind to it by
                     construction, and a harness that reported "no change" for
                     it would be reporting its own blind spot.
``boundary_edges`` / ``nonmanifold_edges`` / ``components``  topology health.
``self_intersections``  clipping, the defect voxel repair downstream has to fix.
``faces`` / ``verts``   the cost side of the ledger.

The rasteriser is an **adaptive point splat**, not a scanline fill: each
triangle is sampled on a barycentric lattice fine enough that its samples land
within half a pixel of one another.  Generated meshes are already sub-pixel at
this raster and take the cheapest lattice; the adaptivity is what keeps the same
function exact on a coarse analytic test mesh, where a fixed pattern leaves gaps
and reports a confidently wrong IoU.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen import glb  # noqa: E402
from meshgen.tools import ab_fixtures  # noqa: E402

#: glTF componentType -> numpy dtype
_COMPONENT = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2",
              5125: "<u4", 5126: "<f4"}
_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

#: dihedral bands, degrees.  Below FLAT an edge is a flat panel; above SHARP it
#: is a real crease; between them is the rounding-over a smoothing pass leaves.
FLAT_DEG = 5.0
SHARP_DEG = 60.0

#: raster size for the scored silhouette, and for the cheap alignment probe
RASTER = 256
ALIGN_RASTER = 64

class MetricError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# reading the mesh
# ---------------------------------------------------------------------------
def _accessor(doc, blob, index):
    acc = doc["accessors"][index]
    dtype = np.dtype(_COMPONENT[acc["componentType"]])
    per = _COUNT[acc["type"]]
    count = int(acc["count"])
    view = doc["bufferViews"][acc["bufferView"]]
    start = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    stride = int(view.get("byteStride", 0)) or dtype.itemsize * per

    raw = np.frombuffer(blob, dtype=np.uint8,
                        offset=start, count=stride * (count - 1) + dtype.itemsize * per)
    strided = np.lib.stride_tricks.as_strided(
        raw, shape=(count, dtype.itemsize * per), strides=(stride, 1))
    out = np.ascontiguousarray(strided).view(dtype).reshape(count, per)
    return out.astype(np.float64) if dtype.kind == "f" else out.astype(np.int64)


def _node_matrices(doc):
    """World matrix per mesh index, composed down the node tree."""
    nodes = doc.get("nodes") or []
    out = {}

    def walk(index, parent):
        node = nodes[index]
        if "matrix" in node:
            local = np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        else:
            local = np.eye(4)
            if "scale" in node:
                local[:3, :3] = np.diag(node["scale"])
            if "rotation" in node:
                x, y, z, w = node["rotation"]
                rot = np.array([
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ])
                local[:3, :3] = rot @ local[:3, :3]
            if "translation" in node:
                local[:3, 3] = node["translation"]
        world = parent @ local
        if "mesh" in node:
            out.setdefault(int(node["mesh"]), world)
        for child in node.get("children") or []:
            walk(int(child), world)

    roots = set(range(len(nodes)))
    for node in nodes:
        for child in node.get("children") or []:
            roots.discard(int(child))
    for index in sorted(roots):
        walk(index, np.eye(4))
    return out


def load_glb(path):
    """``{"verts", "faces", "normals"}`` in world space, as numpy arrays."""
    doc, blob = glb.read_chunks(path)
    if blob is None:
        raise MetricError(f"{path} has no BIN chunk - nothing to measure")
    matrices = _node_matrices(doc)

    verts, faces, normals = [], [], []
    offset = 0
    have_normals = True
    for mesh_index, mesh in enumerate(doc.get("meshes") or []):
        world = matrices.get(mesh_index, np.eye(4))
        normal_world = np.linalg.inv(world[:3, :3]).T
        for prim in mesh.get("primitives") or []:
            if prim.get("mode", 4) != 4:
                continue
            attrs = prim.get("attributes") or {}
            if "POSITION" not in attrs:
                continue
            pos = _accessor(doc, blob, attrs["POSITION"])
            pos = pos @ world[:3, :3].T + world[:3, 3]
            if "indices" in prim and prim["indices"] is not None:
                idx = _accessor(doc, blob, prim["indices"]).reshape(-1)
            else:
                idx = np.arange(pos.shape[0], dtype=np.int64)
            if "NORMAL" in attrs:
                nrm = _accessor(doc, blob, attrs["NORMAL"]) @ normal_world.T
                length = np.linalg.norm(nrm, axis=1, keepdims=True)
                normals.append(nrm / np.where(length > 1e-12, length, 1.0))
            else:
                have_normals = False
                normals.append(np.zeros_like(pos))
            verts.append(pos)
            faces.append(idx.reshape(-1, 3) + offset)
            offset += pos.shape[0]

    if not verts:
        raise MetricError(f"{path} has no triangle geometry")
    return {
        "verts": np.concatenate(verts),
        "faces": np.concatenate(faces),
        "normals": np.concatenate(normals) if have_normals else None,
    }


#: positional weld tolerance, in units of the normalised object (longest axis 1)
WELD_TOLERANCE = 1e-6


def weld(verts, faces, tolerance=WELD_TOLERANCE):
    """Merge vertices that sit at the same point, returning the true surface.

    **This is not optional, and leaving it out is a silent measurement bug.**
    ``UnwrapMesh`` cuts the mesh into UV islands and the glTF that comes out
    carries one vertex per (position, island) pair, so a perfectly closed
    surface exports with tens of thousands of "boundary" edges and a couple of
    thousand "components" — and, far worse, every crease that a UV seam was
    routed along stops being a two-face edge and drops out of the dihedral
    statistics entirely.  Since seams are *preferentially* routed along creases,
    measuring the unwelded mesh would systematically under-count exactly the
    thing this harness exists to measure.

    ``score`` reports both: the welded numbers as the verdict, and the raw
    exported counts under ``exported_*`` because UV-island count is itself a
    cost worth seeing (``crease_angle`` moves it).
    """
    quantised = np.round(verts / tolerance).astype(np.int64)
    _unique, inverse = np.unique(quantised, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    merged = np.zeros((inverse.max() + 1, 3), dtype=np.float64)
    counts = np.zeros(inverse.max() + 1, dtype=np.int64)
    np.add.at(merged, inverse, verts)
    np.add.at(counts, inverse, 1)
    merged /= counts[:, None]
    new_faces = inverse[faces]
    keep = ((new_faces[:, 0] != new_faces[:, 1])
            & (new_faces[:, 1] != new_faces[:, 2])
            & (new_faces[:, 2] != new_faces[:, 0]))
    return merged, new_faces[keep], keep


def normalise(verts):
    """Centre on the bbox midpoint, scale the longest axis to 1.0.

    Exactly :func:`ab_fixtures.fit`, applied to a mesh instead of to solids.
    The generated mesh arrives in its own arbitrary cube, so without this a
    silhouette IoU would be measuring the model's choice of scale.
    """
    lo = verts.min(axis=0)
    hi = verts.max(axis=0)
    extent = hi - lo
    scale = 1.0 / max(float(extent.max()), 1e-12)
    return (verts - (lo + hi) / 2.0) * scale


# ---------------------------------------------------------------------------
# silhouette
# ---------------------------------------------------------------------------
def _project(points, camera):
    origin, right, up, back = ab_fixtures.camera_at(
        camera["azimuth_deg"], camera["elevation_deg"], camera["distance"])
    rel = points - origin
    x = rel @ right
    y = rel @ up
    depth = -(rel @ back)
    half = np.tan(np.radians(camera["fov_deg"]) / 2.0)
    ok = depth > 1e-9
    safe = np.where(ok, depth, 1.0)
    return (x / (safe * half)), (y / (safe * half)), ok


def _bary_grid(level):
    """Barycentric sample lattice with ``level`` subdivisions per edge."""
    i, j = np.meshgrid(np.arange(level + 1), np.arange(level + 1), indexing="ij")
    keep = (i + j) <= level
    a = i[keep] / level
    b = j[keep] / level
    return np.stack([a, b, 1.0 - a - b], axis=1)


def _splat(verts, faces, camera, resolution, max_level=48):
    """Boolean coverage mask, by adaptive barycentric point splatting.

    Each triangle is sampled on a lattice fine enough that neighbouring samples
    land within half a pixel of each other, so a covered pixel always receives
    one.  Real generated meshes are already sub-pixel at this raster and take
    the cheapest lattice; the adaptivity exists so the same function is exact on
    a coarse analytic test mesh, where a fixed pattern would leave gaps and
    quietly report a wrong IoU.
    """
    tris = verts[faces]
    ndc_x, ndc_y, ok = _project(tris.reshape(-1, 3), camera)
    sx = ndc_x.reshape(-1, 3) * 0.5 * resolution
    sy = ndc_y.reshape(-1, 3) * 0.5 * resolution
    span = np.maximum(sx.max(axis=1) - sx.min(axis=1),
                      sy.max(axis=1) - sy.min(axis=1))
    span = np.where(np.isfinite(span), span, 0.0)
    level = np.clip(np.ceil(span * 2.0).astype(np.int64), 1, max_level)

    mask = np.zeros(resolution * resolution, dtype=bool)
    splats = 0
    for value in np.unique(level):
        group = tris[level == value]
        points = np.einsum("sk,mkj->msj", _bary_grid(int(value)), group).reshape(-1, 3)
        gx, gy, good = _project(points, camera)
        px = ((gx + 1.0) * 0.5 * resolution).astype(np.int64)
        py = ((1.0 - gy) * 0.5 * resolution).astype(np.int64)
        inside = good & (px >= 0) & (px < resolution) & (py >= 0) & (py < resolution)
        mask[py[inside] * resolution + px[inside]] = True
        splats += int(inside.sum())
    return mask.reshape(resolution, resolution), splats


def _fill_holes(mask):
    """Fill pixels not reachable from the border - a splat's only failure mode."""
    reachable = np.zeros_like(mask)
    frontier = ~mask
    reachable[0, :] |= frontier[0, :]
    reachable[-1, :] |= frontier[-1, :]
    reachable[:, 0] |= frontier[:, 0]
    reachable[:, -1] |= frontier[:, -1]
    while True:
        grown = reachable.copy()
        grown[1:, :] |= reachable[:-1, :]
        grown[:-1, :] |= reachable[1:, :]
        grown[:, 1:] |= reachable[:, :-1]
        grown[:, :-1] |= reachable[:, 1:]
        grown &= frontier
        if np.array_equal(grown, reachable):
            break
        reachable = grown
    return mask | (frontier & ~reachable)


#: every signed axis permutation - 6 orders x 8 sign patterns.  Mirrors are
#: included deliberately: glTF is Y-up and an exporter's handedness convention
#: is not something to assume, and a mirrored match is still the same shape.
_ORIENTATIONS = [
    (perm, signs)
    for perm in itertools.permutations(range(3))
    for signs in itertools.product((1, -1), repeat=3)
]


def best_orientation(verts, faces, camera, target_mask):
    """The signed axis permutation that lines the mesh up with the ground truth.

    The generated mesh's axes are the exporter's, not the scene's.  Rather than
    assume a mapping (the Phase 18(a) left/right swap is what assuming one costs),
    every one of the 48 signed permutations is scored on a cheap 64² probe and
    the best kept.  It is a search over *labelling*, not a fit: nothing is
    rotated by a free angle, so a genuinely wrong shape cannot be aligned into
    looking right.
    """
    small = _downsample(target_mask, ALIGN_RASTER)
    probe = dict(camera, resolution=ALIGN_RASTER)
    best = (-1.0, _ORIENTATIONS[0])
    for perm, signs in _ORIENTATIONS:
        moved = verts[:, list(perm)] * np.asarray(signs, dtype=np.float64)
        mask, _ = _splat(moved, faces, probe, ALIGN_RASTER)
        score = _iou(_fill_holes(mask), small)
        if score > best[0]:
            best = (score, (perm, signs))
    return best[1], best[0]


def _downsample(mask, resolution):
    src = mask.shape[0]
    if src == resolution:
        return mask
    idx = (np.arange(resolution) * src) // resolution
    block = max(src // resolution, 1)
    out = np.zeros((resolution, resolution), dtype=bool)
    for dy in range(block):
        for dx in range(block):
            out |= mask[np.ix_(np.minimum(idx + dy, src - 1),
                               np.minimum(idx + dx, src - 1))]
    return out


def _iou(a, b):
    union = np.count_nonzero(a | b)
    if union == 0:
        return 0.0
    return float(np.count_nonzero(a & b)) / float(union)


def silhouette(verts, faces, camera, target_mask, orientation=None):
    """IoU of the mesh's outline against ``target_mask``, plus diagnostics."""
    if orientation is None:
        orientation, _ = best_orientation(verts, faces, camera, target_mask)
    perm, signs = orientation
    moved = verts[:, list(perm)] * np.asarray(signs, dtype=np.float64)

    target = _downsample(target_mask, RASTER)
    probe = dict(camera, resolution=RASTER)
    raw, splats = _splat(moved, faces, probe, RASTER)
    filled = _fill_holes(raw)
    covered = max(int(np.count_nonzero(filled)), 1)
    return {
        "silhouette_iou": _iou(filled, target),
        "silhouette_raw_iou": _iou(raw, target),
        "splat_density": splats / covered,
        "splat_holes": int(np.count_nonzero(filled) - np.count_nonzero(raw)),
        "orientation": [list(perm), list(signs)],
    }


# ---------------------------------------------------------------------------
# topology and dihedrals
# ---------------------------------------------------------------------------
def _edges(faces):
    """(unique edges, edge index per face-corner, counts) - all vectorised."""
    corners = np.stack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=1)
    pairs = np.sort(corners.reshape(-1, 2), axis=1)
    unique, inverse, counts = np.unique(pairs, axis=0, return_inverse=True,
                                        return_counts=True)
    return unique, inverse.reshape(-1, 3), counts


def topology(verts, faces):
    unique, inverse, counts = _edges(faces)
    degenerate = (faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) | \
                 (faces[:, 2] == faces[:, 0])
    tris = verts[faces]
    area = 0.5 * np.linalg.norm(
        np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    return {
        "verts": int(verts.shape[0]),
        "faces": int(faces.shape[0]),
        "edges": int(unique.shape[0]),
        "boundary_edges": int(np.count_nonzero(counts == 1)),
        "nonmanifold_edges": int(np.count_nonzero(counts > 2)),
        "degenerate_faces": int(np.count_nonzero(degenerate)),
        "zero_area_faces": int(np.count_nonzero(area <= 1e-14)),
        "watertight": bool(np.all(counts == 2)),
        "surface_area": float(area.sum()),
        "components": _components(faces, inverse, counts),
    }


def _components(faces, inverse, counts):
    """Connected shell count, union-find over faces that share a manifold edge."""
    parent = np.arange(faces.shape[0])

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    order = np.argsort(inverse.reshape(-1), kind="stable")
    edge_ids = inverse.reshape(-1)[order]
    face_ids = order // 3
    starts = np.flatnonzero(np.r_[True, edge_ids[1:] != edge_ids[:-1]])
    for start, edge in zip(starts, edge_ids[starts]):
        if counts[edge] != 2:
            continue
        a, b = find(int(face_ids[start])), find(int(face_ids[start + 1]))
        if a != b:
            parent[a] = b
    return int(len({find(i) for i in range(faces.shape[0])}))


def dihedrals(verts, faces):
    """Area/length-weighted dihedral bands - the sharp-edge preservation score."""
    unique, inverse, counts = _edges(faces)
    tris = verts[faces]
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1)
    # A zero-area face has no normal, and dividing by zero hands it (0,0,0),
    # which scores as a perfect 90 degree crease against anything.  Silently
    # counting slivers as sharp edges would make every smoothing setting look
    # like it preserved detail, so degenerate faces are dropped outright.
    solid = length > 1e-14
    normal = normal / np.where(solid, length, 1.0)[:, None]

    order = np.argsort(inverse.reshape(-1), kind="stable")
    edge_ids = inverse.reshape(-1)[order]
    face_ids = order // 3
    starts = np.flatnonzero(np.r_[True, edge_ids[1:] != edge_ids[:-1]])
    manifold = counts[edge_ids[starts]] == 2
    starts = starts[manifold]
    if starts.size:
        starts = starts[solid[face_ids[starts]] & solid[face_ids[starts + 1]]]
    if starts.size == 0:
        return {"edge_sharpness": 0.0, "flat_edge_fraction": 0.0,
                "soft_edge_fraction": 0.0, "sharp_edge_fraction": 0.0,
                "crease_length": 0.0, "dihedral_p99": 0.0}

    fa = face_ids[starts]
    fb = face_ids[starts + 1]
    cos = np.clip(np.einsum("ij,ij->i", normal[fa], normal[fb]), -1.0, 1.0)
    angle = np.degrees(np.arccos(cos))

    edge = unique[edge_ids[starts]]
    weight = np.linalg.norm(verts[edge[:, 0]] - verts[edge[:, 1]], axis=1)
    total = float(weight.sum()) or 1.0

    flat = weight[angle < FLAT_DEG].sum()
    soft = weight[(angle >= FLAT_DEG) & (angle < SHARP_DEG)].sum()
    sharp = weight[angle >= SHARP_DEG].sum()
    creased = soft + sharp
    scale = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0

    return {
        # of everything that is a crease at all, how much is still a real one
        "edge_sharpness": float(sharp / creased) if creased > 0 else 0.0,
        "flat_edge_fraction": float(flat / total),
        "soft_edge_fraction": float(soft / total),
        "sharp_edge_fraction": float(sharp / total),
        "crease_length": float(sharp / scale),
        "dihedral_p99": float(np.percentile(angle, 99)) if angle.size else 0.0,
        "_angle": angle,
        "_edge_faces": (fa, fb),
    }


def shading(verts, faces, normals, dihedral):
    """How far a shading normal strays from its own face, at real creases.

    ``crease_angle`` splits vertices; it moves no geometry at all, so the
    silhouette and dihedral numbers are blind to it by construction.  This is
    the number it does move: on a hard surface a 90° edge shaded with one
    averaged normal is 45° wrong, and that is the artefact.
    """
    if normals is None:
        return {"normal_p50": None, "normal_p95": None, "crease_normal_p95": None}
    tris = verts[faces]
    face_n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(face_n, axis=1, keepdims=True)
    face_n = face_n / np.where(length > 1e-14, length, 1.0)

    corner = normals[faces]                       # (M, 3, 3)
    cos = np.clip(np.einsum("mkj,mj->mk", corner, face_n), -1.0, 1.0)
    angle = np.degrees(np.arccos(cos)).reshape(-1)

    creased = np.zeros(faces.shape[0], dtype=bool)
    if "_angle" in dihedral:
        fa, fb = dihedral["_edge_faces"]
        hot = dihedral["_angle"] >= SHARP_DEG
        creased[fa[hot]] = True
        creased[fb[hot]] = True
    at_crease = np.degrees(np.arccos(cos[creased])).reshape(-1)

    return {
        "normal_p50": float(np.percentile(angle, 50)),
        "normal_p95": float(np.percentile(angle, 95)),
        "crease_normal_p95": float(np.percentile(at_crease, 95))
        if at_crease.size else None,
        "crease_faces": int(creased.sum()),
    }


# ---------------------------------------------------------------------------
# self-intersections
# ---------------------------------------------------------------------------
def _candidate_pairs(verts, faces, target_occupancy=3.0, max_pairs=4_000_000):
    """Triangle pairs whose bounding boxes share a uniform-grid cell."""
    tris = verts[faces]
    lo = tris.min(axis=1)
    hi = tris.max(axis=1)
    span = verts.max(axis=0) - verts.min(axis=0)
    cells = max(int(round((faces.shape[0] / target_occupancy) ** (1 / 3))), 1)
    size = np.maximum(span / cells, 1e-9)
    base = verts.min(axis=0)

    cell_lo = np.floor((lo - base) / size).astype(np.int64)
    cell_hi = np.floor((hi - base) / size).astype(np.int64)
    # only triangles that sit inside a single cell are bucketed cheaply; a
    # triangle straddling cells is registered in its low cell and its high cell,
    # which is enough for a pair test that only needs ONE shared cell
    keys = []
    members = []
    for corner in (cell_lo, cell_hi):
        keys.append((corner[:, 0] * 73856093) ^ (corner[:, 1] * 19349663)
                    ^ (corner[:, 2] * 83492791))
        members.append(np.arange(faces.shape[0]))
    key = np.concatenate(keys)
    member = np.concatenate(members)

    order = np.lexsort((member, key))
    key = key[order]
    member = member[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], key.size]

    left, right = [], []
    budget = max_pairs
    for start, end in zip(starts, ends):
        group = np.unique(member[start:end])
        n = group.size
        if n < 2 or n > 512:
            continue
        ia, ib = np.triu_indices(n, k=1)
        if ia.size > budget:
            ia, ib = ia[:budget], ib[:budget]
        left.append(group[ia])
        right.append(group[ib])
        budget -= ia.size
        if budget <= 0:
            break
    if not left:
        return np.empty(0, np.int64), np.empty(0, np.int64), budget > 0
    a = np.concatenate(left)
    b = np.concatenate(right)
    pair = np.unique(np.stack([np.minimum(a, b), np.maximum(a, b)], axis=1), axis=0)
    return pair[:, 0], pair[:, 1], budget > 0


def _interval(dist, proj):
    """Möller's crossing interval on the intersection line, vectorised.

    ``dist`` is each vertex's signed distance to the OTHER triangle's plane and
    ``proj`` its coordinate along the intersection direction.  The vertex alone
    on its side of the plane is found by comparing signs rather than branching.
    """
    sign = np.sign(dist)
    lone = np.where(sign[:, 0] == sign[:, 1], 2,
                    np.where(sign[:, 0] == sign[:, 2], 1, 0))
    rows = np.arange(dist.shape[0])
    others = np.stack([(lone + 1) % 3, (lone + 2) % 3], axis=1)

    da = dist[rows, lone][:, None]
    pa = proj[rows, lone][:, None]
    db = dist[rows[:, None], others]
    pb = proj[rows[:, None], others]
    t = pa + (pb - pa) * (da / np.where(np.abs(da - db) > 1e-300, da - db, 1e-300))
    return t.min(axis=1), t.max(axis=1)


def self_intersections(verts, faces, max_pairs=4_000_000, chunk=250_000):
    """Count of intersecting triangle pairs that do not share a vertex.

    Adjacent faces touch along their shared edge by definition, so a pair
    sharing any vertex is excluded — the same call ``mesh_diagnose`` makes in
    the add-on.  Two further exclusions, both deliberate and both conservative:
    exactly-coplanar pairs (counted separately as ``coplanar_pairs``), and pairs
    where a vertex of one lies exactly in the other's plane.  Those are the
    degenerate cases Möller's interval test does not decide, and treating an
    undecided case as a hit would put a floor under every variant's score and
    make the comparison useless.  The count is therefore a lower bound, which is
    the right direction for a number used to compare settings against each other.
    """
    left, right, complete = _candidate_pairs(verts, faces, max_pairs=max_pairs)
    hits = 0
    coplanar = 0
    for start in range(0, left.size, chunk):
        a = faces[left[start:start + chunk]]
        b = faces[right[start:start + chunk]]
        shares = (a[:, :, None] == b[:, None, :]).any(axis=(1, 2))
        keep = ~shares
        if not keep.any():
            continue
        V = verts[a[keep]]
        U = verts[b[keep]]

        n2 = np.cross(U[:, 1] - U[:, 0], U[:, 2] - U[:, 0])
        dv = np.einsum("mkj,mj->mk", V - U[:, 0][:, None, :], n2)
        n1 = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
        du = np.einsum("mkj,mj->mk", U - V[:, 0][:, None, :], n1)

        scale = np.maximum(np.linalg.norm(n2, axis=1), 1e-300)[:, None]
        dv = dv / scale
        scale = np.maximum(np.linalg.norm(n1, axis=1), 1e-300)[:, None]
        du = du / scale

        eps = 1e-9
        flat = (np.abs(dv) < eps).all(axis=1) | (np.abs(du) < eps).all(axis=1)
        crossing = ~flat
        crossing &= ~((dv > eps).all(axis=1) | (dv < -eps).all(axis=1))
        crossing &= ~((du > eps).all(axis=1) | (du < -eps).all(axis=1))
        crossing &= (np.abs(dv) > eps).all(axis=1) & (np.abs(du) > eps).all(axis=1)
        coplanar += int(flat.sum())
        if not crossing.any():
            continue

        V, U = V[crossing], U[crossing]
        dv, du = dv[crossing], du[crossing]
        direction = np.cross(n1[crossing], n2[crossing])
        pv = np.einsum("mkj,mj->mk", V, direction)
        pu = np.einsum("mkj,mj->mk", U, direction)
        v_lo, v_hi = _interval(dv, pv)
        u_lo, u_hi = _interval(du, pu)
        hits += int(np.count_nonzero((v_lo <= u_hi) & (u_lo <= v_hi)))

    return {
        "self_intersections": hits,
        "coplanar_pairs": coplanar,
        "pairs_tested": int(left.size),
        "pairs_complete": bool(complete),
    }


# ---------------------------------------------------------------------------
# the whole verdict
# ---------------------------------------------------------------------------
def score(mesh_path, camera, target_mask, want_self_intersections=True):
    """Every metric for one generated mesh, as one flat dict."""
    mesh = load_glb(mesh_path)
    raw_verts = normalise(mesh["verts"])
    raw_faces = mesh["faces"]
    verts, faces, kept = weld(raw_verts, raw_faces)

    out = {
        "exported_verts": int(raw_verts.shape[0]),
        "exported_faces": int(raw_faces.shape[0]),
        "uv_split_ratio": float(raw_verts.shape[0]) / max(int(verts.shape[0]), 1),
        "welded_away_faces": int(raw_faces.shape[0] - faces.shape[0]),
    }
    out.update(topology(verts, faces))
    out.update(silhouette(verts, faces, camera, target_mask))
    dihedral = dihedrals(verts, faces)
    # shading normals belong to the EXPORTED vertices - welding them would
    # average across exactly the split crease_angle just made
    out.update(shading(raw_verts, raw_faces, mesh["normals"],
                       _lift(dihedral, raw_faces, kept)))
    out.update({k: v for k, v in dihedral.items() if not k.startswith("_")})
    if want_self_intersections:
        out.update(self_intersections(verts, faces))
    return out


def _lift(dihedral, raw_faces, kept):
    """Re-index the welded dihedral result onto the exported face array."""
    if "_angle" not in dihedral:
        return dihedral
    index = np.flatnonzero(kept)
    fa, fb = dihedral["_edge_faces"]
    return {**dihedral, "_edge_faces": (index[fa], index[fb])}
