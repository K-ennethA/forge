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

## Where the 18.5 GB lives

**Nothing heavyweight is in this repo, and nothing heavyweight is in OneDrive.**
The repo sits under OneDrive; the models deliberately do not.

```
C:\forge-models\                     (outside the repo, outside OneDrive)
  comfyui\                           git clone, pinned to tag v0.34.0
    .venv\                           its own venv: Python 3.14.3 + torch 2.14.0+cu130
    extra_model_paths.yaml           points ComfyUI at ..\models instead of copying
  models\
    diffusion_models\  trellis_2_int8_convrot.safetensors      4.89 GB
                       pixal3d_int8_convrot.safetensors        5.20 GB
    vae\               trellis_2_shape_vae_bf16.safetensors     1.02 GB
                       trellis_2_texture_vae_bf16.safetensors   0.88 GB
    clip_vision\       dino_v3_L_naf_fp32.safetensors           1.13 GB
    background_removal\birefnet.safetensors                     0.41 GB
    geometry_estimation\moge_2_vitl_normal_fp16.safetensors     0.62 GB
  comfyui-input\                     staged copies of input images
  comfyui-output\                    where ComfyUI writes before we collect
```

Not present, and not fetched: `pixal3d_multiview_int8_convrot.safetensors`
(5.20 GB) — see [Multi-view](#multi-view-front--side--back) for why downloading
it would not be enough on its own.

Measured on disk: **14.16 GB of weights** plus **4.29 GB** of ComfyUI and its venv
(PyTorch 2.14.0+cu130 is most of it) — **18.45 GB total**.

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
| `pixal3d` | Pixal3D int8 convrot | MIT — `Comfy-Org/Pixal3D` | pixel-aligned; **the only backend that needs MoGe** |

Both were run end to end on the 12 GB card and both work — see the measured
numbers below.

The template's switch node evaluates lazily, so a TRELLIS.2 job never touches the
MoGe branch. `/health` reflects that: MoGe is listed as missing for `pixal3d`
only, and someone who only wants TRELLIS.2 can skip that 0.62 GB download.

Both run **entirely on ComfyUI's own core nodes** (`comfy_extras/nodes_trellis2.py`,
`comfy/ldm/trellis2/`, `comfy_extras/mesh3d/`) and share one official workflow
that selects between them with a boolean — which is why the two adapters differ
by about ten lines and inherit everything else from `backends/comfyui_base.py`.

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
alongside `views` (see [Multi-view](#multi-view-front--side--back)):
`on_unavailable` (`"error"` / `"front_only"`), `multiview_fov_deg` (20.0), and
`views` itself, which may be sent top-level or inside `options`.

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

## Multi-view (front + side + back)

**Status: the request surface is real and tested; the generation is blocked, and
the blocker is not only a download.** Read this section before promising anyone
multi-view.

```json
POST /generate3d
{"views": {"front": "C:/ref/front.png",
           "side":  "C:/ref/side.png",
           "back":  "C:/ref/back.png"},
 "backend": "pixal3d"}
```

`front` is required — Pixal3D's rig treats the first frame as the canonical front
view and orbits everything else from it. `side` (= `right`, 90°), `back` (180°)
and `left` (270°) are optional. `image_path` may be omitted when `views` is
present (front stands in); giving both a different value is a 400, not a guess.
`views` is accepted top-level or inside `options`. **Single `image_path`
behaviour is completely unchanged** — a request without `views` takes exactly the
code path it took before, and a test pins that.

Only `pixal3d` accepts views. `trellis2` refuses them and names the backend that
does, rather than quietly ignoring the extra images.

### Why it does not run here

Two things are missing, and **only one of them is a file**:

1. **The weights.** `pixal3d_multiview_int8_convrot.safetensors`, 5 584 555 824
   bytes (5.20 GB), sha256 `6b1eb332…f477`, from
   `Comfy-Org/Pixal3D/diffusion_models/`. **Not on this machine, and meshgen has
   not fetched it.** It needs approval like every other multi-GB download.

2. **A node that can express a per-view camera — and this one has no download.**
   ComfyUI core v0.34.0 ships exactly one Pixal3D conditioning node,
   `Pixal3DConditioning`, whose only camera input is a single scalar
   `camera_angle_x`. The extrinsics come from a module constant in
   `comfy/ldm/trellis2/model.py`:

   ```python
   _PROJ_FRONT_VIEW_TRANSFORM = [[1, 0,  0,  0],
                                 [0, 0, -1, -2],
                                 [0, 1,  0,  0],
                                 [0, 0,  0,  1]]

   def build_proj_transform_matrix(distance, batch_size, ...):
       T = _PROJ_FRONT_VIEW_TRANSFORM.expand(batch_size, -1, -1).clone()
       T[:, 1, 3] = -distance      # distance only. no rotation. anywhere.
   ```

   There is no rotation parameter and no view-aggregation step in core at all.
   Handing that node a batch of four images does **not** fuse four views of one
   object — `Trellis2ShapeStage` allocates `torch.zeros(batch_size, 32, …)`, one
   latent per batch element, and `_back_project_to_tokens` loops over the batch
   writing into disjoint slices. You get four separate objects, each wrongly
   treated as a front view.

**So downloading the weights would not unlock this.** Worse: the multi-view file
is byte-for-byte the same *size* as the single-view checkpoint (both
5 584 555 824) — the same architecture repacked — so core would happily load it
and then condition every view as the front. That failure mode is confident
garbage, not an error message. Which is exactly why meshgen refuses instead.

`/health` reports both, and `POST /generate3d` with `views` is a **400 before
anything is queued and before ComfyUI is ever started**, naming the file, the
path it looked at, the size, the URL, the sha256 and the approval rule.

### What it does today

```json
{"views": {...}, "options": {"on_unavailable": "front_only"}}
```

falls back to a normal single-image generation from the front view, and **says
so in the result** — `multiview.used: false`, `fell_back_to: "front"`, the list
of views that were ignored, and the rig it would have used:

```
"honesty": "3 views were given and 2 of them were ignored - this mesh was
            generated from the front image alone."
```

The default is `"error"`: silently making a worse mesh than the caller asked for
is the one thing this must never do.

### The camera rig is verified, not guessed

`backends/multiview.py` builds the `transforms.json` upstream's `inference_mv.py`
reads, and it is checked against **two independent upstream sources** rather than
against a mesh we cannot generate:

- `transform_matrix(0°, d)` reproduces ComfyUI core's own
  `_PROJ_FRONT_VIEW_TRANSFORM` exactly, and
- the full 4-view rig reproduces the `transforms.json` TencentARC ships in
  `assets/mv_images/example/` — all four matrices, at 0°/90°/180°/270°, to
  float32 precision.

Camera distance falls out of the same check. Upstream ships
`3.1192049980163574` at a 20° FOV, which is `0.55 / tan(FOV/2)` — the `0.55`
being Pixal3D's **1.1 crop padding** on a 0.5 half-extent (core's
`ImageCropToMask` tooltip: *"pad_factor=1.1 for Pixal3D"*). Core's *single*-view
path uses `0.5 / tan(FOV/2)` instead, because there the crop padding is already
in the image. Our double-precision value lands 2.8e-9 from upstream's, which is
float32 dust — upstream serialises its rig through float32.

Both checks are pytest fixtures, so they hold with no GPU, no weights and no
network, **and** they are re-run against the real installed ComfyUI when it is
present. `test_the_node_probe_reads_the_real_installed_comfyui` fails the day
core ships a multi-view node — which is the day to build the workflow template.

Deliberately **not** built: an actual multi-view ComfyUI graph. Wiring N
`LoadImage` nodes into a conditioning node whose inputs nobody has seen would be
inventing an interface and calling it verified. `multiview.stage_plan()` holds
everything that *is* knowable — which files, in what order, at which azimuth,
with which matrix — which is precisely what such a node will consume.

### Configurations upstream actually supports

`inference_mv.py --num_views N` takes the **first N frames** of `transforms.json`,
so any prefix of the 4-view orbit is a legal run: 2 views (front+side), 3
(front+side+back), or 4. Four is the only one upstream ships an example for, and
the 90° orbit at 20° FOV with elevation 0 is the only rig it ships. **None of
these has been executed here** — no weights, no node. Treat "2 views work well"
as unverified until someone runs it.

### VRAM: projected, not measured

Nothing below was measured, because nothing could be run. It is arithmetic off
the code, and it is here so the first real run has something to check against —
**not** as a basis for setting defaults. No multi-view defaults have been
invented.

- **Resident weights: unchanged.** The multi-view checkpoint is the same 5.20 GB
  as the single-view one.
- **Per-view cost is the conditioning, not the UNet.** `Pixal3DConditioning`
  builds and holds three NAF high-res feature maps per image for the whole run —
  two at 512² and one at 1024², at DINOv3 ViT-L's 1024 channels. At 16-bit that
  is `1024·1024·1024·2` = 2 GiB plus 2 × 0.5 GiB ≈ **3 GiB per view** (≈6 at
  fp32).
- **Mostly RAM, not VRAM.** Those tensors are allocated on
  `comfy.model_management.intermediate_device()`, which is CPU on a 12 GB card;
  VRAM grows where each stage's features move to the compute device. So the
  4-view figure to watch on this machine is ≈12 GiB of *system* RAM (of 32),
  with VRAM growth smaller but unquantified.
- **Measure before trusting any of that.** The single-image `pixal3d` numbers
  (249 s, 9.30 GB peak) are measured; these are not.

---

## Licensing — read this before shipping anything

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
and left the repo path documented as the escape hatch if ComfyUI core never adds
a per-view camera node.

### DINOv3 attribution

The image encoder is Meta's **DINOv3**. Using the *generated assets* carries no
obligation. **Redistributing a derivative model** — anything you fine-tune,
distil or otherwise derive from these weights — requires the
**"Built with DINOv3"** attribution under Meta's DINOv3 licence. Shipping printed
parts, meshes or renders made with meshgen does not.

### What each piece is under

- Weights (all six files): **MIT**, from the official `Comfy-Org` Hugging Face orgs.
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

**80 tests** (39 `test_service.py` + 41 `test_multiview.py`), all runnable with
**none** of the 18.5 GB present — they exercise the job API, config resolution,
option validation, cancellation, the missing-models guidance, both ComfyUI
history output shapes and the VRAM-safe defaults, against a fake adapter that
writes a real one-triangle `.glb`.

The multi-view half adds view validation, the camera rig against its two upstream
golden fixtures, the availability verdict, the clean 400, the `front_only`
fallback, and a guard that single-image behaviour is unchanged. Two of them
re-check the golden fixtures against the **real installed ComfyUI** when it is
present, and skip when it is not — so they are evidence on this machine and still
green on a machine without the models.

The real-model run is a manual gate, not a pytest: it needs the GPU and takes
minutes. There is **no measured multi-view run** — see the VRAM note above.
