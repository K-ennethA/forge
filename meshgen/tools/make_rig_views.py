"""Render the four rig views of a deliberately lopsided test object.

Phase 18(a) verification fixture.  Multi-view's dangerous failure is silent: a
view wired to the wrong camera does not raise, it returns a confidently wrong
mesh.  Catching that needs an object whose four views are all DIFFERENT and
whose orientation survives into the output mesh, so:

* the slab is 1.0 x 0.25 x 0.5 - three distinct extents, so the output mesh's
  sorted bounding box says by itself which axis the front view's width became;
* a tall **fin** sits on the object's LEFT end (+X) and only on its front half;
* a low **foot** sits on the object's RIGHT end (-X) and only on its back half.

So the left view (azimuth 90, camera at +X) and the right view (azimuth 270,
camera at -X) are mirror images of each other rather than identical - which is
exactly what a left/right socket swap would get wrong, and what a symmetric test
object would hide.

Cameras are core's own rig, rebuilt here from
:func:`meshgen.backends.multiview.transform_matrix`: a 20 degree horizontal FOV
at distance ``1.1 * 0.5 / tan(fov/2)``, +Z up, front at (0, -d, 0).  Perspective,
not orthographic, because that is what the rig is.

    C:\\forge-models\\comfyui\\.venv\\Scripts\\python.exe ^
      meshgen\\tools\\make_rig_views.py <output dir>
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen.backends import multiview  # noqa: E402

RESOLUTION = 1024

#: (x0, x1, y0, y1, z0, z1, colour).  +X is the object's LEFT, -Y is the front,
#: +Z is up - core's convention, see multiview.transform_matrix.
BOXES = [
    (-0.50, 0.50, -0.125, 0.125, -0.25, 0.25, (205, 205, 210)),   # slab
    (0.30, 0.50, -0.125, 0.000, 0.25, 0.65, (220, 90, 60)),       # fin: LEFT end, front half
    (-0.50, -0.30, 0.000, 0.125, -0.50, -0.25, (70, 120, 205)),   # foot: RIGHT end, back half
]


def _fit():
    """Centre the object on the origin and scale its longest axis to 1.0.

    The rig frames a unit object; leaving it oversized clips the fin against the
    frame edge, which is exactly the feature the orientation check reads.
    """
    lo = [min(b[i * 2] for b in BOXES) for i in range(3)]
    hi = [max(b[i * 2 + 1] for b in BOXES) for i in range(3)]
    centre = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    scale = 1.0 / max(hi[i] - lo[i] for i in range(3))
    return centre, scale


_CENTRE, _SCALE = _fit()


def _corners(box):
    x0, x1, y0, y1, z0, z1 = box[:6]
    return [tuple((v - _CENTRE[i]) * _SCALE for i, v in enumerate(p))
            for p in ((x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1))]


def _camera(azimuth_deg, distance):
    """(origin, right, up, back) from core's rig, read off the c2w matrix."""
    T = multiview.transform_matrix(azimuth_deg, distance)
    right = (T[0][0], T[1][0], T[2][0])
    up = (T[0][1], T[1][1], T[2][1])
    back = (T[0][2], T[1][2], T[2][2])
    origin = (T[0][3], T[1][3], T[2][3])
    return origin, right, up, back


def _project(point, camera, fov_deg, resolution):
    origin, right, up, back = camera
    rel = [point[i] - origin[i] for i in range(3)]
    x = sum(rel[i] * right[i] for i in range(3))
    y = sum(rel[i] * up[i] for i in range(3))
    z = sum(rel[i] * back[i] for i in range(3))   # camera looks along -back
    depth = -z
    if depth <= 1e-6:
        return None, depth
    scale = 0.5 * resolution / math.tan(math.radians(fov_deg) / 2.0)
    return (resolution / 2.0 + x * scale / depth,
            resolution / 2.0 - y * scale / depth), depth


def _hull(points):
    """Monotone chain - each box projects to a convex octagon at worst."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(reversed(pts))


def render(azimuth_deg, fov_deg=multiview.DEFAULT_FOV_DEG, resolution=RESOLUTION):
    distance = multiview.distance_for_fov(fov_deg)
    camera = _camera(azimuth_deg, distance)
    image = Image.new("RGB", (resolution, resolution), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    faces = []
    for box in BOXES:
        projected, depths = [], []
        for corner in _corners(box):
            point, depth = _project(corner, camera, fov_deg, resolution)
            if point is None:
                break
            projected.append(point)
            depths.append(depth)
        else:
            faces.append((sum(depths) / len(depths), _hull(projected), box[6]))

    for _depth, hull, colour in sorted(faces, reverse=True):   # far to near
        if len(hull) >= 3:
            draw.polygon(hull, fill=colour)
    return image


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    out = Path(argv[0]) if argv else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, azimuth in multiview.CORE_VIEW_AZIMUTHS.items():
        path = out / f"rig_{name}.png"
        render(azimuth).save(path)
        written.append(f"{path.name} (azimuth {azimuth:g})")
    print("\n".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
