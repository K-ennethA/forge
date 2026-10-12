"""Multi-view input framing and matte checks (numpy + PIL, imported lazily).

Two defects of the per-view preparation chain, both measured in the Pixal3D
trial on three conquest-units turnaround sheets (2026-10-11):

(a) **Per-view scale.**  Every view goes through its own
    ``RemoveBackground -> ImageCropToMask(pad 1.1)``, so each view is scaled so
    ITS OWN silhouette fills the frame.  The multi-view rig assumes one object at
    one scale seen from four cameras; a quadruped whose side view is longer than
    it is tall therefore comes out SHORT (gemwing side 0.82x the sheet).  Core's
    node tooltip asks for "the same scale in every view".  :func:`shared_frame`
    places every view on ONE scale and ONE vertical centre: the largest extent
    of the whole object (the union of the views' heights, or the widest view)
    spans ``1 / pad`` of the frame, and each view is centred horizontally on its
    own silhouette bbox (the projection of the object's bbox centre).

    The contract that makes this possible: the views must be cut at ONE pixel
    scale with their rows aligned (the same image height, and row r is the same
    height on the object in every view) - e.g. strips cut out of one turnaround
    sheet between the same two y lines, or with each view's floor line on the
    same row.  :func:`shared_frame` refuses views of different heights rather
    than guessing.

(b) **Enclosed background.**  BiRefNet fills background the subject encloses
    (the eclipse wolf's horn ring came back as a solid disc).
    :func:`enclosed_background` finds flat, background-coloured pockets inside a
    matte; the job result carries a warning, and ``carve_enclosed`` cuts them
    back out of a matte meshgen computed itself.

Nothing here touches the GPU.  The matte graph (:func:`build_matte_graph`) is
built from the multi-view template's own background-removal loader so the
model it runs is the one the generation graph would have run.
"""

from __future__ import annotations

import copy

#: Default frame: core's ImageCropToMask in the template crops to 1024 x 1024 at
#: pad 1.1 (see multiview.PAD_FACTOR), and shared framing keeps both numbers.
FRAME_SIZE = 1024

#: enclosed_background defaults, calibrated on the trial's eclipse wolf (near-
#: black fur on a near-black sheet, BiRefNet filled the horn ring).  Distance
#: from the background colour alone cannot tell the two apart (the fur sits 0-12
#: from the sheet; the ring's interior 10-40, lifted by the magenta glow), and
#: neither can local texture (the painted fur is as flat as the sheet).  What
#: does separate them is CHROMATICITY - colour with its brightness removed: the
#: sheet keeps its own tint inside the ring however the glow brightens it, the
#: fur does not.  Measured: chroma tolerance 2-4 finds the ring pocket in all
#: three views (front 13-16k px, back 14-17k, side 3.7-4.5k) and no fur.
HOLE_CHROMA = 3.0        # max |chromaticity - bg chromaticity| (rgb minus its own mean)
HOLE_TOL = 40.0          # max |rgb - bg| (a glow may lift the pocket this far)
HOLE_DARKER = 6.0        # a pocket is never darker than the sheet by more than this
                         #   (ink lines and dark neutral armour are; goo_shield's helmet)
HOLE_MIN_PX = 150        # pockets smaller than this are specks / outline gaps
HOLE_MIN_FRAC = 0.002    # ... or smaller than this fraction of the subject


class FramingError(ValueError):
    """The views cannot be framed as asked (different heights, empty mask)."""


def _np():
    import numpy as np
    return np


# ---------------------------------------------------------------------------
# masks
# ---------------------------------------------------------------------------
def load_rgba(path):
    """``(rgb uint8 HxWx3, alpha uint8 HxW or None)`` for an image file."""
    np = _np()
    from PIL import Image
    with Image.open(path) as im:
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        rgba = np.asarray(im.convert("RGBA"))
    rgb = np.ascontiguousarray(rgba[:, :, :3])
    return rgb, (np.ascontiguousarray(rgba[:, :, 3]) if has_alpha else None)


