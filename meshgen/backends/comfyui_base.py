"""Shared body of the two ComfyUI-hosted backends.

TRELLIS.2 and Pixal3D ship as ONE official ComfyUI template with a boolean
switch choosing which diffusion model drives the same shape/texture pipeline,
so the adapters differ only in that flag, the weights they insist on, and the
licence they report.  Everything else lives here.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .. import glb
from . import structure_probe
from .base import (Backend, BackendError, Cancelled, NotReady, ensemble_seeds)

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
NODE_SMOOTH_NORMALS_UV = "238"    # decimate -> here -> UnwrapMesh (splits UV islands)
NODE_SMOOTH_NORMALS_OUT = "260"   # textured mesh -> here -> the exported .glb

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

#: Surface-quality settings the official template ships at values that are not
#: its own node defaults, measured and re-chosen on this card.  Applied exactly
#: like VRAM_SAFE_DEFAULTS - on top of the template, under any caller option -
#: so that re-running tools/build_workflow.py after a ComfyUI upgrade cannot
#: silently restore the template's numbers.  workflows/image_to_3d.json carries
#: the same values, and a test pins the two together.
#:
#: The measurements, the losers and the reasons are in meshgen/README.md under
#: "Tuning the surface: what was measured".  In one line each:
#:
#: * ``smooth_iters`` - the template ships 20, which is the node's MAXIMUM and
#:   ten times what its own tooltip recommends ("2-3 cleans DC staircase
#:   artifacts; higher over-smooth QEF edges").  Taubin smoothing does not know
#:   an artifact from a chamfer.
#: * ``qef`` - Quadratic Error Function dual-vertex placement, which is what
#:   recovers a sharp feature instead of averaging across it.  Core defaults it
#:   ON for sdf and OFF for udf; the template runs udf, so it was off.
#: * ``project_back`` - pulls dual-contoured vertices back onto the true input
#:   surface.  0 is pure DC: the remesh's idea of the surface, not the model's.
#: * ``crease_angle`` - 180 means "smooth everything", so a 90 degree edge is
#:   shaded with one averaged normal and reads as a 45 degree lie.
#:
#: Two of the four moved.  The other two were measured and LOST, which is worth
#: as much as the wins and is why they are named here rather than forgotten:
#:
#: * ``project_back`` stays 0.  At 0.5 it bought +0.0007 silhouette IoU for 70
#:   self-intersections where there had been none; at 1.0, 13 391 of them across
#:   465 shells, with 2.6x the object's true crease length - pure voxel
#:   staircase.  It snaps dual-contoured vertices back onto the raw model
#:   surface, which is precisely the self-intersecting mess the remesh exists to
#:   remove.  Both cost ~17% more wall time as well.
#: * ``crease_angle`` stays 180 GLOBALLY, and moves only under ``hard_surface``.
#:   45 is close to free on a hard surface - measured alone it cut crease-face
#:   shading error from 164.4 to 19.2 degrees while changing no geometry at all
#:   (sharpness and crease length identical to the baseline, as it must be: the
#:   node splits vertices, it does not move them).  But on the organic control
#:   it added 4.8% more exported vertices to split edges that should have stayed
#:   smooth, and that is faceting.  The metric cannot tell the difference - a
#:   sphere WANTS its shading normals to deviate from its faces - so defaulting
#:   it would be optimising a number the harness cannot read on half the inputs.
TUNED_DEFAULTS = {
    "smooth_iters": 3,      # template: 20 (the node's max; its tooltip says 2-3)
    "qef": True,            # template: false
}

#: The ``hard_surface`` macro, expanded in :meth:`resolved_options`.  Sets more
#: than one node at once; see OPTION_SPEC.  ``qef`` is already the default and
#: is repeated here on purpose - the macro must mean the same thing to a caller
#: who has overridden the defaults as it does to one who has not.
HARD_SURFACE_PRESET = {
    "crease_angle": 45.0,
    "qef": True,
}

#: Option keys that are real and validated but steer meshgen rather than a node
#: in the graph, so ``build_graph`` must skip them instead of refusing them as
#: unknown.  ``views`` and ``on_unavailable`` are the Phase 18(a) multi-view
#: request surface; they are resolved before a graph is built.  ``hard_surface``
#: is a macro over two graph options, expanded in :meth:`resolved_options`.
NON_GRAPH_OPTIONS = {"backend", "views", "on_unavailable", "multiview_fov_deg",
                     "hard_surface", "ensemble"}


def _boolean(value):
    """Strict bool coercion - ``bool("false")`` is True, and that is a trap.

    A caller sending ``"false"`` for ``qef`` means false, and silently turning
    sharp-feature placement ON because a JSON client stringified a flag is
    exactly the class of wrongness this whole option surface exists to stop.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value in (0, 1) and not isinstance(value, float):
        return bool(value)
    raise ValueError(f"{value!r} is not a boolean")


