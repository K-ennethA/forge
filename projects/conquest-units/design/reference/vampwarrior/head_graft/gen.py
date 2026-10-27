import sys, os
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"; sys.path.insert(0, REPO)
src, out = sys.argv[1], sys.argv[2]
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); be = Pixal3DBackend(cfg, ComfyUIClient(cfg, log=lambda *_a: None))
mv = {"front": os.path.join(src, "head_front.png"), "left": os.path.join(src, "head_side_left.png"),
      "right": os.path.join(src, "head_side_right.png"), "back": os.path.join(src, "head_back.png")}
opts = {"seed": 56, "shared_frame": True, "texture_resolution": 4096, "views": multiview.normalise_views(mv)}
res = be.generate(None, opts, os.path.join(out, "head5_pixal3d.glb"))
print("HEAD5", {k: res.get(k) for k in ("duration_ms", "stats", "warnings")})