def mask_from_alpha(alpha, min_frac=0.01):
    """The alpha channel as a subject mask, or None when it carries no cut-out
    (fully opaque, or fully clear)."""
    if alpha is None:
        return None
    m = alpha > 127
    frac = float(m.mean())
    if frac < min_frac or frac > 1.0 - min_frac:
        return None
    return m


def border_colour(rgb, width=8):
    """Median colour of the image border: the background a cut-out sits on."""
    np = _np()
    w = max(1, min(width, rgb.shape[0] // 4, rgb.shape[1] // 4))
    border = np.concatenate([rgb[:w].reshape(-1, 3), rgb[-w:].reshape(-1, 3),
                             rgb[:, :w].reshape(-1, 3), rgb[:, -w:].reshape(-1, 3)])
    return np.median(border.astype(np.float64), axis=0)


def _shift_or(m, k=1):
    """Binary dilation by k px (4-neighbourhood steps), numpy only."""
    np = _np()
    out = m.copy()
    for _ in range(k):
        cur = out.copy()
        out[1:] |= cur[:-1]; out[:-1] |= cur[1:]
        out[:, 1:] |= cur[:, :-1]; out[:, :-1] |= cur[:, 1:]
    return out


def _shift_and(m, k=1):
    """Binary erosion by k px (4-neighbourhood steps), numpy only."""
    return ~_shift_or(~m, k)


def label(mask):
    """4-connected components: ``(labels int32, count)``.  scipy when present,
    else a flood fill over the set pixels (fine for pocket-sized inputs)."""
    np = _np()
    try:
        from scipy import ndimage
        lab, n = ndimage.label(mask)
        return lab.astype(np.int32), int(n)
    except ImportError:
        pass
    lab = np.zeros(mask.shape, np.int32)
    n = 0
    H, W = mask.shape
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1
        stack = [(y0, x0)]
        lab[y0, x0] = n
        while stack:
            y, x = stack.pop()
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n
                    stack.append((yy, xx))
    return lab, n


def enclosed_background(rgb, mask, bg=None, tol=HOLE_TOL, chroma_tol=HOLE_CHROMA,
                        darker=HOLE_DARKER, min_px=HOLE_MIN_PX, min_frac=HOLE_MIN_FRAC):
    """Background pockets a matte has filled in.

    A pocket is a 4-connected run of mask pixels that has the background's
    CHROMATICITY (within ``chroma_tol``), sits within ``tol`` of its colour, is
    not darker than it by more than ``darker``, and is big enough to matter
    (>= ``min_px`` and >= ``min_frac`` of the subject): the sheet showing through
    a ring, a horn loop or a gap the matte painted over.  See the HOLE_*
    constants for why chromaticity and not distance.

    Returns ``{"bg_rgb", "regions": [{"bbox_px", "area_px", "mean_delta",
    "std"}], "area_px", "frac_of_subject", "carve": bool mask}``.
    """
    np = _np()
    mask = np.asarray(mask, bool)
    rgb = np.asarray(rgb)
    if mask.shape != rgb.shape[:2]:
        raise FramingError(f"mask {mask.shape} and image {rgb.shape[:2]} differ in size")
    bg = border_colour(rgb) if bg is None else np.asarray(bg, np.float64)
    px = rgb.astype(np.float64)
    delta = np.linalg.norm(px - bg, axis=2)
    chroma = np.linalg.norm((px - px.mean(axis=2, keepdims=True)) - (bg - bg.mean()), axis=2)
    lift = px.mean(axis=2) - bg.mean()
    raw = mask & (chroma < chroma_tol) & (delta < tol) & (lift > -darker)
    cand = _shift_or(_shift_and(raw, 1), 1) & raw            # opening: drop 1-px threads
    subject = int(mask.sum())
    floor_px = max(int(min_px), int(min_frac * subject))
    carve = np.zeros(mask.shape, bool)
    regions = []
    if subject and cand.any():
        lab, n = label(cand)
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        for i in np.nonzero(sizes >= floor_px)[0]:
            if i == 0:
                continue
            sel = lab == i
            ys, xs = np.nonzero(sel)
            regions.append({"bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                            "area_px": int(sizes[i]), "mean_delta": round(float(delta[sel].mean()), 2),
                            "std": round(float(px[sel].std(axis=0).max()), 2)})
            carve |= sel
    for _ in range(3):                  # give back the rim the opening shaved off (geodesic, inside raw)
        carve |= _shift_or(carve, 1) & raw
    area = int(carve.sum())
    return {"bg_rgb": [round(float(c), 1) for c in bg], "regions": regions, "area_px": area,
            "frac_of_subject": round(area / subject, 4) if subject else 0.0, "carve": carve}


def carve_enclosed(mask, report):
    """``mask`` with the pockets :func:`enclosed_background` found cut out
    (grown 1 px so the antialiased rim of the pocket goes too)."""
    if not report["regions"]:
        return mask
    return mask & ~_shift_or(report["carve"], 1)


def matte_warning(view, report):
    """The job-result warning for one view's pockets, or None."""
    if not report["regions"]:
        return None
    return {
        "code": "matte_filled_enclosed_background",
        "view": view,
        "regions": report["regions"],
        "area_px": report["area_px"],
        "frac_of_subject": report["frac_of_subject"],
        "bg_rgb": report["bg_rgb"],
        "message": (
            f"the {view} matte filled {len(report['regions'])} background-coloured "
            f"pocket(s) the subject encloses ({report['area_px']} px, "
            f"{100 * report['frac_of_subject']:.1f}% of the subject) - a ring or loop "
            "will come back as a solid disc. Send the view with an alpha cut-out, or "
            "use options.shared_frame + options.carve_enclosed to carve them."),
    }


# ---------------------------------------------------------------------------
# shared framing
# ---------------------------------------------------------------------------
def shared_frame(views, size=FRAME_SIZE, pad=1.1, background=(0, 0, 0)):
    """Frame every view on ONE scale and ONE vertical centre.

    ``views``: ordered ``{name: (rgb HxWx3 uint8, mask HxW bool)}``, all of the
    SAME height with rows aligned (see the module docstring).  Returns
    ``({name: framed rgb size x size uint8}, layout)``; the subject is composited
    over ``background`` (black, like core's ImageCropToMask).

    Layout: ``scale`` = output px per input px; ``extent_px`` = the largest
    object extent in input px (union height, or the widest view);
    ``v_span_px`` = the union of the views' row extents; ``centres`` = the input
    px each view's frame is centred on.
    """
    np = _np()
    from PIL import Image
    if not views:
        raise FramingError("no views to frame")
    heights = {name: np.asarray(m).shape[0] for name, (_rgb, m) in views.items()}
    if len(set(heights.values())) != 1:
        raise FramingError(
            "shared_frame needs every view cut at one pixel scale with its rows "
            "aligned (the same image height; row r = the same height on the object "
            f"in every view). Heights: {heights}")
    ext = {}
    for name, (rgb, m) in views.items():
        m = np.asarray(m, bool)
        if np.asarray(rgb).shape[:2] != m.shape:
            raise FramingError(f"view {name}: mask {m.shape} and image {np.asarray(rgb).shape[:2]} differ")
        ys, xs = np.nonzero(m)
        if not len(ys):
            raise FramingError(f"view {name} has an empty subject mask")
        ext[name] = (int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1)
    top = min(e[2] for e in ext.values())
    bottom = max(e[3] for e in ext.values())
    extent = max(bottom - top, max(e[1] - e[0] for e in ext.values()))
    scale = size / (pad * extent)
    cy = 0.5 * (top + bottom)
    a = 1.0 / scale
    out, centres = {}, {}
    for name, (rgb, m) in views.items():
        x0, x1, _y0, _y1 = ext[name]
        cx = 0.5 * (x0 + x1)
        m = np.asarray(m, bool)[..., None]
        src = np.where(m, np.asarray(rgb, np.uint8), np.array(background, np.uint8)).astype(np.uint8)
        img = Image.fromarray(src).transform(
            (size, size), Image.AFFINE, (a, 0, cx - 0.5 * size * a, 0, a, cy - 0.5 * size * a),
            resample=Image.BICUBIC, fillcolor=tuple(int(c) for c in background))
        out[name] = np.asarray(img)
        centres[name] = [round(cx, 2), round(cy, 2)]
    layout = {"mode": "shared", "size": int(size), "pad": float(pad), "scale": round(scale, 6),
              "extent_px": int(extent), "v_span_px": [int(top), int(bottom)], "centres": centres,
              "extents_px": {n: {"w": e[1] - e[0], "h": e[3] - e[2]} for n, e in ext.items()}}
    return out, layout


def per_view_frame_scales(masks, size=FRAME_SIZE, pad=1.1):
    """What the DEFAULT per-view crop does to scale: each view's own px-per-px.

    ``ImageCropToMask`` fits each view's own bbox (longest side) at ``pad``, so a
    view's scale is ``size / (pad * its longest side)``.  Returned so a caller
    (and the tests) can see the cross-view scale error shared framing removes.
    """
    np = _np()
    out = {}
    for name, m in masks.items():
        ys, xs = np.nonzero(np.asarray(m, bool))
        longest = max(int(xs.max()) - int(xs.min()) + 1, int(ys.max()) - int(ys.min()) + 1)
        out[name] = size / (pad * longest)
    return out


# ---------------------------------------------------------------------------
# the matte graph (BiRefNet only, no diffusion model)
# ---------------------------------------------------------------------------
def build_matte_graph(loader_node: dict, staged_name: str, prefix: str) -> dict:
    """LoadImage -> RemoveBackground -> MaskToImage -> SaveImage.

    ``loader_node`` is the template's own ``LoadBackgroundRemovalModel`` node,
    copied, so the matte is the one the generation graph would have computed.
    """
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": staged_name}},
        "2": copy.deepcopy(loader_node),
        "3": {"class_type": "RemoveBackground",
              "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": prefix}},
    }