_boolean.__name__ = "boolean"


#: options callers may pass through /generate3d -> {"options": {...}}
#:
#: A spec is ``(node id or tuple of node ids, input field, coercion)``.  The
#: tuple form exists for ``crease_angle``, which is one artistic decision -
#: "this object has hard edges" - held by two separate MeshSmoothNormals nodes
#: in the template: one feeding the UV unwrap, one producing the exported
#: normals.  Setting only one of them gives a mesh whose atlas and whose shading
#: disagree about where the creases are.
OPTION_SPEC = {
    "seed": (NODE_STRUCTURE_SAMPLER, "seed", int),
    "steps": (NODE_STRUCTURE_SAMPLER, "steps", int),
    "cfg": (NODE_STRUCTURE_SAMPLER, "cfg", float),
    "texture_resolution": (NODE_TEXTURE_RESOLUTION, "value", int),
    "remesh_resolution": (NODE_REMESH, "resolution", int),
    "target_face_count": (NODE_DECIMATE, "target_face_count", int),
    "uv_padding": (NODE_UNWRAP, "padding", int),
    "shape_resolution": (NODE_UPSAMPLE, "target_resolution", str),
    "smooth_iters": (NODE_REMESH, "smooth_iters", int),
    "qef": (NODE_REMESH, "sign_mode.qef", _boolean),
    "project_back": (NODE_REMESH, "project_back", float),
    "fix_poles": (NODE_REMESH, "fix_poles", _boolean),
    "crease_angle": ((NODE_SMOOTH_NORMALS_UV, NODE_SMOOTH_NORMALS_OUT),
                     "crease_angle", float),
}

#: Inclusive ``(low, high)`` bounds, straight off the ComfyUI node schemas.
#: Out-of-range is refused with the real range rather than clamped: a node that
#: clamps 500 smoothing iterations to 20 does not tell you it did, and the
#: caller then reasons about a setting that never happened.
OPTION_LIMITS = {
    "steps": (1, 200),
    "cfg": (0.0, 100.0),
    "texture_resolution": (16, 8192),
    "remesh_resolution": (32, 2048),
    "target_face_count": (4, 10_000_000),
    "uv_padding": (0, 64),
    "smooth_iters": (0, 20),
    "project_back": (0.0, 1.0),
    "crease_angle": (0.0, 180.0),
}

