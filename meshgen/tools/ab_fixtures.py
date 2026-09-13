"""Deterministic synthetic test inputs for the A/B tuning harness.

The tuning question is "does this setting round off real geometry?", and you
cannot answer that from a photograph: nobody knows what the true surface was.
So the inputs are *synthesised from analytic solids* — axis-aligned boxes and
ellipsoids — which means the ground truth is exact, free, and reproducible on
any machine with no assets at all.

Three scenes, chosen so the settings under test have somewhere to fail:

``hard_slab``    the ``make_rig_views`` object (slab + fin + foot). Crisp 90°
                 dihedrals, three distinct extents, lopsided — the sharp-edge
                 fixture, and the same shape Phase 18(a) verified orientation
                 against.
``hard_steps``   a stepped ziggurat. Creases at four different scales, so a
                 smoothing setting that survives a big edge but melts a small
                 one is visible as a number rather than a feeling.
``organic_blob`` four overlapping ellipsoids. No sharp edge anywhere, so it is
                 the control: a setting that helps hard surfaces must not cost
                 anything here, and ``project_back`` is the one most likely to.

The camera is **core's own rig**, imported from
:mod:`meshgen.tools.make_rig_views` rather than rebuilt, so a fixture render and
a multi-view render cannot drift apart.  Rendering is a vectorised
ray/solid intersection with fixed lights — no random sampling anywhere, so the
same scene produces the same bytes every time and an A/B difference is the
setting under test and nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen.backends import multiview  # noqa: E402
from meshgen.tools import make_rig_views  # noqa: E402

RESOLUTION = 1024

#: Directional key light (world space, normalised on use) plus a headlight
#: term, so faces that differ only in orientation differ in the image.  Fixed
#: constants: shading must never be a source of run-to-run variation.
_KEY_DIR = (-0.4, -0.7, 0.55)
_KEY = 0.62
_HEAD = 0.30
_AMBIENT = 0.16


def box(x0, x1, y0, y1, z0, z1, colour):
    return {"kind": "box", "lo": (x0, y0, z0), "hi": (x1, y1, z1), "colour": colour}


def ellipsoid(cx, cy, cz, rx, ry, rz, colour):
    return {"kind": "ellipsoid", "centre": (cx, cy, cz),
            "radii": (rx, ry, rz), "colour": colour}


#: ``make_rig_views.BOXES`` in this module's solid form — imported, not retyped,
#: so the hard-surface fixture stays the object Phase 18(a) measured.
_HARD_SLAB = [box(*entry) for entry in make_rig_views.BOXES]

_HARD_STEPS = [
    box(-0.50, 0.50, -0.30, 0.30, -0.50, -0.26, (198, 200, 205)),
    box(-0.38, 0.38, -0.22, 0.22, -0.26, -0.04, (214, 160, 110)),
    box(-0.26, 0.26, -0.15, 0.15, -0.04, 0.16, (150, 185, 205)),
    box(-0.14, 0.14, -0.09, 0.09, 0.16, 0.32, (205, 120, 105)),
    box(-0.06, 0.06, -0.04, 0.04, 0.32, 0.42, (120, 140, 165)),
    # one off-centre tab so the object is not four-fold symmetric and its
    # orientation survives into the output mesh the way the slab's does
    box(0.26, 0.50, -0.06, 0.06, -0.16, -0.06, (230, 205, 90)),
]

_ORGANIC_BLOB = [
    ellipsoid(0.00, 0.00, -0.02, 0.34, 0.26, 0.30, (206, 178, 150)),
    ellipsoid(0.00, -0.04, 0.30, 0.21, 0.19, 0.20, (214, 190, 165)),
    ellipsoid(0.26, 0.02, 0.10, 0.17, 0.15, 0.22, (188, 158, 132)),
    ellipsoid(-0.22, 0.06, -0.20, 0.20, 0.17, 0.14, (170, 146, 126)),
]

SCENES = {
    "hard_slab": _HARD_SLAB,
    "hard_steps": _HARD_STEPS,
    "organic_blob": _ORGANIC_BLOB,
}

#: Which scenes carry ground-truth sharp edges.  The dihedral metrics only mean
#: something where the true answer is "90 degrees, everywhere".
HARD_SURFACE_SCENES = ("hard_slab", "hard_steps")


def true_crease_length(scene):
    """Upper bound on ``mesh_metrics.crease_length`` for a box scene.

    Every box edge, buried ones included, over the object's bbox diagonal — so
    the real visible figure is somewhat lower.  It is the anchor that keeps
    ``edge_sharpness`` honest: Dual Contouring's voxel staircase is *perfectly*
    sharp and entirely fake, so a variant can buy sharpness by getting jaggier.
    A measured crease length far above this bound is steps, not detail.
    """
    solids = normalised(SCENES[scene] if isinstance(scene, str) else scene)
    total = 0.0
    for solid in solids:
        if solid["kind"] != "box":
            raise ValueError("crease length is only defined for box scenes")
        extent = np.asarray(solid["hi"]) - np.asarray(solid["lo"])
        total += 4.0 * float(extent.sum())
    lo, hi = bounds(solids)
    return total / float(np.linalg.norm(hi - lo))


# ---------------------------------------------------------------------------
# the object, normalised the way the rig frames it
# ---------------------------------------------------------------------------
def bounds(solids):
    los, his = [], []
    for solid in solids:
        if solid["kind"] == "box":
            los.append(np.asarray(solid["lo"], dtype=np.float64))
            his.append(np.asarray(solid["hi"], dtype=np.float64))
        else:
            centre = np.asarray(solid["centre"], dtype=np.float64)
            radii = np.asarray(solid["radii"], dtype=np.float64)
            los.append(centre - radii)
            his.append(centre + radii)
    return np.min(los, axis=0), np.max(his, axis=0)


def fit(solids):
    """(centre, scale) putting the object on the origin with longest axis 1.0.

    The same normalisation :func:`make_rig_views._fit` applies, and the same one
    :func:`mesh_metrics.normalise` applies to a generated mesh — which is what
    makes a silhouette IoU between the two mean anything.
    """
    lo, hi = bounds(solids)
    return (lo + hi) / 2.0, 1.0 / float(np.max(hi - lo))


def normalised(solids):
    centre, scale = fit(solids)
    out = []
    for solid in solids:
        if solid["kind"] == "box":
            out.append({**solid,
                        "lo": tuple((np.asarray(solid["lo"]) - centre) * scale),
                        "hi": tuple((np.asarray(solid["hi"]) - centre) * scale)})
        else:
            out.append({**solid,
                        "centre": tuple((np.asarray(solid["centre"]) - centre) * scale),
                        "radii": tuple(np.asarray(solid["radii"]) * scale)})
    return out


# ---------------------------------------------------------------------------
# ray casting
# ---------------------------------------------------------------------------
#: The fixture view.  Azimuth 0 / elevation 0 is core's ``front`` camera, and a
#: flat-on box renders as a rectangle — a shape model given that has to invent
#: the whole depth axis, which is noise on top of the setting under test.  A
#: three-quarter view shows three faces of every box, so what comes back is the
#: object rather than the model's prior about slabs.  These are the *single*
#: image path, not the rig, so the camera is free; multi-view still uses core's
#: four azimuths and is unaffected.
DEFAULT_AZIMUTH_DEG = 32.0
DEFAULT_ELEVATION_DEG = 22.0

#: fraction of the frame the object spans at its widest — Pixal3D's own crop
#: padding, so a fixture render is already framed the way the pipeline wants
FRAME_PAD = 1.1


def camera_at(azimuth_deg, elevation_deg, distance):
    """(origin, right, up, back) for an orbit camera, +Z up, looking at origin.

    At ``elevation_deg == 0`` this is exactly
    :func:`make_rig_views.camera_basis` — a test pins that, so the fixture
    camera is core's rig extended by one angle rather than a second rig that
    happens to look similar.
    """
    az = np.radians(azimuth_deg)
    el = np.radians(elevation_deg)
    origin = distance * np.array([np.cos(el) * np.sin(az),
                                  -np.cos(el) * np.cos(az),
                                  np.sin(el)])
    forward = -origin / np.linalg.norm(origin)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return origin, right, up, -forward


def _rays(azimuth_deg, elevation_deg, fov_deg, resolution, distance):
    """Per-pixel origin (one point) and unit directions."""
    origin, right, up, back = camera_at(azimuth_deg, elevation_deg, distance)

    # matches make_rig_views._project exactly, inverted: x right, y up, -back
    half = np.tan(np.radians(fov_deg) / 2.0)
    px = (np.arange(resolution, dtype=np.float64) + 0.5) / resolution * 2.0 - 1.0
    py = 1.0 - (np.arange(resolution, dtype=np.float64) + 0.5) / resolution * 2.0
    gx, gy = np.meshgrid(px * half, py * half)
    dirs = (right[None, None, :] * gx[..., None]
            + up[None, None, :] * gy[..., None]
            - back[None, None, :])
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    return origin, dirs.reshape(-1, 3)


_MISS = np.inf


def _hit_box(origin, dirs, solid):
    lo = np.asarray(solid["lo"], dtype=np.float64)
    hi = np.asarray(solid["hi"], dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / dirs
        t0 = (lo - origin) * inv
        t1 = (hi - origin) * inv
    near = np.nanmin(np.stack([t0, t1]), axis=0)
    far = np.nanmax(np.stack([t0, t1]), axis=0)
    t_near = np.max(np.where(np.isnan(near), -np.inf, near), axis=1)
    t_far = np.min(np.where(np.isnan(far), np.inf, far), axis=1)
    hit = (t_far >= np.maximum(t_near, 0.0))
    t = np.where(hit & (t_near > 0.0), t_near, _MISS)

    # normal = the axis whose slab produced t_near
    point = origin + np.where(np.isfinite(t)[:, None], t[:, None], 0.0) * dirs
    centre = (lo + hi) / 2.0
    half = np.maximum((hi - lo) / 2.0, 1e-12)
    rel = (point - centre) / half
    axis = np.argmax(np.abs(rel), axis=1)
    normal = np.zeros_like(dirs)
    normal[np.arange(dirs.shape[0]), axis] = np.sign(rel[np.arange(dirs.shape[0]), axis])
    return t, normal


def _hit_ellipsoid(origin, dirs, solid):
    centre = np.asarray(solid["centre"], dtype=np.float64)
    radii = np.asarray(solid["radii"], dtype=np.float64)
    o = (origin - centre) / radii
    d = dirs / radii
    a = np.einsum("ij,ij->i", d, d)
    b = 2.0 * (d @ o)
    c = float(o @ o) - 1.0
    disc = b * b - 4.0 * a * c
    ok = disc > 0.0
    sq = np.sqrt(np.where(ok, disc, 0.0))
    t_a = (-b - sq) / (2.0 * a)
    t_b = (-b + sq) / (2.0 * a)
    t = np.where(t_a > 1e-9, t_a, t_b)
    t = np.where(ok & (t > 1e-9), t, _MISS)

    point = origin + np.where(np.isfinite(t)[:, None], t[:, None], 0.0) * dirs
    normal = (point - centre) / (radii ** 2)
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    return t, normal / np.where(length > 1e-12, length, 1.0)


def frame_distance(solids, azimuth_deg, elevation_deg, fov_deg, pad=FRAME_PAD,
                   probe_resolution=192, iterations=4):
    """Distance at which the object spans ``1 / pad`` of the frame at its widest.

    A rotated box projects wider than a flat-on one, so the rig's fixed distance
    would clip a three-quarter view.  Solved by measuring rather than by a
    bounding-sphere bound, which for these lopsided objects would sit the camera
    much too far back and waste most of the 1024 pixels on background.  Four
    fixed iterations of measure-and-scale, at a fixed probe resolution: no
    tolerance loop, so the answer is the same on every machine.
    """
    distance = multiview.distance_for_fov(fov_deg)
    for _ in range(iterations):
        _t, _n, index, _d, _s = _cast_at(solids, azimuth_deg, elevation_deg,
                                         fov_deg, probe_resolution, distance)
        mask = (index >= 0).reshape(probe_resolution, probe_resolution)
        if not mask.any():
            return distance
        rows = np.flatnonzero(mask.any(axis=1))
        cols = np.flatnonzero(mask.any(axis=0))
        centre = probe_resolution / 2.0
        span = 2.0 * max(
            abs(rows[0] - centre), abs(rows[-1] + 1 - centre),
            abs(cols[0] - centre), abs(cols[-1] + 1 - centre),
        ) / probe_resolution
        if span <= 1e-6:
            return distance
        distance *= span * pad
    return distance


def _cast_at(solids, azimuth_deg, elevation_deg, fov_deg, resolution, distance):
    origin, dirs = _rays(azimuth_deg, elevation_deg, fov_deg, resolution, distance)
    solids = normalised(solids)

    best_t = np.full(dirs.shape[0], _MISS)
    best_n = np.zeros_like(dirs)
    best_i = np.full(dirs.shape[0], -1, dtype=np.int32)
    for index, solid in enumerate(solids):
        hit = _hit_box if solid["kind"] == "box" else _hit_ellipsoid
        t, normal = hit(origin, dirs, solid)
        closer = t < best_t
        best_t = np.where(closer, t, best_t)
        best_n = np.where(closer[:, None], normal, best_n)
        best_i = np.where(closer, index, best_i)
    return best_t, best_n, best_i, dirs, solids


def camera_for(scene, azimuth_deg=DEFAULT_AZIMUTH_DEG,
               elevation_deg=DEFAULT_ELEVATION_DEG, fov_deg=None,
               resolution=RESOLUTION):
    """The exact camera a scene is rendered through, as a plain dict.

    This is the whole contract between a fixture image and the scorer: both the
    ground-truth mask and the generated mesh's silhouette are rasterised through
    *this*, so an IoU between them is a comparison of shapes and not of framings.
    """
    solids = SCENES[scene] if isinstance(scene, str) else scene
    fov_deg = multiview.DEFAULT_FOV_DEG if fov_deg is None else fov_deg
    return {
        "azimuth_deg": float(azimuth_deg),
        "elevation_deg": float(elevation_deg),
        "fov_deg": float(fov_deg),
        "resolution": int(resolution),
        "distance": float(frame_distance(solids, azimuth_deg, elevation_deg, fov_deg)),
    }


def rays_for(camera):
    """(origin, unit directions) for a :func:`camera_for` dict."""
    return _rays(camera["azimuth_deg"], camera["elevation_deg"],
                 camera["fov_deg"], camera["resolution"], camera["distance"])


def cast(solids, camera):
    return _cast_at(solids, camera["azimuth_deg"], camera["elevation_deg"],
                    camera["fov_deg"], camera["resolution"], camera["distance"])


def render(scene, camera=None):
    """(rgb uint8 HxWx3, mask bool HxW) for a named or literal scene."""
    solids = SCENES[scene] if isinstance(scene, str) else scene
    camera = camera or camera_for(scene)
    t, normal, index, dirs, solids = cast(solids, camera)

    key = np.asarray(_KEY_DIR, dtype=np.float64)
    key /= np.linalg.norm(key)
    lam = np.clip(normal @ key, 0.0, 1.0) * _KEY
    head = np.clip(np.einsum("ij,ij->i", normal, -dirs), 0.0, 1.0) * _HEAD
    shade = np.clip(_AMBIENT + lam + head, 0.0, 1.0)

    palette = np.asarray([s["colour"] for s in solids], dtype=np.float64)
    colour = np.zeros((dirs.shape[0], 3))
    hit = index >= 0
    colour[hit] = palette[index[hit]] * shade[hit, None]

    side = int(np.sqrt(dirs.shape[0]))
    rgb = np.clip(colour, 0, 255).astype(np.uint8).reshape(side, side, 3)
    return rgb, hit.reshape(side, side)


def write_scene(scene, out_dir, camera=None):
    """Write ``<scene>.png`` plus its exact ``<scene>_mask.png`` ground truth."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    camera = camera or camera_for(scene)
    rgb, mask = render(scene, camera)
    image_path = out_dir / f"{scene}.png"
    mask_path = out_dir / f"{scene}_mask.png"
    Image.fromarray(rgb, "RGB").save(image_path)
    Image.fromarray((mask * 255).astype(np.uint8), "L").save(mask_path)
    return image_path, mask_path


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    out = Path(argv[0]) if argv else Path.cwd()
    for scene in SCENES:
        image_path, mask_path = write_scene(scene, out)
        print(f"{image_path.name}  +  {mask_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
