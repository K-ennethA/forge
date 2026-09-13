"""Tests for the surface-tuning options and the A/B measurement harness.

Two jobs here, and they guard opposite failure modes.

**The option surface** (``smooth_iters``, ``qef``, ``project_back``,
``fix_poles``, ``crease_angle``, ``hard_surface``) is covered the way the
existing options are: every key reaches the node it claims to, a wrong type or
an out-of-range value is refused with the real range rather than clamped, and
the values meshgen chose are pinned against ``workflows/image_to_3d.json`` so a
future ``tools/build_workflow.py`` refresh cannot quietly restore the template's
numbers.  That regression would be invisible: the pipeline would keep running
and just start producing rounder meshes.

**The metrics** are covered against solids whose answer is known in closed form
— a welded cube must score ``edge_sharpness`` 1.0 and a 90 degree p99, a sphere
must score 0.0 — because a scoring harness that is itself wrong is worse than no
harness: it produces a table, and the table is believed.

Everything in this file runs with none of the 23.8 GB present and no GPU.  The
GPU half is ``tools/ab_tuning.py run``, a manual gate like the real-model runs,
and its measured output is the table in meshgen/README.md.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path

import pytest

os.environ.setdefault("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")

from meshgen import config as config_module  # noqa: E402
from meshgen.backends import comfyui_base as base  # noqa: E402
from meshgen.backends.base import BackendError  # noqa: E402
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend  # noqa: E402
from meshgen.backends.comfyui_trellis2 import Trellis2Backend  # noqa: E402
from meshgen.comfyui_client import ComfyUIClient  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


@pytest.fixture
def backend():
    config = config_module.load()
    return Trellis2Backend(config, ComfyUIClient(config))


@pytest.fixture
def graph(backend):
    return backend.build_graph("pic.png", {}, "forge/x")


def _numpy_tools():
    pytest.importorskip("numpy")
    from meshgen.tools import mesh_metrics
    return mesh_metrics


# ==========================================================================
# the option surface
# ==========================================================================
def test_every_tuning_option_is_in_the_spec():
    for key in ("smooth_iters", "qef", "project_back", "fix_poles", "crease_angle"):
        assert key in base.OPTION_SPEC, key


def test_the_remesh_options_reach_the_remesh_node(backend):
    built = backend.build_graph(
        "pic.png",
        {"smooth_iters": 3, "qef": True, "project_back": 0.5, "fix_poles": True},
        "forge/x")
    inputs = built[base.NODE_REMESH]["inputs"]
    assert built[base.NODE_REMESH]["class_type"] == "RemeshMesh"
    assert inputs["smooth_iters"] == 3
    assert inputs["project_back"] == 0.5
    assert inputs["fix_poles"] is True
    # qef is a DynamicCombo sub-input and serialises as a namespaced sibling,
    # not as a nested object - writing a plain "qef" key would be ignored
    assert inputs["sign_mode.qef"] is True
    assert "qef" not in inputs


def test_crease_angle_reaches_both_smooth_normals_nodes(backend):
    built = backend.build_graph("pic.png", {"crease_angle": 45}, "forge/x")
    for node_id in (base.NODE_SMOOTH_NORMALS_UV, base.NODE_SMOOTH_NORMALS_OUT):
        assert built[node_id]["class_type"] == "MeshSmoothNormals"
        assert built[node_id]["inputs"]["crease_angle"] == 45.0


def test_the_two_smooth_normals_nodes_are_distinct_and_both_present(graph):
    assert base.NODE_SMOOTH_NORMALS_UV != base.NODE_SMOOTH_NORMALS_OUT
    # one feeds the unwrap, one produces the exported normals; setting only the
    # exported one leaves the atlas cut for a different set of creases
    assert graph[base.NODE_UNWRAP]["inputs"]["mesh"][0] == base.NODE_SMOOTH_NORMALS_UV


@pytest.mark.parametrize("value,expected",
                         [(True, True), (False, False), ("true", True),
                          ("false", False), (1, True), (0, False)])
def test_boolean_options_accept_the_honest_spellings(backend, value, expected):
    built = backend.build_graph("pic.png", {"qef": value}, "forge/x")
    assert built[base.NODE_REMESH]["inputs"]["sign_mode.qef"] is expected


@pytest.mark.parametrize("value", ["yes", "on", 2, 0.5, None, [], "True "])
def test_a_boolean_option_that_is_not_a_boolean_is_refused(backend, value):
    """``bool("false")`` is True - a silently-inverted flag is the trap here."""
    with pytest.raises(BackendError) as exc:
        backend.build_graph("pic.png", {"qef": value}, "forge/x")
    assert "boolean" in str(exc.value)


@pytest.mark.parametrize("key,value,low,high", [
    ("smooth_iters", 50, 0, 20),
    ("smooth_iters", -1, 0, 20),
    ("project_back", 2.0, 0.0, 1.0),
    ("crease_angle", 200, 0.0, 180.0),
    ("remesh_resolution", 4096, 32, 2048),
])
def test_an_out_of_range_option_is_refused_with_the_real_range(backend, key, value,
                                                               low, high):
    """Refused, never clamped: a clamp does not tell you it happened."""
    with pytest.raises(BackendError) as exc:
        backend.build_graph("pic.png", {key: value}, "forge/x")
    message = str(exc.value)
    assert key in message and str(low) in message and str(high) in message


def test_shape_resolution_lists_the_real_choices(backend):
    with pytest.raises(BackendError) as exc:
        backend.build_graph("pic.png", {"shape_resolution": "2048"}, "forge/x")
    assert "'1024'" in str(exc.value) and "'1536'" in str(exc.value)


def test_the_limits_match_the_comfyui_node_schemas():
    """Every bound is core's own, so a caller is never told a fiction."""
    assert base.OPTION_LIMITS["smooth_iters"] == (0, 20)
    assert base.OPTION_LIMITS["project_back"] == (0.0, 1.0)
    assert base.OPTION_LIMITS["crease_angle"] == (0.0, 180.0)
    assert base.OPTION_LIMITS["remesh_resolution"] == (32, 2048)


