"""Shared multi-view framing + the enclosed-background matte check.

The two meshgen defects the Pixal3D trial found on the conquest-units sheets
(2026-10-11), each pinned on synthetic images so the suite needs no GPU, no
weights and no ComfyUI:

(a) per-view crops give each view its own scale, so a long quadruped's side
    view is shrunk against its front (gemwing side 0.82x) - ``shared_frame``
    puts every view on one scale and one vertical centre;
(b) BiRefNet fills background the subject encloses (the wolf's horn ring) -
    ``enclosed_background`` finds it, the job result warns, ``carve_enclosed``
    cuts it out.

The default (neither option) must stay byte-for-byte the old behaviour.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from meshgen import config as config_module, framing
from meshgen.backends import comfyui_base as cbase, multiview
from meshgen.backends.base import BackendError
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.tests.fake_backend import write_triangle_glb

BG = (30, 33, 38)          # the eclipse wolf sheet's background
WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"


# --------------------------------------------------------------------------
# synthetic views
# --------------------------------------------------------------------------
def _canvas(h, w, colour=BG):
    return np.zeros((h, w, 3), np.uint8) + np.array(colour, np.uint8)


def _box(h, w, x0, y0, x1, y1):
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    return m


def _bbox(img, thresh=20):
    m = np.asarray(img).astype(int).sum(axis=2) > thresh
    ys, xs = np.nonzero(m)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _quadruped(h=400):
    """A long quadruped cut from one sheet: front 100 wide, side 300 long, both
    200 tall standing on the same floor row (rows aligned)."""
    front_m = _box(h, 200, 50, 150, 150, 350)
    side_m = _box(h, 400, 50, 150, 350, 350)
    front = np.where(front_m[..., None], np.uint8(200), _canvas(h, 200))
    side = np.where(side_m[..., None], np.uint8(200), _canvas(h, 400))
    return {"front": (front, front_m), "left": (side, side_m)}


# --------------------------------------------------------------------------
# (a) shared framing
# --------------------------------------------------------------------------
def test_shared_frame_keeps_the_side_length_against_the_front_height():
    views = _quadruped()
    framed, layout = framing.shared_frame(views)
    fx0, fy0, fx1, fy1 = _bbox(framed["front"])
    sx0, sy0, sx1, sy1 = _bbox(framed["left"])
    # sheet: side length / front height = 300 / 200 = 1.5, kept to the pixel
    assert abs((sx1 - sx0) / (fy1 - fy0) - 1.5) < 0.02
    # the largest extent (the side's 300 px) spans 1 / 1.1 of the frame
    assert abs((sx1 - sx0) - 1024 / 1.1) <= 4      # bicubic edge: +-1 px a side
    assert layout["extent_px"] == 300 and layout["mode"] == "shared"


def test_the_per_view_crop_is_what_shrank_the_side():
    """The defect, measured: each view fitted on its own longest side gives the
    side 2/3 the front's px-per-px, i.e. a 300-long body comes out 200 long."""
    scales = framing.per_view_frame_scales({k: m for k, (_i, m) in _quadruped().items()})
    assert abs(scales["left"] / scales["front"] - 200 / 300) < 1e-9


def test_shared_frame_shares_one_vertical_centre():
    h = 400
    tall = _box(h, 200, 50, 100, 150, 350)       # 250 tall, floor row 350
    short = _box(h, 200, 60, 250, 140, 350)      # 100 tall, same floor row
    img = _canvas(h, 200, (0, 0, 0)) + 0
    views = {"front": (np.where(tall[..., None], np.uint8(220), img), tall),
             "back": (np.where(short[..., None], np.uint8(220), img), short)}
    framed, _layout = framing.shared_frame(views)
    assert abs(_bbox(framed["front"])[3] - _bbox(framed["back"])[3]) <= 2    # same floor row
    assert _bbox(framed["back"])[1] > _bbox(framed["front"])[1] + 300        # still the shorter one


def test_views_of_different_heights_are_refused_not_guessed():
    a = _box(300, 100, 10, 10, 90, 290)
    b = _box(310, 100, 10, 10, 90, 300)
    with pytest.raises(framing.FramingError, match="rows aligned"):
        framing.shared_frame({"front": (_canvas(300, 100), a), "left": (_canvas(310, 100), b)})


