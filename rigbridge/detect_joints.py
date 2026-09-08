#!/usr/bin/env python
"""Run UniRig's skeleton (joint-detection) stage on one mesh and emit joints JSON.

This is a *standalone CLI*, not a service.  Run it with the UniRig virtualenv's
interpreter::

    C:\\forge-models\\unirig\\.venv\\Scripts\\python.exe rigbridge\\detect_joints.py ^
        --input  path\\to\\mesh.glb ^
        --output path\\to\\joints.json

It imports nothing from the Forge add-on and never touches Blender's bundled
``bpy`` -- the ``bpy`` it uses is UniRig's own pip wheel, and even that is
confined to a child process so it can never share an interpreter with torch.

See README.md next to this file for the rationale and the output contract.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

SCHEMA = "forge.joints/1"

DEFAULT_UNIRIG_ROOT = r"C:\forge-models\unirig"
DEFAULT_WEIGHTS = os.path.join(
    "weights", "skeleton", "articulation-xl_quantization_256", "model.ckpt"
)
HF_REPO_ID = "VAST-AI/UniRig"
HF_REVISION = "36842e2b5947e9e60f89275b83208c8e74071c63"
HF_WEIGHT_FILE = "skeleton/articulation-xl_quantization_256/model.ckpt"

TASK_CONFIG = "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml"

SUPPORTED_SUFFIXES = ("obj", "fbx", "dae", "glb", "gltf", "vrm")


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------

def unirig_root() -> str:
    root = os.environ.get("FORGE_UNIRIG_ROOT", DEFAULT_UNIRIG_ROOT)
    root = os.path.abspath(root)
    if not os.path.isdir(os.path.join(root, "src")):
        raise SystemExit(
            "UniRig checkout not found at %r (no src/). Set FORGE_UNIRIG_ROOT." % root
        )
    return root


def unirig_commit(root: str) -> str:
    """The pinned commit the checkout is sitting on, or a marker if unknowable."""
    head = os.path.join(root, ".git", "HEAD")
    try:
        with open(head, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return "unknown"
    if text.startswith("ref:"):
        ref = text.split(" ", 1)[1].strip()
        try:
            with open(os.path.join(root, ".git", ref), "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return "unknown"
    return text


def prepare_sys_path(root: str) -> None:
    """Put the UniRig checkout and its import shims on sys.path.

    forge_shims/ supplies a real pure-torch ``torch_cluster.fps`` plus
    import-only stubs for the skinning-only CUDA extensions.  See the
    docstrings in those files for the file:line evidence.
    """
    shims = os.path.join(root, "forge_shims")
    for entry in (shims, root):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    if not os.path.isdir(shims):
        raise SystemExit(
            "missing %r -- the UniRig install is incomplete." % shims
        )


def resolve_weights(root: str) -> str:
    override = os.environ.get("FORGE_UNIRIG_WEIGHTS")
    if override:
        if not os.path.isfile(override):
            raise SystemExit("FORGE_UNIRIG_WEIGHTS=%r does not exist" % override)
        return os.path.abspath(override)
    local = os.path.join(root, DEFAULT_WEIGHTS)
    if os.path.isfile(local):
        return local
    raise SystemExit(
        "skeleton checkpoint not found at %r.\n"
        "Fetch it with:\n"
        "  huggingface_hub.hf_hub_download(repo_id=%r, filename=%r,\n"
        "      revision=%r, local_dir=r'%s')"
        % (local, HF_REPO_ID, HF_WEIGHT_FILE, HF_REVISION, os.path.join(root, "weights"))
    )


# --------------------------------------------------------------------------
# stage 1 -- mesh extraction (child process; this is the only bpy user)
# --------------------------------------------------------------------------

def run_extract(root: str, mesh: str, out_dir: str, faces_target_count: int) -> str:
    """Drive UniRig's ``extract_builtin`` in a child interpreter.

    We call ``extract_builtin`` rather than ``src.data.extract``'s ``__main__``
    because that module's ``get_files`` builds its output directory with
    ``os.path.join(out_dir, input_path_without_suffix)``.  On Windows an
    absolute input path makes that join discard ``out_dir`` entirely and write
    next to the input file.  Passing the (file, output_dir) pair ourselves is
    both safer and less code.

    bpy lives in a child process on purpose: loading Blender's Python module
    into the same interpreter as torch is a known source of OpenMP and
    allocator conflicts, and we get a clean teardown for free.
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y_%m_%d_%H_%M_%S")
    bootstrap = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from src.data.extract import extract_builtin\n"
        "extract_builtin(output_folder=%r, target_count=%d, num_runs=1, id=0,\n"
        "                time=%r, files=[(%r, %r)])\n"
        % (root, out_dir, faces_target_count, stamp, mesh, out_dir)
    )
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=root,
        capture_output=True,
        text=True,
    )
    npz = os.path.join(out_dir, "raw_data.npz")
    if not os.path.isfile(npz):
        raise SystemExit(
            "UniRig mesh extraction produced no raw_data.npz.\n"
            "--- stdout ---\n%s\n--- stderr ---\n%s" % (proc.stdout, proc.stderr)
        )
    return npz


