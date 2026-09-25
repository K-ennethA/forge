"""Pixal3D image-to-3D, run through ComfyUI's own core nodes.

Pixal3D shares the whole back half of the pipeline with TRELLIS.2 - same shape
and texture VAEs, same DINOv3 encoder, same remesh/unwrap/bake tail - and
differs by driving it with a pixel-aligned diffusion model that also takes a
camera FOV estimated by MoGe.  In the official template the two are literally
one graph behind a boolean, which is why both adapters share
:mod:`comfyui_base` and set one flag.

Heavier than TRELLIS.2 (a ~5.2 GB UNet against ~4.9 GB, plus MoGe resident).
Measured on the RTX 5070 at the meshgen defaults it still fits: 249 s and a
9.30 GB peak, against TRELLIS.2's 304 s and 8.15 GB.

Multi-view (Phase 18(a)) is live as of ComfyUI core v0.35.0, which added
``Pixal3DMultiViewConditioning``.  This adapter owns the request surface, the
validation, the camera rig and the multi-view graph; :mod:`multiview` owns the
socket mapping and documents the azimuth convention.
"""

from __future__ import annotations

from . import multiview
from .base import BackendError
from .comfyui_base import (HF, MOGE_WEIGHT, NODE_SAVE, NODE_TRELLIS2_SWITCH,
                           ComfyUIBackend)