#: ``shape_resolution`` is a combo, not a number.
OPTION_CHOICES = {
    "shape_resolution": ("1024", "1536"),
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
    def _workflow_path(self, name: str = "image_to_3d.json") -> Path:
        return self.config.workflows_dir / name

    def load_workflow(self, name: str = "image_to_3d.json",
                      required=(NODE_LOAD_IMAGE, NODE_TRELLIS2_SWITCH, NODE_SAVE)) -> dict:
        path = self._workflow_path(name)
        if not path.is_file():
            raise BackendError(f"workflow template missing: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
        for node_id in required:
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
        caller = dict(options or {})
        merged = dict(VRAM_SAFE_DEFAULTS)
        merged.update(TUNED_DEFAULTS)
        merged.update(self.default_options)
        if "hard_surface" in caller:
            try:
                wanted = _boolean(caller["hard_surface"])
            except ValueError:
                raise BackendError(
                    f"option hard_surface={caller['hard_surface']!r} is not a boolean"
                ) from None
            if wanted:
                # a macro, not a mode: it only pre-seeds the two options it is
                # shorthand for, so an explicit crease_angle next to it still
                # wins rather than being quietly overruled
                merged.update(HARD_SURFACE_PRESET)
        merged.update(caller)
        return merged

    @staticmethod
    def _check_range(key, value):
        choices = OPTION_CHOICES.get(key)
        if choices is not None and value not in choices:
            raise BackendError(
                f"option {key}={value!r} is not one of {', '.join(map(repr, choices))}")
        limits = OPTION_LIMITS.get(key)
        if limits is not None and not (limits[0] <= value <= limits[1]):
            raise BackendError(
                f"option {key}={value!r} is outside the supported range "
                f"{limits[0]} to {limits[1]}")

    def build_graph(self, image_name: str, options: dict, prefix: str) -> dict:
        graph = self.load_workflow()
        graph[NODE_LOAD_IMAGE]["inputs"]["image"] = image_name
        return self.finish_graph(graph, options, prefix)

    def finish_graph(self, graph: dict, options: dict, prefix: str) -> dict:
        """Everything that is the same whichever template the graph came from.

        Split out of :meth:`build_graph` so the multi-view template - which has
        four ``LoadImage`` nodes instead of one - gets the identical switch,
        output prefix and option handling rather than a parallel copy of it.
        """
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
            node_ids, field, coerce = spec
            if isinstance(node_ids, str):
                node_ids = (node_ids,)
            try:
                coerced = coerce(value)
            except (TypeError, ValueError):
                raise BackendError(
                    f"option {key}={value!r} is not a valid {coerce.__name__}") from None
            self._check_range(key, coerced)
            for node_id in node_ids:
                if node_id in graph:
                    graph[node_id]["inputs"][field] = coerced
        if unknown:
            raise BackendError(
                f"unknown option(s): {', '.join(sorted(unknown))}. "
                f"Supported: {', '.join(sorted(OPTION_SPEC))}"
            )
        return graph

    # -- generation ------------------------------------------------------
    def resolve_multiview(self, image_path, options):
        """Hook: settle a ``views`` request before any GPU work starts.

        Returns ``(image_path, note_or_None, plan_or_None)``.  ``plan`` is a
        :func:`multiview.stage_plan` result when the run really is multi-view;
        it is what :meth:`prepare_run` needs and it is deliberately NOT the
        note, which goes into the caller's result payload.

        The base implementation refuses views outright - only an adapter that
        declares ``supports_multiview`` overrides this.  Called before ComfyUI is
        even started so an unsatisfiable request costs nothing.
        """
        if (options or {}).get("views"):
            raise BackendError(
                f"backend {self.name!r} does not accept multi-view input; "
                "send a single image_path"
            )
        return image_path, None, None

    def stage_inputs(self, image_path, plan=None):
        """Copy the caller's image(s) into ComfyUI's input dir.

        Returns ``(handle, [staged names])``.  The handle is whatever
        :meth:`graph_for` needs; for the single-image path it is the one staged
        filename.  Split out of :meth:`prepare_run` so an ensemble stages **once**
        and builds N graphs from it - staging per candidate would give every
        candidate a different ``LoadImage`` filename, which misses ComfyUI's
        execution cache and re-runs background removal and the image encoder for
        every seed.
        """
        image_name = self.client.stage_image(image_path)
        return image_name, [image_name]

    def graph_for(self, staged, options, prefix, plan=None):
        """The finished graph for one run, from an already-staged input."""
        return self.build_graph(staged, options or {}, prefix)

    def prepare_run(self, image_path, options, prefix, plan=None):
        """Stage the inputs and build the graph.

        Returns ``(graph, [staged names])``; the caller unstages every name in
        that list when the run is over, however it ended.
        """
        staged, names = self.stage_inputs(image_path, plan)
        try:
            return self.graph_for(staged, options, prefix, plan), names
        except Exception:
            for name in names:
                self.client.unstage_image(name)
            raise

    # -- the seed ensemble ------------------------------------------------
    @staticmethod
    def load_scorer():
        """Import the numpy scorer, or say plainly why the ensemble cannot run.

        Deliberately lazy.  meshgen's request path is stdlib-only and stays that
        way for a single-shot generation; picking between candidates is geometry
        and needs numpy (and PIL, to read the prepared silhouette back).  A
        caller who asked for an ensemble on a machine without them gets a
        sentence, not a single-seed mesh that quietly pretends it was chosen.
        """
        try:
            from .. import ensemble as ensemble_module
            from ..tools import mesh_metrics
        except ImportError as exc:
            raise BackendError(
                "the ensemble picker needs numpy and PIL (both are in "
                f"service/.venv) and one of them is missing: {exc}. "
                'Drop "ensemble" from options to run a single seed.') from None
        return ensemble_module, mesh_metrics

    def generate(self, image_path, options, out_path, progress=None, cancel_event=None, job_id=None):
        self.ensure_ready()
        progress = progress or (lambda *a: None)
        options = dict(options or {})
        ensemble_options = self.resolved_ensemble(options)

        image_path, multiview_note, multiview_plan = self.resolve_multiview(image_path, options)

        progress(0.0, "starting ComfyUI")
        self.client.ensure_running(cancel_event=cancel_event)

        prefix = f"forge/{self.name}"
        started = time.time()
        telemetry = {}
        before = self.client.vram_report()
        report = None

        staged, staged_names = self.stage_inputs(image_path, multiview_plan)
        try:
            if ensemble_options["best_of"] > 1:
                result, report = self._run_best_of(
                    staged, options, out_path, prefix, multiview_plan,
                    ensemble_options, progress, cancel_event, job_id, telemetry)
            else:
                if ensemble_options["structure_n"] > 1:
                    winning_seed, report = self._structure_consensus(
                        staged, options, prefix, multiview_plan, ensemble_options,
                        progress, cancel_event, job_id, telemetry)
                    options["seed"] = winning_seed
                graph = self.graph_for(staged, options, prefix, multiview_plan)
                entry = self.client.run_graph(
                    graph,
                    prompt_id=job_id,
                    cancel_event=cancel_event,
                    progress=progress,
                    timeout_s=self.config.job_timeout_s,
                    telemetry=telemetry,
                )
                result = self.client.collect_output(entry, out_path)
        finally:
            for name in staged_names:
                self.client.unstage_image(name)
        after = self.client.vram_report()
        duration_ms = int((time.time() - started) * 1000)

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
        if report is not None:
            report["wall_ms"] = duration_ms
            payload["ensemble"] = report
        return payload

    # -- tier 1: structure consensus --------------------------------------
    def _structure_consensus(self, staged, options, prefix, plan, ensemble_options,
                             progress, cancel_event, job_id, telemetry):
        """Run the cheap head N times, pick the medoid grid, return its seed.

        The winner is replayed as an ordinary full generation at that seed rather
        than re-injected as a grid - core has no node that can take a VOXEL back
        in, and meshgen never installs one.  See
        :mod:`meshgen.backends.structure_probe` for the whole argument.
        """
        ensemble_module, _metrics = self.load_scorer()
        count = ensemble_options["structure_n"]
        base_seed = int(self.resolved_options(options).get("seed", 0))
        seeds = ensemble_seeds(base_seed, count)

        candidates = []
        mask = None
        probe_started = time.time()
        for index, seed in enumerate(seeds, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled("cancelled during the structure ensemble")
            progress(index / count, f"structure candidate {index}/{count} (seed {seed})")
            probe_prefix = f"{prefix}_probe"
            graph = self.graph_for(staged, {**options, "seed": seed},
                                   probe_prefix, plan)
            probe = structure_probe.build_structure_graph(
                graph, probe_prefix, resolution=self._structure_resolution(graph))
            entry = self.client.run_graph(
                probe,
                # a fresh canonical uuid per sub-run: ComfyUI refuses anything
                # that is not one, so an ensemble's candidates cannot be named
                # after the job that owns them.  Cancellation still works - it
                # rides ``cancel_event``, which run_graph checks every poll.
                prompt_id=None,
                cancel_event=cancel_event,
                progress=lambda fraction, label, i=index: progress(
                    None, f"structure {i}/{count}: {label}" if label else None),
                timeout_s=self.config.job_timeout_s,
                telemetry=telemetry,
            )
            meshes = self.client.find_mesh_outputs(entry, suffixes=(".glb",))
            if not meshes:
                raise BackendError(
                    "the structure probe produced no grid mesh - the SaveGLB tail "
                    "in backends/structure_probe.py did not run")
            verts, faces = self._read_grid_mesh(meshes[-1])
            grid = ensemble_module.occupancy_from_cube_mesh(
                verts, faces, self._structure_resolution(graph))
            candidates.append({"seed": seed, "grid": grid})
            if mask is None:
                mask = self._read_mask(entry)

        report = ensemble_module.select_structure(candidates, mask=mask)
        report.update({
            "tier": "structure_consensus",
            "structure_n": count,
            "seeds": seeds,
            "seed_base": base_seed,
            "mask_used": mask is not None,
            "probe_ms": int((time.time() - probe_started) * 1000),
            "replay": (
                "the winning seed is re-run as one ordinary full generation - "
                "ComfyUI core has no node that can take a structure VOXEL back "
                "into a graph, and meshgen never installs a custom one, so the "
                "winner's cheap head runs a second time"),
        })
        if not report["mask_used"]:
            report["honesty"] = (
                "no prepared silhouette came back from the probe, so the "
                "drawing-agreement gate did not run and the pick is pure "
                "grid consensus")
        return int(report["winner"]["seed"]), report

    # -- tier 2: best-of full generations ---------------------------------
    def _run_best_of(self, staged, options, out_path, prefix, plan,
                     ensemble_options, progress, cancel_event, job_id, telemetry):
        ensemble_module, mesh_metrics = self.load_scorer()
        count = ensemble_options["best_of"]
        base_seed = int(self.resolved_options(options).get("seed", 0))
        seeds = ensemble_seeds(base_seed, count)
        out_path = Path(out_path)

        mask, mask_ms = self._harvest_mask(staged, options, prefix, plan,
                                           cancel_event, job_id, telemetry)

        candidates = []
        for index, seed in enumerate(seeds, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled("cancelled during the best_of ensemble")
            progress(0.0, f"candidate {index}/{count} (seed {seed})")
            candidate_path = out_path.with_name(f"{out_path.stem}_seed{seed}{out_path.suffix}")
            graph = self.graph_for(staged, {**options, "seed": seed}, prefix, plan)
            entry = self.client.run_graph(
                graph,
                prompt_id=None,
                cancel_event=cancel_event,
                progress=lambda fraction, label, i=index: progress(
                    fraction, f"{i}/{count}: {label}" if label else None),
                timeout_s=self.config.job_timeout_s,
                telemetry=telemetry,
            )
            produced = self.client.collect_output(entry, candidate_path)
            candidates.append(self._score_candidate(
                seed, produced["mesh_path"], mask, ensemble_module, mesh_metrics))

        report = ensemble_module.select_mesh(candidates)
        report.update({
            "tier": "best_of",
            "best_of": count,
            "seeds": seeds,
            "seed_base": base_seed,
            "mask_used": mask is not None,
            "mask_probe_ms": mask_ms,
            "kept": [c["mesh_path"] for c in candidates],
            "note": ("every candidate is kept on disk next to the output - you "
                     "paid for all of them"),
        })
        winner_path = report["winner"]["mesh_path"]
        shutil.copy2(winner_path, out_path)
        return {"mesh_path": str(out_path), "comfyui_path": winner_path}, report

    def _score_candidate(self, seed, mesh_path, mask, ensemble_module, mesh_metrics):
        entry = {"seed": seed, "mesh_path": mesh_path, "metrics": {}, "points": None}
        try:
            mesh = mesh_metrics.load_glb(mesh_path)
        except Exception as exc:                       # noqa: BLE001 - recorded
            entry["error"] = f"{type(exc).__name__}: {exc}"
            return entry
        verts = mesh_metrics.normalise(mesh["verts"])
        welded, faces, _kept = mesh_metrics.weld(verts, mesh["faces"])
        metrics = {}
        metrics.update(mesh_metrics.topology(welded, faces))
        metrics.update({k: v for k, v in mesh_metrics.dihedrals(welded, faces).items()
                        if not k.startswith("_")})
        metrics.update(mesh_metrics.self_intersections(welded, faces))
        if mask is not None:
            metrics.update(mesh_metrics.silhouette(
                welded, faces, self._scoring_camera(), mask))
        entry["metrics"] = metrics
        entry["points"] = ensemble_module.sample_surface(welded, faces)
        return entry

    @staticmethod
    def _scoring_camera():
        """A fixed camera for the best_of silhouette.

        The camera behind a caller's image is unknown, so this is not an attempt
        to reconstruct it: it is one fixed frame that every candidate is drawn
        through, and ``mesh_metrics.best_orientation`` searches the 48 signed
        axis labellings inside it.  The IoU that comes out is a comparison
        between candidates, never an absolute quality score - exactly the reading
        the structure tier gives its own silhouette number.
        """
        from . import multiview
        fov = multiview.DEFAULT_FOV_DEG
        return {"azimuth_deg": 0.0, "elevation_deg": 0.0, "fov_deg": fov,
                "resolution": 256, "distance": multiview.distance_for_fov(fov)}

    # -- shared probe plumbing --------------------------------------------
    def _harvest_mask(self, staged, options, prefix, plan, cancel_event, job_id,
                      telemetry):
        """The prepared front silhouette, from a graph with no sampler in it."""
        started = time.time()
        graph = self.graph_for(staged, options, prefix, plan)
        if not structure_probe.has_mask_chain(graph):
            return None, 0
        try:
            entry = self.client.run_graph(
                structure_probe.build_mask_graph(graph, f"{prefix}_probe"),
                prompt_id=None,
                cancel_event=cancel_event,
                progress=lambda *a: None,
                timeout_s=self.config.job_timeout_s,
                telemetry=telemetry,
            )
        except Cancelled:
            raise            # Cancelled IS a BackendError - do not swallow it
        except BackendError:
            # A missing silhouette costs the gate, not the job: best_of still
            # ranks on topology and the Chamfer medoid, and the report says
            # mask_used: false rather than implying a check that never ran.
            return None, int((time.time() - started) * 1000)
        return self._read_mask(entry), int((time.time() - started) * 1000)

    def _read_mask(self, entry):
        paths = structure_probe.find_images(entry, structure_probe.NODE_MASK_SAVE,
                                            self.config)
        for path in reversed(paths):
            if not Path(path).is_file():
                continue
            try:
                import numpy as np
                from PIL import Image
                with Image.open(path) as handle:
                    grey = np.asarray(handle.convert("L"), dtype=np.float64) / 255.0
            except Exception:                          # noqa: BLE001 - optional
                continue
            # ImageCropToMask composites ``image * mask`` over the template's
            # black background, and the image here IS the mask, so the saved
            # pixel is mask**2.  0.25 is therefore mask > 0.5, not a guess.
            return grey > 0.25
        return None

    @staticmethod
    def _structure_resolution(graph):
        node = graph.get(structure_probe.NODE_STRUCTURE_DECODE) or {}
        return str((node.get("inputs") or {}).get("resolution", "32"))

    @staticmethod
    def _read_grid_mesh(path):
        from ..tools import mesh_metrics
        mesh = mesh_metrics.load_glb(path)
        return mesh["verts"], mesh["faces"]

    def cancel(self, handle):
        if handle:
            return self.client.cancel_prompt(handle)
        return self.client.interrupt()
