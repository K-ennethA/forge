# meshgen — image-to-3D behind one stable API

Port **8902**, 127.0.0.1 only, stdlib-only Python. Turns a picture into a
textured `.glb`. The model is a plug, not a dependency: adapters live in
`backends/`, and swapping which one runs is an environment variable or one line
of `config.json`.

Meshgen **generates**. The rest of Forge **finishes** — the output mesh goes on
into the existing pipeline: Blender import → voxel repair → `/check_mesh` →
`/segment_mesh` or retopo.

```
POST /generate3d  {"image_path", "backend"?, "options"?, "output"?}  -> {"job_id"}
                  {"views": {"front", "side"?, "back"?, "left"?}, ...}   (pixal3d)
                  options.ensemble {"structure_n"?, "best_of"?}   (pick among seeds)
GET  /job/<id>    queued | running | done | error | cancelled  (+ progress, stage)
POST /cancel/<id> best-effort interrupt
GET  /health      backend name/model/license/loaded + available_backends
```

A finished job carries `mesh_path`, `stats: {verts, faces, ...}`, `duration_ms`,
`backend`, `model`, the `options` actually used, and a `vram` block with
`before_gb` / `peak_gb` / `after_gb` / `total_gb` (peak is sampled while the job
runs — the before/after pair misses it, and peak is the number that decides
whether a setting fits on this card).

---

## Where the 23.8 GB lives

**Nothing heavyweight is in this repo, and nothing heavyweight is in OneDrive.**
The repo sits under OneDrive; the models deliberately do not.

```
C:\forge-models\                     (outside the repo, outside OneDrive)
  comfyui\                           git clone, pinned to tag v0.35.1
    .venv\                           its own venv: Python 3.14.3 + torch 2.14.0+cu130
    extra_model_paths.yaml           points ComfyUI at ..\models instead of copying
  models\
    diffusion_models\  trellis_2_int8_convrot.safetensors      4.89 GB
                       pixal3d_int8_convrot.safetensors        5.20 GB
                       pixal3d_multiview_int8_convrot.sft      5.20 GB  (multi-view)
    vae\               trellis_2_shape_vae_bf16.safetensors     1.02 GB
                       trellis_2_texture_vae_bf16.safetensors   0.88 GB
    clip_vision\       dino_v3_L_naf_fp32.safetensors           1.13 GB
    background_removal\birefnet.safetensors                     0.41 GB
    geometry_estimation\moge_2_vitl_normal_fp16.safetensors     0.62 GB
  comfyui-input\                     staged copies of input images
  comfyui-output\                    where ComfyUI writes before we collect
```

`pixal3d_multiview_int8_convrot.safetensors` (5 584 555 824 bytes, sha256
`6b1eb332…f477`) **is present and verified** — approved and fetched 2026-09-08.
It is the same *size* as the single-view checkpoint: same architecture repacked.

Measured on disk: **19.36 GB of weights** plus **4.45 GB** of ComfyUI and its venv
(PyTorch 2.14.0+cu130 is most of it) — **23.81 GB total**.

### Relocating the model store

Two files, both listing the same paths:

1. `meshgen/config.json` — what the service and the "what's missing" messages read.
2. `C:\forge-models\comfyui\extra_model_paths.yaml` — what ComfyUI itself reads.

Edit both to the new root and nothing else changes. For a temporary override
(a second disk, a test rig) the environment wins over the file:
`FORGE_MESHGEN_MODELS_ROOT`, `FORGE_MESHGEN_COMFYUI_ROOT`,
`FORGE_MESHGEN_COMFYUI_PYTHON`, `FORGE_MESHGEN_OUTPUT_DIR`,
`FORGE_MESHGEN_INPUT_DIR`, `FORGE_MESHGEN_PORT`, `FORGE_MESHGEN_COMFYUI_PORT`,
`FORGE_MESHGEN_BACKEND`, `FORGE_MESHGEN_JOB_TIMEOUT`,
`FORGE_MESHGEN_STARTUP_TIMEOUT`, `FORGE_MESHGEN_CONFIG`.

If a weight is absent, `/health` says so *by name*, with the exact path meshgen
looked at and the URL to fetch it from — it never downloads anything on its own.

---

## Backends

| name | model | weights licence | notes |
|---|---|---|---|
| `trellis2` *(default)* | TRELLIS.2 int8 convrot | MIT — `Comfy-Org/TRELLIS.2` | the one that fits comfortably in 12 GB |
| `pixal3d` | Pixal3D int8 convrot | MIT — `Comfy-Org/Pixal3D` | pixel-aligned; **the only backend that needs MoGe**, and the only one that takes `views` |

Both were run end to end on the 12 GB card and both work — see the measured
numbers below.

The template's switch node evaluates lazily, so a TRELLIS.2 job never touches the
MoGe branch. `/health` reflects that: MoGe is listed as missing for `pixal3d`
only, and someone who only wants TRELLIS.2 can skip that 0.62 GB download.