def test_an_empty_view_mask_is_refused():
    a = _box(100, 100, 10, 10, 90, 90)
    with pytest.raises(framing.FramingError, match="empty"):
        framing.shared_frame({"front": (_canvas(100, 100), a),
                              "back": (_canvas(100, 100), np.zeros((100, 100), bool))})


# --------------------------------------------------------------------------
# (b) enclosed background
# --------------------------------------------------------------------------
def _ring_creature(fur=(40, 34, 40)):
    """A magenta ring (the wolf's horns) around a background pocket, next to a
    patch of near-black purplish fur; the 'matte' fills the whole ring disc."""
    h = w = 300
    yy, xx = np.mgrid[:h, :w]
    r = np.hypot(yy - 150, xx - 150)
    img = _canvas(h, w)
    ring = (r > 80) & (r < 100)
    img[ring] = (200, 40, 200)
    pocket = r <= 80
    fur_m = (yy > 255) & (yy < 295) & (xx > 5) & (xx < 90)      # clear of the ring disc
    img[fur_m] = fur
    matte = (r < 100) | fur_m
    return img, matte, pocket, fur_m


def test_the_horn_ring_pocket_is_found_and_the_dark_fur_is_not():
    img, matte, pocket, fur_m = _ring_creature()
    report = framing.enclosed_background(img, matte)
    assert len(report["regions"]) == 1
    assert report["carve"][pocket].mean() > 0.95
    assert not report["carve"][fur_m].any()


def test_a_glow_brightened_pocket_still_counts():
    img, matte, pocket, _fur = _ring_creature()
    img[pocket] = np.array(BG) + 12                 # lifted, same tint
    assert framing.enclosed_background(img, matte)["carve"][pocket].mean() > 0.95


def test_dark_neutral_armour_is_not_a_pocket():
    """goo_shield: a near-black neutral helmet on a neutral sheet - same tint as
    the background, but darker; ink and armour never read as a pocket."""
    img, matte, pocket, _fur = _ring_creature()
    img[pocket] = (np.array(BG) - 12).clip(0)
    assert framing.enclosed_background(img, matte)["regions"] == []


def test_carve_cuts_the_pocket_out_of_the_matte():
    img, matte, pocket, fur_m = _ring_creature()
    carved = framing.carve_enclosed(matte, framing.enclosed_background(img, matte))
    assert not carved[pocket].any()
    assert carved[fur_m].all()


def test_specks_are_ignored():
    img, matte, _pocket, _fur = _ring_creature()
    img[:] = (200, 40, 200)
    img[150:155, 150:155] = BG                      # 25 px of background: an outline gap
    assert framing.enclosed_background(img, matte, bg=BG)["regions"] == []


def test_the_warning_names_the_view_and_the_way_out():
    img, matte, *_ = _ring_creature()
    warning = framing.matte_warning("front", framing.enclosed_background(img, matte))
    assert warning["code"] == "matte_filled_enclosed_background"
    assert warning["view"] == "front" and "carve_enclosed" in warning["message"]
    assert framing.matte_warning("front", {"regions": []}) is None


def test_alpha_is_a_mask_only_when_it_cuts_something_out():
    assert framing.mask_from_alpha(None) is None
    assert framing.mask_from_alpha(np.full((10, 10), 255, np.uint8)) is None
    a = np.zeros((10, 10), np.uint8); a[2:8, 2:8] = 255
    assert framing.mask_from_alpha(a).sum() == 36


# --------------------------------------------------------------------------
# the graph: framed views bypass the per-view chain
# --------------------------------------------------------------------------
def _template():
    return json.loads((WORKFLOWS / multiview.MULTIVIEW_WORKFLOW).read_text(encoding="utf-8"))


def _plan(tmp_path, names=("front", "left", "back")):
    paths = {}
    for n in names:
        p = tmp_path / f"{n}.png"
        p.write_bytes(b"x")
        paths[n] = str(p)
    return multiview.stage_plan(multiview.normalise_views(paths))


def test_each_chains_mask_preview_is_a_mask_preview():
    graph = _template()
    for socket, node_id in multiview.VIEW_CHAIN_MASK.items():
        assert graph[node_id]["class_type"] == "MaskPreview", socket
    assert multiview.VIEW_CHAIN_MASK["front"] == "303"
    assert graph[multiview.NODE_BG_REMOVAL_LOADER]["class_type"] == "LoadBackgroundRemovalModel"