def test_every_limited_option_is_a_real_option():
    for key in itertools.chain(base.OPTION_LIMITS, base.OPTION_CHOICES):
        assert key in base.OPTION_SPEC, key


# -- the hard_surface macro -------------------------------------------------
def test_hard_surface_sets_the_pair_it_is_shorthand_for(backend):
    built = backend.build_graph("pic.png", {"hard_surface": True}, "forge/x")
    for key, value in base.HARD_SURFACE_PRESET.items():
        node_ids, field, coerce = base.OPTION_SPEC[key]
        node_ids = (node_ids,) if isinstance(node_ids, str) else node_ids
        for node_id in node_ids:
            assert built[node_id]["inputs"][field] == coerce(value), (key, node_id)


def test_hard_surface_is_a_macro_so_an_explicit_option_still_wins(backend):
    """It pre-seeds; it does not take the wheel."""
    built = backend.build_graph(
        "pic.png", {"hard_surface": True, "crease_angle": 20}, "forge/x")
    assert built[base.NODE_SMOOTH_NORMALS_OUT]["inputs"]["crease_angle"] == 20.0


def test_hard_surface_false_changes_nothing(backend):
    plain = backend.build_graph("pic.png", {}, "forge/x")
    off = backend.build_graph("pic.png", {"hard_surface": False}, "forge/x")
    assert plain == off


def test_hard_surface_never_reaches_the_graph_as_a_node_input(backend):
    assert "hard_surface" in base.NON_GRAPH_OPTIONS
    built = backend.build_graph("pic.png", {"hard_surface": True}, "forge/x")
    for node in built.values():
        assert "hard_surface" not in node["inputs"]


def test_hard_surface_is_reported_in_the_options_that_were_used(backend):
    """The result must say the macro was asked for AND what it expanded to."""
    used = backend.resolved_options({"hard_surface": True})
    assert used["hard_surface"] is True
    for key, value in base.HARD_SURFACE_PRESET.items():
        assert used[key] == value


def test_a_non_boolean_hard_surface_is_refused(backend):
    with pytest.raises(BackendError) as exc:
        backend.resolved_options({"hard_surface": "sort of"})
    assert "boolean" in str(exc.value)


# -- pinning the template ---------------------------------------------------
def test_the_tuned_defaults_reach_the_graph_under_no_options(graph):
    for key, value in base.TUNED_DEFAULTS.items():
        node_ids, field, coerce = base.OPTION_SPEC[key]
        node_ids = (node_ids,) if isinstance(node_ids, str) else node_ids
        for node_id in node_ids:
            assert graph[node_id]["inputs"][field] == coerce(value), (key, node_id)


