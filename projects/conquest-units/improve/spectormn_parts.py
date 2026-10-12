"""Spectormn parametric pieces (pure numpy, deterministic): the generators behind the cloth-ghost SDF + its closed parts.

The hooded cloth body (hood, shoulders, torso, the skirt that turns to mist), the sleeves and the mist wisps are ONE SDF
(spectormn_build.py); the eyes, chest slits, tattered hem, fold shadows and mist bands are iso-cut into that shell. The
hard pieces are closed parametric solids (0 open edges): the wrist SHACKLES (torus) and the CHAIN LINKS (stadium tubes).

    Part / open_edges / orient_outward / torus     the firefly_parts idioms (copied, so the open-edge table reads alike)
    Profile / sd_polygon2       ELLIPTIC LATHE of a measured profile (eye_totem_parts, copied) + a per-row y centre (the
                                mist trails back) and the elliptic front angle theta_f the cloth fields read
    oval_tilted                 front-projected tilted oval field (> 0 inside): the eyes + chest slits as drawn
    hem_drop                    the TATTERED HEM: seeded pointed tatters (concave triangles) hanging off a hem line, as an
                                even function of theta_f (mirror-symmetric about X = 0 by construction)
    fold_field                  the soft vertical FOLD ridges: integer cos harmonics of theta_f (even -> symmetric, no
                                crease at the front / back midline), amplitude varying with height (never ribbed rings)
    wisp_blobs / blob_box       a mist WISP as a chain of flattened tapering blobs (fogeist_parts, copied)
    stadium_link                one CHAIN LINK: a closed round tube along a stadium (two semicircles + straight sides)
    chain_strand                links at a fixed pitch along a hanging path, alternating link-plane orientation by 90 deg
    mirror_part                 X -> -X copy with the face winding reversed (the R side of every L part)
"""
import math
import numpy as np

import magmoo_sdf as SD          # read-only use (the SDF kit)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


