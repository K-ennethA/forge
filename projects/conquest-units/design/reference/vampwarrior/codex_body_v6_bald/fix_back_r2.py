# fix_back_r2.py: review-round-1 fixes on the BACK view head only (rows < 222, inside the hair mask):
#  1. the grey angular ghost patch left of the neck (x 440-492, y 140-200) -> local background
#  2. the stepped crown contour (y 50-72) -> head silhouette smoothed (gaussian alpha) over the local background
#  3. skin tone of the back head matched (per-channel mean/std) to the side view's head skin (the original side face tone)
import os
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

here = os.path.dirname(os.path.abspath(__file__))
fn = os.path.join(here, "aligned_body_back.png")
a = np.asarray(Image.open(fn).convert("RGB")).astype(np.float32)
orig = np.asarray(Image.open(os.path.join(here, "orig_back.png")).convert("RGB")).astype(np.float32)
mask = np.asarray(Image.open(os.path.join(here, "hairmask_back.png"))).astype(np.float32) / 255
side = np.asarray(Image.open(os.path.join(here, "aligned_body_side_left.png")).convert("RGB")).astype(np.float32)

def skin(img):
    return (img[..., 0] > 150) & (img[..., 0] - img[..., 2] > 8) & (img.max(2) - img.min(2) < 70)

H = 222
bg0 = np.median(orig[:20, :20].reshape(-1, 3), 0)
obg = (np.abs(orig - bg0).max(2) < 14).astype(np.float32)
num = np.stack([ndi.gaussian_filter(orig[..., c] * obg, 30) for c in range(3)], -1)
local_bg = num / np.maximum(ndi.gaussian_filter(obg, 30)[..., None], 1e-3)

out = a.copy()
sk = skin(a); sk[H:] = False
head = ndi.binary_fill_holes(ndi.binary_closing(sk, iterations=2)); head[H:] = False
lab, n = ndi.label(head); sizes = ndi.sum(head, lab, range(1, n + 1)); head = lab == (1 + int(np.argmax(sizes)))
# 1. ghost patch: non-head, non-dark pixels left of the neck above the collar -> background
gp = np.zeros_like(head); gp[140:200, 440:492] = True
gp &= ~ndi.binary_dilation(head, iterations=1) & (a.mean(2) > 80)
out[gp] = local_bg[gp]
# 3. tone match (head skin only)
s_side = skin(side); s_side[H:] = False; s_side[:, :380] = False
ref = side[s_side]; cur = a[head]
mu_r, sd_r = ref.mean(0), ref.std(0); mu_c, sd_c = cur.mean(0), cur.std(0)
out[head] = (a[head] - mu_c) / np.maximum(sd_c, 1) * sd_r + mu_r
# 2. crown: smooth the silhouette in rows 48-74 (alpha from the head mask) over the local background
alpha = ndi.gaussian_filter(ndi.binary_opening(head, iterations=2).astype(np.float32), 1.6)
band = np.zeros(head.shape, bool); band[48:74] = True
skin_fill = ndi.grey_dilation(out * head[..., None], size=(5, 5, 1))      # push skin colour outward under the new edge
edge = band & (np.abs(alpha - head) > 0.02)
out[edge] = (skin_fill[edge] * alpha[edge][:, None] + local_bg[edge] * (1 - alpha[edge][:, None]))
# only inside the (pass-1 + pass-2) hair mask
w = (mask > 0.01)[..., None]
res = np.where(w, out, a)
Image.fromarray(np.clip(res + 0.5, 0, 255).astype(np.uint8)).save(fn)
print("head px", int(head.sum()), "ghost px", int(gp.sum()), "tone", mu_c.round(1), "->", mu_r.round(1))