Both run **entirely on ComfyUI's own core nodes** (`comfy_extras/nodes_trellis2.py`,
`comfy/ldm/trellis2/`, `comfy_extras/mesh3d/`) and share one official workflow
that selects between them with a boolean — which is why the two adapters differ
by about ten lines and inherit everything else from `backends/comfyui_base.py`.
The multi-view graph is a fourth thing derived from that same template; see
[Multi-view](#multi-view-front--side--back--live).

Pick the default with `FORGE_MESHGEN_BACKEND=trellis2|pixal3d`, or
`"default_backend"` in `config.json`. Any single request can override it with
`{"backend": "..."}`.

### Options

Passed through as `{"options": {...}}`; anything unrecognised is refused with the
supported list rather than silently ignored.

| option | effect | ComfyUI template | **meshgen default** |
|---|---|---|---|
| `shape_resolution` | shape cascade target (`"1024"` / `"1536"`) | `"1536"` | **`"1024"`** |
| `remesh_resolution` | remesh grid | 768 | **512** |
| `texture_resolution` | baked texture size | 4096 | **2048** |
| `target_face_count` | decimation target | 700000 | **200000** |
| `seed` | structure sampler seed | 56 | 56 |
| `steps` | structure sampler steps | 12 | 12 |
| `cfg` | structure sampler CFG | 7.5 | 7.5 |
| `uv_padding` | UV atlas padding | 1 | 1 |

Five more are the **surface-quality** knobs, measured rather than inherited —
the table, the winners and the losers are in
[Tuning the surface](#tuning-the-surface-what-was-measured):

| option | node input | range | ComfyUI template | **meshgen default** |
|---|---|---|---|---|
| `smooth_iters` | `RemeshMesh.smooth_iters` | 0–20 | 20 | **3** |
| `qef` | `RemeshMesh.sign_mode.qef` | bool | `false` | **`true`** |
| `project_back` | `RemeshMesh.project_back` | 0.0–1.0 | 0 | **0.5** |
| `crease_angle` | **both** `MeshSmoothNormals` | 0–180 | 180 | 180 |
| `fix_poles` | `RemeshMesh.fix_poles` | bool | `false` | `false` |

Plus `hard_surface` (bool) — a convenience **macro, not a mode**: it pre-seeds
`crease_angle: 45` and `qef: true` together, and an explicit option next to it
still wins. The result's `options` block reports both the macro and what it
expanded to.

Ranges are core's own node schemas, and out-of-range is **refused with the real
range, never clamped** — a node that quietly clamps 500 iterations to 20 does not
tell you it did, and you then reason about a setting that never happened.
Booleans are strict: `"true"` / `"false"` / `true` / `false` / `1` / `0` and
nothing else, because `bool("false")` is `True` and a silently inverted
sharp-feature flag is precisely the bug this surface exists to prevent.

Three more steer meshgen rather than a node in the graph, and only mean anything
alongside `views` (see [Multi-view](#multi-view-front--side--back--live)):
`on_unavailable` (`"error"` / `"front_only"`), `multiview_fov_deg` (20.0 — the
rig's FOV; it reaches `Pixal3DMultiViewConditioning.fov` directly), and `views`
itself, which may be sent top-level or inside `options`.

And one more that steers meshgen rather than the graph: **`ensemble`**
(`{"structure_n": 1-9, "best_of": 1-5}`) — draw the sampler more than once and
pick between the draws deterministically. Both default to 1, i.e. off. See
[Seed ensemble](#seed-ensemble-draw-more-than-once-then-pick-deterministically).

**There is no `negative_prompt`, and there should never be one.** At the shape
stage the graph wires `negative` from the conditioning node's second output,
which for both backends is a zero tensor — the architecture has no text
conditioning to negate. The option would be a control you could turn and watch
do nothing.

### Why the defaults differ from the template — this is a 12 GB card

The official template is tuned for a much larger GPU. **Measured on the RTX 5070
(12 GB):** at the stock `shape_resolution` of `"1536"` the shape stage emits
**17.9M vertices / 35.9M faces**, and `RemeshMesh` at 768 then requests ~20 GiB
and dies with `torch.OutOfMemoryError` after about 7 minutes of work.

So meshgen applies `VRAM_SAFE_DEFAULTS` (in `backends/comfyui_base.py`) on top of
the template before any caller options. On a bigger card, pass the stock values
straight back in:

```json
{"options": {"shape_resolution": "1536", "remesh_resolution": 768,
             "texture_resolution": 4096, "target_face_count": 700000}}
```

Peak VRAM at the meshgen defaults stays inside 12 GB; the stock settings peaked
at 11.9/12.2 GB before the remesh stage blew past it.

### What a run costs (measured, RTX 5070 12 GB)

A 1024×1024 render of a printable part, at the meshgen defaults, end to end
through `/generate3d`:

| | `trellis2` | `pixal3d` |
|---|---|---|
| wall clock | **304 s** | **249 s** |
| peak VRAM | **8.15 GB** of 11.94 | **9.30 GB** of 11.94 |
| output | 199,694 tris / 134,216 verts, ~14 MB | 199,250 tris / 120,704 verts, ~12 MB |

Both include ~10 s of ComfyUI cold start on the first job, and both produce one
material with three textures. Slowest stages for TRELLIS.2: shape upsample
(~85 s), remesh (~65 s), ambient-occlusion bake (~40 s).

**Both backends fit on this 12 GB card at the meshgen defaults.** Pixal3D costs
about 1.2 GB more VRAM (bigger UNet plus MoGe resident) but finished faster here.

The intermediate shape mesh is ~16.9M faces before decimation, which is what
makes the remesh stage the memory ceiling.

### What comes out is a *diagnosable* mesh, not a printable one

Generated meshes are organic soup by 3D-printing standards. The same part above,
scaled to 150 mm and pushed straight through the live `/check_mesh` on 8765:

```
overall: fail
  pass   bed_fit      fits the 256x256x256 mm bed in 6 of 6 orientations
  fail   min_wall     3586 of 3861 probes under the 0.8 mm minimum (thinnest 0.001 mm)
  warn   overhangs    44975.9 mm2 unsupported (steepest 90 deg)
  fail   watertight   9 boundary and 210 non-manifold edges; inconsistent winding
```

That is the correct answer, not a failure of the integration — `/check_mesh`
never refuses a broken mesh, and this is what the downstream half of the pipeline
(Blender import → **voxel repair** → re-check → `/segment_mesh`) exists to fix.
Expect to repair before printing. 200k triangles checked in ~4 s.

---

## Tuning the surface: what was measured

`workflows/image_to_3d.json` began as the official ComfyUI template, and a
template is a demo. Several of its values are **not the node's own default** and
were never justified anywhere — they were inherited, not chosen:

| node input | node default | template ships | what the template's value means |
|---|---|---|---|
| `RemeshMesh.smooth_iters` | 0 | **20** | the node's *maximum*, against its own tooltip: "2-3 cleans DC staircase-like artifacts; higher over-smooth QEF edges" |
| `RemeshMesh.sign_mode.qef` | `false` in udf | **false** | sharp-feature dual-vertex placement **off** |
| `RemeshMesh.project_back` | 0.0 | **0** | nothing is pulled back onto the surface the model produced |
| `MeshSmoothNormals.crease_angle` (×2) | 180 | **180** | "no edge is ever hard" — a 90° edge shaded with one averaged normal |
| `KSampler(structure).steps` / `cfg` | — | **12 / 7.5** | the demo's numbers, which every SEO table copies |

None of that is knowable by reading, so it was measured.

### The harness

`tools/ab_tuning.py` runs the real pipeline once per setting and scores each
output mesh **deterministically — geometry only, no VLM, no eyeballing**:

```
service\.venv\Scripts\python.exe -m meshgen.tools.ab_tuning run    --out <dir>
service\.venv\Scripts\python.exe -m meshgen.tools.ab_tuning report <dir>
```

Without meshgen up on 8902 and the weights present it prints why and **exits 0**,
the same contract the pytest suites keep. Rows append to `<dir>/results.jsonl`
one at a time and a re-run skips what is already there, so an interrupted sweep
resumes instead of paying twice for finished GPU minutes.

**The inputs are synthesised, not photographed** (`tools/ab_fixtures.py`), which
is the only way the ground truth can be exact: a vectorised ray/solid cast over
axis-aligned boxes and ellipsoids, through **core's own camera rig** — imported
from `tools/make_rig_views.py`, extended by one elevation angle, and pinned by a
test to agree with it exactly at elevation 0. Three scenes:

- **`hard_steps`** — a stepped ziggurat with an off-centre tab. Creases at four
  different scales, so a setting that survives a big edge and melts a small one
  shows up as a number. This is the sweep scene. Its **true crease length is
  ≤ 15.24** (`ab_fixtures.true_crease_length`, counting every box edge including
  the buried ones, so the visible figure is lower) — the anchor that separates
  "sharper" from "jaggier".
- **`hard_slab`** — the `make_rig_views` object (slab + fin + foot), reused
  rather than retyped, so the hard-surface control is the same shape Phase 18(a)
  verified orientation against.
- **`organic_blob`** — four overlapping ellipsoids, no sharp edge anywhere. The
  control that says whether a hard-surface win costs anything on a round shape.

### What the scores mean

`tools/mesh_metrics.py`, all from vertices and triangles:

| metric | what it catches |
|---|---|
| `silhouette_iou` | the mesh's outline against the input's exact mask through the identical camera — anything that eats volume |
| `edge_sharpness` | of the edge length that is a crease **at all** (dihedral > 5°), the fraction still a *real* crease (≥60°) rather than melted into a 5–60° ramp. A cube scores 1.0, a sphere 0.0 — both pinned as tests |
| `soft_edge_fraction` | the rounding-over signature directly: edge length sitting in that 5–60° band |
| `crease_length` | total sharp edge length ÷ bbox diagonal — **the staircase detector.** `edge_sharpness` alone can be gamed by Dual Contouring's voxel steps, which are perfectly sharp and entirely fake; a ziggurat has a handful of real creases, so a jump here is artifacts, not detail |
| `crease_normal_p95` | 95th-percentile angle between a shading normal and its own face, over faces touching a real crease. **The only number `crease_angle` moves** — it splits vertices, so it changes shading and never geometry |
| `boundary_edges` / `nonmanifold_edges` / `components` | topology health |
| `self_intersections` | clipping, vertex-sharing pairs excluded (adjacent faces touch by definition) |
| `uv_split_ratio` | exported vertices ÷ welded vertices — the atlas cost |

Two things in there are load-bearing and were bugs first:

- **The mesh is welded by position before anything is measured.** `UnwrapMesh`
  cuts the surface into UV islands and the glTF carries one vertex per
  (position, island) pair, so a closed mesh exports with ~58 000 "boundary"
  edges and ~1 800 "components". Worse, an edge split by a seam stops being a
  two-face edge and **drops out of the dihedral statistics** — and seams are
  routed preferentially along creases, so the unwelded number under-counts
  exactly what is being measured. Welding moved the baseline's `edge_sharpness`
  from 0.006 to 0.061: a **ten-fold** difference, in the metric the whole
  exercise turns on.
- **Zero-area faces are dropped.** A sliver has no normal; dividing by its zero
  length hands it `(0,0,0)`, which scores a flawless 90° crease against
  anything. Counting slivers as sharp edges would have put a floor under every
  variant and made smoothing look harmless.

The mesh's axes are the exporter's, not the scene's, so the silhouette is scored
after a search over all **48 signed axis permutations** (these meshes come back
Y-up, `[x, z, y]` with two flips). It is a search over *labelling*, not a fit —
nothing is rotated by a free angle, so a genuinely wrong shape cannot be aligned
into looking right, and a test pins that a sphere cannot pass as a slab.

### Measured — RTX 5070 12 GB, ComfyUI v0.35.1, `trellis2`, seed 56

One knob moved at a time from the template's values, on `hard_steps`, one job at
a time, at the meshgen VRAM-safe defaults. Peak VRAM is `nvidia-smi` at 200 ms,
not meshgen's own coarser sample. **`creaseL` against the ground-truth bound of
15.24** is what separates recovered detail from voxel staircase.

| variant | smooth | qef | proj | crease | steps/cfg | IoU | sharp | soft% | creaseL | crease n95 | selfX | shells | time | VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline` | 20 | off | 0 | 180 | 12/7.5 | 0.9771 | 0.061 | 10.8 | 15.7 | 164.4 | 0 | 33 | 177 s | 11.63 |
| `smooth3` | 3 | off | 0 | 180 | 12/7.5 | 0.9767 | 0.097 | 9.4 | 23.2 | 156.0 | 1 | 43 | 175 s | 11.05 |
| `smooth2` | 2 | off | 0 | 180 | 12/7.5 | 0.9768 | 0.110 | 9.1 | 25.6 | 152.0 | 0 | 37 | 173 s | 11.17 |
| `smooth0` | 0 | off | 0 | 180 | 12/7.5 | 0.9766 | 0.147 | 8.2 | **32.5** | 143.0 | 0 | 33 | 173 s | 11.27 |
| `qef_on` | 20 | **on** | 0 | 180 | 12/7.5 | 0.9763 | 0.068 | 9.9 | 16.5 | 163.9 | 0 | 28 | 175 s | 11.25 |
| `project05` | 20 | off | **0.5** | 180 | 12/7.5 | 0.9778 | 0.051 | 12.0 | 14.8 | 169.8 | **70** | 35 | 207 s | 11.24 |
| `project10` | 20 | off | **1** | 180 | 12/7.5 | 0.9787 | 0.129 | 13.0 | **39.6** | 180.0 | **13,391** | **465** | 207 s | 10.93 |
| `crease45` | 20 | off | 0 | **45** | 12/7.5 | 0.9769 | 0.061 | 10.7 | 15.8 | **19.2** | 1 | 34 | 169 s | 11.24 |
| `steps25_cfg5` | 20 | off | 0 | 180 | **25/5** | **0.9801** | **0.006** | 9.6 | **1.2** | 180.0 | 0 | 26 | 143 s | 10.31 |

**Silhouette IoU barely moves — 0.976 to 0.980 across every variant.** That is
itself a finding: at 200k triangles the outline is settled long before these
settings matter, so IoU is the guard that a change did not eat the object, and
`edge_sharpness` / `creaseL` / `selfX` are what actually decide. A tuning table
built on IoU alone would have picked `project10`, which is the worst mesh here.

### The winners, and why

| setting | template | **chosen** | one-line reason |
|---|---|---|---|
| `smooth_iters` | 20 | **3** | 20 is the node's maximum and its tooltip says 2-3; at 3, `edge_sharpness` rises 59% (0.061 → 0.097) and the soft-ramp band falls, for no time or VRAM. Lower keeps helping the score and stops being real: `smooth0` reaches 0.147 only by carrying **2.1× the object's true crease length** — that is Dual Contouring's staircase, perfectly sharp and entirely fake. 3 is the safe end of core's own range. |
| `qef` | off | **on** | free and directionally clean: sharpness up 11%, soft ramps down 8%, shells 33 → 28, **and `creaseL` stays at the ground truth** (16.5 vs 15.24) — it recovers features by *placing* the dual vertex, not by getting jaggier. Muted alone because 20 smoothing iterations then erase what it placed; the stack is where it pays. |
| `project_back` | 0 | **0** (unchanged) | the clearest loss measured. 0.5 buys +0.0007 IoU and **70 self-intersections where there were none**; 1.0 buys +0.0016 and **13,391 across 465 shells**, at 2.6× the true crease length. It snaps dual-contoured vertices back onto the raw model surface — reintroducing exactly the self-intersecting mess the remesh exists to remove. Both also cost ~17% more wall time. |
| `crease_angle` | 180 | **180 globally, 45 under `hard_surface`** | on hard surfaces 45 is close to free: geometry is *bit-identical* by construction (sharp 0.061, creaseL 15.8 — unchanged), it cut crease-face shading error from **164° to 19°**, and it ran 8 s faster with slightly *fewer* exported vertices. It is not defaulted because **the metric that scores it is only valid on hard surfaces** — a sphere *wants* its shading normals to deviate from its faces, so defaulting 45 would be optimising a number the harness cannot read on half the inputs. Hence the opt-in. |
| `steps` / `cfg` | 12 / 7.5 | **12 / 7.5** (unchanged) | 25/5.0 posts the best IoU of the whole sweep (0.9801) and is 34 s *faster*, and it is still refused: `creaseL` collapses to **1.2 against a true 15.24** — it loses ~92% of the object's creases, fitting the outline by rounding the object off. A silhouette-only table would have shipped this. Both knobs were then separated; see below. |

### The stack, against all three scenes

`winner` = `smooth_iters: 3` + `qef: true` (what meshgen now ships by default);
`winner_hard` = that plus `crease_angle: 45` (what `hard_surface` adds).
**Ground-truth `creaseL`: `hard_steps` ≤ 15.24, `hard_slab` ≤ 7.90.**

| scene | variant | IoU | sharp | creaseL | soft% | crease n95 | exported verts | selfX | shells | time |
|---|---|---|---|---|---|---|---|---|---|---|
| `hard_steps` | `baseline` | 0.9771 | 0.061 | 15.7 | 10.8 | 164.4 | 135,123 | 0 | 33 | 177 s |
| `hard_steps` | `winner` | 0.9752 | **0.281** | 57.8 | 6.5 | 120.7 | 130,222 | 0 | 33 | 175 s |
| `hard_steps` | `winner_hard` | 0.9755 | 0.280 | 57.3 | 6.5 | **13.2** | 128,954 | 0 | 37 | 179 s |
| `hard_slab` | `baseline` | 0.9016 | 0.006 | **0.4** | 4.7 | 180.0 | 147,702 | 0 | 2 | 78 s |
| `hard_slab` | `winner` | 0.9004 | **0.161** | **7.9** | 2.9 | 55.6 | 146,213 | 0 | 2 | 78 s |
| `hard_slab` | `winner_hard` | 0.9006 | 0.160 | 7.8 | 2.8 | **4.9** | 145,842 | 0 | 2 | 76 s |
| `organic_blob` | `baseline` | 0.9121 | 0.068 | 16.3 | 9.2 | 110.8 | 122,160 | 5 | 28 | 278 s |
| `organic_blob` | `winner` | 0.9120 | 0.129 | 28.7 | 8.0 | 100.4 | 122,206 | **1** | 25 | 282 s |
| `organic_blob` | `winner_hard` | 0.9121 | 0.128 | 28.4 | 8.0 | 29.3 | **128,121** | 2 | 29 | 284 s |

Four things this says that the single-knob table could not:

- **The two winners compound far beyond their sum.** Alone, `smooth_iters: 3`
  reached 0.097 and `qef` reached 0.068 against a 0.061 baseline. Together they
  reach **0.281** — 4.6×. That is the mechanism working as core describes it:
  QEF *places* the dual vertex on the feature, and low smoothing is what leaves
  it there. Either one alone is half a fix.
- **On `hard_slab` the winner lands on the ground truth exactly** — `creaseL`
  7.9 against an analytic 7.90 — while the template sits at **0.4**, having
  melted 95% of the object's creases. That is the single most convincing number
  in this exercise, because the target was computed from the source geometry and
  never from a mesh.
- **`hard_steps` over-creases** (57.8 against ≤15.24) where `hard_slab` does
  not. The ziggurat's top boxes are near the remesh grid's resolution, so the
  model returns micro-detail there and QEF sharpens all of it, noise included.
  Reported rather than hidden: the win is real on both, the *excess* is
  fixture-specific, and anyone re-running this should expect it.
- **It costs nothing on an organic shape.** Identical IoU (0.9120 vs 0.9121),
  fewer self-intersections (**5 → 1**), fewer shells, +1.5% wall time.

And the reason `crease_angle` is opt-in rather than default is in the last row:
on the blob, `winner_hard` adds **4.8% more exported vertices** (122,206 →
128,121) to split edges that should have stayed smooth, and drives `normal_p50`
*down* — shading normals hugging their faces, which on a curved surface is the
definition of faceting. No metric here scores that as bad, which is precisely
why it is not defaulted.

### The structure sampler: 12/7.5 vs the checkpoint's 25/5.0, separated

The two knobs move together in every write-up, so `steps25_cfg5` could not say
which one did what. The full 2×2 (`hard_steps`, seed 56):

| steps | cfg | IoU | sharp | creaseL | shells | time | VRAM |
|---|---|---|---|---|---|---|---|
| **12** | **7.5** *(template)* | 0.9771 | **0.061** | **15.7** | 33 | 177 s | 11.63 |
| 12 | 5.0 | **0.9653** | 0.004 | 0.7 | 10 | 141 s | 10.13 |
| 25 | 7.5 | 0.9729 | 0.008 | 1.4 | 10 | 145 s | 10.64 |
| 25 | 5.0 *(checkpoint)* | **0.9801** | 0.006 | 1.2 | 26 | 143 s | 10.31 |

**The template's pairing is the only one of the four that keeps the object's
creases at all** — 0.061 / 15.7 against ≈0.005 / ≈1 everywhere else, i.e. a 12×
difference in crease length. It is an interaction, not two independent knobs:
moving *either* one off 12/7.5 collapses the structure into a smooth blob, and
`cfg` 5.0 at the template's 12 steps is the worst silhouette of the entire
sweep (0.9653). The ~35 s the template costs over the other three is the remesh
having real geometry to work on.

So the demo's numbers survive, but now for a measured reason rather than because
they were inherited. **Caveat, stated plainly:** this is one fixture at one
seed. It is enough to refuse a change, not enough to claim 12/7.5 is optimal —
`ab_tuning.py` is checked in so the question can be reopened cheaply.

`fix_poles` is exposed but **not** swept — it stays `false`, which is both core's
default and the template's. It collapses valence-3 vertex pairs (a Dual
Contouring T-junction artifact); nothing in these fixtures isolates it, so
changing it would have been a guess wearing a measurement's clothes.

**Cost of the whole exercise: 19 GPU runs, ~55 minutes**, one job at a time.



## Seed ensemble: draw more than once, then pick deterministically

A diffusion sampler's seed is a lottery ticket, and this pipeline's structure
stage is a *high-variance* one. Measured on `hard_slab`, five seeds produced five
occupancy grids whose pairwise IoU ran **0.53 to 0.72** — and two of the five
disagreed with the input drawing so badly (silhouette IoU 0.18 against the best
draw's 0.80) that no amount of downstream tuning would have saved them. Every
setting in the section above moves the mesh by a few percent. The seed moves it
by more than that, and until now meshgen took whatever the first draw gave.

Published oracle best-of-5 on this class of model is roughly a **26% Chamfer
improvement** (arXiv 2604.27106): the good mesh is usually already in a handful
of draws, and the whole problem is *picking* it. Two findings settle how:

- **Consensus beats a judge.** A symmetric-Chamfer **medoid** — the candidate
  that agrees most with the others — outperformed a VLM verifier on CAD
  generation, with the benefit plateauing around N = 9 (arXiv 2608.09706).
- **The one image-side signal that works is geometric.** ShapeGen's picker
  compares a rendered front-view normal map against the input with DINO cosine
  (arXiv 2511.20624). **Render-CLIP is chance** on this task and is named here
  only so that nobody re-adds it.

So: no VLM, no CLIP, no eyeballing. Everything below is numpy over boolean grids
and triangles, and the same request produces the same pick on any machine.

```json
POST /generate3d
{"image_path": "C:/ref/part.png",
 "options": {"ensemble": {"structure_n": 5}}}
```

| key | what it does | default | range |
|---|---|---|---|
| `structure_n` | run the **cheap head** N times at varied seeds, pick the medoid grid, then pay for the expensive tail once | **1** (off) | 1–9 |
| `best_of` | run N **full** generations and choose between the finished meshes | **1** (off) | 1–5 |

Seeds are `seed`, `seed+1`, … — never random, so two runs of the same request
compare the same candidates. Both above 1 at once is **refused**: `best_of`
already draws N independent structures, so consensus inside each one would hand
every candidate the same grid and you would pay N full generations for N copies
of one mesh. Out of range is refused with the real range, never clamped.

### Why the two halves of the pipeline can be separated at all

The structure stage and the shape stage meet at one narrow place:

```
KSampler(structure) → VaeDecodeStructureTrellis2 → VOXEL → Trellis2ShapeStage
```

`VaeDecodeStructureTrellis2` emits a **plain dense boolean occupancy grid**,
`[B, R, R, R]` at R = 32 or 64, and `Trellis2ShapeStage` consumes it with
`torch.argwhere(...)`. Everything before that seam is **seconds**; everything
after it — shape cascade, upsample, texture, remesh, unwrap, bake — is the
two-to-four minute tail.

### Why the winner is replayed by seed, not re-injected as a grid

The obvious design is to capture the VOXEL from one `/prompt` and feed it into a
second one that starts at `Trellis2ShapeStage`. **Core cannot express that, and
meshgen may not add a node that can.** Searched across the whole install:
exactly four node classes touch the `VOXEL` type, and all four either produce
one or consume one — there is **no save node and no load node for it**. Writing
one would be a custom node, and `custom_nodes/` staying empty is the standing law
that keeps the licence audit clean (see [the nvdiffrast trap](#the-nvdiffrast-trap)).

What core *does* have is a lossless way **out**. `VoxelToMeshBasic` emits one
axis-aligned unit cube face per exposed voxel face at **integer lattice points**,
and `SaveGLB` writes those vertices through untouched (no node matrix, no
rotation — checked in core's `save_glb`). That mesh *is* the grid, and
`ensemble.occupancy_from_cube_mesh` inverts it exactly, with two details that are
load-bearing:

- **Interior voxels have no exposed face**, so the face set alone under-reports a
  solid block. The solid is recovered by **parity**: along any axis, the faces
  perpendicular to it sit exactly at the solid/empty transitions, so voxel *i* is
  solid iff an odd number of transitions lie at or below it.
- The answer is computed independently from **all three axis families and they
  must agree**. They cannot, unless the mesh really is a closed voxel boundary —
  which is the check that keeps a silently wrong grid, and the plausible-looking
  consensus table built on top of it, out of the report.

So the head is a **probe**, and the winner is re-run as one ordinary full
generation at the winning seed. That costs **one extra head execution** (the
winner's head runs twice) and needs nothing core does not already ship. It rests
on one assumption, and the assumption is measured rather than asserted:

> **Seed replay is deterministic here.** Three probes of seed 56 on `hard_steps`
> returned byte-identical grids — 4 752 occupied voxels each, pairwise IoU
> **1.000**. Re-check with `ab_ensemble.py repeat` after any ComfyUI or driver
> change; if it ever stops being 1.0, the structure tier is picking a seed whose
> grid it will not get back, and this README has to say so.

### The probe graph

Not a second committed template — a **pruning** of whichever graph the backend
would have run anyway (single-view or multi-view), by reachability from the
structure decode node, plus a small tail:

```
VaeDecodeStructureTrellis2 → VoxelToMeshBasic → SaveGLB      (the grid)
MaskPreview → MaskToImage → ImageCropToMask → SaveImage      (the drawing)
```

55 nodes become **31**. A template refresh therefore flows into the probe
automatically and the two can never drift — a stronger guarantee than the
derived-and-compared one `multiview_to_3d.json` gets. Reachability is the same
rule `tools/build_workflow.py` uses and for the same reason: this template wires
`PreviewImage` and `MaskPreview` as **pass-through data nodes**, so pruning by
"looks like a preview" guts the graph.

The mask branch is not decoration. It puts the prepared mask through **the same
`ImageCropToMask`** the model's input image went through, so the silhouette a
grid is scored against is framed exactly the way the model framed the picture —
reusing core's framing rather than re-deriving one. (`ImageCropToMask`
composites `image × mask` over the template's black background, and here the
image *is* the mask, so the saved pixel is `mask²` and the read-back threshold of
0.25 is `mask > 0.5`, not a guess.)

### How the pick is made

**Tier 1, `structure_n`** — silhouette gate, then medoid:

1. Each grid's silhouette is compared against the prepared drawing over the 24
   axis-aligned labellings (3 projection axes × 4 quarter-turns × 2 mirrors) — a
   search over *labelling*, not a free rotation fit, the same discipline
   `mesh_metrics.best_orientation` uses. The labelling is chosen **once**, by the
   candidate that explains the drawing best under any labelling, and every
   candidate is then scored under that one; candidates read different ways round
   are not comparable.
2. A grid more than **0.15 IoU** below the best is rejected as not explaining the
   drawing. The margin is **relative and has to be**: the camera behind a found
   image is unknown — a photo is perspective at an arbitrary azimuth, a grid is
   orthographic and axis-aligned — so the absolute number is not a quality score
   and is never treated as one. The gate **never leaves fewer than two
   survivors**: a gate that can empty the field decides the answer by itself.
   It runs *before* the consensus, because two bad draws that agree with each
   other would otherwise outvote three good ones.
3. The **medoid** of the survivors' pairwise grid IoU wins. Ties go to the higher
   silhouette, then to the lowest seed.

**Tier 2, `best_of`** — hard gates, then rank:

1. Candidates that produced no readable mesh are dropped.
2. Topology gates on self-intersections and shell count, **relative to the best
   in this batch** (3×, with a floor). Absolute gates are useless here and this
   README has the measurement that proves it — a *good* run of this pipeline
   exports 9 boundary and 210 non-manifold edges, so "must be manifold" would
   reject every candidate every time. The limit is anchored on the batch minimum,
   so the best candidate always survives; the gates can legitimately cut to a
   single survivor, and when they do the report says there was no consensus left
   to take.
3. Rank by silhouette IoU **rounded to 3 decimals**, then by symmetric-Chamfer
   medoid over 4 096 area-weighted surface points per mesh, then by
   `edge_sharpness`, then by seed. The rounding is the measured part: across the
   *entire* tuning sweep above, IoU moved only 0.9763 → 0.9801 while the meshes
   differed enormously. It is a guard that the object was not eaten, not a
   quality ordering — so rounding hands the decision to the consensus medoid
   wherever the silhouettes are indistinguishable, which is nearly always.
4. Every candidate mesh is **kept on disk** next to the output as
   `<name>_seed<N>.glb`. You paid for them.

### What the job tells you

`/job/<id>` carries the request block from the moment it is queued, and replaces
it with the finished verdict: the seeds tried, every candidate's numbers, who was
rejected and why, the pairwise agreement matrix, the winner, one sentence of
`why`, and `probe_ms` next to `wall_ms` so the cost is never hidden. A real run:

```
"why": "seed 58 is the medoid: its occupancy grid agrees with the other 2
        candidate(s) at mean IoU 0.763, the highest of the 3 compared
        (of 5 generated; 2 rejected on silhouette).",
"rejected": [{"seed": 57, "silhouette_iou": 0.1772,
              "reason": "silhouette IoU 0.177 is more than 0.15 below the best
                         candidate's 0.796 - this grid does not explain the drawing"}]
```

### Measured — RTX 5070 12 GB, ComfyUI v0.35.1, `trellis2`, base seed 56

**This table is PARTIAL and says so.** The sweep was stopped part-way when the
GPU was needed elsewhere; three of nine planned rows landed. What is here was
measured, and the gaps are named rather than estimated. Finish it with

```
service\.venv\Scripts\python.exe -m meshgen.tools.ab_ensemble run --out <dir>
service\.venv\Scripts\python.exe -m meshgen.tools.ab_ensemble report <dir>
```

which resumes from `results.jsonl` and re-runs only what is missing.

**Seed replay**: 3 probes of seed 56 on `hard_steps` agreed at grid IoU
**1.000, 1.000** — 4 752 occupied voxels every time. Probe cost **6.2 s** cold,
**1.5–1.6 s** for a repeat of a seed already drawn (ComfyUI caches the whole
identical graph). A *distinct* seed costs ~6 s.

| scene | arm | picked seed | IoU | sharp | creaseL | selfX | shells | probe | wall | VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `hard_steps` | `baseline` | 56 | 0.9755 | 0.284 | 57.9 | 0 | 38 | — | 177 s | 11.21 |
| `hard_steps` | `structure5` | **56** | 0.9753 | 0.284 | 57.8 | 3 | 35 | 32 s | 219 s | 11.21 |
| `hard_slab` | `structure5` | **58** | 0.8866 | 0.167 | 8.3 | 0 | 5 | 30 s | 105 s | 10.18 |
| `hard_slab` | `baseline` | — | *not run* | | | | | | | |
| `hard_steps` / `hard_slab` | `best_of3` | — | *not run* | | | | | | | |
| `organic_blob` | all three | — | *not run* | | | | | | | |

**Cost is ~6 s per distinct seed, and it is the only thing here that is fully
settled.** `structure_n: 5` added **32 s to a 177 s** job (+24%) and **30 s to a
75 s** job (+40%) — cheap in absolute terms, not free, and proportionally worse
the smaller the object. VRAM is unchanged: the probe is the same head the full
run already executes.

### What the partial table already shows

**On `hard_steps` the consensus picked the seed it started with**, and the mesh
is the baseline mesh: IoU 0.9755 → 0.9753, sharpness 0.284 → 0.284, crease length
57.9 → 57.8. Those deltas are **not** the ensemble — they are the *tail's* own
run-to-run wobble at a fixed seed (self-intersections 0 → 3, shells 38 → 35 with
identical face counts). Worth stating plainly: **the structure stage is
bit-deterministic at a fixed seed (IoU 1.000) but the shape/texture/remesh tail
is not quite.** So on this fixture the tier cost 42 s and bought nothing, which
is the honest average case and exactly what a consensus picker should do when the
first draw was already the typical one.

**On `hard_slab` it earned its keep, and this is the finding.** Five seeds gave
five very different structures — pairwise grid IoU **0.53–0.72** — and **two of
the five contradicted the drawing outright**, at silhouette IoU **0.177 and
0.182 against the best draw's 0.796**. Those two were rejected before the
consensus vote, and the medoid of the survivors was seed **58**, not the seed the
service would have used. The same spread showed on `hard_steps`: seeds 56/57/58
agreed at IoU 0.68 / 0.72 / 0.53.

So the honest reading of the tier is **insurance, not an upgrade**. It cannot
make the typical mesh better — it can only stop you shipping the atypical one,
and 2-in-5 draws being unusable on `hard_slab` is the measured size of that risk.
Whether the insurance is worth 6 s per seed is the caller's call, which is why
both tiers default to 1 rather than being switched on for everybody.

**Not yet measured, and not guessed at:** whether the picked mesh scores better
than the baseline mesh on a scene where the pick actually differs (`hard_slab`'s
baseline row is the missing half of that comparison), anything at all about
`best_of` on real output, and the organic control. `best_of` has been exercised
only as far as submitting and running its candidates; its gates, its Chamfer
medoid and its winner-copy are covered by offline tests against synthetic
candidates, not yet by a GPU run.

The harness is `tools/ab_ensemble.py`, same contract as `ab_tuning.py` — real
jobs through the live service, scored by `mesh_metrics` against the fixtures'
exact ground truth, one JSONL row at a time so an interrupted sweep resumes, and
**exit 0 with a reason** on a machine with no GPU and no weights:

```
service\.venv\Scripts\python.exe -m meshgen.tools.ab_ensemble repeat --out <dir>
service\.venv\Scripts\python.exe -m meshgen.tools.ab_ensemble run    --out <dir>
service\.venv\Scripts\python.exe -m meshgen.tools.ab_ensemble report <dir>
```

`repeat` is the load-bearing one and should be run first: it is the seed-replay
determinism check the whole structure tier rests on.

**One dependency note.** meshgen's single-shot request path is stdlib-only and
stays that way. The picker is geometry and needs **numpy** (and **PIL**, to read
the prepared silhouette back) — both already in `service/.venv`. `meshgen.ensemble`
is imported lazily, only when an ensemble is actually requested, and a machine
missing either gets a sentence rather than a single-seed mesh that quietly
pretends it was chosen.

---

## Multi-view (front + side + back) — LIVE

**Status: runs end to end, measured on this machine (2026-09-13).** It was
blocked through ComfyUI core v0.34.x; core **v0.35.0** added
`Pixal3DMultiViewConditioning` and that was the missing piece.

```json
POST /generate3d
{"views": {"front": "C:/ref/front.png",
           "side":  "C:/ref/side.png",
           "back":  "C:/ref/back.png"},
 "backend": "pixal3d"}
```

`front` is required — core poses the mesh to the first connected view. `image_path`
may be omitted when `views` is present (front stands in); giving both a different
value is a 400, not a guess. `views` is accepted top-level or inside `options`.
**Single `image_path` behaviour is completely unchanged** — a request without
`views` takes exactly the code path it took before, and a test pins that.

Only `pixal3d` accepts views. `trellis2` refuses them and names the backend that
does, rather than quietly ignoring the extra images. 2, 3 or 4 views; one view
alone is refused with a pointer to `image_path`, because the single-image path
estimates the camera FOV with MoGe instead of assuming a rig.

### Which name is which camera — read this before wiring anything

ComfyUI core's own table, from `comfy_extras/nodes_trellis2.py`:

```python
_VIEW_AZIMUTHS = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}
```

| meshgen name | azimuth | core socket | camera sits at |
|---|---|---|---|
| `front` *(required)* | 0° | `front` | −Y, in front of the object |
| `left` | 90° | `left` | +X, off the object's **left** |
| `back` | 180° | `back` | +Y, behind |
| `right` | 270° | `right` | −X, off the object's **right** |
| **`side`** | **270°** | **`right`** | alias of `right` — **the object's RIGHT side** |

**meshgen used to map `right`→90 and `left`→270 — exactly swapped**, a 180°
error on both side cameras, fixed 2026-09-13. There is nothing to catch such a
mistake at runtime: a correctly named socket handed the other side's image
raises nothing and returns a confidently wrong mesh. So three things guard it
now: `VIEW_AZIMUTHS` is *derived* from core's dict rather than written out
again, views are wired to sockets **by azimuth value** (`core_socket_for_azimuth`)
and never by name, and the orientation of a real run was measured (below).

`side` is meshgen's own name and core has no equivalent. It means **the object's
right side**. Ask for `left` by name if you want the other one; giving both
`side` and `right` is refused rather than guessed.

### The graph

`workflows/multiview_to_3d.json` is **derived** from `image_to_3d.json` by
`tools/build_multiview_workflow.py`, never hand-edited, and a test regenerates it
in memory and compares. The transform is four steps:

1. The per-view preparation chain (`LoadImage` → `RemoveBackground` → mask →
   `ImageCropToMask` 1024² at `pad_factor=1.1` → the pass-through preview) is
   cloned three more times; the background-removal model is loaded once and
   shared. **The crop contract did not have to move**: core's node documents
   exactly that framing and uses the same `_VIEW_PAD = 1.1`, so the existing
   single-view preparation feeds all four sockets unchanged.
2. `Pixal3DConditioning` → `Pixal3DMultiViewConditioning`, same two outputs, so
   everything downstream is untouched. `fov` is 20.0 for rig renders.
3. **MoGe is deleted.** Its only consumer was the single-view node's per-image
   FOV estimate; a rig has a known FOV. A multi-view run never loads that 0.62 GB
   weight and never spends VRAM on it.
4. The Pixal3D `UNETLoader` is pointed at
   `pixal3d_multiview_int8_convrot.safetensors`. **This is the step that must
   never be skipped** — the two checkpoints are byte-for-byte the same size and
   the same architecture repacked, so the single-view one loads without complaint
   and then conditions every view as the front.

At request time the chains for views the caller did not send are **deleted**,
socket and all: core's inputs are optional and an optional input must be absent,
not present-and-dangling. A test walks the pruned graph and asserts no link
points at a node that is gone.

### Measured — RTX 5070 12 GB, ComfyUI v0.35.1

The test object is `tools/make_rig_views.py`: a 1.0 × 0.25 × 0.5 slab with a tall
fin on one end and a low foot on the other, rendered on core's own rig. Three
distinct extents and a lopsided profile, so the output mesh's own geometry says
whether the views landed on the right cameras.

| | 1 view (`image_path`) | 2 views | 3 views | 4 views |
|---|---|---|---|---|
| wall clock | 98.6 s | **82.4 s** | **91.0 s** | **106.3 s** |
| peak VRAM (`nvidia-smi`, 200 ms) | — | **10.88 GB** | **10.94 GB** | **11.12 GB** of 11.94 |
| …above idle | — | 8.50 GB | 8.47 GB | 8.70 GB |
| output | 199,960 tris | 199,972 tris | — | 199,966 tris |

Two things worth knowing:

- **Extra views cost time, not VRAM.** 2 → 4 views adds ~24 s but only ~0.24 GB
  of VRAM. That matches the code: the per-view cost is three NAF high-res feature
  maps (two 512², one 1024², 1024 channels) allocated on
  `comfy.model_management.intermediate_device()`, which is **CPU** on a 12 GB
  card. The number that actually grows is system RAM — ComfyUI's peak working set
  reached **21.0 GB of 31.6 GB** across these runs.
- **`job.vram.peak_gb` under-reports here.** meshgen samples ComfyUI's device
  report on a coarse interval and returned 9.4–10.5 GB for the same runs
  `nvidia-smi` caught at 10.9–11.1 GB. Use the table above for headroom
  decisions.

### Orientation: verified against a real mesh, not just the source

The bounding box of the 2-view mesh, normalised against its longest axis, versus
the object that was rendered:

```
object   0.8696 / 1.0000 / 0.2174     (front width / height / side width)
2 views  0.8745 / 1.0000 / 0.2255
4 views  0.8837 / 1.0000 / 0.2303
```

Front-width ÷ side-width came out **3.88** (2 views) and **3.84** (4 views)
against the object's 4.00. A front/side confusion — the 90° class of error —
would have inverted that to ~0.25. **It did not.**

The left↔right swap is a *mirror*, so a bounding box cannot see it. The
discriminator is depth: the fin is on the object's front half and the foot on its
back half. Mean depth of the fin minus mean depth of the foot:

| run | fin − foot depth |
|---|---|
| 2 views (front + side) | **+0.116** |
| 4 views, correct | **+0.113** |
| 4 views, **left/right deliberately swapped** | **−0.098** |

The correct runs agree with each other; feeding the 90° socket the 270° render —
which is precisely what the old mapping did — flips the sign. That is the
180° error, caught quantitatively on real output.

**And the control that shows why any of this is worth doing:** the same front
image through the plain single-image path produced 0.585 / 0.659 / 1.000 — the
thin slab came out a chunky blob, depth off by roughly 3×. Multi-view recovered
the real proportions to within 4%.

### The camera rig is verified, not guessed

`backends/multiview.py` builds the `transforms.json` upstream's `inference_mv.py`
reads. It is not sent to ComfyUI — core rebuilds the identical rig internally
from the connected sockets and `fov` — but it stays because it is the independent
check that a socket assignment means the camera we think it does, and because it
is what the upstream escape hatch would consume. It agrees with **three**
sources:

- `transform_matrix(0°, d)` reproduces core's `_PROJ_FRONT_VIEW_TRANSFORM` exactly;
- all four azimuths reproduce core's own `_orbit_camera_to_world`;
- all four reproduce the `transforms.json` TencentARC ships in
  `assets/mv_images/example/`, to float32 precision.

Camera distance falls out of the same check. Upstream ships
`3.1192049980163574` at a 20° FOV, which is `0.55 / tan(FOV/2)` — the `0.55`
being Pixal3D's **1.1 crop padding** on a 0.5 half-extent. Core's multi-view node
computes `_VIEW_PAD * 0.5 / tan(fov/2)` with `_VIEW_PAD = 1.1`: the same number.
Core's *single*-view path uses `0.5 / tan(FOV/2)` instead, because there the crop
padding is already in the image. Our double-precision value lands 2.8e-9 from
upstream's, which is float32 dust — upstream serialises its rig through float32.

These are pytest fixtures, so they hold with no GPU, no weights and no network,
**and** they are re-run against the real installed ComfyUI when it is present.

### The working envelope, and why the defaults did not change

Multi-view inherits `VRAM_SAFE_DEFAULTS` unchanged, and nothing new was invented:

- **4 views is the ceiling** — core has four cameras, and 4 views at the meshgen
  defaults peaks at 11.12 GB of 11.94, about **0.8 GB of headroom**. That headroom
  is shared with whatever else is on the GPU (~2.4 GB of desktop here), so a
  heavy browser is the thing most likely to push a 4-view run over. Drop to 3 or
  2 views and it is ~0.2 GB roomier.
- **`shape_resolution: "1536"` still OOMs**, multi-view or not — the remesh stage
  is the ceiling and views do not move it. Same for `remesh_resolution: 768`.
- **System RAM is the real multi-view budget**: 21.0 GB peak of 31.6 GB at these
  settings. A 16 GB machine should expect to swap at 4 views.

### If it is not available

`/health` carries the verdict per backend, and `POST /generate3d` with `views` on
a machine that cannot run one is a **400 before anything is queued and before
ComfyUI is ever started**, naming the file, the path, the size, the URL, the
sha256 and the approval rule. Opt in to a fallback instead:

```json
{"views": {...}, "options": {"on_unavailable": "front_only"}}
```

which does a normal single-image generation from the front view and **says so in
the result** — `multiview.used: false`, `fell_back_to: "front"`, the views that
were ignored, and the rig it would have used:

```
"honesty": "3 views were given and 2 of them were ignored - this mesh was
            generated from the front image alone."
```

The default is `"error"`: silently making a worse mesh than the caller asked for
is the one thing this must never do. The single-view control above is the
measured reason that matters.

### Why not the official Pixal3D repo

`TencentARC/Pixal3D`'s `inference_mv.py` remains the escape hatch and remains
unused — see [Licensing](#why-multi-view-is-not-implemented-against-the-official-pixal3d-repo).
ComfyUI core got there first.

---

## Licensing — read this before shipping anything

### Standing law: re-audit after every ComfyUI update

A ComfyUI upgrade can pull new transitive dependencies, so the licence audit is
not a one-off. After any change to `C:\forge-models\comfyui`, enumerate the venv
and check `custom_nodes/`:

```
C:\forge-models\comfyui\.venv\Scripts\python.exe -m pip list --format=freeze
dir C:\forge-models\comfyui\custom_nodes
```

**Audit of 2026-09-13, ComfyUI v0.34.0 → v0.35.1** (`12d52794` → `856a922b`):

| check | result |
|---|---|
| installed packages | **86** |
| `nvdiffrast` / `diffoctreerast` / `diff-gaussian-rasterization` / `simple-knn` | **0 — none present** |
| other NC-licensed 3D packs (`kaolin`, `pytorch3d`, `vox2seq`, `flexicubes`, `xatlas`, `gsplat`, `natten`, `RMBG`/`BriaRMBG`, `utils3d`, `nerfacc`, `tiny-cuda-nn`) | **0 — none present** |
| dist metadata containing "noncommercial" / "research only" / "CC-BY-NC" / "NVIDIA Source Code License" | **0 flagged** |
| third-party node packs in `custom_nodes/` | **0** — only ComfyUI's own `websocket_image_save.py` and `example_node.py.example` |

**Verdict: clean.** Everything the graph executes is MIT / Apache-2.0 / BSD /
PSF / MPL, under GPL-3.0 ComfyUI run as a separate process. The v0.35.1 update
moved five pinned packages (`comfyui-frontend-package` 1.49.6→1.51.10,
`comfyui-workflow-templates` 0.11.48→0.11.59, `comfyui-embedded-docs`
0.5.10→0.5.11, `comfy-kitchen` 0.2.31→0.2.33, `comfy-aimdo` 0.4.15→0.5.3) and
nothing else; torch stayed 2.14.0+cu130. No new dependency was added by meshgen.

### The nvdiffrast trap

The original TRELLIS research code depends on **nvdiffrast** / **nvdiffrec** and
**RMBG**, which are *non-commercial*. ComfyUI's core TRELLIS.2 nodes are a native
reimplementation written specifically to replace them — background removal is
BiRefNet, rasterisation is ComfyUI's own. That is the entire reason this service
uses core nodes.

**Therefore: meshgen never installs custom nodes.** Do not install
`visualbruno/ComfyUI-Trellis2` or any other third-party 3D node pack into
`C:\forge-models\comfyui\custom_nodes\` — they pull the non-commercial
dependencies straight back in and quietly contaminate output you intended to
sell. The clone's `custom_nodes/` should stay empty.

### Why multi-view is not implemented against the official Pixal3D repo

*(Moot since 2026-09-13 — ComfyUI core v0.35.0 ships the node and multi-view runs
on core. Kept because the reasoning still governs any future "just use the
research repo" temptation.)*

`TencentARC/Pixal3D` is **MIT** — licensing is not the objection, and its
`inference_mv.py` is the reference implementation of exactly what Phase 18(a)
wants. It was still rejected, for reasons worth writing down:

- Its install requires **`natten==0.21.0` built from source** with
  `NATTEN_CUDA_ARCH` set per GPU (`--no-build-isolation`), on top of a second
  multi-GB torch environment. This machine is Windows + Python 3.14 + cu130,
  which is not where that build is exercised.
- It is a second, parallel model runtime. meshgen's whole shape is *one*
  heavyweight host reached over HTTP; a second one is not a `meshgen/` change.
- The 12 GB budget is unproven for it either way.

So: shipped the request surface and the verified rig, refused the run honestly,
and left the repo path documented as the escape hatch if ComfyUI core never added
a per-view camera node. Core added one three weeks later.

### DINOv3 attribution

The image encoder is Meta's **DINOv3**. Using the *generated assets* carries no
obligation. **Redistributing a derivative model** — anything you fine-tune,
distil or otherwise derive from these weights — requires the
**"Built with DINOv3"** attribution under Meta's DINOv3 licence. Shipping printed
parts, meshes or renders made with meshgen does not.

### What each piece is under

- Weights (all seven files): **MIT**, from the official `Comfy-Org` Hugging Face orgs.
- ComfyUI itself (the code executing the graph): **GPL-3.0**. It runs as a separate
  child process reached over HTTP, which is the arrangement meshgen deliberately
  keeps — the repo contains no ComfyUI code.
- This adapter: same licence as the rest of Forge.

---

## How it works

ComfyUI runs as a **hidden child process** on port 8188, started lazily the first
time a job actually needs it (`CREATE_NO_WINDOW`, `--disable-auto-launch`,
`--listen 127.0.0.1`). Nothing spawns at service start, so `/health` is instant
and costs no VRAM.

`comfyui_client.py` owns that child plus the HTTP conversation: it stages the
input image into ComfyUI's input directory, submits the workflow with the job's
own UUID as ComfyUI's `prompt_id` (one id addresses both services), follows
`/api/jobs/<id>` for state and the websocket for per-step progress, then copies
the produced `.glb` out to wherever the caller asked.

Two runtime facts about ComfyUI that cost real debugging here, both now handled
and covered by tests:

- **ComfyUI re-execs itself on Windows.** The process meshgen spawns is only a
  launcher; a grandchild is what actually binds 8188. Terminating the parent
  leaves a stray holding the port, so `stop()` kills the whole tree.
- **`Save3DAdvanced` reports its output as a bare relative path string**
  (`{"result": ["forge/trellis2_00001.glb", null, []]}`), not the usual
  `{"filename", "subfolder", "type"}` dict that image save nodes emit. Parsing
  only the dict form loses the mesh at the last step, after minutes of GPU work.

### What `progress` means

`progress` is the fraction **through the current stage**, not through the whole
job, and it resets each time the pipeline moves on — ComfyUI reports per-node
step counts and nothing more. `stage` names the node doing the work
(`Trellis2UpsampleStage`, `RemeshMesh`, `UnwrapMesh`, …), so the pair reads as
"which step, and how far into it". A UI should show the stage name and treat the
bar as within-stage. If the websocket cannot be reached the job still runs;
`progress` simply stays `null` until it finishes.

`workflows/image_to_3d.json` is the **official ComfyUI template**
(`3d_pixal3d_trellis2_image_to_model`) converted to API format and pruned to the
55 nodes that actually feed the save node.

### Regenerating the workflow after a ComfyUI upgrade

```
# with ComfyUI running on 8188
C:\forge-models\comfyui\.venv\Scripts\python.exe meshgen\tools\build_workflow.py ^
  C:\forge-models\comfyui\.venv\Lib\site-packages\comfyui_workflow_templates_json\templates\3d_pixal3d_trellis2_image_to_model.json ^
  meshgen\workflows\image_to_3d.json
```

The converter refuses to write a graph it is not sure about. Three things about
ComfyUI's UI format are load-bearing and easy to get wrong — it handles all three
and asserts every widget value was consumed:

1. A widget dragged out into a socket **still occupies its slot** in
   `widgets_values` (marked by a `widget: {name}` key). Skipping it shifts every
   later value by one — silently producing a valid graph with wrong numbers.
2. `COMFY_DYNAMICCOMBO_V3` eats its own slot **plus one per sub-input of the
   selected option**, and serialises as namespaced siblings (`sign_mode.qef`).
3. `control_after_generate` (seeds) and `image_upload` (LoadImage) each add a
   frontend-only slot with no backend input.

And a trap worth naming: in this template `PreviewImage`, `MaskPreview` and
`GetMeshInfo` are wired as **pass-through data nodes** — `ApplyTextureToMesh`
reads its base colour straight out of a `PreviewImage`. Pruning "preview-looking"
nodes guts the graph. Reachability from the save node is the only safe rule, and
that is what the converter uses.

If node ids move, `test_workflow_template_has_the_nodes_the_adapters_patch`
fails and the `NODE_*` constants at the top of `backends/comfyui_base.py` need
updating.

**A refresh overwrites the tuned surface values with the demo's again** — it
rewrites the whole file from the template. Re-apply `TUNED_DEFAULTS`
(`RemeshMesh.smooth_iters: 3`, `sign_mode.qef: true`; see
[Tuning the surface](#tuning-the-surface-what-was-measured)) before regenerating
the sibling. `test_the_committed_templates_carry_the_tuned_values` fails until
you do, in both templates. Behaviour is safe either way — `TUNED_DEFAULTS` is
applied on top of whatever the JSON says, exactly like `VRAM_SAFE_DEFAULTS`, so
a forgotten re-apply cannot make meshgen *run* the demo's numbers; it would only
leave the committed graph lying to the next person who reads it.

Then regenerate the multi-view sibling from it — it is derived, not maintained:

```
service\.venv\Scripts\python.exe meshgen\tools\build_multiview_workflow.py
```

That script refuses to write if any of the eleven nodes it depends on changed
class, and `test_the_multiview_template_is_exactly_what_the_generator_produces`
rebuilds it in memory and compares, so the committed JSON cannot drift from the
single-view template it came from.

---

## Adding a backend

One file in `backends/`, three methods:

```python
from .base import Backend

class MyBackend(Backend):
    name = "my-model"
    model = "My Model v1"
    license = "Apache-2.0"
    vram_gb = 8

    def readiness(self):
        # {"ready": bool, "missing": [{"what", "path", "source", "bytes"}]}
        return self._check_files([...])

    def generate(self, image_path, options, out_path, progress=None,
                 cancel_event=None, job_id=None):
        self.ensure_ready()
        ...
        return {"mesh_path": str(out_path), "stats": {...}, "backend": self.name}
```

Then add it to `COMFYUI_BACKENDS` in `backends/__init__.py` (or, if it does not
host on ComfyUI, to a list of its own). `ensure_ready()` must **report before it
fetches** and raise `NotReady` rather than downloading gigabytes unasked —
`/health` turns that list into the "here is what is missing and where to get it"
message.

Backends that need heavyweight dependencies get **their own venv**, exactly as
the ComfyUI pair does, so nothing leaks into the other Forge services.

Adapters can also be injected without touching the registry via
`FORGE_MESHGEN_BACKEND_MODULES=pkg.module` (comma-separated) — which is how the
tests supply their fake backend.

---

## Running it

Normally `start_forge.ps1` handles this — it starts meshgen only if the
configured paths exist, so a machine without the models is unaffected.

By hand:

```
service\.venv\Scripts\python.exe -m meshgen
```

`stop_forge.ps1` stops meshgen on 8902 **and** the ComfyUI child on 8188.

### Tests

```
service\.venv\Scripts\python.exe -m pytest meshgen/tests -q
```

**223 tests** (39 `test_service.py` + 53 `test_multiview.py` + 61
`test_tuning.py` + 70 `test_ensemble.py`), all runnable with **none** of the 23.8 GB present — they exercise the job API, config resolution,
option validation, cancellation, the missing-models guidance, both ComfyUI
history output shapes and the VRAM-safe defaults, against a fake adapter that
writes a real one-triangle `.glb`.

The multi-view half adds view validation, the azimuth mapping against core's own
`_VIEW_AZIMUTHS`, the camera rig against its upstream golden fixtures, the
socket-by-azimuth wiring, the derived workflow template, chain pruning, the
availability verdict, the clean 400, the `front_only` fallback, and a guard that
single-image behaviour is unchanged. Three re-check their golden fixtures against
the **real installed ComfyUI** when it is present and skip when it is not — so
they are evidence on this machine and still green on a machine without the
models. One of those, `test_the_node_probe_reads_the_real_installed_comfyui`,
**had its polarity flipped** on 2026-09-13: it used to fail the day core shipped
a multi-view node, and now fails if a downgrade takes it away again.

`test_tuning.py` covers the two halves that can go wrong independently. The
**option surface**: every new key reaches the node it claims to (and
`crease_angle` reaches *both* `MeshSmoothNormals` nodes), `qef` writes the
DynamicCombo's namespaced `sign_mode.qef` key rather than a nested object that
would be ignored, a non-boolean is refused, an out-of-range value is refused
*with the real range*, and the `hard_surface` macro expands without overruling an
explicit option next to it. And the **metrics themselves**, against solids whose
answer is known in closed form — a welded cube must score `edge_sharpness` 1.0
with a 90° p99, a sphere 0.0, a sphere must not be alignable onto a slab's
silhouette, welding must recover a UV-split export's real topology, and a
zero-area face must not count as a perfect crease. A scoring harness that is
itself wrong is worse than none: it produces a table, and the table is believed.

The tuned values are pinned against both committed workflow templates, so a
future `tools/build_workflow.py` refresh that restores the demo's numbers fails
a test instead of quietly shipping rounder meshes.

`test_ensemble.py` covers the three halves of the seed ensemble that can go wrong
independently. **The probe graph** is a pruning, so a mistake there does not
raise — it produces a graph that runs and samples something slightly different
from what the full run will sample, and every consensus number computed on top of
it is then about the wrong thing; so the pruned graph is walked for dangling
links, the sampler chain is asserted present and the expensive tail asserted
gone, in both the single-view and the multi-view template. **The pickers** are
tested against grids and meshes whose answer is known by construction: a grid the
others agree with must be the medoid, a grid that contradicts the drawing must be
rejected *before* the consensus, a gate must never empty the field, and the
answer must not depend on the order the candidates arrived in. **The inverse of
`VoxelToMeshBasic`** is tested against a numpy transcription of core's own
`voxel_to_mesh`, over random grids *with a solid interior block* — the interior
is exactly what a naive reading of the face set loses, and without that block the
test would pass while being wrong.

The real-model runs are manual gates, not pytest: they need the GPU and take
minutes. The numbers they produced are in
[Multi-view](#multi-view-front--side--back--live) and
[Tuning the surface](#tuning-the-surface-what-was-measured); reproduce them with
`tools/make_rig_views.py` and `tools/ab_tuning.py run`.