@pytest.mark.parametrize("template", ["image_to_3d.json", "multiview_to_3d.json"])
def test_the_committed_templates_carry_the_tuned_values(template):
    """A ComfyUI template refresh must not silently restore the demo's numbers.

    ``TUNED_DEFAULTS`` is applied on top of whatever the JSON says, so a refresh
    cannot change behaviour - but it CAN leave the committed graph disagreeing
    with the documented tuning table, and then the next person to read the JSON
    believes the wrong thing.  Re-run tools/build_workflow.py, re-apply these
    values, then tools/build_multiview_workflow.py.
    """
    graph = json.loads((WORKFLOWS / template).read_text(encoding="utf-8"))
    for key, value in base.TUNED_DEFAULTS.items():
        node_ids, field, coerce = base.OPTION_SPEC[key]
        node_ids = (node_ids,) if isinstance(node_ids, str) else node_ids
        for node_id in node_ids:
            assert node_id in graph, (template, key, node_id)
            assert graph[node_id]["inputs"][field] == coerce(value), (template, key,
                                                                     node_id)


@pytest.mark.parametrize("template", ["image_to_3d.json", "multiview_to_3d.json"])
def test_the_tuned_nodes_are_still_the_classes_we_think(template):
    graph = json.loads((WORKFLOWS / template).read_text(encoding="utf-8"))
    assert graph[base.NODE_REMESH]["class_type"] == "RemeshMesh"
    assert graph[base.NODE_SMOOTH_NORMALS_UV]["class_type"] == "MeshSmoothNormals"
    assert graph[base.NODE_SMOOTH_NORMALS_OUT]["class_type"] == "MeshSmoothNormals"
    assert graph[base.NODE_STRUCTURE_SAMPLER]["class_type"] == "KSampler"


def test_both_backends_get_the_same_tuning(backend):
    """The settings are the pipeline's, not the diffusion model's."""
    config = config_module.load()
    pixal = Pixal3DBackend(config, ComfyUIClient(config))
    a = backend.build_graph("pic.png", {}, "forge/x")[base.NODE_REMESH]["inputs"]
    b = pixal.build_graph("pic.png", {}, "forge/x")[base.NODE_REMESH]["inputs"]
    assert a == b


def test_there_is_no_negative_prompt_option():
    """Architecturally absent, and a knob that does nothing is worse than none.

    At the shape stage both backends wire ``negative`` from their conditioning
    node's second output, which is a zero tensor - there is no text conditioning
    to negate.  An option here would be a control the caller could turn and
    watch do nothing, which is the most expensive kind of documentation lie.
    """
    for key in base.OPTION_SPEC:
        assert "negative" not in key and "prompt" not in key


# ==========================================================================
# the metrics
# ==========================================================================
def _cube(n=6, size=(1.0, 1.0, 1.0)):
    """A welded, closed, axis-aligned box: every dihedral is exactly 0 or 90."""
    import numpy as np
    index = {}
    faces = []

    def vid(point):
        key = tuple(round(c, 9) for c in point)
        if key not in index:
            index[key] = len(index)
        return index[key]

    for axis in range(3):
        for sign in (-1, 1):
            u, v = [a for a in range(3) if a != axis]
            grid = [[0] * (n + 1) for _ in range(n + 1)]
            for i in range(n + 1):
                for j in range(n + 1):
                    point = [0.0, 0.0, 0.0]
                    point[axis] = 0.5 * sign * size[axis]
                    point[u] = (-0.5 + i / n) * size[u]
                    point[v] = (-0.5 + j / n) * size[v]
                    grid[i][j] = vid(point)
            for i in range(n):
                for j in range(n):
                    a, b, c, d = grid[i][j], grid[i][j + 1], grid[i + 1][j], grid[i + 1][j + 1]
                    faces += [[a, b, d], [a, d, c]] if sign > 0 else [[a, d, b], [a, c, d]]

    verts = np.zeros((len(index), 3))
    for key, i in index.items():
        verts[i] = key
    return verts, np.asarray(faces, dtype="int64")


def _sphere(nu=48, nv=24):
    import numpy as np
    points = []
    for j in range(nv + 1):
        theta = math.pi * j / nv
        for i in range(nu):
            phi = 2 * math.pi * i / nu
            points.append([math.sin(theta) * math.cos(phi),
                           math.sin(theta) * math.sin(phi), math.cos(theta)])
    faces = []
    for j in range(nv):
        for i in range(nu):
            a, b = j * nu + i % nu, j * nu + (i + 1) % nu
            c, d = (j + 1) * nu + i % nu, (j + 1) * nu + (i + 1) % nu
            faces += [[a, b, d], [a, d, c]]
    return np.asarray(points), np.asarray(faces, dtype="int64")