class Part:
    def __init__(self, name, V, F, R, bone=None):
        self.name = name
        self.V = np.asarray(V, float)
        self.F = [list(map(int, f)) for f in F]
        self.R = list(R)
        self.bone = bone
        assert len(self.F) == len(self.R), name

    def tris(self):
        return int(sum(len(f) - 2 for f in self.F))


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def orient_outward(V, F, inside_pt):
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def torus(R0, r, n_major, n_minor, centre, axis, up_hint=(0, 0, 1), region="rim"):
    """round ring tube (the shackles): centre-line radius R0, tube radius r, about 'axis'"""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, F = [], []
    for i in range(n_major):
        t = 2 * math.pi * i / n_major
        radial = math.cos(t) * u + math.sin(t) * v
        ring_c = c + R0 * radial
        for j in range(n_minor):
            p = 2 * math.pi * j / n_minor
            V.append(ring_c + r * (math.cos(p) * radial + math.sin(p) * a))
    for i in range(n_major):
        for j in range(n_minor):
            i1, j1 = (i + 1) % n_major, (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    V = np.array(V)
    f0 = F[0]
    nrm = np.cross(V[f0[1]] - V[f0[0]], V[f0[2]] - V[f0[0]])
    if np.dot(nrm, V[f0[0]] - (c + R0 * u)) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


def mirror_part(V, F):
    return np.asarray(V, float) * np.array([-1.0, 1.0, 1.0]), [list(f[::-1]) for f in F]


# --------------------------------------------------------------------------- elliptic lathe of a measured profile
class Profile:
    """rows (z, half_width, half_depth, y_centre) sorted by z: half-width from the FRONT / BACK orthos, half-depth and
    y centre estimated from the 3/4 side. The closed (q, z) polygon = the axis + the half-width polyline; ends with
    w = 0 are poles on the axis. q is the elliptic radius in half-width units (y scaled by w / d about y_centre)."""

    def __init__(self, rows, n_per=4):
        R = np.asarray(sorted(rows), float)
        self.rows = R
        # smooth the measured rows (Catmull-Rom through (z, w, d, yc), n_per samples per span): a piecewise-linear
        # profile shows every row as a horizontal ring under flat shading (the 'stacked' look)
        S = SD.catmull(R, n_per)
        S = S[np.argsort(S[:, 0], kind="stable")]
        S[:, 1:3] = np.maximum(S[:, 1:3], 0.0)
        S[0, 1:3] = R[0, 1:3]; S[-1, 1:3] = R[-1, 1:3]          # the poles stay poles
        keep = np.r_[True, np.diff(S[:, 0]) > 1e-6]
        S = S[keep]
        self.z, self.w, self.d, self.yc = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
        pts = [(0.0, self.z[0])] + [(max(w, 0.0), z) for w, z in zip(self.w, self.z)] + [(0.0, self.z[-1])]
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 1e-9:
                out.append(p)
        self.poly = np.array(out)

    def local(self, P):
        """-> (x', y', z): x' = x, y' = (y - y_centre(z)) * w / d (the section made circular)"""
        P = np.atleast_2d(P)
        z = P[:, 2]
        yc = np.interp(z, self.z, self.yc)
        s = np.interp(z, self.z, np.maximum(self.w, 1e-3) / np.maximum(self.d, 1e-3))
        return P[:, 0], (P[:, 1] - yc) * s, z

    def theta_f(self, P):
        """elliptic angle from the FRONT (-Y): 0 at the front midline, +-pi at the back; even under X -> -X"""
        x, yy, _ = self.local(P)
        return np.arctan2(x, -yy)

    def sd(self, P):
        x, yy, z = self.local(P)
        return sd_polygon2(np.stack([np.sqrt(x * x + yy * yy), z], 1), self.poly)


def sd_polygon2(Q, V, skip_axis=True):
    """IQ's exact signed distance to a closed 2D polygon V (n, 2), vectorised over points Q (m, 2); < 0 inside.
    skip_axis: segments lying on q = 0 (the lathe axis) count for the inside test but are not surface."""
    d = np.full(len(Q), 1e18)
    s = np.ones(len(Q))
    n = len(V)
    for i in range(n):
        a = V[i - 1]; b = V[i]
        e = a - b
        w = Q - b
        if not (skip_axis and abs(a[0]) < 1e-12 and abs(b[0]) < 1e-12):
            t = np.clip((w @ e) / max(float(e @ e), 1e-18), 0.0, 1.0)
            bb = w - np.outer(t, e)
            d = np.minimum(d, (bb * bb).sum(1))
        c1 = Q[:, 1] >= b[1]
        c2 = Q[:, 1] < a[1]
        c3 = e[0] * w[:, 1] > e[1] * w[:, 0]
        flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
        s = np.where(flip, -s, s)
    return s * np.sqrt(d)


# --------------------------------------------------------------------------- face features
def oval_tilted(P, cx, cz, a, b, tilt_deg):
    """> 0 inside the front-projected oval (half-width a, half-height b) centred at (x cx, z cz), its long axis tilted
    tilt_deg from vertical (+ = the top leans to +X). 1 - (u / a)^2 - (v / b)^2, independent of y."""
    t = math.radians(tilt_deg)
    dx, dz = P[:, 0] - cx, P[:, 2] - cz
    v = dx * math.sin(t) + dz * math.cos(t)          # along the long axis
    u = dx * math.cos(t) - dz * math.sin(t)
    return 1.0 - (u / a) ** 2 - (v / b) ** 2


# --------------------------------------------------------------------------- cloth fields
def tatters(seed, n_half, depth_rng, width_rng, jitter=0.25):
    """seeded tatters over theta_f in [0, pi] (front midline -> back midline); mirrored by evenness. -> [(theta, depth,
    half_width_rad)]. Tatters at the midlines (theta 0 / pi) are their own mirror images."""
    rng = np.random.default_rng(seed)
    th = np.linspace(0.0, math.pi, n_half)
    step = math.pi / max(n_half - 1, 1)
    out = []
    for i, t in enumerate(th):
        tj = t + (0.0 if i in (0, n_half - 1) else rng.uniform(-jitter, jitter) * step)
        out.append((tj, rng.uniform(*depth_rng), rng.uniform(*width_rng)))
    return out


def hem_drop(theta_f, tat, power):
    """drop below the hem line at theta_f: max over the tatters of depth * max(0, 1 - |dtheta| / w) ** power (power > 1:
    concave sides -> pointed tips). Evaluated on |theta_f| (even -> mirror-symmetric)."""
    th = np.abs(theta_f)
    d = np.zeros_like(th)
    for t0, D, w in tat:
        for tt in (t0, -t0, 2 * math.pi - t0):       # wrap round the back midline
            d = np.maximum(d, D * np.clip(1.0 - np.abs(th - tt) / w, 0.0, 1.0) ** power)
    return d


def fold_field(theta_f, z, terms):
    """soft vertical folds: sum of a_k(z) cos(m_k theta_f) with integer m_k (even in theta_f -> symmetric and smooth
    across both midlines); a_k(z) = a_k * (1 + b_k sin(kz_k z + ph_k)) varies the folds with height. Normalised to ~[-1, 1]."""
    out = np.zeros_like(theta_f)
    tot = 0.0
    for m, a, b, kz, ph in terms:
        out += a * (1.0 + b * np.sin(kz * z + ph)) * np.cos(m * theta_f)
        tot += a * (1.0 + abs(b))
    return out / max(tot, 1e-9)


def streak_field(theta_f, z, terms, round_=0.03):
    """flowing mist STREAKS: sum of a_k sin(m_k u + ph_k + kz_k z) / sum a_k over u = arccos((1 - round_) cos theta_f):
    |theta_f| with its two kinks ROUNDED (zero slope across the front / back midlines over ~sqrt(2 round_) rad), so the
    field is even (mirror-symmetric) and smooth. Plain |theta| left thin V wedges at the midlines that fragmented into
    blocky patches (preview r5); a fully flat reparametrisation (1 - cos) turned the streaks into arches there (r6).
    kz leans each streak family with height so the streaks flow instead of standing as regular candy stripes."""
    u = np.arccos((1.0 - round_) * np.cos(theta_f))
    out = np.zeros_like(u)
    tot = 0.0
    for m, a, ph, kz in terms:
        out += a * np.sin(m * u + ph + kz * z)
        tot += abs(a)
    return out / max(tot, 1e-9)


# --------------------------------------------------------------------------- mist wisps (fogeist_parts, copied)
def wisp_blobs(ctrl, n, rx, rz, face, overlap=0.75, wobble=0.15):
    """a WISP as a chain of n flattened blobs along the Catmull-Rom curve through ctrl (root -> tip half-width rx and
    half-thickness rz, linear; alternate blobs swell / shrink by +-wobble; a rounded end blob). The thickness axis
    follows 'face' (the outward direction the strand shows its broad side to; the fogeist original used world up, which
    is degenerate for hanging strands). Returns [(centre, radii, frame)] for SD.sd_ellipsoid; frame rows = the blob's
    local x (side), y (tangent), z (thickness) axes."""
    pts = SD.catmull(np.asarray(ctrl, float), 16)
    s = SD.arclen(pts)
    L = float(s[-1])
    sp = L / max(n - 1, 1)
    out = []
    for i in range(n):
        t = i / max(n - 1, 1)
        si = t * L
        c = np.array([np.interp(si, s, pts[:, k]) for k in range(3)])
        j = min(int(np.searchsorted(s, si)), len(pts) - 1)
        j0, j1 = max(j - 2, 0), min(j + 2, len(pts) - 1)
        ty = unit(pts[j1] - pts[j0])
        ref = unit(face)
        tz = unit(ref - ty * float(ty @ ref))
        tx = np.cross(ty, tz)
        w = 1.0 + wobble * (1.0 if i % 2 == 0 else -1.0) * (1.0 if 0 < i < n - 1 else 0.0)
        r = np.array([(rx[0] + (rx[1] - rx[0]) * t) * w, overlap * sp + 0.5 * (rx[0] + (rx[1] - rx[0]) * t),
                      (rz[0] + (rz[1] - rz[0]) * t) * w])
        if i == n - 1:
            r[1] = 1.2 * max(r[0], r[2])
        out.append((c, r, np.stack([tx, ty, tz], 0)))
    return out


def blob_box(c, r):
    m = float(np.max(r))
    return np.asarray(c, float) - m, np.asarray(c, float) + m


# --------------------------------------------------------------------------- chain
def stadium_link(centre, t_axis, n_axis, half_len, half_w, r_wire, n_end=5, n_minor=6, region="chain_link"):
    """ONE closed chain link: a round tube (radius r_wire, n_minor sides) along a stadium centre-line in the plane
    spanned by t_axis (long, half_len to the end-curve centre-line) and n_axis x t_axis (short, half_w), i.e. the link
    plane's normal is n_axis. Two semicircles (n_end samples each, inclusive) joined by straight sides. 0 open edges.
    Returns V, F, R, s_along (per vertex: signed offset along t_axis from the centre)."""
    t = unit(t_axis)
    nrm = unit(np.asarray(n_axis, float) - t * float(np.dot(n_axis, t)))
    b = np.cross(nrm, t)                                   # in-plane short axis
    c = np.asarray(centre, float)
    h = half_len - half_w                                  # semicircle centres at +-h along t
    P, N = [], []
    for sgn in (1.0, -1.0):
        cc = c + sgn * h * t
        for k in range(n_end):
            a = -math.pi / 2 + math.pi * k / (n_end - 1)    # -90 .. 90 deg round the end
            d = sgn * (math.cos(a) * t + math.sin(a) * b)
            P.append(cc + half_w * d); N.append(d)
    nc = len(P)
    V, S = [], []
    for p, d in zip(P, N):
        for j in range(n_minor):
            ph = 2 * math.pi * j / n_minor
            q = p + r_wire * (math.cos(ph) * d + math.sin(ph) * nrm)
            V.append(q); S.append(float(np.dot(q - c, t)))
    F = []
    for i in range(nc):
        i1 = (i + 1) % nc
        for j in range(n_minor):
            j1 = (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    V = np.array(V)
    Fo = []
    for f in F:                                           # outward = away from the tube's own centre-line point
        q = V[f]
        i_ = f[0] // n_minor
        nn = np.cross(q[1] - q[0], q[2] - q[0]) + np.cross(q[2] - q[0], q[3] - q[0])
        mid = 0.5 * (P[i_] + P[(i_ + 1) % nc])
        Fo.append(f if np.dot(nn, q.mean(0) - mid) >= 0 else f[::-1])
    return V, Fo, [region] * len(Fo), np.array(S)


def chain_strand(ctrl, n_links, pitch, half_len, half_w, r_wire, face_normal, n_end=5, n_minor=6, region="chain_link",
                 end_region=None):
    """n_links links whose centres sit at arc s_i = half_len_inner + i * pitch along the Catmull-Rom path through ctrl
    (ctrl[0] = the hanging point: link 0's top end-curve centre-line sits on it). Link planes alternate: even links face
    face_normal (made perpendicular to the tangent), odd links are turned 90 deg about the tangent. Returns
    (V, F, R, s_vertex, info): s_vertex = each vertex's arc position along the strand (rigging). end_region: the region
    of the FIRST (at the shackle) and LAST (free end) link -- the sheet draws those in dark iron."""
    pts = SD.catmull(np.asarray(ctrl, float), 24)
    s = SD.arclen(pts)
    V, F, R, Sv, centres = [], [], [], [], []
    for i in range(n_links):
        si = half_len + i * pitch
        assert si <= s[-1] + 1e-6, ("chain path too short", si, float(s[-1]))
        c = np.array([np.interp(si, s, pts[:, k]) for k in range(3)])
        j = min(int(np.searchsorted(s, si)), len(pts) - 1)
        tg = unit(pts[min(j + 2, len(pts) - 1)] - pts[max(j - 2, 0)])
        n0 = unit(np.asarray(face_normal, float) - tg * float(np.dot(face_normal, tg)))
        nn = n0 if i % 2 == 0 else np.cross(tg, n0)
        reg = end_region if (end_region and i in (0, n_links - 1)) else region
        v_, f_, r_, s_ = stadium_link(c, tg, nn, half_len, half_w, r_wire, n_end, n_minor, reg)
        o = len(V)
        V += list(v_); F += [[k + o for k in f] for f in f_]; R += r_; Sv += list(si + s_)
        centres.append(c)
    return np.array(V), F, R, np.array(Sv), {"length": float(half_len * 2 + (n_links - 1) * pitch),
                                             "path_length": float(s[-1]), "centres": np.array(centres)}