class Pixal3DBackend(ComfyUIBackend):
    name = "pixal3d"
    model = "Pixal3D (int8 convrot)"
    license = "MIT (weights: Comfy-Org/Pixal3D; code: ComfyUI core nodes, GPL-3.0)"
    vram_gb = 12
    description = (
        "Image to textured 3D mesh via ComfyUI core Pixal3D nodes; "
        "pixel-aligned generation using a MoGe-estimated camera FOV."
    )

    use_trellis2 = False
    #: Pixal3D is the family that HAS a multi-view checkpoint upstream, so this
    #: is the adapter that owns the ``views`` request surface.  Whether a given
    #: machine can RUN one is :meth:`multiview_readiness`, never this flag.
    supports_multiview = True
    diffusion_weight = (
        "diffusion_models",
        "pixal3d_int8_convrot.safetensors",
        5584555824,
        f"{HF}/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors",
    )
    # MoGe estimates the camera FOV that Pixal3DConditioning needs.  TRELLIS.2
    # never touches it - the template's switch node evaluates lazily - so it is
    # required here and nowhere else.
    extra_weights = (MOGE_WEIGHT,)

    # -- multi-view ------------------------------------------------------
    def multiview_readiness(self, use_client: bool = False) -> dict:
        """``use_client`` asks the RUNNING ComfyUI instead of reading its source.

        Off by default and off for /health: ``object_info`` is a large HTTP
        payload, and /health is promised to be instant.  The source scan is the
        right authority anyway - meshgen forbids custom nodes, so the only place
        a multi-view node can appear is core's own ``nodes_trellis2.py``.
        """
        missing = multiview.multiview_missing(
            self.config, self.client if use_client else None)
        return {
            "supported": True,
            "available": not missing,
            "missing": missing,
            "detail": (
                "2-4 views on a 90 degree orbit through "
                f"{multiview.MULTIVIEW_NODE}; front=0, left=90, back=180, "
                "right/side=270"
                if not missing else
                "declared, not runnable here - see missing"
            ),
        }

    def info(self) -> dict:
        data = super().info()
        state = self.multiview_readiness()
        data["multiview"] = {
            "supported": state["supported"],
            "available": state["available"],
            "views": list(multiview.VIEW_ORDER),
            "azimuths": dict(multiview.VIEW_AZIMUTHS),
            "workflow": multiview.MULTIVIEW_WORKFLOW,
            "missing": state["missing"],
        }
        return data

    def capabilities(self) -> dict:
        data = super().capabilities()
        data["conditioning"]["single_image"]["detail"] = (
            "one image; camera FOV estimated by MoGe (Pixal3DConditioning)")
        data["conditioning"]["multi_view"].update({
            "request": 'views: {"front", "side"?, "back"?, "left"?} '
                       "(top level or inside options)",
            "views": list(multiview.VIEW_ORDER),
            "view_counts": sorted(multiview.SUPPORTED_VIEW_COUNTS),
            "azimuths_deg": dict(multiview.VIEW_AZIMUTHS),
            "fov_deg_default": multiview.DEFAULT_FOV_DEG,
            "node": multiview.MULTIVIEW_NODE,
            "model": multiview.multiview_unet_name(),
            "on_unavailable": ["error", "front_only"],
        })
        return data

    # -- the multi-view graph --------------------------------------------
    def load_multiview_workflow(self) -> dict:
        """The sibling template, checked for the nodes this adapter patches."""
        required = (multiview.NODE_MULTIVIEW_CONDITIONING,
                    multiview.NODE_PIXAL3D_UNET,
                    NODE_TRELLIS2_SWITCH, NODE_SAVE)
        return self.load_workflow(multiview.MULTIVIEW_WORKFLOW, required=required)

    def build_multiview_graph(self, staged, plan, options, prefix) -> dict:
        graph = multiview.build_multiview_graph(
            self.load_multiview_workflow(), plan, staged,
            fov_deg=plan["camera_angle_x_deg"])
        return self.finish_graph(graph, options or {}, prefix)

    def stage_inputs(self, image_path, plan=None):
        """One staged copy per view; the handle is ``{view name: staged name}``.

        Staging is split from graph building (base class) so an ensemble stages
        the four views ONCE and builds N graphs over them - a fresh staged name
        per candidate would miss ComfyUI's execution cache and re-run background
        removal and the image encoder for every seed.
        """
        if plan is None:
            return super().stage_inputs(image_path, plan)
        staged = {}
        try:
            for view in plan["views"]:
                staged[view["name"]] = self.client.stage_image(view["path"])
        except Exception:
            for name in staged.values():
                self.client.unstage_image(name)
            raise
        return staged, list(staged.values())

    def graph_for(self, staged, options, prefix, plan=None):
        if plan is None:
            return super().graph_for(staged, options, prefix, plan)
        return self.build_multiview_graph(staged, plan, options, prefix)

    def resolve_multiview(self, image_path, options):
        """Settle a ``views`` request before ComfyUI is started.

        Three outcomes, all of them explicit:

        * no ``views``        -> unchanged single-image behaviour, no note;
        * views + available   -> run the multi-view graph, and say which camera
          each image was used as;
        * views + unavailable -> refuse by name, unless the caller opted into
          ``on_unavailable: "front_only"``, in which case fall back to the front
          image and SAY SO in the result.

        Returns ``(image_path, note, plan)``.
        """
        options = options or {}
        views = options.get("views")
        if not views:
            return image_path, None, None

        records = (views if isinstance(views, list)
                   else multiview.normalise_views(views))
        front = records[0]["path"]
        fov_deg = float(options.get("multiview_fov_deg")
                        or multiview.DEFAULT_FOV_DEG)
        plan = multiview.stage_plan(records, fov_deg=fov_deg)

        if len(records) not in multiview.SUPPORTED_VIEW_COUNTS:
            # One view is a single-image generation with extra ceremony: core
            # would accept it, but Pixal3DConditioning's MoGe-estimated FOV is
            # the better path for one found image.  Say so instead of silently
            # taking the worse route.
            raise BackendError(
                "views gave only a front image - drop views and send it as "
                "image_path for the single-image path (which estimates the "
                "camera FOV with MoGe instead of assuming a rig)"
            )

        # A request is worth the round-trip that /health is not: this one is
        # about to spend minutes of GPU time if it proceeds.
        state = self.multiview_readiness(use_client=True)
        if state["available"]:
            return front, {
                "requested": [record["name"] for record in records],
                "used": True,
                "view_count": plan["view_count"],
                "node": multiview.MULTIVIEW_NODE,
                "workflow": multiview.MULTIVIEW_WORKFLOW,
                "model": multiview.multiview_unet_name(),
                "fov_deg": fov_deg,
                # by azimuth, which is the mapping that actually reached the
                # graph - not the request name it arrived under
                "cameras": [
                    {"name": view["name"], "socket": view["socket"],
                     "azimuth_deg": view["azimuth_deg"]}
                    for view in plan["views"]
                ],
                "camera_rig": plan["transforms_json"],
            }, plan

        mode = options.get("on_unavailable", "error")
        if mode == "front_only":
            return front, {
                "requested": [record["name"] for record in records],
                "used": False,
                "fell_back_to": "front",
                "front_image": front,
                "reason": "multi-view is not available on this machine",
                "missing": state["missing"],
                "honesty": (
                    f"{len(records)} views were given and {len(records) - 1} of "
                    "them were ignored - this mesh was generated from the front "
                    "image alone."
                ),
                "camera_rig": plan["transforms_json"],
            }, None
        if mode != "error":
            raise BackendError(
                f"on_unavailable must be 'error' or 'front_only', got {mode!r}"
            )
        raise BackendError(multiview.unavailable_message(state["missing"]))
