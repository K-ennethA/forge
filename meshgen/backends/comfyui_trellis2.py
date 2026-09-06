"""TRELLIS.2 image-to-3D, run through ComfyUI's own core nodes.

Licence note that matters commercially: this uses ComfyUI's NATIVE
reimplementation (comfy_extras/nodes_trellis2.py plus comfy/ldm/trellis2),
which exists specifically to replace the original research code's
non-commercial pieces - nvdiffrast/nvdiffrec and RMBG.  The weights in
Comfy-Org/TRELLIS.2 are MIT.  Installing a third-party 3D node pack
(visualbruno/ComfyUI-Trellis2 and friends) drags those non-commercial
dependencies back in, so meshgen never installs custom nodes.

The image encoder is DINOv3.  Using generated assets carries no obligation;
REDISTRIBUTING a derivative model does - see meshgen/README.md.
"""

from __future__ import annotations

from .comfyui_base import HF, ComfyUIBackend


class Trellis2Backend(ComfyUIBackend):
    name = "trellis2"
    model = "TRELLIS.2 (int8 convrot)"
    license = "MIT (weights: Comfy-Org/TRELLIS.2; code: ComfyUI core nodes, GPL-3.0)"
    vram_gb = 12
    description = (
        "Image to textured 3D mesh via ComfyUI core TRELLIS.2 nodes. "
        "Commercially clean: no nvdiffrast, no RMBG, no custom node packs."
    )

    use_trellis2 = True
    diffusion_weight = (
        "diffusion_models",
        "trellis_2_int8_convrot.safetensors",
        5253048192,
        f"{HF}/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors",
    )
