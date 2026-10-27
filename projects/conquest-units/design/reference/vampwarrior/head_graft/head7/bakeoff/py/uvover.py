import bpy, numpy as np, sys
ob = bpy.data.objects["head"]; me = ob.data
uv = np.empty(len(me.loops) * 2); me.uv_layers[0].data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
N = 512; cnt = np.zeros((N, N), np.int32)
for p in me.polygons:
    L = list(p.loop_indices); P = uv[L] * N
    x0, y0 = np.floor(P.min(0)).astype(int); x1, y1 = np.ceil(P.max(0)).astype(int)
    xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    inside = np.zeros(xs.shape, bool)
    for t in range(1, len(L) - 1):
        a, b, c = P[0], P[t], P[t + 1]
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-12: continue
        l1 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / d
        l2 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / d
        inside |= (l1 >= 0) & (l2 >= 0) & (1 - l1 - l2 >= 0)
    yy = np.clip(ys[inside].astype(int), 0, N - 1); xx = np.clip(xs[inside].astype(int), 0, N - 1)
    np.add.at(cnt, (yy, xx), 1)
print("UVCOV used", round(float((cnt > 0).mean()), 3), "overlap px frac of used", round(float((cnt > 1).sum() / max((cnt > 0).sum(), 1)), 3))
