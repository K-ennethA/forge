import sys, os
REPO = r"C:\Users\kenne\OneDrive\Desktop\git\forge"
sys.path.insert(0, REPO)
from PIL import Image
out = sys.argv[1]
im = Image.open(os.path.join(REPO, r"projects\conquest-units\design\reference\vampwarrior-character-sheet.png")).convert("RGB")
crop = im.crop((1228, 270, 1476, 520)).resize((744, 750), Image.LANCZOS)
p = os.path.join(out, "head_panel.png"); crop.save(p)
from meshgen import config as mconfig
from meshgen.comfyui_client import ComfyUIClient
from meshgen.backends.comfyui_pixal3d import Pixal3DBackend
cfg = mconfig.load(); cl = ComfyUIClient(cfg, log=lambda *_a: None)
be = Pixal3DBackend(cfg, cl)
res = be.generate(p, {"seed": 56}, os.path.join(out, "head_pixal3d.glb"))
print("HEAD", {k: res.get(k) for k in ("seed", "duration_ms", "warnings")})
