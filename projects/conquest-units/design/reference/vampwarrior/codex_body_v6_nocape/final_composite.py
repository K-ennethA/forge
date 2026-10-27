# final_composite.py (round 2, supersedes the composites of cape_masks.py / composite_r2.py):
# final = the BASE everywhere except inside the CAPE-ONLY mask (+4 px, never the collar zone, never base skin), where the
# Codex edit is taken (front: edited_front.png; side/back: the round-2 edits edited2_*.png). Inside that region the edit's
# background is replaced by a smooth continuation of the base's own surrounding background (multi-scale normalised
# convolution from the nearest base background) plus matching noise, so no tone patch / cape ghost remains.
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage as ndi

here = os.path.dirname(os.path.abspath(__file__))
OUT = {"front": ("body_front.png", "edited_front.png"), "side_left": ("aligned_body_side_left.png", "edited2_side_left.png"),
       "back": ("aligned_body_back.png", "edited2_back.png")}
COLLAR = json.load(open(os.path.join(here, "collar_protect.json")))
L = lambda p: np.asarray(Image.open(os.path.join(here, p)).convert("RGB")).astype(np.float32)
rep = json.load(open(os.path.join(here, "edit_diff.json")))
rng = np.random.default_rng(1)
for v, (outname, ename) in OUT.items():
    base = L("base_%s.png" % v); E = L(ename)
    prot = Image.new("L", (base.shape[1], base.shape[0])); ImageDraw.Draw(prot).polygon([tuple(p) for p in COLLAR[v]], fill=255); prot = np.asarray(prot) > 0
    cape = np.asarray(Image.open(os.path.join(here, "cape_mask_%s.png" % v))) > 0
    skin = (base[..., 0] > 140) & (base.mean(2) > 135) & (base[..., 0] - base[..., 2] > 4)
    skin = ndi.binary_opening(skin, iterations=1) & ~cape
    M = ndi.binary_dilation(cape, iterations=4) & ~prot & ~skin
    w = ndi.gaussian_filter(M.astype(np.float32), 1.2); w[ndi.binary_erosion(M, iterations=2)] = 1.0; w[~M] = 0.0
    cur = E * w[..., None] + base * (1 - w[..., None])
    # background inside M: smooth continuation of the base background around it
    bg0 = np.median(base[:20, :20].reshape(-1, 3), 0); bgE = np.median(E[:20, :20].reshape(-1, 3), 0)
    known = (np.abs(base - bg0).max(2) < 12) & ~ndi.binary_dilation(M, iterations=2)
    body_E = ndi.binary_dilation(np.abs(E - bgE).max(2) >= 22, iterations=2)
    fillpx = M & ~body_E
    fill = np.zeros_like(base); done = np.zeros(fillpx.shape, bool)
    for s in (2, 4, 8, 16, 32, 64, 128):
        num = np.stack([ndi.gaussian_filter(base[..., c] * known, s) for c in range(3)], -1); den = ndi.gaussian_filter(known.astype(np.float32), s)
        ok = fillpx & ~done & (den > 0.15)
        fill[ok] = num[ok] / den[ok][:, None]; done |= ok
    rest = fillpx & ~done; fill[rest] = bg0
    noise_sd = float(base[known].std(0).mean()) if known.any() else 1.0
    noise_sd = min(noise_sd, 1.2)
    cur[fillpx] = fill[fillpx] + rng.normal(0, noise_sd, (int(fillpx.sum()), 1))
    # leftover burgundy cape lining anywhere below the collar zone (dark red; the bright gem and the collar are excluded):
    # take the edit where the edit is not burgundy there, otherwise inpaint from the non-burgundy neighbours
    def dred(x): return (x[..., 0] < 140) & (x[..., 0] > 45) & (x[..., 0] - x[..., 1] > 20) & (x[..., 0] - x[..., 2] > 12)
    rem = dred(cur) & ~prot & ~skin
    rem = ndi.binary_opening(rem, iterations=1); lab, n = ndi.label(rem); sz = ndi.sum(rem, lab, range(1, n + 1))
    rem = ndi.binary_dilation(np.isin(lab, 1 + np.nonzero(sz >= 20)[0]), iterations=2) & ~prot & ~skin
    takeE = rem & ~dred(E); cur[takeE] = E[takeE]
    inp = rem & ~takeE
    if inp.any():
        good = (~rem).astype(np.float32)
        for s_ in (2, 4, 8):
            num = np.stack([ndi.gaussian_filter(cur[..., c] * good, s_) for c in range(3)], -1); den = ndi.gaussian_filter(good, s_)
            ok = inp & (den > 0.3); cur[ok] = num[ok] / den[ok][:, None]; inp &= ~ok
    w = np.maximum(w, rem.astype(np.float32))
    cape_new = cape | rem
    Image.fromarray((cape_new * 255).astype(np.uint8)).save(os.path.join(here, "cape_mask_%s.png" % v))
    rep[v]["FINAL_burgundy_remnant_px_fixed"] = int(rem.sum())
    Image.fromarray(np.clip(cur + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(here, outname))
    Image.fromarray((w * 255).astype(np.uint8)).save(os.path.join(here, "composite_mask_%s.png" % v))
    fin = L(outname); dc = np.abs(fin - base).mean(2); outside = w < 0.01
    dE = np.abs(E - base).mean(2)
    rep[v].update({"FINAL_mask_pct": round(100 * float((~outside).mean()), 2), "FINAL_MAD_outside_mask": round(float(dc[outside].mean()), 4),
                   "FINAL_max_outside_mask": float(dc[outside].max()), "FINAL_whole_image_MAD": round(float(dc.mean()), 2),
                   "FINAL_edit_used": ename, "FINAL_edit_MAD_outside_mask": round(float(dE[outside].mean()), 3),
                   "FINAL_edit_pct_px_gt10_outside": round(100 * float((dE[outside] > 10).mean()), 3)})
    print(v, {k: rep[v][k] for k in rep[v] if k.startswith("FINAL")})
ImageOps.mirror(Image.open(os.path.join(here, "aligned_body_side_left.png")).convert("RGB")).save(os.path.join(here, "aligned_body_side_right.png"))
json.dump(rep, open(os.path.join(here, "edit_diff.json"), "w"), indent=1)
