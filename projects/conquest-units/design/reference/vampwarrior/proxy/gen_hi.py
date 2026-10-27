"""vampwarrior v5.1: the same 3 views as proxy b8352fbf (front clean, back, side=left, seed 56, shared_frame) but
texture_resolution 4096 + target_face_count 400000 (VRAM_SAFE_DEFAULTS are 2048 / 200k)."""
import os, sys, json, copy
ROOT = r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"; REPO = os.path.dirname(os.path.dirname(ROOT))
sys.path.insert(0, os.path.join(ROOT, "improve")); sys.path.insert(0, REPO)
import numpy as np, sheet_proxy as SP
from PIL import Image
out = sys.argv[1]; tex = int(sys.argv[2]); faces = int(sys.argv[3])
intake, idir = SP.load_intake(os.path.join(ROOT, "design/reference/vampwarrior/intake.json"))
rgb = np.asarray(Image.open(SP.sheet_path(intake)).convert("RGB"))
masks = SP.view_masks(intake, idir, rgb); SP.clip_below_floor(intake, masks)
cuts, frame = SP.cut_views(intake, masks, rgb, ["front", "back", "side"], out)
mv = {"front": cuts["front"], "back": cuts["back"], "left": cuts["side"]}
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); be = Pixal3DBackend(cfg, ComfyUIClient(cfg, log=lambda *_a: None))
opts = {"seed": 56, "shared_frame": True, "texture_resolution": tex, "target_face_count": faces,
        "views": multiview.normalise_views(mv)}
res = be.generate(None, opts, os.path.join(out, "vampwarrior_hi.glb"))
print("GENERATED", {k: res.get(k) for k in ("duration_ms", "stats", "vram")})
