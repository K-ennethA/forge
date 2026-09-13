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

Three more steer meshgen rather than a node in the graph, and only mean anything
alongside `views` (see [Multi-view](#multi-view-front--side--back--live)):
`on_unavailable` (`"error"` / `"front_only"`), `multiview_fov_deg` (20.0 — the
rig's FOV; it reaches `Pixal3DMultiViewConditioning.fov` directly), and `views`
itself, which may be sent top-level or inside `options`.

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

**92 tests** (39 `test_service.py` + 53 `test_multiview.py`), all runnable with
**none** of the 23.8 GB present — they exercise the job API, config resolution,
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

The real-model run is a manual gate, not a pytest: it needs the GPU and takes
minutes. The numbers it produced are in
[Multi-view](#multi-view-front--side--back--live); reproduce them with
`tools/make_rig_views.py`.
