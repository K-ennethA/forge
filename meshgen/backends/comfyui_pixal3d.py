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
"""

from __future__ import annotations

from .comfyui_base import HF, MOGE_WEIGHT, ComfyUIBackend


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
