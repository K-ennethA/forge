"""Phase 18(a) multi-view: request validation, the camera rig, the graph.

Every test here runs with **none** of the weights present, no GPU, no ComfyUI
and no network - same rule as the rest of the meshgen suite.  A handful
additionally re-check their golden fixtures against the real installed ComfyUI
and skip when it is absent, so they are evidence on this machine without being a
requirement on any other.

The rig and socket tests are the load-bearing ones, because the failure they
guard against is silent.  meshgen originally mapped ``right -> 90`` and
``left -> 270``; ComfyUI core's ``_VIEW_AZIMUTHS`` is
``{"front": 0, "left": 90, "back": 180, "right": 270}`` - **exactly swapped**.
Feeding a correctly named socket the other side's image raises nothing, costs
minutes of GPU time and returns a confidently wrong mesh.  So:

* :data:`CORE_VIEW_AZIMUTHS_GOLDEN` pins core's dict, and a second test reads it
  straight out of the installed ``nodes_trellis2.py`` to prove the pin is real;
* the camera maths is checked against three independent sources - core's
  ``_PROJ_FRONT_VIEW_TRANSFORM``, core's ``_orbit_camera_to_world``, and the
  ``transforms.json`` TencentARC ships in ``assets/mv_images/example/``;
* the graph tests assert the wiring goes through the AZIMUTH, so a name-level
  mistake cannot reach a socket.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshgen import config as config_module, service
from meshgen.backends import multiview
from meshgen.backends.comfyui_base import NON_GRAPH_OPTIONS

from meshgen.tests.test_service import (  # noqa: F401  (fixtures)
    Client, _clean_fake_env, config_file, image, live,
)


# --------------------------------------------------------------------------
# golden fixtures
# --------------------------------------------------------------------------
#: comfy_extras/nodes_trellis2.py, ``_VIEW_AZIMUTHS``, verbatim (core v0.35.0+).
#: THE authority for which socket is which camera.  Written out here rather than
#: imported so that a change to meshgen's own table cannot make this test agree
#: with itself; ``test_core_view_azimuths_match_the_installed_comfyui`` then
#: checks this literal against the real file.
CORE_VIEW_AZIMUTHS_GOLDEN = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}

#: comfy/ldm/trellis2/model.py, _PROJ_FRONT_VIEW_TRANSFORM with T[1][3] = -d.
#: The single-view camera: a module constant with no rotation parameter.
CORE_FRONT_VIEW = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, -1.0, None],   # None = -distance, filled in per test
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]

#: TencentARC/Pixal3D assets/mv_images/example/transforms.json, verbatim.
#: 20 degree FOV, four frames at 0/90/180/270 degrees, camera distance
#: 3.1192049980163574.  This is the rig meshgen's transforms are checked against.
UPSTREAM_TRANSFORMS_JSON = {
    "camera_angle_x": 0.3490658503988659,
    "mesh_scale": 1.0,
    "frames": [
        {"file_path": "view00_azim000.png", "name": "azim000", "transform_matrix": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, -1.0, -3.1192049980163574],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]},
        {"file_path": "view01_azim090.png", "name": "azim090", "transform_matrix": [
            [0.0, 0.0, 1.0, 3.1192049980163574],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]},
        {"file_path": "view02_azim180.png", "name": "azim180", "transform_matrix": [
            [-1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 3.1192049980163574],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]},
        {"file_path": "view03_azim270.png", "name": "azim270", "transform_matrix": [
            [0.0, 0.0, -1.0, -3.1192049980163574],
            [-1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]},
    ],
}

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005574bd10000000049454e44ae426082"
)


def _png(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(PNG_1X1)
    return path


@pytest.fixture
def views(tmp_path):
    """front + side + back, all real files on disk."""
    return {
        "front": str(_png(tmp_path, "front.png")),
        "side": str(_png(tmp_path, "side.png")),
        "back": str(_png(tmp_path, "back.png")),
    }


def _close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for row_a, row_b in zip(a, b)
               for x, y in zip(row_a, row_b))


# --------------------------------------------------------------------------
# the camera rig - verified against upstream, not invented
# --------------------------------------------------------------------------
def test_front_view_reproduces_comfyui_cores_own_constant():
    distance = multiview.distance_for_fov(20.0)
    expected = [row[:] for row in CORE_FRONT_VIEW]
    expected[1][3] = -distance
    assert _close(multiview.transform_matrix(0.0, distance), expected)


def test_front_view_matches_the_installed_comfyui_source():
    """Belt and braces: check the golden constant against the real file."""
    source = Path(config_module.load().comfyui_root) / "comfy" / "ldm" / "trellis2" / "model.py"
    if not source.is_file():
        pytest.skip("ComfyUI not installed on this machine")
    text = source.read_text(encoding="utf-8", errors="replace")
    assert "_PROJ_FRONT_VIEW_TRANSFORM" in text
    # The rotation rows of the constant, as written upstream.
    for row in ("[1.0, 0.0, 0.0, 0.0]", "[0.0, 0.0, -1.0, -2.0]", "[0.0, 1.0, 0.0, 0.0]"):
        assert row in text, f"core's front-view transform changed: {row} is gone"


#: Upstream serialises its rig through float32, so the shipped numbers carry
#: float32 dust: 3.1192049980163574 * tan(10 deg) is 0.5499999995, not 0.55.
#: Our double-precision 0.55 / tan(10 deg) lands 2.8e-9 away - well inside
#: float32 resolution at this magnitude (~3.7e-7) and far outside the ~1e-2 a
#: wrong padding constant (0.5 instead of 0.55) would cost.
FLOAT32_DUST = 1e-6


def test_camera_distance_is_upstreams_within_float32():
    # 0.55 / tan(10 deg): the 0.55 is Pixal3D's 1.1 crop padding on a 0.5
    # half-extent, and it lands on the shipped value to float32 precision.
    got = multiview.distance_for_fov(20.0)
    assert got == pytest.approx(3.1192049980163574, abs=FLOAT32_DUST)
    # the padding really is 1.1, not 1.0 - core's single-view path uses 0.5
    assert multiview.distance_for_fov(20.0, pad_factor=1.0) == pytest.approx(
        2.8356409098088545, abs=FLOAT32_DUST)


def test_the_four_view_rig_reproduces_upstreams_shipped_transforms_json(tmp_path):
    # Which name goes with which azim file is DERIVED from core's dict, not
    # chosen: azim090 is core's "left", azim270 is core's "right".
    names = {int(az): name for name, az in CORE_VIEW_AZIMUTHS_GOLDEN.items()}
    assert names == {0: "front", 90: "left", 180: "back", 270: "right"}
    records = multiview.normalise_views({
        names[0]:   str(_png(tmp_path, "view00_azim000.png")),
        names[90]:  str(_png(tmp_path, "view01_azim090.png")),
        names[180]: str(_png(tmp_path, "view02_azim180.png")),
        names[270]: str(_png(tmp_path, "view03_azim270.png")),
    })
    rig = multiview.camera_rig(records, fov_deg=20.0)

    assert rig["camera_angle_x"] == pytest.approx(
        UPSTREAM_TRANSFORMS_JSON["camera_angle_x"], abs=1e-12)
    assert rig["mesh_scale"] == UPSTREAM_TRANSFORMS_JSON["mesh_scale"]
    assert len(rig["frames"]) == 4

    for got, want in zip(rig["frames"], UPSTREAM_TRANSFORMS_JSON["frames"]):
        assert got["name"] == want["name"]
        assert got["file_path"] == want["file_path"]
        assert _close(got["transform_matrix"], want["transform_matrix"],
                      tol=FLOAT32_DUST)


def test_every_camera_looks_at_the_origin_with_z_up():
    distance = multiview.distance_for_fov(20.0)
    for azimuth in (0.0, 90.0, 180.0, 270.0):
        T = multiview.transform_matrix(azimuth, distance)
        position = [T[0][3], T[1][3], T[2][3]]
        # camera-to-world column 2 is local +Z; the camera looks along -Z
        forward = [-T[0][2], -T[1][2], -T[2][2]]
        # local +Y is up
        up = [T[0][1], T[1][1], T[2][1]]

        assert math.dist(position, (0, 0, 0)) == pytest.approx(distance, abs=1e-9)
        # it points from where it stands back at the origin
        to_origin = [-p / distance for p in position]
        assert _close([forward], [to_origin], tol=1e-9)
        assert _close([up], [[0.0, 0.0, 1.0]], tol=1e-9)


def test_front_is_always_frame_zero_whatever_order_it_was_given(views):
    shuffled = {"back": views["back"], "side": views["side"], "front": views["front"]}
    records = multiview.normalise_views(shuffled)
    # rig order is core's socket order (front, left, back, right), and "side"
    # IS "right", so it sorts last - not second as its old 90 degrees implied
    assert [r["name"] for r in records] == ["front", "back", "side"]
    assert records[0]["azimuth_deg"] == 0.0


# --------------------------------------------------------------------------
# the azimuth mapping - the bug that used to live here
# --------------------------------------------------------------------------
def test_view_azimuths_are_cores_dict_plus_one_documented_alias():
    assert multiview.CORE_VIEW_AZIMUTHS == CORE_VIEW_AZIMUTHS_GOLDEN
    assert multiview.VIEW_AZIMUTHS == dict(CORE_VIEW_AZIMUTHS_GOLDEN, side=270.0)
    # the exact thing that was wrong: left and right are NOT swapped
    assert multiview.VIEW_AZIMUTHS["left"] == 90.0
    assert multiview.VIEW_AZIMUTHS["right"] == 270.0


def test_core_view_azimuths_match_the_installed_comfyui():
    """The pin above, checked against the real file rather than against us."""
    source = Path(config_module.load().comfyui_root) / "comfy_extras" / "nodes_trellis2.py"
    if not source.is_file():
        pytest.skip("ComfyUI not installed on this machine")
    text = source.read_text(encoding="utf-8", errors="replace")
    literal = ", ".join(f'"{name}": {az}'
                        for name, az in CORE_VIEW_AZIMUTHS_GOLDEN.items())
    assert f"_VIEW_AZIMUTHS = {{{literal}}}" in text, (
        "core's _VIEW_AZIMUTHS is not what meshgen pins - re-derive "
        "VIEW_AZIMUTHS in backends/multiview.py from it"
    )
    assert "_VIEW_PAD = 1.1" in text
    assert multiview.PAD_FACTOR == multiview.CORE_VIEW_PAD == 1.1


def test_sockets_are_chosen_by_azimuth_not_by_name():
    for name, az in CORE_VIEW_AZIMUTHS_GOLDEN.items():
        assert multiview.core_socket_for_azimuth(az) == name
    # the alias resolves through its azimuth to core's own socket name
    assert multiview.core_socket_for_azimuth(multiview.VIEW_AZIMUTHS["side"]) == "right"
    assert multiview.core_socket_for_azimuth(-90.0) == "right"  # wraps
    with pytest.raises(multiview.MultiviewError, match="not one of core's four"):
        multiview.core_socket_for_azimuth(45.0)


def test_every_record_carries_the_socket_its_azimuth_resolves_to(views):
    records = multiview.normalise_views(views)
    assert [(r["name"], r["azimuth_deg"], r["socket"]) for r in records] == [
        ("front", 0.0, "front"),
        ("back", 180.0, "back"),
        ("side", 270.0, "right"),
    ]


def test_stage_plan_carries_everything_the_multiview_node_needs(views):
    plan = multiview.stage_plan(multiview.normalise_views(views))
    assert plan["view_count"] == 3
    assert [v["index"] for v in plan["views"]] == [0, 1, 2]
    assert [v["azimuth_deg"] for v in plan["views"]] == [0.0, 180.0, 270.0]
    assert [v["socket"] for v in plan["views"]] == ["front", "back", "right"]
    assert [v["path"] for v in plan["views"]] == [views["front"], views["back"], views["side"]]
    assert plan["camera_distance"] == pytest.approx(3.1192049980163574,
                                                    abs=FLOAT32_DUST)
    assert plan["transforms_json"]["frames"][0]["name"] == "azim000"
    # every view carries its own camera, which is the whole point
    assert len({json.dumps(v["transform_matrix"]) for v in plan["views"]}) == 3


# --------------------------------------------------------------------------
# request validation
# --------------------------------------------------------------------------
def test_front_is_required(views):
    with pytest.raises(multiview.MultiviewError, match="views.front is required"):
        multiview.normalise_views({"side": views["side"]})


def test_views_must_be_an_object():
    with pytest.raises(multiview.MultiviewError, match="must be an object"):
        multiview.normalise_views(["a.png", "b.png"])


def test_empty_views_is_refused():
    with pytest.raises(multiview.MultiviewError, match="empty"):
        multiview.normalise_views({})


def test_unknown_view_name_lists_the_real_ones(views):
    with pytest.raises(multiview.MultiviewError) as exc:
        multiview.normalise_views({"front": views["front"], "top": views["side"]})
    assert "top" in str(exc.value)
    for name in ("front", "side", "back", "left"):
        assert name in str(exc.value)


def test_side_and_right_are_the_same_camera_so_both_is_refused(views):
    assert multiview.VIEW_AZIMUTHS["side"] == multiview.VIEW_AZIMUTHS["right"] == 270.0
    with pytest.raises(multiview.MultiviewError, match="same 270 degree camera"):
        multiview.normalise_views({"front": views["front"], "side": views["side"],
                                   "right": views["back"]})


def test_relative_view_path_is_refused(views):
    with pytest.raises(multiview.MultiviewError, match="must be absolute"):
        multiview.normalise_views({"front": views["front"], "side": "side.png"})


def test_absent_view_file_is_refused(views, tmp_path):
    with pytest.raises(multiview.MultiviewError, match="not found"):
        multiview.normalise_views({"front": views["front"],
                                   "side": str(tmp_path / "nope.png")})


def test_non_image_extension_is_refused(views, tmp_path):
    stray = tmp_path / "notes.txt"
    stray.write_text("not an image", encoding="utf-8")
    with pytest.raises(multiview.MultiviewError, match="must be an image"):
        multiview.normalise_views({"front": views["front"], "side": str(stray)})


def test_non_string_view_path_is_refused(views):
    with pytest.raises(multiview.MultiviewError, match="absolute path"):
        multiview.normalise_views({"front": views["front"], "side": 7})


# --------------------------------------------------------------------------
# availability - the honest verdict about this machine
# --------------------------------------------------------------------------
def test_multiview_weights_are_reported_missing_by_name(config_file):
    cfg = config_module.load(config_file)
    missing = multiview.multiview_missing(cfg)
    weight = [m for m in missing if "safetensors" in m["what"]]
    assert len(weight) == 1
    entry = weight[0]
    assert "pixal3d_multiview_int8_convrot.safetensors" in entry["what"]
    assert entry["bytes"] == 5584555824
    assert entry["source"].startswith("https://huggingface.co/Comfy-Org/Pixal3D/")
    assert entry["sha256"] == multiview.MULTIVIEW_WEIGHT_SHA256
    assert entry["needs_approval"] is True


def test_a_present_weight_file_stops_being_reported(config_file, tmp_path):
    cfg = config_module.load(config_file)
    folder, filename, size, _url = multiview.MULTIVIEW_WEIGHT
    target = Path(cfg.models_root) / folder / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * 16)  # present but the wrong size

    missing = multiview.multiview_missing(cfg)
    weight = [m for m in missing if filename in m["what"]]
    assert len(weight) == 1
    assert "truncated" in weight[0]["what"]


def test_a_comfyui_without_the_node_is_reported_as_the_blocker(config_file):
    """``config_file`` points at an empty temp tree, which stands in for an
    install older than v0.35.0.  The verdict must still be honest there."""
    cfg = config_module.load(config_file)
    support = multiview.core_multiview_support(cfg)
    assert support["node"] is None
    blockers = [m for m in multiview.multiview_missing(cfg) if m.get("blocker")]
    assert len(blockers) == 1
    assert "v0.35.0" in blockers[0]["note"] or "v0.35.0" in blockers[0]["source"]


def test_the_node_probe_reads_the_real_installed_comfyui():
    """Polarity flipped on 2026-09-13: this used to assert the node was ABSENT
    and to fail the day core shipped one.  Core shipped one (v0.35.0), this
    machine is on v0.35.1, so it now asserts the node is THERE - and fails if a
    downgrade or a reinstall would silently take multi-view away again."""
    cfg = config_module.load()
    source = Path(cfg.comfyui_root) / "comfy_extras" / "nodes_trellis2.py"
    if not source.is_file():
        pytest.skip("ComfyUI not installed on this machine")
    support = multiview.core_multiview_support(cfg)
    assert support["checked"] == "source"
    assert support["node"] == multiview.MULTIVIEW_NODE, (
        "the installed ComfyUI no longer declares "
        f"{multiview.MULTIVIEW_NODE} - multi-view needs core v0.35.0 or newer"
    )
    text = source.read_text(encoding="utf-8", errors="replace")
    # the single-view node it also has is the one we say it has
    assert 'node_id="Pixal3DConditioning"' in text
    # and the node's real socket names are the ones the graph wires
    for socket in multiview.CORE_VIEW_AZIMUTHS:
        assert f'"{socket}"' in text


def test_the_single_view_template_stayed_single_view():
    """The multi-view graph is DERIVED from this one, so its shape is a
    contract: one LoadImage, one Pixal3DConditioning, one 1024/pad-1.1 crop."""
    path = Path(__file__).resolve().parents[1] / "workflows" / "image_to_3d.json"
    graph = json.loads(path.read_text(encoding="utf-8"))
    kinds = [node["class_type"] for node in graph.values()]
    assert kinds.count("LoadImage") == 1
    assert kinds.count("Pixal3DConditioning") == 1
    assert kinds.count("ImageCropToMask") == 1


# --------------------------------------------------------------------------
# the multi-view workflow
# --------------------------------------------------------------------------
WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"


def _multiview_template():
    return json.loads((WORKFLOWS / multiview.MULTIVIEW_WORKFLOW).read_text(
        encoding="utf-8"))


def test_the_multiview_template_is_exactly_what_the_generator_produces():
    """It is derived from image_to_3d.json, never hand-edited: regenerate in
    memory and compare, so the two cannot drift."""
    from meshgen.tools import build_multiview_workflow

    single = json.loads((WORKFLOWS / "image_to_3d.json").read_text(encoding="utf-8"))
    assert build_multiview_workflow.build(single) == _multiview_template()


def test_the_multiview_template_has_four_prepared_views_and_no_moge():
    graph = _multiview_template()
    kinds = [node["class_type"] for node in graph.values()]
    assert kinds.count("LoadImage") == 4
    assert kinds.count("ImageCropToMask") == 4
    assert kinds.count(multiview.MULTIVIEW_NODE) == 1
    assert kinds.count("Pixal3DConditioning") == 0
    # MoGe only ever existed to estimate Pixal3DConditioning's FOV; a rig has a
    # known one, so the branch is gone and multi-view never loads that weight
    assert "MoGeInference" not in kinds and "MoGeGeometryToFOV" not in kinds
    # every view crop is the framing the node documents
    for node in graph.values():
        if node["class_type"] == "ImageCropToMask":
            assert node["inputs"]["pad_factor"] == multiview.CORE_VIEW_PAD
            assert node["inputs"]["width"] == node["inputs"]["height"] == 1024
    # one background-removal model, shared
    assert kinds.count("LoadBackgroundRemovalModel") == 1


def test_the_multiview_template_loads_the_multiview_checkpoint():
    """The two checkpoints are the same size and the same architecture
    repacked, so loading the wrong one is silent."""
    graph = _multiview_template()
    unet = graph[multiview.NODE_PIXAL3D_UNET]
    assert unet["class_type"] == "UNETLoader"
    assert unet["inputs"]["unet_name"] == "pixal3d_multiview_int8_convrot.safetensors"
    assert unet["inputs"]["unet_name"] == multiview.MULTIVIEW_WEIGHT[1]


def test_the_multiview_node_defaults_to_the_rig_fov():
    node = _multiview_template()[multiview.NODE_MULTIVIEW_CONDITIONING]
    assert node["class_type"] == multiview.MULTIVIEW_NODE
    assert node["inputs"]["fov"] == multiview.DEFAULT_FOV_DEG == 20.0


def test_building_a_graph_wires_each_view_to_its_azimuths_socket(views):
    records = multiview.normalise_views(views)
    plan = multiview.stage_plan(records)
    staged = {"front": "a.png", "back": "b.png", "side": "c.png"}
    graph = multiview.build_multiview_graph(_multiview_template(), plan, staged)

    node = graph[multiview.NODE_MULTIVIEW_CONDITIONING]
    # "side" landed on core's RIGHT socket because its azimuth is 270
    assert set(node["inputs"]) == {"clip_vision_model", "fov", "front", "back", "right"}
    assert "left" not in node["inputs"]

    for socket, staged_name in (("front", "a.png"), ("back", "b.png"), ("right", "c.png")):
        load = multiview.VIEW_CHAIN_NODES[socket]["load"]
        assert graph[load]["inputs"]["image"] == staged_name
        # the socket is fed by the END of that chain, not by the LoadImage
        assert node["inputs"][socket] == [multiview.VIEW_CHAIN_OUTPUT[socket], 0]


def test_an_unused_view_chain_is_deleted_not_left_dangling(views):
    records = multiview.normalise_views({"front": views["front"], "side": views["side"]})
    plan = multiview.stage_plan(records)
    graph = multiview.build_multiview_graph(
        _multiview_template(), plan, {"front": "a.png", "side": "c.png"})

    for socket in ("left", "back"):
        chain = multiview.VIEW_CHAIN_NODES[socket]
        for node_id in (chain["load"],) + tuple(chain["chain"]):
            assert node_id not in graph, f"{socket} chain node {node_id} survived"
    # nothing left in the graph points at a node that is gone
    for node_id, node in graph.items():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in graph, f"{node_id}.{key} -> missing {value[0]}"


def test_a_graph_without_a_front_view_is_refused(views, tmp_path):
    """Core poses the mesh onto whichever socket is connected first, so a
    frontless rig silently re-bases the whole orbit."""
    records = multiview.normalise_views(views)
    plan = multiview.stage_plan(records)
    plan["views"] = [v for v in plan["views"] if v["name"] != "front"]
    with pytest.raises(multiview.MultiviewError, match="must have a front view"):
        multiview.build_multiview_graph(_multiview_template(), plan,
                                        {"back": "b.png", "side": "c.png"})


def test_the_fov_the_caller_asked_for_reaches_the_node(views):
    plan = multiview.stage_plan(multiview.normalise_views(views), fov_deg=33.0)
    graph = multiview.build_multiview_graph(
        _multiview_template(), plan,
        {"front": "a.png", "back": "b.png", "side": "c.png"}, fov_deg=33.0)
    assert graph[multiview.NODE_MULTIVIEW_CONDITIONING]["inputs"]["fov"] == 33.0


def test_unavailable_message_names_the_file_the_size_and_the_approval_rule(config_file):
    cfg = config_module.load(config_file)
    text = multiview.unavailable_message(multiview.multiview_missing(cfg))
    assert "pixal3d_multiview_int8_convrot.safetensors" in text
    assert "5.20 GB" in text
    assert "huggingface.co/Comfy-Org/Pixal3D" in text
    assert "never downloads weights on its own" in text
    assert "front_only" in text


# --------------------------------------------------------------------------
# the pixal3d adapter
# --------------------------------------------------------------------------
def test_pixal3d_declares_multiview_and_reports_it_unavailable(live):
    client, app = live
    backend = app.backends["pixal3d"]
    assert backend.supports_multiview is True
    state = backend.multiview_readiness()
    assert state["supported"] is True
    assert state["available"] is False
    assert any("pixal3d_multiview" in m["what"] for m in state["missing"])
    assert any(m.get("blocker") for m in state["missing"]), (
        "the missing core node must be reported as a blocker, not just the weights"
    )


def test_trellis2_does_not_pretend_to_support_views(live):
    client, app = live
    assert app.backends["trellis2"].supports_multiview is False
    assert app.backends["trellis2"].multiview_readiness()["supported"] is False


def test_health_carries_the_multiview_verdict(live):
    client, _app = live
    status, health = client.get("/health")
    assert status == 200
    pixal = [b for b in health["available_backends"] if b["name"] == "pixal3d"][0]
    assert pixal["multiview"]["supported"] is True
    assert pixal["multiview"]["available"] is False
    assert "front" in pixal["multiview"]["views"]
    trellis = [b for b in health["available_backends"] if b["name"] == "trellis2"][0]
    assert "multiview" not in trellis


def test_multiview_option_keys_never_reach_the_graph_builder(live, image):
    """views/on_unavailable are real options that steer meshgen, not nodes -
    build_graph must skip them rather than refuse them as unknown."""
    client, app = live
    for key in ("views", "on_unavailable", "multiview_fov_deg"):
        assert key in NON_GRAPH_OPTIONS
    backend = app.backends["pixal3d"]
    graph = backend.build_graph("in.png", {"views": [], "on_unavailable": "error"},
                                "forge/x")
    assert graph  # no BackendError about unknown options


def test_the_fallback_graph_is_the_plain_single_image_graph(live, image):
    """Falling back must be the ordinary single-image path, byte for byte -
    not a degraded variant of a multi-view one."""
    client, app = live
    backend = app.backends["pixal3d"]
    plain = backend.build_graph("front.png", {}, "forge/pixal3d")
    fell_back = backend.build_graph(
        "front.png", {"views": [], "on_unavailable": "front_only"}, "forge/pixal3d")
    assert json.dumps(plain, sort_keys=True) == json.dumps(fell_back, sort_keys=True)


def test_pixal3d_refuses_views_with_the_full_missing_report(live, views):
    client, app = live
    backend = app.backends["pixal3d"]
    records = multiview.normalise_views(views)
    with pytest.raises(Exception) as exc:
        backend.resolve_multiview(views["front"], {"views": records})
    assert "pixal3d_multiview_int8_convrot.safetensors" in str(exc.value)


def test_pixal3d_front_only_falls_back_and_says_so(live, views):
    client, app = live
    backend = app.backends["pixal3d"]
    records = multiview.normalise_views(views)
    image_path, note, plan = backend.resolve_multiview(
        views["front"], {"views": records, "on_unavailable": "front_only"})
    assert image_path == views["front"]
    assert plan is None, "a fallback must not carry a multi-view plan"
    assert note["used"] is False
    assert note["fell_back_to"] == "front"
    assert note["requested"] == ["front", "back", "side"]
    assert "2 of them were ignored" in note["honesty"]
    # the rig it WOULD have used still travels with the answer
    assert len(note["camera_rig"]["frames"]) == 3


def test_a_bad_on_unavailable_is_refused(live, views):
    client, app = live
    backend = app.backends["pixal3d"]
    records = multiview.normalise_views(views)
    with pytest.raises(Exception, match="on_unavailable"):
        backend.resolve_multiview(views["front"],
                                  {"views": records, "on_unavailable": "shrug"})


# --------------------------------------------------------------------------
# the service surface
# --------------------------------------------------------------------------
@pytest.fixture
def mv_capable(monkeypatch):
    """Make the fake adapter claim it CAN run multi-view.

    Needed for the plumbing tests: with no backend on this machine able to run
    one, every request would otherwise stop at the availability gate and the
    routing below would never be exercised.
    """
    monkeypatch.setenv("FORGE_MESHGEN_FAKE_MV", "1")


def test_views_without_image_path_uses_front(live, views, mv_capable):
    client, _app = live
    status, body = client.post("/generate3d", {"views": views})
    assert status == 202, body
    assert body["views"] == ["front", "back", "side"]
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    assert job["image_path"] == views["front"]


def test_a_queued_multiview_job_lists_its_views(live, views, mv_capable):
    client, _app = live
    status, body = client.post("/generate3d", {"views": views})
    assert status == 202
    _status, job = client.get(f"/job/{body['job_id']}")
    assert [v["name"] for v in job["views"]] == ["front", "back", "side"]
    assert [v["azimuth_deg"] for v in job["views"]] == [0.0, 180.0, 270.0]


def test_views_may_also_arrive_inside_options(live, views, mv_capable):
    client, _app = live
    status, body = client.post("/generate3d", {"options": {"views": views}})
    assert status == 202, body
    assert body["views"] == ["front", "back", "side"]


def test_image_path_disagreeing_with_views_front_is_a_400(live, views, tmp_path):
    client, _app = live
    other = _png(tmp_path, "other.png")
    status, body = client.post("/generate3d",
                               {"image_path": str(other), "views": views})
    assert status == 400
    assert "disagree" in body["error"]


def test_image_path_equal_to_views_front_is_fine(live, views, mv_capable):
    client, _app = live
    status, body = client.post("/generate3d",
                               {"image_path": views["front"], "views": views})
    assert status == 202, body


def test_a_backend_without_multiview_refuses_views_and_names_one_that_has_it(live, views):
    client, _app = live
    status, body = client.post("/generate3d",
                               {"views": views, "backend": "trellis2"})
    assert status == 400
    assert "trellis2" in body["error"]
    assert "pixal3d" in body["error"]


def test_pixal3d_views_are_a_clean_400_naming_the_file(live, views):
    """The headline fallback contract: a well-formed request on a machine that
    cannot run it gets the file, the path, the size, the URL and the approval
    note - before anything is queued and before ComfyUI is ever started."""
    client, app = live
    status, body = client.post("/generate3d",
                               {"views": views, "backend": "pixal3d"})
    assert status == 400
    assert "pixal3d_multiview_int8_convrot.safetensors" in body["error"]
    assert "5.20 GB" in body["error"]
    assert "never downloads weights on its own" in body["error"]
    assert body["multiview"]["available"] is False
    names = " ".join(m["what"] for m in body["multiview"]["missing"])
    assert "pixal3d_multiview" in names
    assert "per-view camera" in names
    assert app.store.all() == [], "nothing should have been queued"


def test_front_only_lets_the_same_request_through(live, views):
    client, _app = live
    status, body = client.post("/generate3d", {
        "views": views, "options": {"on_unavailable": "front_only"},
    })
    assert status == 202, body
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    assert job["multiview"]["used"] is False
    assert job["multiview"]["fell_back_to"] == "front"


def test_views_reach_a_backend_that_can_run_them(live, views, mv_capable):
    client, _app = live
    status, body = client.post("/generate3d", {"views": views})
    assert status == 202, body
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    assert job["multiview"]["used"] is True
    assert job["multiview"]["requested"] == ["front", "back", "side"]


def test_bad_views_are_refused_before_anything_is_queued(live, views, tmp_path):
    client, app = live
    status, body = client.post("/generate3d",
                               {"views": {"side": views["side"]}})
    assert status == 400
    assert "views.front is required" in body["error"]
    assert app.store.all() == []


def test_single_image_requests_are_completely_unchanged(live, image, tmp_path):
    """The whole point of the fallback contract: adding views changed nothing
    for callers that do not use them."""
    client, _app = live
    out = tmp_path / "plain.glb"
    status, body = client.post("/generate3d",
                               {"image_path": str(image), "output": str(out)})
    assert status == 202, body
    assert "views" not in body
    job = client.wait(body["job_id"])
    assert job["state"] == "done", job
    assert "views" not in job
    assert "multiview" not in job
    assert Path(job["mesh_path"]).is_file()
