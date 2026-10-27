# runs gen_body6b.py's exact method for seeds 11 and 56 in ONE process, then stops the ComfyUI child it started
import sys, os, time
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"; sys.path.insert(0, REPO)
src, out = sys.argv[1], sys.argv[2]; seeds = [int(s) for s in sys.argv[3:]] or [11, 56]
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); cl = ComfyUIClient(cfg, log=lambda *a: print("[comfy]", *a, flush=True)); be = Pixal3DBackend(cfg, cl)
mv = {"front": os.path.join(src, "body_front.png"), "left": os.path.join(src, "aligned_body_side_left.png"),
      "right": os.path.join(src, "aligned_body_side_right.png"), "back": os.path.join(src, "aligned_body_back.png")}
try:
    for seed in seeds:
        t = time.time()
        opts = {"seed": seed, "shared_frame": True, "texture_resolution": 4096, "views": multiview.normalise_views(mv)}
        res = be.generate(None, opts, os.path.join(out, "vamp_body6b_s%d.glb" % seed))
        print("BODY6B", seed, round(time.time() - t), {k: res.get(k) for k in ("duration_ms", "stats", "warnings")}, flush=True)
finally:
    if cl.owns_child():
        cl.stop(); print("comfy stopped", flush=True)