def test_a_cube_scores_a_perfect_sharp_edge_preservation():
    mm = _numpy_tools()
    verts, faces = _cube()
    stats = mm.dihedrals(mm.normalise(verts), faces)
    assert stats["edge_sharpness"] == pytest.approx(1.0)
    assert stats["dihedral_p99"] == pytest.approx(90.0, abs=1e-6)
    assert stats["soft_edge_fraction"] == pytest.approx(0.0, abs=1e-9)


def test_a_sphere_scores_no_sharp_edges_at_all():
    """The other end of the scale, so the metric is a scale and not a constant."""
    mm = _numpy_tools()
    verts, faces = _sphere()
    stats = mm.dihedrals(mm.normalise(verts), faces)
    assert stats["edge_sharpness"] == pytest.approx(0.0)
    assert stats["dihedral_p99"] < 15.0


def test_a_cube_is_watertight_one_shell_and_free_of_self_intersections():
    mm = _numpy_tools()
    verts, faces = _cube()
    verts = mm.normalise(verts)
    stats = mm.topology(verts, faces)
    assert stats["watertight"] is True
    assert stats["boundary_edges"] == 0
    assert stats["nonmanifold_edges"] == 0
    assert stats["components"] == 1
    assert mm.self_intersections(verts, faces)["self_intersections"] == 0


def test_two_crossing_triangles_are_found_and_neighbours_are_not():
    """Adjacent faces touch by definition - counting those is noise, not clipping."""
    mm = _numpy_tools()
    import numpy as np
    verts = np.array([
        [-1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],      # 0,1,2 flat triangle
        [0.0, 0.3, -1.0], [0.0, 0.3, 1.0], [0.5, 0.9, 0.4],      # 3,4,5 skewers it
        [0.0, -0.5, 0.8],                                        # 6 folds off edge 0-1
    ])
    faces = np.array([[0, 1, 2], [3, 4, 5], [0, 1, 6]], dtype="int64")
    result = mm.self_intersections(verts, faces)
    assert result["self_intersections"] == 1

    # the neighbour alone must score zero: it really does touch face 0, along
    # the edge they share, and that is what every closed mesh looks like
    assert mm.self_intersections(verts, faces[[0, 2]])["self_intersections"] == 0


def test_zero_area_faces_are_not_counted_as_perfect_creases():
    """A sliver has no normal; dividing by zero hands it a flawless 90 degrees."""
    mm = _numpy_tools()
    import numpy as np
    verts, faces = _cube()
    verts = mm.normalise(verts)
    # a degenerate fan at one existing vertex - zero area, three "edges"
    extra = np.array([[faces[0][0], faces[0][0], faces[0][1]]], dtype="int64")
    stats = mm.dihedrals(verts, np.vstack([faces, extra]))
    assert stats["edge_sharpness"] == pytest.approx(1.0)


def test_welding_recovers_the_surface_a_uv_split_export_hides():
    """Every crease routed along a UV seam would otherwise vanish from the stats."""
    mm = _numpy_tools()
    import numpy as np
    verts, faces = _cube()
    verts = mm.normalise(verts)
    # explode into unshared triangles, exactly what an unwrap-then-export does
    exploded_verts = verts[faces].reshape(-1, 3)
    exploded_faces = np.arange(exploded_verts.shape[0], dtype="int64").reshape(-1, 3)
    assert mm.topology(exploded_verts, exploded_faces)["boundary_edges"] > 0

    welded_v, welded_f, _kept = mm.weld(exploded_verts, exploded_faces)
    stats = mm.topology(welded_v, welded_f)
    assert stats["watertight"] is True
    assert stats["components"] == 1
    assert mm.dihedrals(welded_v, welded_f)["edge_sharpness"] == pytest.approx(1.0)


def test_the_silhouette_of_a_slab_matches_its_own_analytic_mask():
    mm = _numpy_tools()
    pytest.importorskip("PIL")
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.125, 0.125, -0.25, 0.25, (200, 200, 200))]
    camera = ab_fixtures.camera_for(scene)
    _rgb, mask = ab_fixtures.render(scene, camera)

    verts, faces = _cube(n=6, size=(1.0, 0.25, 0.5))
    result = mm.silhouette(mm.normalise(verts), faces, camera, mask)
    assert result["silhouette_iou"] > 0.99
    assert result["splat_holes"] == 0


