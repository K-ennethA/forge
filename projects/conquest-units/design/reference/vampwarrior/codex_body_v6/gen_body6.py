import sys, os
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"; sys.path.insert(0, REPO)
src, out, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); be = Pixal3DBackend(cfg, ComfyUIClient(cfg, log=lambda *_a: None))
# vamp body v6: codex_body_v6 full-body turnaround; the right profile = the aligned left profile mirrored
mv = {"front": os.path.join(src, "body_front.png"), "left": os.path.join(src, "aligned_body_side_left.png"),
      "right": os.path.join(src, "aligned_body_side_right.png"), "back": os.path.join(src, "aligned_body_back.png")}
opts = {"seed": seed, "shared_frame": True, "texture_resolution": 4096, "views": multiview.normalise_views(mv)}
res = be.generate(None, opts, os.path.join(out, "vamp_body6_s%d.glb" % seed))
print("BODY6", seed, {k: res.get(k) for k in ("duration_ms", "stats", "warnings")})
