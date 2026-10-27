# the outline helpers of cape_masks.py (Moore tracing, Douglas-Peucker), shared with composite_r2.py
import numpy as np

def moore_trace(m):
    """Outer boundary of a single 8-connected blob (bool array), Moore-neighbour tracing, Jacob's stopping criterion.
    Returns [(x, y), ...] pixel centres, clockwise in image coordinates."""
    H, W = m.shape; p = np.zeros((H + 2, W + 2), bool); p[1:-1, 1:-1] = m
    ys, xs = np.nonzero(p); i = np.lexsort((xs, ys))[0]; start = (int(ys[i]), int(xs[i]))
    nb = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]      # N, NE, E, SE, S, SW, W, NW
    cur = start; back = 6                                                            # came from the west
    out = [cur]; first_dir = None
    for _ in range(4 * p.size):
        for k in range(8):
            d = (back + 1 + k) % 8; y, x = cur[0] + nb[d][0], cur[1] + nb[d][1]
            if p[y, x]:
                prev = cur; cur = (y, x); back = (d + 4) % 8
                break
        else:
            break                                                                    # isolated pixel
        if first_dir is None: first_dir = cur
        elif prev == start and cur == first_dir: out.pop(); break
        out.append(cur)
    return [(x - 1, y - 1) for y, x in out]

def dp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3: return pts.tolist()
    a, b = pts[0], pts[-1]; ab = b - a; L = np.hypot(*ab)
    q = pts - a; d = np.abs(ab[0] * q[:, 1] - ab[1] * q[:, 0]) / L if L > 0 else np.hypot(q[:, 0], q[:, 1])
    i = int(np.argmax(d))
    if d[i] > eps: return dp(pts[:i + 1], eps)[:-1] + dp(pts[i:], eps)
    return [a.tolist(), b.tolist()]

def dp_closed(ring, eps):
    r = np.asarray(ring, float); far = int(np.argmax(np.hypot(*(r - r[0]).T)))
    A = dp(r[:far + 1], eps); B = dp(np.vstack([r[far:], r[:1]]), eps)
    return [[round(x, 1), round(y, 1)] for x, y in (A[:-1] + B[:-1])]