def test_a_wrong_shape_cannot_be_aligned_into_looking_right():
    """The 48-orientation search relabels axes; it does not fit a free rotation."""
    mm = _numpy_tools()
    pytest.importorskip("PIL")
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.125, 0.125, -0.25, 0.25, (200, 200, 200))]
    camera = ab_fixtures.camera_for(scene)
    _rgb, mask = ab_fixtures.render(scene, camera)

    verts, faces = _sphere()
    result = mm.silhouette(mm.normalise(verts), faces, camera, mask)
    assert result["silhouette_iou"] < 0.75


def test_the_orientation_search_finds_a_swapped_axis(monkeypatch):
    """A y/z swap is what a glTF Y-up export actually does to these meshes."""
    mm = _numpy_tools()
    pytest.importorskip("PIL")
    import numpy as np
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.125, 0.125, -0.25, 0.25, (200, 200, 200))]
    camera = ab_fixtures.camera_for(scene)
    _rgb, mask = ab_fixtures.render(scene, camera)

    verts, faces = _cube(n=6, size=(1.0, 0.25, 0.5))
    swapped = mm.normalise(verts)[:, [0, 2, 1]]
    result = mm.silhouette(swapped, faces, camera, mask)
    assert result["silhouette_iou"] > 0.99
    assert list(result["orientation"][0]) == [0, 2, 1]


def test_score_reads_a_real_glb_end_to_end(tmp_path):
    mm = _numpy_tools()
    pytest.importorskip("PIL")
    from meshgen.tests.fake_backend import write_glb
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.125, 0.125, -0.25, 0.25, (200, 200, 200))]
    camera = ab_fixtures.camera_for(scene)
    _rgb, mask = ab_fixtures.render(scene, camera)

    verts, faces = _cube(n=6, size=(1.0, 0.25, 0.5))
    path = write_glb(tmp_path / "slab.glb", verts, faces)
    result = mm.score(path, camera, mask)
    assert result["silhouette_iou"] > 0.99
    assert result["edge_sharpness"] == pytest.approx(1.0)
    assert result["watertight"] is True
    assert result["faces"] == faces.shape[0]
    assert result["self_intersections"] == 0


def test_score_reports_the_uv_split_as_well_as_the_welded_truth(tmp_path):
    mm = _numpy_tools()
    pytest.importorskip("PIL")
    import numpy as np
    from meshgen.tests.fake_backend import write_glb
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.125, 0.125, -0.25, 0.25, (200, 200, 200))]
    camera = ab_fixtures.camera_for(scene)
    _rgb, mask = ab_fixtures.render(scene, camera)

    verts, faces = _cube(n=6, size=(1.0, 0.25, 0.5))
    exploded_v = mm.normalise(verts)[faces].reshape(-1, 3)
    exploded_f = np.arange(exploded_v.shape[0], dtype="int64").reshape(-1, 3)
    path = write_glb(tmp_path / "split.glb", exploded_v, exploded_f)

    result = mm.score(path, camera, mask)
    assert result["exported_verts"] == exploded_v.shape[0]
    assert result["uv_split_ratio"] > 1.0
    assert result["watertight"] is True            # the welded truth
    assert result["boundary_edges"] == 0


# ==========================================================================
# the fixtures and the runner
# ==========================================================================
def test_the_fixture_camera_is_the_rig_at_elevation_zero():
    """Not a second rig that happens to look similar - the same one, extended."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import numpy as np
    from meshgen.backends import multiview
    from meshgen.tools import ab_fixtures, make_rig_views

    distance = multiview.distance_for_fov(multiview.DEFAULT_FOV_DEG)
    for azimuth in multiview.CORE_VIEW_AZIMUTHS.values():
        mine = ab_fixtures.camera_at(azimuth, 0.0, distance)
        theirs = make_rig_views.camera_basis(azimuth, distance)
        for a, b in zip(mine, theirs):
            assert np.allclose(np.asarray(a, dtype=float),
                               np.asarray(b, dtype=float), atol=1e-12)


def test_the_fixtures_are_byte_for_byte_deterministic(tmp_path):
    """An A/B difference has to be the setting, so the input cannot wobble."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_fixtures

    first = ab_fixtures.write_scene("hard_steps", tmp_path / "a")[0]
    second = ab_fixtures.write_scene("hard_steps", tmp_path / "b")[0]
    assert first.read_bytes() == second.read_bytes()


