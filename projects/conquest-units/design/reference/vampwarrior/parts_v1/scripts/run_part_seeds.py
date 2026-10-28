# run_part_seeds.py <src_dir> <prefix> <out_dir> <name> seed...  (proxy_v6_bald/run_seeds.py's exact method; views = aligned_<prefix>_{front,side_left,side_right,back}.png)
import sys, os, time
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"; sys.path.insert(0, REPO)
src, pre, out, name = sys.argv[1:5]; seeds = [int(s) for s in sys.argv[5:]] or [7, 11, 56]
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); cl = ComfyUIClient(cfg, log=lambda *a: print("[comfy]", *a, flush=True)); be = Pixal3DBackend(cfg, cl)
mv = {k: os.path.join(src, "aligned_%s_%s.png" % (pre, v)) for k, v in (("front", "front"), ("left", "side_left"), ("right", "side_right"), ("back", "back"))}
os.makedirs(out, exist_ok=True)
try:
    for seed in seeds:
        t = time.time()
        opts = {"seed": seed, "shared_frame": True, "texture_resolution": 4096, "views": multiview.normalise_views(mv)}
        res = be.generate(None, opts, os.path.join(out, "%s_s%d.glb" % (name, seed)))
        print(name.upper(), seed, round(time.time() - t), {k: res.get(k) for k in ("duration_ms", "stats", "warnings")}, flush=True)
finally:
    if cl.owns_child():
        cl.stop(); print("comfy stopped", flush=True)
