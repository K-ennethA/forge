"""duskmaw v6 proxies from the Codex turnarounds: views front / back / right = side_right / left = side mirrored,
seed 56, shared_frame (same 1024x1536 canvases, same floor line), texture 4096."""
import sys, os
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"; sys.path.insert(0, REPO)
from PIL import Image
src, out, state = sys.argv[1], sys.argv[2], sys.argv[3]
side = os.path.join(src, state + "_side_right.png"); mir = os.path.join(out, state + "_side_left_mirrored.png")
Image.open(side).transpose(Image.FLIP_LEFT_RIGHT).save(mir)
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
from meshgen.backends import multiview
cfg = mconfig.load(); be = Pixal3DBackend(cfg, ComfyUIClient(cfg, log=lambda *_a: None))
mv = {"front": os.path.join(src, state + "_front.png"), "back": os.path.join(src, state + "_back.png"), "right": side, "left": mir}
res = be.generate(None, {"seed": 56, "shared_frame": True, "texture_resolution": 4096, "views": multiview.normalise_views(mv)},
                  os.path.join(out, "duskmaw_%s_v6.glb" % state))
print("GEN", state, {k: res.get(k) for k in ("duration_ms", "stats")})
