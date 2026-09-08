# rigbridge

A file handoff between Forge and [UniRig](https://github.com/VAST-AI-Research/UniRig),
a neural **joint detector**. Mesh file in, joints JSON out.

UniRig is installed out-of-tree at `C:\forge-models\unirig` with its own Python
3.11 virtualenv. Nothing in this directory imports the Forge add-on, and nothing
here is importable *by* it — the contract is the JSON file, not a module.

## Why a CLI and not a service

The obvious shape for this would be another resident service next to meshgen
and Ollama. It is the wrong shape here, for two reasons:

1. **VRAM.** Skeleton inference peaks around **8.5 GB** on this 12 GB card. A
   resident process would sit on that for the whole session, and TRELLIS.2 /
   meshgen needs the card. A one-shot process gives every byte back on exit.
2. **Duty cycle.** Joint detection is a batch operation that happens once per
   mesh and takes ~5 seconds. There is no warm state worth keeping between
   calls that is cheaper than the ~5 s it costs to rebuild — and holding 8.5 GB
   for hours to save 5 seconds is a bad trade.

So: launch, detect, write JSON, exit. The consumer reads the file.

## Usage

```
C:\forge-models\unirig\.venv\Scripts\python.exe rigbridge\detect_joints.py ^
    --input  C:\path\to\mesh.glb ^
    --output C:\path\to\joints.json
```

| flag | default | meaning |
| --- | --- | --- |
| `--input` | required | `.glb` `.gltf` `.obj` `.fbx` `.dae` `.vrm` |
| `--output` | required | joints JSON to write |
| `--seed N` | `12345` | UniRig's decode is sampled, not greedy — see *Determinism* |
| `--keep-temp` | off | keep the intermediate `raw_data.npz` extraction |
| `--unit-scale` | `1000.0` | millimetres per input-file unit |
| `--faces-target-count` | `50000` | UniRig decimates to about this many faces first |

Environment overrides: `FORGE_UNIRIG_ROOT` (checkout location),
`FORGE_UNIRIG_WEIGHTS` (checkpoint path).

Producing a test mesh — the Phase 3/4 synthetic tagged biped, exported straight
out of the existing add-on test builders, which this script imports rather than
copies:

```
"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" ^
    --background --factory-startup ^
    --python rigbridge\export_test_mesh.py -- --output C:\tmp\biped.glb
```

Always `--background`. Never open a window.

## Output contract — `forge.joints/1`

```jsonc
{
  "schema": "forge.joints/1",
  "source": "unirig",
  "detector": {"name": "UniRig", "commit": "...", "weights": "...",
               "seconds": 5.4, "vram_peak_mb": 8473.0, "device": "cuda:0"},
  "mesh":     {"file": "<abs path of input>", "faces": 50000, "vertices": 24991,
               "bbox_min_mm": [x,y,z], "bbox_max_mm": [x,y,z]},
  "frame":    {"unit": "mm", "space": "mesh_local", "axis_up": "Z"},
  "joints":   [{"index": 0, "name": "bone_0" | null, "head_mm": [x,y,z],
                "tail_mm": [x,y,z] | null, "parent": 3 | null,
                "confidence": null}],
  "notes":    ["..."]
}
```

### What a consumer needs to know

**`axis_up` is `Z`, not `Y`.** Coordinates are in the frame Blender's importer
produces. UniRig loads meshes through `bpy`, and every Blender importer
(glTF, OBJ, FBX) rotates a Y-up file +90° about X on the way in. For a Forge
mesh that round-trips Blender → `.glb` → UniRig, this lands back in the
*original Blender authoring frame*, which is the frame the add-on wants anyway.
No further rotation is needed on the consumer side.

**Units are millimetres**, converted from the file's own units by
`--unit-scale` (default 1000, i.e. the file is metres, as glTF requires).

**Coordinates are in the input mesh's own frame, not a unit cube.** UniRig
normalizes internally to `[-1, 1]`; the runner inverts that and *verifies* the
inverse by round-tripping the mesh vertices through it. The residual is
reported in `notes` (observed: ~2e-07 input units) along with a count of
joints falling outside `bbox_min_mm`/`bbox_max_mm` (observed: 0).

**`name` is never anatomical.** UniRig emits `bone_0`, `bone_1`, … — positional
placeholders carrying no more information than `index`. `notes` says so
explicitly when it detects them. **Match by position, never by name.** If a
future checkpoint emits nothing, `name` is `null`.

**`confidence` is always `null`.** UniRig's autoregressive decoder emits joint
*tokens*, not calibrated probabilities. There is no per-joint confidence to
report, so `null` is the honest value rather than a fabricated `1.0`.

**`parent` is a joint index or `null`** (root only). The graph is a tree.

**`tail_mm` comes from UniRig's own tails** when it emits them (it does for this
checkpoint). Where it does not, each joint's tail falls back to its first
child's head; a leaf with no child gets `null` rather than an invented
direction.

**`mesh.faces` / `mesh.vertices` describe the mesh UniRig actually consumed** —
triangulated, then decimated toward `--faces-target-count`. They are not the
raw counts from the input file. `bbox_min_mm` / `bbox_max_mm` are unaffected
by decimation and do describe the input.

### Determinism

UniRig's AR decode samples (`do_sample: True`, `top_k: 5`, `temperature: 1.5`),
so `--seed` genuinely changes the answer. Across seeds 12345 and 999 on the
test biped the topology was identical (22 joints, same parent array) and joint
positions moved by at most **26.7 mm**. Positions are also quantized to 256
bins across the mesh's longest axis — about **6.7 mm** on a 1.7 m figure — which
is the precision floor regardless of seed.

## Limitations worth planning around

UniRig detects joints. It does **not** produce skinning weights (Forge uses
Rigify for that), bone roll, or IK/FK structure. On the synthetic biped it
placed joints a mean **58.6 mm** (3.4% of figure height) from the ground-truth
landmarks, and **missed both ear chains entirely** — consistent with the
paper's own account of out-of-domain extremities (hands, tails, wings). Treat
the output as a strong positional seed for a metarig, not as a rig.

Full install notes, license verification, benchmark numbers and the list of
packages deliberately skipped: `C:\forge-models\unirig\FORGE-NOTES.md`.