# --------------------------------------------------------------------------
# stage 2 -- skeleton inference (this process; torch only, never bpy)
# --------------------------------------------------------------------------

class _VramWatch:
    """Poll nvidia-smi for the process-wide peak while inference runs.

    torch.cuda.max_memory_allocated only counts torch's own allocator, which
    under-reports by however much CUDA context and cuBLAS workspace cost.  We
    report both numbers and use the larger one.
    """

    def __init__(self, interval=0.25):
        self.interval = interval
        self.peak_mb = 0.0
        self._stop = threading.Event()
        self._thread = None

    def _poll(self):
        cmd = [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
        while not self._stop.is_set():
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                value = float(out.stdout.strip().splitlines()[0])
                self.peak_mb = max(self.peak_mb, value)
            except Exception:  # noqa: BLE001 - telemetry must never break the run
                pass
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return False


def build_capture_writer(base_cls):
    class CaptureWriter(base_cls):
        """Keeps the prediction in memory instead of writing .npz/.obj/.fbx.

        UniRig's own ARWriter re-enters Blender to export an FBX.  We only want
        numbers, so we intercept the same callback and stash the arrays.
        """

        def __init__(self):
            super().__init__(write_interval="batch")
            self.captured = []

        def write_on_batch_end(self, trainer, pl_module, prediction, batch_indices,
                               batch, batch_idx, dataloader_idx=None):
            import numpy as np
            import torch

            def to_numpy(value):
                if isinstance(value, torch.Tensor):
                    return value.detach().cpu().numpy()
                return np.asarray(value)

            origin = to_numpy(batch["origin_vertices"])
            counts = to_numpy(batch["num_points"])
            for index, out in enumerate(prediction):
                self.captured.append({
                    "joints": np.asarray(out.joints, dtype=np.float64),
                    "tails": None if out.tails is None else np.asarray(out.tails, dtype=np.float64),
                    "parents": list(out.parents) if out.parents is not None else None,
                    "names": list(out.names) if out.names is not None else None,
                    "cls": out.cls,
                    "continuous_range": tuple(out.continuous_range),
                    "normalized_vertices": origin[index][: int(counts[index])],
                })

    return CaptureWriter


def predict_skeleton(root: str, npz_dir: str, weights: str, seed: int):
    """Reproduce run.py's predict path, with two deliberate changes.

    1. ``_attn_implementation`` is forced from ``flash_attention_2`` to
       ``sdpa``.  flash-attn has no Windows wheels; HuggingFace OPT computes
       the same dense attention either way, so this is a backend swap and not
       a model change.
    2. The writer is replaced by an in-memory capture (see above).
    """
    import lightning as L
    import torch
    import yaml
    from box import Box

    from src.data.datapath import Datapath
    from src.data.dataset import DatasetConfig, UniRigDatasetModule
    from src.data.transform import TransformConfig
    from src.model.parse import get_model
    from src.system.parse import get_system
    from src.tokenizer.parse import get_tokenizer
    from src.tokenizer.spec import TokenizerConfig
    from lightning.pytorch.callbacks import BasePredictionWriter

    def load_yaml(path):
        return Box(yaml.safe_load(open(os.path.join(root, path), "r", encoding="utf-8")))

    torch.set_float32_matmul_precision("high")
    L.seed_everything(seed, workers=True)

    task = load_yaml(TASK_CONFIG)
    components = task.components

    data_config = load_yaml(os.path.join("configs/data", components.data + ".yaml"))
    transform_config = load_yaml(
        os.path.join("configs/transform", components.transform + ".yaml"))
    tokenizer_config = TokenizerConfig.parse(
        config=load_yaml(os.path.join("configs/tokenizer", components.tokenizer + ".yaml")))
    model_config = load_yaml(os.path.join("configs/model", components.model + ".yaml"))
    system_config = load_yaml(os.path.join("configs/system", components.system + ".yaml"))

    attn_before = model_config.llm.get("_attn_implementation", None)
    model_config.llm._attn_implementation = "sdpa"

    predict_dataset_config = DatasetConfig.parse(
        config=data_config.predict_dataset_config).split_by_cls()
    predict_transform_config = TransformConfig.parse(
        config=transform_config.predict_transform_config)

    tokenizer = get_tokenizer(config=tokenizer_config)
    model = get_model(tokenizer=tokenizer, **model_config)

    data = UniRigDatasetModule(
        process_fn=model._process_fn,
        predict_dataset_config=predict_dataset_config,
        predict_transform_config=predict_transform_config,
        tokenizer_config=tokenizer_config,
        debug=False,
        data_name=components.get("data_name", "raw_data.npz"),
        datapath=Datapath(files=[npz_dir], cls=None),
        cls=None,
    )

    system = get_system(
        **system_config,
        model=model,
        optimizer_config=None,
        loss_config=None,
        scheduler_config=None,
        steps_per_epoch=1,
    )

    writer = build_capture_writer(BasePredictionWriter)()
    trainer = L.Trainer(callbacks=[writer], logger=False, **task.get("trainer", {}))

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    started = time.perf_counter()
    with _VramWatch() as watch:
        trainer.predict(system, datamodule=data, ckpt_path=weights,
                        return_predictions=False)
    seconds = time.perf_counter() - started

    torch_peak_mb = 0.0
    device = "cpu"
    if torch.cuda.is_available():
        torch_peak_mb = torch.cuda.max_memory_allocated() / (1024.0 * 1024.0)
        device = "cuda:%d" % torch.cuda.current_device()

    if not writer.captured:
        raise SystemExit("UniRig returned no prediction for this mesh.")

    return writer.captured[0], {
        "seconds": seconds,
        "torch_peak_mb": torch_peak_mb,
        "smi_peak_mb": watch.peak_mb,
        "device": device,
        "attn_before": attn_before,
    }


# --------------------------------------------------------------------------
# coordinate recovery
# --------------------------------------------------------------------------

def denormalize(prediction, npz_path):
    """Undo UniRig's unit-cube normalization, back into extraction coordinates.

    ``src/data/augment.py`` (AugmentAffine.transform) normalizes with a single
    uniform scale and a translation::

        centre = (bound_max + bound_min) / 2
        scale  = max(bound_max - bound_min) / (hi - lo)
        v'     = (v - centre) / scale + (lo + hi) / 2

    with ``normalize_into: [-1.0, 1.0]`` from
    ``configs/transform/inference_ar_transform.yaml``, so the bias term is 0.
    At predict time ``asset.joints`` is None, so the bounds come from the mesh
    vertices alone.  The inverse is exact.

    Rather than trusting that derivation blind, we fit scale and offset from
    the data we have on both sides -- the raw (pre-transform) vertices in
    raw_data.npz and the normalized vertices the model actually saw -- and
    return the residual so the caller can assert the fit is real.
    """
    import numpy as np

    raw = np.load(npz_path, allow_pickle=True)
    raw_vertices = np.asarray(raw["vertices"], dtype=np.float64)
    normalized = np.asarray(prediction["normalized_vertices"], dtype=np.float64)

    raw_min, raw_max = raw_vertices.min(axis=0), raw_vertices.max(axis=0)
    centre = (raw_max + raw_min) / 2.0

    lo, hi = prediction["continuous_range"]
    scale = float(np.max(raw_max - raw_min)) / float(hi - lo)
    bias = (lo + hi) / 2.0

    def to_raw(points):
        if points is None:
            return None
        return (np.asarray(points, dtype=np.float64) - bias) * scale + centre

    # Round-trip check on the vertices, which we know the answer for.
    residual_mm = None
    if normalized.shape == raw_vertices.shape:
        residual = np.linalg.norm(to_raw(normalized) - raw_vertices, axis=1)
        residual_mm = float(residual.max())

    return {
        "joints": to_raw(prediction["joints"]),
        "tails": to_raw(prediction["tails"]),
        "vertices": raw_vertices,
        "faces": np.asarray(raw["faces"]) if "faces" in raw else None,
        "scale": scale,
        "centre": centre,
        "residual": residual_mm,
    }


def derive_tails(joints, tails, parents):
    """Prefer UniRig's own tails; fall back to the first child's head.

    UniRig's AR tokenizer does emit tails for this task, but the contract has
    to survive a checkpoint that does not, so the fallback walks the parent
    graph.  A joint with no child gets ``None`` -- inventing a direction for a
    leaf would be a guess dressed up as data.
    """
    count = len(joints)
    if tails is not None and len(tails) == count:
        return [list(map(float, t)) for t in tails]

    first_child = [None] * count
    if parents:
        for index, parent in enumerate(parents):
            if parent is None or parent < 0 or parent >= count:
                continue
            if first_child[parent] is None:
                first_child[parent] = index

    out = []
    for index in range(count):
        child = first_child[index]
        out.append(None if child is None else list(map(float, joints[child])))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Detect skeleton joints in a mesh with UniRig; write joints JSON.")
    parser.add_argument("--input", required=True,
                        help="mesh file (.glb/.gltf/.obj/.fbx/.dae/.vrm)")
    parser.add_argument("--output", required=True, help="joints JSON to write")
    parser.add_argument("--seed", type=int, default=12345,
                        help="sampling seed; UniRig's AR decode is stochastic")
    parser.add_argument("--keep-temp", action="store_true",
                        help="keep the intermediate extraction directory")
    parser.add_argument("--unit-scale", type=float, default=1000.0,
                        help="millimetres per input file unit (default 1000, i.e. "
                             "the file is authored in metres, as glTF requires)")
    parser.add_argument("--faces-target-count", type=int, default=50000,
                        help="UniRig decimates to roughly this many faces first")
    args = parser.parse_args(argv)

    mesh = os.path.abspath(args.input)
    if not os.path.isfile(mesh):
        raise SystemExit("input mesh not found: %s" % mesh)
    suffix = mesh.rsplit(".", 1)[-1].lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SystemExit("unsupported mesh suffix %r (want one of %s)"
                         % (suffix, ", ".join(SUPPORTED_SUFFIXES)))

    root = unirig_root()
    prepare_sys_path(root)
    weights = resolve_weights(root)
    os.environ.setdefault("HF_HOME", os.path.join(root, "hf_cache"))

    temp_dir = tempfile.mkdtemp(prefix="forge_unirig_")
    npz_dir = os.path.join(temp_dir, "mesh")
    previous_cwd = os.getcwd()
    notes = []

    try:
        # The order config in inference_ar_transform.yaml points at
        # ./configs/skeleton/*.yaml, so UniRig must run from its own root.
        os.chdir(root)
        npz_path = run_extract(root, mesh, npz_dir, args.faces_target_count)
        prediction, timing = predict_skeleton(root, npz_dir, weights, args.seed)
        recovered = denormalize(prediction, npz_path)
    finally:
        os.chdir(previous_cwd)
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            notes.append("intermediate extraction kept at %s" % temp_dir)

    import numpy as np

    scale_mm = float(args.unit_scale)
    joints_mm = recovered["joints"] * scale_mm
    tails_raw = recovered["tails"]
    tails_mm = None if tails_raw is None else tails_raw * scale_mm
    vertices_mm = recovered["vertices"] * scale_mm

    bbox_min = vertices_mm.min(axis=0)
    bbox_max = vertices_mm.max(axis=0)

    parents = prediction["parents"]
    names = prediction["names"]
    tails_list = derive_tails(joints_mm, tails_mm, parents)

    # Honesty check: joints that fall outside the mesh bounds mean the
    # normalization inverse is wrong, and the caller must be told.
    slack = 0.02 * float(np.max(bbox_max - bbox_min))
    outside = int(np.sum(
        np.any((joints_mm < bbox_min - slack) | (joints_mm > bbox_max + slack), axis=1)))

    joints = []
    for index, head in enumerate(joints_mm):
        parent = None
        if parents is not None and index < len(parents):
            value = parents[index]
            if value is not None and int(value) >= 0:
                parent = int(value)
        name = None
        if names is not None and index < len(names) and names[index]:
            name = str(names[index])
        joints.append({
            "index": index,
            "name": name,
            "head_mm": [float(v) for v in head],
            "tail_mm": tails_list[index],
            "parent": parent,
            # UniRig's autoregressive decoder emits joint tokens, not
            # calibrated probabilities. There is no per-joint confidence to
            # report, so null is the honest value.
            "confidence": None,
        })

    notes.append(
        "coordinates are in the frame Blender's importer produces (Z-up); a "
        "glTF/OBJ/FBX file authored Y-up is rotated +90 deg about X on import, "
        "which for Forge meshes restores the original Blender authoring frame")
    notes.append(
        "un-normalized from UniRig's [%g, %g] cube with uniform scale %.6f and "
        "centre [%.6f, %.6f, %.6f] (input units), then scaled by %g mm/unit"
        % (prediction["continuous_range"][0], prediction["continuous_range"][1],
           recovered["scale"], recovered["centre"][0], recovered["centre"][1],
           recovered["centre"][2], scale_mm))
    if recovered["residual"] is not None:
        notes.append(
            "normalization inverse verified against the mesh vertices: max "
            "round-trip error %.3e input units" % recovered["residual"])
    else:
        notes.append(
            "could not verify the normalization inverse against vertices "
            "(shape mismatch between raw and normalized vertex arrays)")
    notes.append(
        "%d of %d joints fall outside the mesh bounding box (2%% slack)"
        % (outside, len(joints)))
    notes.append(
        "mesh.faces/mesh.vertices describe the mesh UniRig actually consumed "
        "(triangulated, then decimated toward %d faces), not the raw counts in "
        "the input file; bbox_min_mm/bbox_max_mm are unaffected by decimation "
        "and do describe the input" % args.faces_target_count)
    if names is None:
        notes.append("UniRig returned no joint names for this mesh; match by position")
    elif all(str(n).startswith("bone_") for n in names if n):
        notes.append(
            "joint names are UniRig's generic 'bone_<index>' placeholders, not "
            "anatomical labels -- they carry no more information than the index "
            "and must not be matched on; match by position")
    if timing["attn_before"] and timing["attn_before"] != "sdpa":
        notes.append(
            "attention backend switched from %r to 'sdpa' (flash-attn has no "
            "Windows wheels); identical dense attention for HuggingFace OPT"
            % timing["attn_before"])
    notes.append(
        "UniRig detects joints only. Skinning weights come from Rigify "
        "downstream; UniRig's own skinning stage is not installed.")
    if prediction["cls"]:
        notes.append("UniRig classified this mesh as %r" % prediction["cls"])

    vram_peak = max(timing["torch_peak_mb"], timing["smi_peak_mb"])

    document = {
        "schema": SCHEMA,
        "source": "unirig",
        "detector": {
            "name": "UniRig",
            "commit": unirig_commit(root),
            "weights": "%s@%s:%s" % (HF_REPO_ID, HF_REVISION, HF_WEIGHT_FILE),
            "seconds": round(timing["seconds"], 3),
            "vram_peak_mb": round(vram_peak, 1),
            "device": timing["device"],
        },
        "mesh": {
            "file": mesh,
            "faces": int(len(recovered["faces"])) if recovered["faces"] is not None else 0,
            "vertices": int(len(recovered["vertices"])),
            "bbox_min_mm": [float(v) for v in bbox_min],
            "bbox_max_mm": [float(v) for v in bbox_max],
        },
        "frame": {"unit": "mm", "space": "mesh_local", "axis_up": "Z"},
        "joints": joints,
        "notes": notes,
    }

    out_path = os.path.abspath(args.output)
    parent_dir = os.path.dirname(out_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")

    print("wrote %s" % out_path)
    print("  joints      %d (%s)"
          % (len(joints), "named" if names else "unnamed"))
    print("  seconds     %.1f" % timing["seconds"])
    print("  vram peak   %.0f MB (torch %.0f MB, nvidia-smi %.0f MB)"
          % (vram_peak, timing["torch_peak_mb"], timing["smi_peak_mb"]))
    print("  outside box %d" % outside)
    return 0


if __name__ == "__main__":
    sys.exit(main())