def test_every_scene_is_framed_inside_its_own_render():
    """A clipped fixture would score a silhouette IoU against a cropped truth."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import numpy as np
    from meshgen.tools import ab_fixtures

    for scene in ab_fixtures.SCENES:
        _rgb, mask = ab_fixtures.render(scene)
        assert mask.any(), scene
        assert not mask[0, :].any() and not mask[-1, :].any(), scene
        assert not mask[:, 0].any() and not mask[:, -1].any(), scene
        # and it fills the frame rather than sitting as a dot in the middle
        assert mask.mean() > 0.05, scene


def test_the_scenes_know_their_own_true_crease_length():
    """Without the anchor, "sharper" and "jaggier" are the same reading."""
    pytest.importorskip("numpy")
    from meshgen.tools import ab_fixtures

    for scene in ab_fixtures.HARD_SURFACE_SCENES:
        assert ab_fixtures.true_crease_length(scene) > 0.0
    with pytest.raises(ValueError):
        ab_fixtures.true_crease_length("organic_blob")


def test_a_cube_never_exceeds_its_own_true_crease_length():
    """The bound really is a bound: an exact box cannot measure above it."""
    mm = _numpy_tools()
    from meshgen.tools import ab_fixtures

    scene = [ab_fixtures.box(-0.5, 0.5, -0.5, 0.5, -0.5, 0.5, (200, 200, 200))]
    verts, faces = _cube(n=8)
    measured = mm.dihedrals(mm.normalise(verts), faces)["crease_length"]
    assert measured <= ab_fixtures.true_crease_length(scene) + 1e-6


def test_the_hard_surface_scenes_really_are_hard_surfaces():
    """The dihedral metrics only mean something where the truth is 90 degrees."""
    pytest.importorskip("numpy")
    from meshgen.tools import ab_fixtures

    for scene in ab_fixtures.HARD_SURFACE_SCENES:
        assert all(s["kind"] == "box" for s in ab_fixtures.SCENES[scene]), scene
    assert all(s["kind"] == "ellipsoid" for s in ab_fixtures.SCENES["organic_blob"])


def test_every_variant_option_is_a_real_option():
    """A typo in a variant would run, cost GPU minutes and change nothing."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_tuning

    for variant in ab_tuning.VARIANTS + ab_tuning.STACKS:
        for key in variant["options"]:
            assert key in base.OPTION_SPEC, (variant["name"], key)


def test_every_variant_states_all_five_knobs():
    """A row has to stay readable after the defaults it was measured against move."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_tuning

    for variant in ab_tuning.VARIANTS + ab_tuning.STACKS:
        for key in ("smooth_iters", "qef", "project_back", "crease_angle",
                    "steps", "cfg", "seed"):
            assert key in variant["options"], (variant["name"], key)


def test_the_sweep_covers_every_knob_under_test():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_tuning

    groups = {v["group"] for v in ab_tuning.VARIANTS}
    assert {"smooth_iters", "qef", "project_back", "crease_angle",
            "sampler", "baseline"} <= groups


def test_the_baseline_variant_is_the_template_not_the_new_defaults():
    """Comparing the winners against themselves would prove nothing."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_tuning

    template = json.loads((WORKFLOWS / "image_to_3d.json").read_text(encoding="utf-8"))
    baseline = next(v for v in ab_tuning.VARIANTS if v["name"] == "baseline")
    assert baseline["options"]["smooth_iters"] == 20
    assert baseline["options"]["qef"] is False
    assert baseline["options"]["project_back"] == 0.0
    assert baseline["options"]["crease_angle"] == 180.0
    # and it must differ from what meshgen now ships, or there was no decision
    assert any(base.TUNED_DEFAULTS.get(k) not in (None, v)
               for k, v in baseline["options"].items())
    assert template[base.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"] == \
        baseline["options"]["seed"]


def test_the_runner_says_why_rather_than_failing_when_meshgen_is_absent():
    """Same contract as the rest of the suite: no GPU, no models, no failure."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_tuning

    ok, reason = ab_tuning.availability("definitely-not-a-backend")
    assert ok is False
    assert reason and isinstance(reason, str)


def test_the_plan_runs_the_full_sweep_on_a_hard_surface_scene():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from meshgen.tools import ab_fixtures, ab_tuning

    rows = ab_tuning.plan()
    assert ab_tuning.SWEEP_SCENE in ab_fixtures.HARD_SURFACE_SCENES
    sweep = [v["name"] for scene, v in rows if scene == ab_tuning.SWEEP_SCENE]
    assert sweep == [v["name"] for v in ab_tuning.VARIANTS + ab_tuning.STACKS]
    # and the organic control is there, or a hard-surface win could cost
    # something on round shapes and nobody would know
    assert any(scene == "organic_blob" for scene, _v in rows)
