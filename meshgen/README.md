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

39 tests, all runnable with **none** of the 18.5 GB present — they exercise the job
API, config resolution, option validation, cancellation, the missing-models
guidance, both ComfyUI history output shapes and the VRAM-safe defaults, against
a fake adapter that writes a real one-triangle `.glb`. The real-model run is a
manual gate, not a pytest: it needs the GPU and takes minutes.
