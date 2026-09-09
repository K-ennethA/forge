"""Shared body of the two ComfyUI-hosted backends.

TRELLIS.2 and Pixal3D ship as ONE official ComfyUI template with a boolean
switch choosing which diffusion model drives the same shape/texture pipeline,
so the adapters differ only in that flag, the weights they insist on, and the
licence they report.  Everything else lives here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import glb
from .base import Backend, BackendError, NotReady

HF = "https://huggingface.co"

#: node ids inside workflows/image_to_3d.json (converted from the official
#: ComfyUI template).  Named so a template refresh that renumbers them fails
#: loudly in one place instead of silently generating the wrong thing.
NODE_LOAD_IMAGE = "122"
NODE_TRELLIS2_SWITCH = "316"
NODE_SAVE = "322"
NODE_TEXTURE_RESOLUTION = "288"
NODE_REMESH = "241"
NODE_DECIMATE = "186"
NODE_UNWRAP = "196"
NODE_STRUCTURE_SAMPLER = "3"   # the first of four KSamplers; the one seed/steps steer
NODE_UPSAMPLE = "94"

#: Settings applied on top of the official template before any caller options.
#:
#: The template ships tuned for a much larger card.  Measured on the RTX 5070
#: (12 GB): at the stock shape_resolution "1536" the shape stage emits ~17.9M
#: verts / 35.9M faces, and RemeshMesh at 768 then asks for ~20 GiB and dies
#: with torch.OutOfMemoryError.  These defaults keep the whole run inside 12 GB.
#: A caller with a bigger card can pass the stock values straight back in.
VRAM_SAFE_DEFAULTS = {
    "shape_resolution": "1024",     # template: "1536"
    "remesh_resolution": 512,       # template: 768
    "texture_resolution": 2048,     # template: 4096
    "target_face_count": 200000,    # template: 700000
}

#: Option keys that are real and validated but steer meshgen rather than a node
#: in the graph, so ``build_graph`` must skip them instead of refusing them as
#: unknown.  ``views`` and ``on_unavailable`` are the Phase 18(a) multi-view
#: request surface; they are resolved before a graph is built.
NON_GRAPH_OPTIONS = {"backend", "views", "on_unavailable", "multiview_fov_deg"}

#: options callers may pass through /generate3d -> {"options": {...}}
OPTION_SPEC = {
    "seed": (NODE_STRUCTURE_SAMPLER, "seed", int),
    "steps": (NODE_STRUCTURE_SAMPLER, "steps", int),
    "cfg": (NODE_STRUCTURE_SAMPLER, "cfg", float),
    "texture_resolution": (NODE_TEXTURE_RESOLUTION, "value", int),
    "remesh_resolution": (NODE_REMESH, "resolution", int),
    "target_face_count": (NODE_DECIMATE, "target_face_count", int),
    "uv_padding": (NODE_UNWRAP, "padding", int),
    "shape_resolution": (NODE_UPSAMPLE, "target_resolution", str),
}

# Weights the shared pipeline always needs, whichever diffusion model drives it.
#
# MoGe is deliberately NOT here: the template's switch node evaluates lazily, so
# the MoGe -> FOV -> Pixal3DConditioning branch never runs on a TRELLIS.2 job.
# Verified against the ComfyUI log - a full trellis2 run never loads MoGeModelV2.
# Listing it as common would make /health demand 0.6 GB that trellis2 never opens.
COMMON_WEIGHTS = [
    ("vae", "trellis_2_shape_vae_bf16.safetensors", 1095844024,
     f"{HF}/Comfy-Org/TRELLIS.2/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors"),
    ("vae", "trellis_2_texture_vae_bf16.safetensors", 948461364,
     f"{HF}/Comfy-Org/TRELLIS.2/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors"),
    ("clip_vision", "dino_v3_L_naf_fp32.safetensors", 1215214176,
     f"{HF}/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors"),
    ("background_removal", "birefnet.safetensors", 444473596,
     f"{HF}/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors"),
]

MOGE_WEIGHT = (
    "geometry_estimation", "moge_2_vitl_normal_fp16.safetensors", 661859924,
    f"{HF}/Comfy-Org/MoGe/resolve/main/geometry_estimation/moge_2_vitl_normal_fp16.safetensors",
)


class ComfyUIBackend(Backend):
    """Base for the two ComfyUI-hosted image-to-3D backends."""

    #: True selects TRELLIS.2 on the template's switch node, False Pixal3D
    use_trellis2 = True
    #: the diffusion model this backend insists on: (folder, filename, bytes, url)
    diffusion_weight = None
    #: anything beyond COMMON_WEIGHTS this backend's branch of the graph reaches
    extra_weights = ()

    def __init__(self, config, client):
        super().__init__(config)
        self.client = client

    # -- weights ---------------------------------------------------------
    def required_weights(self):
        entries = [self.diffusion_weight] + COMMON_WEIGHTS + list(self.extra_weights)
        return [
            {
                "what": f"{name} ({size / 2**30:.1f} GB)",
                "path": str(self.config.model_path(folder, name)),
                "source": url,
                "bytes": size,
            }
            for folder, name, size, url in entries
        ]

    def readiness(self):
        runtime = self.client.runtime_missing()
        weights = self._check_files(self.required_weights())
        missing = runtime + weights["missing"]
        return {"ready": not missing, "missing": missing}

    def is_loaded(self):
        return self.client.is_running()

    def info(self):
        data = super().info()
        data["comfyui_running"] = self.client.is_running()
        data["workflow"] = str(self._workflow_path().name)
        return data

    # -- workflow --------------------------------------------------------
    def _workflow_path(self) -> Path:
        return self.config.workflows_dir / "image_to_3d.json"

    def load_workflow(self) -> dict:
        path = self._workflow_path()
        if not path.is_file():
            raise BackendError(f"workflow template missing: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
        for node_id in (NODE_LOAD_IMAGE, NODE_TRELLIS2_SWITCH, NODE_SAVE):
            if node_id not in graph:
                raise BackendError(
                    f"workflow template {path.name} has no node {node_id} - it was "
                    "rebuilt from a ComfyUI template whose node ids moved; "
                    "re-run meshgen/tools/build_workflow.py and update the NODE_* "
                    "constants in backends/comfyui_base.py"
                )
        return graph

    #: per-backend overrides on top of VRAM_SAFE_DEFAULTS
    default_options = {}

    def resolved_options(self, options: dict) -> dict:
        merged = dict(VRAM_SAFE_DEFAULTS)
        merged.update(self.default_options)
        merged.update(options or {})
        return merged

    def build_graph(self, image_name: str, options: dict, prefix: str) -> dict:
        graph = self.load_workflow()
        graph[NODE_LOAD_IMAGE]["inputs"]["image"] = image_name
        graph[NODE_TRELLIS2_SWITCH]["inputs"]["value"] = bool(self.use_trellis2)
        graph[NODE_SAVE]["inputs"]["filename_prefix"] = prefix

        unknown = []
        for key, value in self.resolved_options(options).items():
            if key in NON_GRAPH_OPTIONS:
                continue
            spec = OPTION_SPEC.get(key)
            if spec is None:
                unknown.append(key)
                continue
            node_id, field, coerce = spec
            if node_id not in graph:
                continue
            try:
                graph[node_id]["inputs"][field] = coerce(value)
            except (TypeError, ValueError):
                raise BackendError(f"option {key}={value!r} is not a valid {coerce.__name__}") from None
        if unknown:
            raise BackendError(
                f"unknown option(s): {', '.join(sorted(unknown))}. "
                f"Supported: {', '.join(sorted(OPTION_SPEC))}"
            )
        return graph

    # -- generation ------------------------------------------------------
    def resolve_multiview(self, image_path, options):
        """Hook: settle a ``views`` request before any GPU work starts.

        Returns ``(image_path, note_or_None)``.  The base implementation refuses
        views outright - only an adapter that declares ``supports_multiview``
        overrides this.  Called before ComfyUI is even started so an
        unsatisfiable request costs nothing.
        """
        if (options or {}).get("views"):
            raise BackendError(
                f"backend {self.name!r} does not accept multi-view input; "
                "send a single image_path"
            )
        return image_path, None

    def generate(self, image_path, options, out_path, progress=None, cancel_event=None, job_id=None):
        self.ensure_ready()
        progress = progress or (lambda *a: None)

        image_path, multiview_note = self.resolve_multiview(image_path, options)

        progress(0.0, "starting ComfyUI")
        self.client.ensure_running(cancel_event=cancel_event)

        image_name = self.client.stage_image(image_path)
        prefix = f"forge/{self.name}"
        graph = self.build_graph(image_name, options or {}, prefix)

        started = time.time()
        telemetry = {}
        before = self.client.vram_report()
        try:
            entry = self.client.run_graph(
                graph,
                prompt_id=job_id,
                cancel_event=cancel_event,
                progress=progress,
                timeout_s=self.config.job_timeout_s,
                telemetry=telemetry,
            )
        finally:
            self.client.unstage_image(image_name)
        after = self.client.vram_report()
        duration_ms = int((time.time() - started) * 1000)

        result = self.client.collect_output(entry, out_path)
        try:
            mesh_stats = glb.stats(result["mesh_path"])
        except (glb.GlbError, OSError):
            mesh_stats = {}

        payload = {
            "mesh_path": result["mesh_path"],
            "stats": mesh_stats,
            "backend": self.name,
            "model": self.model,
            "duration_ms": duration_ms,
            "options": self.resolved_options(options),
            "vram": {
                "before_gb": (before or {}).get("vram_used_gb"),
                "after_gb": (after or {}).get("vram_used_gb"),
                "peak_gb": telemetry.get("peak_vram_gb"),
                "total_gb": telemetry.get("vram_total_gb") or (before or {}).get("vram_total_gb"),
                "device": (before or {}).get("name"),
            },
        }
        if multiview_note is not None:
            # Never let a fallback be invisible: if the caller asked for views
            # and got one image, the result says so.
            payload["multiview"] = multiview_note
        return payload

    def cancel(self, handle):
        if handle:
            return self.client.cancel_prompt(handle)
        return self.client.interrupt()
