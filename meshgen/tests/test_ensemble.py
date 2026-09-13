"""Tests for the seed ensemble: the probe graph, the pickers, the request surface.

Three things can go wrong independently here, and each would fail silently:

**The probe graph.**  It is a *pruning* of the real template by reachability from
the structure decode node, so a mistake does not raise - it produces a graph that
runs and samples something slightly different from what the full run will sample,
and every consensus number computed on top of it is then about the wrong thing.
Covered by walking the pruned graph: no dangling link, the sampler and its whole
dependency chain still present, the expensive tail gone, the tail nodes added.

**The pickers.**  A verifier that is itself wrong is worse than none: it produces
a winner, and the winner is believed.  So the scorers are tested against grids
and meshes whose answer is known by construction - a grid the others agree with
*must* be the medoid; a grid that contradicts the drawing *must* be rejected
*before* the consensus vote; a gate *must never* be able to empty the field; and
the answer *must not* depend on the order the candidates arrived in.

**The inverse of ``VoxelToMeshBasic``.**  ``occupancy_from_cube_mesh`` claims to
recover core's dense boolean grid exactly.  It is tested against a numpy
transcription of core's own ``voxel_to_mesh`` (``comfy_extras/nodes_hunyuan3d.py``)
over random grids *with a solid interior block*, because the interior is what the
naive face-set reading loses and what the parity fill exists to recover.

Everything here runs with none of the 23.8 GB present and no GPU.  The GPU half
is ``tools/ab_ensemble.py``, a manual gate like the rest of the real-model runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("FORGE_MESHGEN_BACKEND_MODULES", "meshgen.tests.fake_backend")

from meshgen import config as config_module  # noqa: E402
from meshgen import service  # noqa: E402
from meshgen.backends import base as backend_base  # noqa: E402
from meshgen.backends import comfyui_base as cbase  # noqa: E402
from meshgen.backends import structure_probe as probe  # noqa: E402
from meshgen.backends.base import BackendError  # noqa: E402
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend  # noqa: E402
from meshgen.backends.comfyui_trellis2 import Trellis2Backend  # noqa: E402
from meshgen.comfyui_client import ComfyUIClient  # noqa: E402
from meshgen.tests.test_service import (Client, config_file, image,  # noqa: E402,F401
                                        live)

np = pytest.importorskip("numpy")
ensemble = pytest.importorskip("meshgen.ensemble")


@pytest.fixture
def backend():
    config = config_module.load()
    return Trellis2Backend(config, ComfyUIClient(config))


# ==========================================================================
# core's VoxelToMeshBasic, transcribed - the fixture the inverse is tested on
# ==========================================================================
#: Straight from ``comfy_extras/nodes_hunyuan3d.py:voxel_to_mesh``, in numpy.
#: Transcribed rather than imported because importing it would pull torch and the
#: whole ComfyUI tree into a suite that must run on a machine with neither - and
#: because a transcription that has drifted fails these tests loudly, which is
#: the outcome we want when core changes the export.
_NEIGHBOURS = np.array([[0, 0, 1], [0, 0, -1], [0, 1, 0],
                        [0, -1, 0], [1, 0, 0], [-1, 0, 0]])
_CORNERS = [
    np.array([[0, 0, 1], [0, 1, 1], [1, 1, 1], [1, 0, 1]]),
    np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
    np.array([[0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1]]),
    np.array([[0, 0, 0], [0, 0, 1], [1, 0, 1], [1, 0, 0]]),
    np.array([[1, 0, 1], [1, 1, 1], [1, 1, 0], [1, 0, 0]]),
    np.array([[0, 1, 0], [0, 1, 1], [0, 0, 1], [0, 0, 0]]),
]


def cube_mesh(grid):
    """``(verts, faces)`` exactly as core would export ``grid``."""
    binary = np.asarray(grid, dtype=float)
    padded = np.pad(binary, 1)
    D, H, W = binary.shape
    z, y, x = np.meshgrid(np.arange(D), np.arange(H), np.arange(W), indexing="ij")
    cells = np.stack([z.ravel(), y.ravel(), x.ravel()], axis=1)
    solid = cells[binary.ravel() > 0]

    verts, faces, count = [], [], 0
    for index, offset in enumerate(_NEIGHBOURS):
        padded_index = solid + offset + 1
        exposed = padded[padded_index[:, 0], padded_index[:, 1],
                         padded_index[:, 2]] == 0
        if not exposed.any():
            continue
        cell = solid[exposed]
        verts.append((cell[:, None, :] + _CORNERS[index][None]).reshape(-1, 3))
        quads = np.arange(count, count + 4 * cell.shape[0]).reshape(-1, 4)
        faces.append(np.stack([quads[:, 0], quads[:, 1], quads[:, 2]], axis=1))
        faces.append(np.stack([quads[:, 0], quads[:, 2], quads[:, 3]], axis=1))
        count += 4 * cell.shape[0]

    v = np.concatenate(verts).astype(float)
    side = max(binary.shape)
    v = np.fliplr((v - side / 2.0) / (side / 2.0))
    return v, np.concatenate(faces)


def solid_box(resolution, lo, hi):
    grid = np.zeros((resolution,) * 3, dtype=bool)
    grid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] = True
    return grid


# ==========================================================================
# the inverse of VoxelToMeshBasic
# ==========================================================================
@pytest.mark.parametrize("trial", range(5))
def test_the_grid_round_trips_through_core_s_cube_mesh_exactly(trial):
    rng = np.random.default_rng(trial)
    grid = rng.random((8, 8, 8)) > 0.62
    # a solid block: its interior voxels have NO exposed face, so a reader that
    # only looks at the face set loses them.  The parity fill is what recovers
    # them, and without this block the test would pass while being wrong.
    grid[2:6, 2:6, 2:6] = True
    verts, faces = cube_mesh(grid)
    assert np.array_equal(ensemble.occupancy_from_cube_mesh(verts, faces, 8), grid)


def test_a_single_voxel_round_trips_at_the_right_index():
    grid = np.zeros((4, 4, 4), dtype=bool)
    grid[1, 2, 3] = True
    verts, faces = cube_mesh(grid)
    back = ensemble.occupancy_from_cube_mesh(verts, faces, 4)
    assert np.array_equal(back, grid)
    # and it is indexed the way core indexed it, not the way the exporter's
    # fliplr wrote it out
    assert list(np.argwhere(back)[0]) == [1, 2, 3]


def test_a_hollow_shell_keeps_its_hollow():
    grid = solid_box(8, (1, 1, 1), (7, 7, 7))
    grid[3:5, 3:5, 3:5] = False
    verts, faces = cube_mesh(grid)
    assert np.array_equal(ensemble.occupancy_from_cube_mesh(verts, faces, 8), grid)


def test_a_mesh_off_the_lattice_is_refused_rather_than_rounded():
    verts, faces = cube_mesh(solid_box(8, (2, 2, 2), (5, 5, 5)))
    with pytest.raises(ensemble.EnsembleError) as exc:
        ensemble.occupancy_from_cube_mesh(verts * 1.37, faces, 8)
    assert "off the" in str(exc.value)


def test_reading_a_grid_at_the_wrong_resolution_is_refused_somehow():
    """Doubling the resolution keeps every vertex ON the finer lattice, so the
    cheap residual check cannot see it - the three-axis agreement check is what
    catches this one, and it must."""
    verts, faces = cube_mesh(solid_box(8, (2, 2, 2), (5, 5, 5)))
    with pytest.raises(ensemble.EnsembleError):
        ensemble.occupancy_from_cube_mesh(verts, faces, 16)


def test_an_empty_structure_grid_is_an_error_not_an_empty_answer():
    with pytest.raises(ensemble.EnsembleError):
        ensemble.occupancy_from_cube_mesh(np.zeros((0, 3)), np.zeros((0, 3), int), 8)


# ==========================================================================
# grid agreement and the medoid
# ==========================================================================
def test_grid_iou_is_one_for_identical_and_zero_for_disjoint():
    a = solid_box(8, (0, 0, 0), (4, 4, 4))
    b = solid_box(8, (4, 4, 4), (8, 8, 8))
    assert ensemble.grid_iou(a, a) == 1.0
    assert ensemble.grid_iou(a, b) == 0.0


def test_agreement_excludes_the_diagonal():
    """With the diagonal in, every candidate carries a free 1.0 and an N of 2
    cannot be separated at all."""
    matrix = np.array([[1.0, 0.2], [0.2, 1.0]])
    assert list(ensemble.agreement(matrix)) == pytest.approx([0.2, 0.2])


def test_the_medoid_is_the_grid_the_others_agree_with():
    base = solid_box(8, (1, 1, 1), (6, 6, 6))
    near_a = base.copy(); near_a[1, 1, 1] = False
    near_b = base.copy(); near_b[5, 5, 5] = False
    outlier = solid_box(8, (0, 0, 0), (2, 2, 2))
    report = ensemble.select_structure([
        {"seed": 10, "grid": near_a},
        {"seed": 11, "grid": base},
        {"seed": 12, "grid": near_b},
        {"seed": 13, "grid": outlier},
    ])
    assert report["winner"]["seed"] == 11
    assert "medoid" in report["why"]


def test_the_pick_is_the_same_whatever_order_the_candidates_arrive_in():
    grids = {10: solid_box(8, (1, 1, 1), (6, 6, 6))}
    grids[11] = grids[10].copy(); grids[11][1, 1, 1] = False
    grids[12] = grids[10].copy(); grids[12][5, 5, 5] = False
    forward = [{"seed": s, "grid": grids[s]} for s in (10, 11, 12)]
    winners = {ensemble.select_structure(forward)["winner"]["seed"],
               ensemble.select_structure(forward[::-1])["winner"]["seed"]}
    assert len(winners) == 1


def test_identical_candidates_tie_break_on_the_lowest_seed():
    grid = solid_box(8, (1, 1, 1), (6, 6, 6))
    report = ensemble.select_structure(
        [{"seed": 99, "grid": grid.copy()}, {"seed": 7, "grid": grid.copy()},
         {"seed": 40, "grid": grid.copy()}])
    assert report["winner"]["seed"] == 7


# ==========================================================================
# the silhouette gate
# ==========================================================================
def _mask_of(grid, axis=0):
    """The drawing a grid would have produced, at the resolution an image has."""
    flat = np.asarray(grid, dtype=bool).any(axis=axis)
    return np.repeat(np.repeat(flat, 16, axis=0), 16, axis=1)


def test_a_grid_that_contradicts_the_drawing_is_rejected_before_the_consensus():
    """Two bad draws that agree with each other must not outvote the good ones -
    which is exactly what happens if the gate runs after the medoid."""
    tall = solid_box(16, (2, 2, 6), (14, 14, 10))     # the thing in the picture
    mask = _mask_of(tall, axis=0)

    wrong_a = solid_box(16, (6, 2, 2), (10, 14, 14))  # same volume, wrong shape
    wrong_b = wrong_a.copy(); wrong_b[6, 2, 2] = False
    nearly = tall.copy(); nearly[2, 2, 6] = False

    report = ensemble.select_structure([
        {"seed": 1, "grid": wrong_a},
        {"seed": 2, "grid": wrong_b},
        {"seed": 3, "grid": tall},
        {"seed": 4, "grid": nearly},
    ], mask=mask)
    rejected = {entry["seed"] for entry in report["rejected"]}
    assert {1, 2} <= rejected
    assert report["winner"]["seed"] in (3, 4)


def test_the_silhouette_gate_never_leaves_fewer_than_two_survivors():
    """A gate that can empty the field decides the answer by itself."""
    grids = [solid_box(8, (0, 0, 0), (8, 8, 8)),
             solid_box(8, (0, 0, 0), (1, 1, 1)),
             solid_box(8, (7, 7, 7), (8, 8, 8))]
    mask = _mask_of(grids[0])
    report = ensemble.select_structure(
        [{"seed": i, "grid": g} for i, g in enumerate(grids)], mask=mask)
    assert len(report["compared_seeds"]) >= 2


def test_every_candidate_is_scored_under_one_shared_labelling():
    """Candidates read different ways round are not comparable."""
    grid = solid_box(16, (2, 2, 6), (14, 14, 10))
    scores, label = ensemble.silhouette_agreement([grid, grid.copy()],
                                                  _mask_of(grid))
    assert scores[0] == scores[1]
    assert len(label) == 3
    report = ensemble.select_structure(
        [{"seed": 1, "grid": grid}, {"seed": 2, "grid": grid.copy()}],
        mask=_mask_of(grid))
    assert report["silhouette_labelling"] is not None


def test_the_mask_normaliser_keeps_proportion_and_drops_scale():
    small = np.zeros((10, 10), dtype=bool); small[2:8, 4:6] = True
    big = np.zeros((100, 100), dtype=bool); big[20:80, 40:60] = True
    assert np.array_equal(ensemble.normalise_mask(small, 32),
                          ensemble.normalise_mask(big, 32))


def test_a_report_without_a_mask_says_the_gate_did_not_run():
    grid = solid_box(8, (1, 1, 1), (6, 6, 6))
    report = ensemble.select_structure(
        [{"seed": 1, "grid": grid}, {"seed": 2, "grid": grid.copy()}])
    assert report["rejected"] == []
    assert report["silhouette_labelling"] is None


# ==========================================================================
# Chamfer
# ==========================================================================
def _tetra():
    verts = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.0, 1, 0], [0.0, 0, 1]])
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])
    return verts, faces


def test_sampling_the_same_mesh_twice_gives_the_same_points():
    verts, faces = _tetra()
    a = ensemble.sample_surface(verts, faces, 256)
    b = ensemble.sample_surface(verts, faces, 256)
    assert np.array_equal(a, b)


def test_chamfer_of_a_point_set_against_itself_is_zero_and_symmetric():
    verts, faces = _tetra()
    points = ensemble.sample_surface(verts, faces, 256)
    moved = points + np.array([0.5, 0.0, 0.0])
    assert ensemble.symmetric_chamfer(points, points) == 0.0
    assert ensemble.symmetric_chamfer(points, moved) == pytest.approx(
        ensemble.symmetric_chamfer(moved, points))
    assert ensemble.symmetric_chamfer(points, moved) > 0.0


def test_the_chamfer_consensus_picks_the_middle_of_three():
    verts, faces = _tetra()
    points = ensemble.sample_surface(verts, faces, 256)
    sets = [points, points + [0.4, 0, 0], points + [0.8, 0, 0]]
    consensus = ensemble.chamfer_consensus(ensemble.chamfer_matrix(sets))
    assert int(np.argmin(consensus)) == 1


def test_area_weighting_is_what_makes_the_sample_a_sample_of_the_surface():
    """A finely tessellated small panel must not outvote a coarse big one."""
    verts = np.array([[0.0, 0, 0], [10.0, 0, 0], [0.0, 10, 0],   # big triangle
                      [0.0, 0, 5], [0.1, 0, 5], [0.0, 0.1, 5]])  # tiny one
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    points = ensemble.sample_surface(verts, faces, 2000)
    on_tiny = int(np.count_nonzero(points[:, 2] > 4.9))
    assert on_tiny < 20          # area ratio is ~1:10000


# ==========================================================================
# the best_of picker: gates first, then rank
# ==========================================================================
def _candidate(seed, iou=0.97, selfx=0, shells=10, faces=200000, sharp=0.2,
               points=None):
    return {
        "seed": seed,
        "mesh_path": f"c{seed}.glb",
        "metrics": {"silhouette_iou": iou, "self_intersections": selfx,
                    "components": shells, "faces": faces,
                    "nonmanifold_edges": 210, "edge_sharpness": sharp,
                    "crease_length": 15.0},
        "points": points,
    }


def test_a_candidate_that_produced_no_mesh_is_dropped_first():
    broken = _candidate(2)
    broken["metrics"] = {}
    report = ensemble.select_mesh([_candidate(1), broken, _candidate(3)])
    assert 2 in {r["seed"] for r in report["rejected"]}
    assert report["winner"]["seed"] in (1, 3)


def test_the_topology_gates_run_before_the_ranking():
    """The clipped candidate has the best silhouette and must still lose."""
    report = ensemble.select_mesh([
        _candidate(1, iou=0.99, selfx=13391, shells=465),
        _candidate(2, iou=0.95),
        _candidate(3, iou=0.94),
    ])
    assert 1 in {r["seed"] for r in report["rejected"]}
    assert report["winner"]["seed"] == 2


def test_the_gates_can_never_empty_the_field():
    """The limit is anchored on the best in the batch, so the best always
    survives however bad the whole batch is."""
    report = ensemble.select_mesh([
        _candidate(1, selfx=9000, shells=400),
        _candidate(2, selfx=9100, shells=402),
    ])
    assert len(report["compared_seeds"]) == 2
    assert report["winner"]["seed"] in (1, 2)


def test_a_single_survivor_wins_and_the_report_admits_there_was_no_consensus():
    report = ensemble.select_mesh([
        _candidate(1, selfx=10, shells=6),
        _candidate(2, selfx=9000, shells=400),
        _candidate(3, selfx=9100, shells=402),
    ])
    assert report["winner"]["seed"] == 1
    assert report["compared_seeds"] == [1]
    assert "nothing to take a consensus over" in report["why"]


def test_the_consensus_medoid_decides_when_the_silhouettes_are_the_same():
    """Measured reason for rounding the IoU: across the whole A/B sweep it moved
    only 0.9763 -> 0.9801 while the meshes differed enormously."""
    verts, faces = _tetra()
    points = ensemble.sample_surface(verts, faces, 256)
    report = ensemble.select_mesh([
        _candidate(1, iou=0.9763, points=points + [0.8, 0, 0]),
        _candidate(2, iou=0.9767, points=points + [0.4, 0, 0]),
        _candidate(3, iou=0.9761, points=points),
    ])
    assert report["winner"]["seed"] == 2
    assert "medoid" in report["why"]


def test_a_clearly_better_silhouette_still_wins_over_the_medoid():
    verts, faces = _tetra()
    points = ensemble.sample_surface(verts, faces, 256)
    report = ensemble.select_mesh([
        _candidate(1, iou=0.60, points=points + [0.8, 0, 0]),
        _candidate(2, iou=0.61, points=points + [0.4, 0, 0]),
        _candidate(3, iou=0.95, points=points),
    ])
    assert report["winner"]["seed"] == 3


def test_every_candidate_is_reported_whether_it_won_or_not():
    report = ensemble.select_mesh([_candidate(1), _candidate(2)])
    assert [c["seed"] for c in report["candidates"]] == [1, 2]
    assert all("silhouette_iou" in c for c in report["candidates"])


# ==========================================================================
# the probe graph
# ==========================================================================
def test_the_probe_keeps_the_sampler_chain_and_drops_the_expensive_tail(backend):
    full = backend.build_graph("pic.png", {}, "forge/x")
    pruned = probe.build_structure_graph(full, "forge/x_probe")

    for node_id in (cbase.NODE_STRUCTURE_SAMPLER, probe.NODE_STRUCTURE_DECODE,
                    cbase.NODE_LOAD_IMAGE, cbase.NODE_TRELLIS2_SWITCH):
        assert node_id in pruned, node_id
    # the two-to-four minute half, gone
    for node_id in (cbase.NODE_UPSAMPLE, cbase.NODE_REMESH, cbase.NODE_DECIMATE,
                    cbase.NODE_UNWRAP, cbase.NODE_SAVE,
                    cbase.NODE_SMOOTH_NORMALS_OUT):
        assert node_id not in pruned, node_id
    assert len(pruned) < len(full)


def test_the_probe_graph_has_no_dangling_link(backend):
    pruned = probe.build_structure_graph(
        backend.build_graph("pic.png", {}, "forge/x"), "forge/x_probe")
    for node_id, node in pruned.items():
        for field, value in (node.get("inputs") or {}).items():
            if probe.is_link(value):
                assert value[0] in pruned, f"{node_id}.{field} -> {value[0]}"


def test_the_probe_tail_saves_a_grid_mesh_and_the_prepared_mask(backend):
    pruned = probe.build_structure_graph(
        backend.build_graph("pic.png", {}, "forge/x"), "forge/x_probe")
    assert pruned[probe.NODE_VOXEL_TO_MESH]["class_type"] == "VoxelToMeshBasic"
    assert pruned[probe.NODE_VOXEL_TO_MESH]["inputs"]["voxel"] == \
        [probe.NODE_STRUCTURE_DECODE, 0]
    assert pruned[probe.NODE_VOXEL_SAVE]["class_type"] == "SaveGLB"
    assert pruned[probe.NODE_MASK_SAVE]["class_type"] == "SaveImage"
    # the mask goes through the SAME crop node class the model's image did
    assert pruned[probe.NODE_MASK_CROP_CLONE]["class_type"] == "ImageCropToMask"
    assert pruned[probe.NODE_MASK_CROP_CLONE]["inputs"]["pad_factor"] == \
        pruned[probe.NODE_MASK_CROP]["inputs"]["pad_factor"]


def test_the_probe_carries_the_caller_s_options_into_the_sampler(backend):
    full = backend.build_graph("pic.png", {"seed": 4242, "steps": 20, "cfg": 6.0},
                               "forge/x")
    pruned = probe.build_structure_graph(full, "forge/x_probe")
    sampler = pruned[cbase.NODE_STRUCTURE_SAMPLER]["inputs"]
    assert sampler["seed"] == 4242 and sampler["steps"] == 20 and sampler["cfg"] == 6.0


@pytest.mark.parametrize("resolution", ["32", "64"])
def test_the_grid_resolution_reaches_the_decode_node(backend, resolution):
    pruned = probe.build_structure_graph(
        backend.build_graph("pic.png", {}, "forge/x"), "forge/x_probe",
        resolution=resolution)
    assert pruned[probe.NODE_STRUCTURE_DECODE]["inputs"]["resolution"] == resolution


def test_an_unsupported_grid_resolution_is_refused_with_the_real_choices(backend):
    with pytest.raises(BackendError) as exc:
        probe.build_structure_graph(
            backend.build_graph("pic.png", {}, "forge/x"), "forge/x", resolution=128)
    assert "32" in str(exc.value) and "64" in str(exc.value)


def test_a_moved_structure_decode_node_fails_loudly(backend):
    full = backend.build_graph("pic.png", {}, "forge/x")
    full[probe.NODE_STRUCTURE_DECODE]["class_type"] = "SomethingElse"
    with pytest.raises(BackendError) as exc:
        probe.build_structure_graph(full, "forge/x")
    assert "structure_probe.py" in str(exc.value)


def test_a_tail_id_that_collides_with_a_template_node_is_refused(backend):
    full = backend.build_graph("pic.png", {}, "forge/x")
    full[probe.NODE_VOXEL_SAVE] = {"class_type": "PrimitiveInt", "inputs": {}}
    with pytest.raises(BackendError) as exc:
        probe.build_structure_graph(full, "forge/x")
    assert "collides" in str(exc.value)


def test_the_mask_only_graph_has_no_sampler_in_it_at_all(backend):
    mask_graph = probe.build_mask_graph(
        backend.build_graph("pic.png", {}, "forge/x"), "forge/x_probe")
    classes = {node["class_type"] for node in mask_graph.values()}
    assert "KSampler" not in classes and "UNETLoader" not in classes
    assert probe.NODE_MASK_SAVE in mask_graph


def test_the_probe_prunes_the_multi_view_template_too():
    config = config_module.load()
    pixal = Pixal3DBackend(config, ComfyUIClient(config))
    graph = json.loads(
        (Path(__file__).resolve().parent.parent / "workflows"
         / "multiview_to_3d.json").read_text(encoding="utf-8"))
    pruned = probe.build_structure_graph(graph, "forge/mv_probe")
    assert probe.NODE_VOXEL_SAVE in pruned
    assert cbase.NODE_SAVE not in pruned
    for node_id, node in pruned.items():
        for value in (node.get("inputs") or {}).values():
            if probe.is_link(value):
                assert value[0] in pruned, node_id
    assert pixal.name == "pixal3d"


# ==========================================================================
# the request surface
# ==========================================================================
def test_no_ensemble_block_means_one_seed(backend):
    assert backend.resolved_ensemble({}) == {"structure_n": 1, "best_of": 1}
    assert backend.resolved_ensemble({"ensemble": {}}) == backend.resolved_ensemble({})


def test_ensemble_is_a_non_graph_option_and_never_reaches_a_node(backend):
    assert "ensemble" in cbase.NON_GRAPH_OPTIONS
    built = backend.build_graph("pic.png", {"ensemble": {"structure_n": 5}}, "forge/x")
    assert all("ensemble" not in (n.get("inputs") or {}) for n in built.values())


@pytest.mark.parametrize("raw", [5, "5", [5], True])
def test_an_ensemble_that_is_not_an_object_is_refused(backend, raw):
    with pytest.raises(BackendError) as exc:
        backend.resolved_ensemble({"ensemble": raw})
    assert "object" in str(exc.value)


def test_an_unknown_ensemble_key_is_refused_with_the_supported_list(backend):
    with pytest.raises(BackendError) as exc:
        backend.resolved_ensemble({"ensemble": {"n": 5}})
    assert "structure_n" in str(exc.value) and "best_of" in str(exc.value)


@pytest.mark.parametrize("value", [1.5, "5", None, True, False])
def test_a_non_integer_candidate_count_is_refused(backend, value):
    with pytest.raises(BackendError) as exc:
        backend.resolved_ensemble({"ensemble": {"structure_n": value}})
    assert "integer" in str(exc.value)


@pytest.mark.parametrize("key,value,low,high", [
    ("structure_n", 0, 1, 9), ("structure_n", 12, 1, 9),
    ("best_of", 0, 1, 5), ("best_of", 9, 1, 5),
])
def test_an_out_of_range_count_is_refused_with_the_real_range(backend, key, value,
                                                              low, high):
    """Refused, never clamped - a clamped ensemble is a run that never happened."""
    with pytest.raises(BackendError) as exc:
        backend.resolved_ensemble({"ensemble": {key: value}})
    assert f"{low} to {high}" in str(exc.value)


def test_both_tiers_at_once_is_refused_with_the_reason(backend):
    with pytest.raises(BackendError) as exc:
        backend.resolved_ensemble({"ensemble": {"structure_n": 5, "best_of": 3}})
    assert "Pick a tier" in str(exc.value)


def test_the_seeds_are_the_base_seed_and_its_successors():
    assert backend_base.ensemble_seeds(56, 5) == [56, 57, 58, 59, 60]
    assert backend_base.ensemble_seeds(0, 1) == [0]


# ==========================================================================
# staging is split from building, and single-image behaviour is unchanged
# ==========================================================================
def test_staging_once_feeds_many_graphs(backend, monkeypatch, tmp_path):
    """An ensemble must not restage per candidate: a fresh LoadImage filename
    misses ComfyUI's execution cache and re-runs background removal and the
    image encoder for every seed."""
    calls = []
    monkeypatch.setattr(backend.client, "stage_image",
                        lambda path: (calls.append(path), "staged.png")[1])
    staged, names = backend.stage_inputs(str(tmp_path / "pic.png"))
    graphs = [backend.graph_for(staged, {"seed": seed}, "forge/x")
              for seed in (1, 2, 3)]
    assert len(calls) == 1
    assert names == ["staged.png"]
    assert [g[cbase.NODE_STRUCTURE_SAMPLER]["inputs"]["seed"] for g in graphs] == [1, 2, 3]
    assert all(g[cbase.NODE_LOAD_IMAGE]["inputs"]["image"] == "staged.png"
               for g in graphs)


def test_prepare_run_still_returns_a_graph_and_its_staged_names(backend, monkeypatch):
    monkeypatch.setattr(backend.client, "stage_image", lambda path: "staged.png")
    graph, names = backend.prepare_run("pic.png", {"seed": 7}, "forge/x")
    assert names == ["staged.png"]
    assert graph == backend.build_graph("staged.png", {"seed": 7}, "forge/x")


# ==========================================================================
# through the service
# ==========================================================================
def test_a_well_formed_ensemble_block_is_accepted_and_echoed(live, image):
    client, _app = live
    status, body = client.post("/generate3d", {
        "image_path": str(image), "options": {"ensemble": {"best_of": 3}}})
    assert status == 202, body
    assert body["ensemble"] == {"structure_n": 1, "best_of": 3}


def test_the_job_record_carries_the_request_then_the_verdict():
    from meshgen import jobs as jobs_module
    job = jobs_module.Job("id", "pic.png", "fake",
                          {"ensemble": {"structure_n": 5, "best_of": 1}}, "out.glb")
    assert job.as_dict()["ensemble"] == {"structure_n": 5, "best_of": 1}

    job.state = jobs_module.DONE
    job.started = job.finished = 1.0
    job.result = {"mesh_path": "out.glb",
                  "ensemble": {"tier": "structure_consensus",
                               "winner": {"seed": 58}, "why": "..."}}
    # the finished report replaces the request block rather than sitting beside
    # it - two "ensemble" keys meaning different things is how a reader gets the
    # candidate count and thinks it is the verdict
    assert job.as_dict()["ensemble"]["winner"] == {"seed": 58}


def test_a_sub_run_without_a_job_id_gets_a_canonical_uuid(backend, monkeypatch):
    """ComfyUI v0.35 refuses a prompt_id that is not a canonical UUID, and every
    poll addresses the job by the id WE hold - so a missing one used to mean
    polling a job that had already finished until the timeout."""
    import uuid as uuid_module
    seen = []
    monkeypatch.setattr(backend.client, "submit",
                        lambda graph, prompt_id: seen.append(prompt_id))
    monkeypatch.setattr(backend.client, "job_state",
                        lambda prompt_id: {"status": "completed"})
    monkeypatch.setattr(backend.client, "history", lambda prompt_id: {"outputs": {}})
    monkeypatch.setattr(backend.client, "watch_progress",
                        lambda *a, **k: None)
    backend.client.run_graph({}, prompt_id=None, timeout_s=5)
    assert len(seen) == 1
    assert str(uuid_module.UUID(seen[0])) == seen[0]


@pytest.mark.parametrize("block,needle", [
    ({"structure_n": 40}, "1 to 9"),
    ({"best_of": 40}, "1 to 5"),
    ({"nope": 2}, "unknown ensemble key"),
    ({"structure_n": 5, "best_of": 3}, "Pick a tier"),
    ("five", "object"),
])
def test_a_malformed_ensemble_block_is_a_400_before_anything_is_queued(
        live, image, block, needle):
    """The same contract a bad views block gets: a malformed request costs no
    GPU time and is not a job that fails minutes later."""
    client, app = live
    status, body = client.post("/generate3d", {
        "image_path": str(image), "options": {"ensemble": block}})
    assert status == 400, body
    assert needle in body["error"]
    assert app.queue.qsize() == 0
