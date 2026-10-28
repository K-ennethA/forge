"""vampwarrior_rawhead_uvextend.py -- <raw.npz> <raw_islands.npy> <in.npz> <out.npz> [texisl.npy [texture.png]] : eye-box-only UV fix for faces that
straddle two raw UV islands (after vampwarrior_rawhead_uvfix.py). uvfix clamps such a face's off-island corner onto its
island's border, which squeezes the island's edge colour (iris red / sclera white) over the whole face (the wedges).
Here, for faces whose centroid is in an eye box, an off-island corner instead gets the UV EXTRAPOLATED from the nearest
face of the face's island (unclamped barycentrics in that face's plane), i.e. the UV runs past the island border into the
gutter, where vampwarrior_rawhead_gutterfill.py has put the colours the raw surface has there in 3D.
Faces outside the eye boxes and vertex positions are untouched (asserted)."""
import sys, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
a = sys.argv[sys.argv.index("--") + 1:]
R = np.load(a[0]); isl = np.load(a[1]); C = dict(np.load(a[2]))
TISL = np.load(a[4]) if len(a) > 4 else None       # texel island map (gutterfill): extrapolate only into gutter / own island
n_back = 0
V, F, FUV = R["V"], R["F"], R["FUV"]
Vc, Fc, Uc, fis = C["V"], C["F"], C["FUV"].copy(), C["fisland"]
tree = BVHTree.FromPolygons(V.tolist(), F.tolist())
Cc = Vc[Fc].mean(1)
box = (Cc[:, 1] < -0.06) & (np.abs(np.abs(Cc[:, 0]) - 0.13) < 0.11) & (Cc[:, 2] > 0.03) & (Cc[:, 2] < 0.14)
trees = {}; n_ext = 0
gn = {}
for i in np.nonzero(box)[0]:
    s = fis[i]
    for k in range(3):
        v = Fc[i, k]
        if v not in gn:
            gn[v] = tree.find_nearest(Vector(Vc[v].tolist()))[2]
        if isl[gn[v]] == s:
            continue
        if s not in trees:
            idx = np.nonzero(isl == s)[0]; trees[s] = (BVHTree.FromPolygons(V.tolist(), F[idx].tolist()), idx)
        t, idx = trees[s]
        f = idx[t.find_nearest(Vector(Vc[v].tolist()))[2]]
        T = V[F[f]]; p = Vc[v]
        v0, v1, v2 = T[1] - T[0], T[2] - T[0], p - T[0]
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-20:
            continue
        b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
        b = np.array([1 - b1 - b2, b1, b2])
        if b.min() < -2.0 or b.max() > 3.0:      # too far past the island face: keep clamped
            n_back += 1; continue
        un = b @ FUV[f]
        if (un < 1.0 / 4096).any() or (un > 1 - 1.0 / 4096).any():      # would wrap round the texture edge
            n_back += 1; continue
        if TISL is not None:
            th_, tw_ = TISL.shape
            okk = True
            for q in (un, 0.5 * (un + Uc[i, (k + 1) % 3]), 0.5 * (un + Uc[i, (k + 2) % 3])):
                lab = TISL[int(np.clip(q[1] * th_, 0, th_ - 1)), int(np.clip(q[0] * tw_, 0, tw_ - 1))]
                if lab not in (-1, s):
                    okk = False
            if not okk:
                n_back += 1; continue
        Uc[i, k] = un; n_ext += 1
# per face keep whichever UV variant (clamped = uvfix, or extrapolated) reproduces the raw colours better (texture a[5])
if len(a) > 5:
    import bpy
    im = bpy.data.images.load(a[5]); w_, h_ = im.size; px = np.empty(w_ * h_ * 4, np.float32); im.pixels.foreach_get(px)
    px = px.reshape(h_, w_, 4)[::-1, :, :3] * 255.0          # row 0 = top, like the FUV (v-down) convention

    def look(uv):
        return px[np.clip((uv[:, 1] * h_).astype(int), 0, h_ - 1), np.clip((uv[:, 0] * w_).astype(int), 0, w_ - 1)]
    BC = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6], [0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8],
                   [0.5, 0.5, 0.0], [0.0, 0.5, 0.5], [0.5, 0.0, 0.5], [0.45, 0.45, 0.1], [0.1, 0.45, 0.45], [0.45, 0.1, 0.45]])
    bi = np.nonzero(box)[0]; U0 = C["FUV"]
    e0 = np.zeros(len(bi)); e1 = np.zeros(len(bi))
    for b in BC:
        Pp = np.einsum("k,nkd->nd", b, Vc[Fc[bi]])
        ref = np.empty((len(bi), 2))
        for j, p in enumerate(Pp):
            h = tree.find_nearest(Vector(p.tolist())); f = h[2]; T = V[F[f]]; q = np.array(h[0])
            v0, v1, v2 = T[1] - T[0], T[2] - T[0], q - T[0]
            d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1; den = max(d00 * d11 - d01 * d01, 1e-20)
            b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
            ref[j] = np.array([1 - b1 - b2, b1, b2]) @ FUV[f]
        cr = look(ref)
        e0 = np.maximum(e0, np.linalg.norm(look(np.einsum("k,nkd->nd", b, U0[bi])) - cr, axis=1))
        e1 = np.maximum(e1, np.linalg.norm(look(np.einsum("k,nkd->nd", b, Uc[bi])) - cr, axis=1))
    worse = e1 > e0                # keep the extrapolation only where it is not worse
    Uc[bi[worse]] = U0[bi[worse]]
    print("UVEXTEND choose: extrapolated kept on", int((~worse & (np.abs(Uc[bi] - U0[bi]).max((1, 2)) > 0)).sum()), "faces, reverted", int(worse.sum()),
          "| face colour error max p99 clamped %.1f -> final %.1f" % (np.percentile(e0, 99), np.percentile(np.minimum(e0, e1), 99)), flush=True)
assert (Uc[~box] == C["FUV"][~box]).all()
C["FUV"] = Uc
np.savez_compressed(a[3], **C)
print("UVEXTEND eye-box faces", int(box.sum()), "corners extrapolated", n_ext, "kept clamped (would land on another island)", n_back, flush=True)