def test_a_framed_graph_feeds_each_load_straight_to_its_socket(tmp_path):
    plan = _plan(tmp_path)
    staged = {"front": "f.png", "left": "l.png", "back": "b.png"}
    graph = multiview.build_multiview_graph(_template(), plan, staged, framed=True)
    classes = {v["class_type"] for v in graph.values()}
    assert "RemoveBackground" not in classes and "ImageCropToMask" not in classes
    assert "LoadBackgroundRemovalModel" not in classes
    cond = graph[multiview.NODE_MULTIVIEW_CONDITIONING]["inputs"]
    for socket in ("front", "left", "back"):
        preview = cond[socket][0]
        assert preview == multiview.VIEW_CHAIN_OUTPUT[socket]
        load = graph[preview]["inputs"]["images"][0]
        assert load == multiview.VIEW_CHAIN_NODES[socket]["load"]
        assert graph[load]["inputs"]["image"] == staged[socket]
    assert "right" not in cond


def test_the_unframed_graph_is_unchanged_by_the_new_parameter(tmp_path):
    plan = _plan(tmp_path)
    staged = {"front": "f.png", "left": "l.png", "back": "b.png"}
    assert (multiview.build_multiview_graph(_template(), plan, staged)
            == multiview.build_multiview_graph(_template(), plan, staged, framed=False))


# --------------------------------------------------------------------------
# options + the backend
# --------------------------------------------------------------------------
class FramingClient:
    """Records staged files (reading them at stage time - the framed ones live
    in a temp dir) and returns a mesh for any graph."""

    def __init__(self, tmp_path, entry_outputs=None):
        self.tmp = Path(tmp_path)
        self.graphs, self.staged_sizes, self.staged_paths = [], [], []
        self.entry_outputs = entry_outputs or {}

    def ensure_running(self, cancel_event=None):
        pass

    def is_running(self):
        return False

    def runtime_missing(self):
        return []

    def vram_report(self):
        return {"vram_used_gb": 1.0, "vram_total_gb": 12.0, "name": "fake-gpu"}

    def stage_image(self, path):
        with Image.open(path) as im:
            self.staged_sizes.append(im.size)
        self.staged_paths.append(str(path))
        return f"staged_{len(self.staged_paths)}.png"

    def unstage_image(self, name):
        pass

    def run_graph(self, graph, prompt_id, cancel_event=None, progress=None,
                  timeout_s=None, telemetry=None):
        self.graphs.append(copy.deepcopy(graph))
        return {"outputs": self.entry_outputs}

    def collect_output(self, entry, out_path):
        write_triangle_glb(out_path)
        return {"mesh_path": str(out_path)}


def _pixal(client):
    backend = Pixal3DBackend(config_module.load(), client)
    backend.ensure_ready = lambda: {"ready": True, "missing": []}
    backend.multiview_readiness = lambda use_client=False: {"supported": True, "available": True,
                                                            "missing": [], "detail": ""}
    return backend


def _rgba_views(tmp_path):
    out = {}
    for name, (rgb, m) in _quadruped().items():
        rgba = np.dstack([rgb, (m * 255).astype(np.uint8)])
        p = tmp_path / f"{name}.png"
        Image.fromarray(rgba).save(p)
        out[name] = str(p)
    return out


def test_framing_options_default_off_and_are_strict(tmp_path):
    backend = _pixal(FramingClient(tmp_path))
    assert backend.framing_options({}) == {"shared_frame": False, "carve_enclosed": False}
    assert backend.framing_options({"shared_frame": "true"})["shared_frame"] is True
    with pytest.raises(BackendError, match="not a boolean"):
        backend.framing_options({"shared_frame": "yes"})
    with pytest.raises(BackendError, match="carve_enclosed needs shared_frame"):
        backend.framing_options({"carve_enclosed": True})
    assert {"shared_frame", "carve_enclosed"} <= cbase.NON_GRAPH_OPTIONS


def test_shared_frame_without_views_is_refused(tmp_path, ):
    backend = _pixal(FramingClient(tmp_path))
    with pytest.raises(BackendError, match="no views block"):
        backend.resolve_multiview(str(tmp_path / "x.png"), {"shared_frame": True})