MATTE_SAVE_NODE = "5"


def read_mask_image(path, shape=None):
    """A saved matte (grey image) as a bool mask, resized to ``shape`` if given."""
    np = _np()
    from PIL import Image
    with Image.open(path) as im:
        g = im.convert("L")
        if shape is not None and (g.height, g.width) != tuple(shape):
            g = g.resize((shape[1], shape[0]), Image.BILINEAR)
        return np.asarray(g) > 127


def birefnet_mattes(paths, config=None, client=None, loader_node=None, timeout_s=None):
    """BiRefNet mattes for image files, through a RUNNING ComfyUI (no diffusion model is loaded).

    The one matte path every caller shares: meshgen's shared framing (an input
    without an alpha cut-out), and the conquest-units intake / sheet_proxy
    dark-subject fallback, which call it in-process against the ComfyUI that
    meshgen owns.  ``config`` / ``client`` default to meshgen's own; the loader
    node defaults to the multi-view template's (so the model is the one the
    generation graph would run).  Returns one bool mask per path, at each
    image's own size.  Raises RuntimeError when ComfyUI is not running.
    """
    import json as _json
    from pathlib import Path as _Path
    if config is None:
        from . import config as _config
        config = _config.load()
    if client is None:
        from .comfyui_client import ComfyUIClient
        client = ComfyUIClient(config)
    if not client.is_running():
        raise RuntimeError("ComfyUI is not running - start meshgen (or any generation) first")
    from .backends import multiview, structure_probe
    if loader_node is None:
        template = _Path(config.workflows_dir) / multiview.MULTIVIEW_WORKFLOW
        loader_node = _json.loads(template.read_text(encoding="utf-8"))[multiview.NODE_BG_REMOVAL_LOADER]
    masks = []
    for path in paths:
        name = client.stage_image(path)
        try:
            entry = client.run_graph(build_matte_graph(loader_node, name, "forge/matte"), prompt_id=None,
                                     progress=lambda *a: None, timeout_s=timeout_s or config.job_timeout_s)
        finally:
            client.unstage_image(name)
        found = structure_probe.find_images(entry, MATTE_SAVE_NODE, config)
        if not found:
            raise RuntimeError(f"the matte graph wrote no image for {path}")
        rgb, _alpha = load_rgba(path)
        masks.append(read_mask_image(found[-1], shape=rgb.shape[:2]))
    return masks