def test_a_shared_frame_run_frames_stages_and_reports(tmp_path):
    client = FramingClient(tmp_path)
    backend = _pixal(client)
    out = tmp_path / "out.glb"
    payload = backend.generate(None, {"seed": 56, "views": _rgba_views(tmp_path), "shared_frame": True},
                               str(out))
    # every view staged as a framed 1024 x 1024 image, never the caller's file
    assert client.staged_sizes == [(1024, 1024), (1024, 1024)]
    assert all(Path(p).name.startswith("framed_") for p in client.staged_paths)
    graph = client.graphs[-1]
    assert not any(v["class_type"] == "RemoveBackground" for v in graph.values())
    framing_note = payload["multiview"]["framing"]
    assert framing_note["mode"] == "shared"
    assert framing_note["layout"]["mask_source"] == {"front": "alpha", "left": "alpha"}
    assert framing_note["layout"]["extent_px"] == 300
    assert "warnings" not in payload
    json.dumps(payload)                       # the job record must stay serialisable


def test_a_default_multiview_run_is_unchanged(tmp_path):
    client = FramingClient(tmp_path)
    backend = _pixal(client)
    views = _rgba_views(tmp_path)
    payload = backend.generate(None, {"seed": 56, "views": views}, str(tmp_path / "o.glb"))
    assert client.staged_paths == [views["front"], views["left"]]      # the caller's own files
    assert any(v["class_type"] == "RemoveBackground" for v in client.graphs[-1].values())
    assert payload["multiview"]["framing"] == {"mode": "per_view"}


def test_the_default_path_warns_when_the_graphs_matte_fills_a_pocket(tmp_path):
    img, matte, *_ = _ring_creature()
    src = tmp_path / "creature.png"
    Image.fromarray(img).save(src)
    out_dir = tmp_path / "comfy_out"
    out_dir.mkdir()
    Image.fromarray((matte * 255).astype(np.uint8)).save(out_dir / "mask.png")
    backend = _pixal(FramingClient(tmp_path))
    backend.config = SimpleNamespace(output_dir=out_dir, comfyui_dir=tmp_path)
    entry = {"outputs": {"303": {"images": [{"filename": "mask.png", "subfolder": "", "type": "output"}]}}}
    warnings = backend.matte_warnings(entry, str(src))
    assert len(warnings) == 1 and warnings[0]["view"] == "front"
    # a clean matte: no warning; a broken entry: no warning and no exception
    Image.fromarray(((matte & ~_ring_creature()[2]) * 255).astype(np.uint8)).save(out_dir / "mask.png")
    assert backend.matte_warnings(entry, str(src)) == []
    assert backend.matte_warnings({"outputs": {"303": "garbage"}}, str(src)) == []


def test_carve_enclosed_cuts_the_pocket_before_framing(tmp_path):
    img, matte, pocket, _fur = _ring_creature()
    paths = {}
    for name in ("front", "back"):
        p = tmp_path / f"{name}.png"
        Image.fromarray(np.dstack([img, (matte * 255).astype(np.uint8)])).save(p)
        paths[name] = str(p)
    client = FramingClient(tmp_path)
    backend = _pixal(client)
    warned = backend.generate(None, {"seed": 1, "views": paths, "shared_frame": True},
                              str(tmp_path / "a.glb"))
    assert {w["view"] for w in warned["warnings"]} == {"front", "back"}
    carved = backend.generate(None, {"seed": 1, "views": paths, "shared_frame": True,
                                     "carve_enclosed": True}, str(tmp_path / "b.glb"))
    assert "warnings" not in carved
    pockets = carved["multiview"]["framing"]["layout"]["enclosed_background"]
    assert pockets["front"]["carved"] is True and pockets["front"]["regions"] == 1


def test_capabilities_advertise_the_framing_options_on_pixal3d_only(tmp_path):
    from meshgen.backends.comfyui_trellis2 import Trellis2Backend
    backend = _pixal(FramingClient(tmp_path))
    assert {"shared_frame", "carve_enclosed"} <= set(backend.capabilities()["options"])
    assert "framing" in backend.capabilities()["conditioning"]["multi_view"]
    trellis = Trellis2Backend(config_module.load(), FramingClient(tmp_path))
    assert "shared_frame" not in trellis.capabilities()["options"]
