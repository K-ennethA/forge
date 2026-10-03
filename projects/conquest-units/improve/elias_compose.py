"""Elias v1 draft comparison sheet (plain Python + PIL, no Blender; run with the forge service venv):

    python -P elias_compose.py

renders/elias/ inputs: elias_v1_<view>.png (front / side / back / threequarter full body; portrait / face_tq / glasses /
staff_head / book / satchel / belt / brooch close-ups) + improved/elias.json + rigged/elias.json + the face probe json.
Output: renders/elias/elias_v1_sheet.png -- ONE sheet (render economy): row 1 the full body four ways, row 2 the face +
props detail, a strip of the measured numbers under it.
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "elias")
V = os.environ.get("ELIAS_V", "elias_v1")
IMP = json.load(open(os.path.join(ROOT, "improved", "elias.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "elias.json")))
FP = os.path.join(R, V + "_face_probe.json")
PROBE = json.load(open(FP)) if os.path.exists(FP) else None
ROW1 = [("front", "front"), ("side", "side (his left)"), ("back", "back"), ("threequarter", "three-quarter")]
ROW2 = [("portrait", "face"), ("face_tq", "face 3/4"), ("glasses", "glasses"), ("staff_head", "staff: armillary + orb"),
        ("book", "tome (left hand)"), ("satchel", "satchel + crest"), ("belt", "belt / pouches / scroll tubes"),
        ("brooch", "cravat + trident brooch")]
W1, W2, PAD, LAB = 560, 280, 8, 24
try:
    FONT = ImageFont.truetype("arial.ttf", 18); FONT_S = ImageFont.truetype("arial.ttf", 15)
except Exception:
    FONT = FONT_S = ImageFont.load_default()


def tile(name, w):
    p = os.path.join(R, "%s_%s.png" % (V, name))
    if not os.path.exists(p):
        return Image.new("RGB", (w, w), (60, 30, 30))
    return Image.open(p).convert("RGB").resize((w, w), Image.LANCZOS)


cols2 = 4
rows2 = (len(ROW2) + cols2 - 1) // cols2
Wt = 4 * W1 + 5 * PAD
row2_w = (Wt - (cols2 + 1) * PAD) // cols2
H = PAD + LAB + W1 + PAD + rows2 * (LAB + row2_w + PAD) + 200
sheet = Image.new("RGB", (Wt, H), (28, 28, 30))
d = ImageDraw.Draw(sheet)
y = PAD
for k, (v, lab) in enumerate(ROW1):
    x = PAD + k * (W1 + PAD)
    d.text((x + 4, y + 2), lab, fill=(235, 235, 235), font=FONT)
    sheet.paste(tile(v, W1), (x, y + LAB))
y += LAB + W1 + PAD
for k, (v, lab) in enumerate(ROW2):
    r_, c_ = divmod(k, cols2)
    x = PAD + c_ * (row2_w + PAD)
    yy = y + r_ * (LAB + row2_w + PAD)
    d.text((x + 4, yy + 2), lab, fill=(235, 235, 235), font=FONT)
    sheet.paste(tile(v, row2_w), (x, yy + LAB))
y += rows2 * (LAB + row2_w + PAD)
t = IMP["tris"]
pr = IMP.get("proportions", {})
mp = IMP.get("mouth_placement", {})
ep = IMP.get("eye_proof", {})
gl = IMP.get("parts", {}).get("glasses", {})
lines = [
    "PROFESSOR ELIAS %s (hero tier)   tris %d (main %d + staff %d + book %d; budget 30-50k)   body %d, scalp locks %d, beard+mustache %d%s"
    % (V.split("_")[-1] + " draft", t["total"], t["main"], t["staff"], t["book"], t["body"], t["scalp_locks"],
       t.get("beard_mustache", t.get("beard_mustache_locks", 0)), " (v4: shells)" if "beard_mustache" in t else ""),
    "heads tall %.2f (house rule; Wren 5.94)  /  %.2f by chin outline (Wren 6.90)   height %.3f m (staff top %.3f m)"
    % (pr.get("heads_tall_house_rule", 0), pr.get("heads_tall_chin_outline", 0), IMP["landmarks"]["height_total"],
       IMP["measure"]["height"]),
    "mouth: v_ratio %.3f (Ashe 0.29), width / eye spacing %.3f (house 0.65-0.73), seam %.1f mm   eyes: iris coverage L %.1f%% (55-65%%)"
    % (mp.get("v_ratio", 0), mp.get("w_eyes", 0), mp.get("seam_width_mm", 0), ep.get("L", {}).get("iris_coverage_pct", 0)),
    "glasses rim r %.1f mm, %.1f mm in front of the cornea, open rims (no lens)   staff roll %s deg, wrist bend %.1f deg (hold evaluation)   clips: none (artist-gated)"
    % (gl.get("rim_r_mm", {}).get("L", 0), gl.get("rim_in_front_of_cornea_mm", {}).get("L", 0), RIG["grip"]["staff"]["roll_deg"],
       RIG["grip"]["staff"]["wrist_bend_deg"]),
]
if PROBE:
    ue = PROBE.get("undereye", {})
    try:
        lines.append("face probe (under-eye, max): flatness L %.2f / R %.2f mm, lid line L %.2f / R %.2f mm, crease L %.2f mm "
                     "(Wren v6.1: 0.55 / 0.47 / 1.16)"
                     % (ue["L"]["flatness_mm"]["max"], ue["R"]["flatness_mm"]["max"], ue["L"]["lid_line_mm"]["max"],
                        ue["R"]["lid_line_mm"]["max"], ue["L"]["crease_mm_dense"]["max"]))
    except Exception:
        lines.append("face probe: " + ", ".join(sorted(PROBE.keys()))[:160])
for k, ln in enumerate(lines):
    d.text((PAD + 4, y + 6 + 26 * k), ln, fill=(220, 220, 210), font=FONT_S)
out = os.path.join(R, V + "_sheet.png")
sheet.save(out)
print("SHEET", out, sheet.size)
# ---- v2: the ONE before / after hair strip (v1 | this version): portrait, face 3/4, front, back + the hair-diag numbers
BASE = os.environ.get("ELIAS_BASE", "elias_v1")
if V != BASE:
    pairs = [("portrait", "face"), ("face_tq", "face 3/4"), ("portrait_low", "face from below (chin)"), ("front", "front")]
    if os.environ.get("ELIAS_STRIP_VIEWS"):          # v5: "view:label,view:label" (the head views the artist compared)
        pairs = [tuple(x_.split(":", 1)) for x_ in os.environ["ELIAS_STRIP_VIEWS"].split(",")]
    if os.environ.get("ELIAS_ONETONE"):              # v4: + the one-tone hair shading column (every hair region painted the
        pairs.append((os.environ.get("ELIAS_ONETONE_VIEW", "onetone_face_tq"), "hair one-tone"))   #   one grey: shading only)
    # v5: + the SHEET's own view beside each pair (the judge: design/reference/elias/elias_sheet.webp crops, 1536 x 1024 px)
    REF = {"headc_front": (170, 85, 300, 215), "headc_tq": (1056, 52, 1300, 296), "headc_side": (480, 85, 610, 215),
           "headc_back": (800, 95, 930, 225),
           # v6: the head + beard reads (square crops including the whole beard)
           "hb_front": (160, 95, 310, 245), "hb_side": (475, 95, 625, 245), "hb_tq": (1056, 80, 1300, 324)}         if os.environ.get("ELIAS_STRIP_REF") else {}
    SHEET_IMG = os.path.join(ROOT, "design", "reference", "elias", "elias_sheet.webp")
    S = 420
    ncol = [3 if v in REF else 2 for v, _ in pairs]
    st = Image.new("RGB", (PAD + sum(c_ * (S + PAD) + PAD for c_ in ncol), PAD + LAB + S + PAD + 6 * 22), (28, 28, 30))
    ds = ImageDraw.Draw(st)
    x0 = PAD
    for k, (v, lab) in enumerate(pairs):
        for j, ver in enumerate((BASE, V)):
            p = os.path.join(R, "%s_%s.png" % (ver, v))
            im = Image.open(p).convert("RGB").resize((S, S), Image.LANCZOS) if os.path.exists(p) else Image.new("RGB", (S, S), (60, 30, 30))
            st.paste(im, (x0 + j * (S + PAD), PAD + LAB))
            ds.text((x0 + j * (S + PAD) + 4, PAD + 2), "%s  %s" % (lab, ver.split("_")[-1]), fill=(235, 235, 235), font=FONT)
        if v in REF:
            im = Image.open(SHEET_IMG).convert("RGB").crop(REF[v]).resize((S, S), Image.LANCZOS)
            st.paste(im, (x0 + 2 * (S + PAD), PAD + LAB))
            ds.text((x0 + 2 * (S + PAD) + 4, PAD + 2), "%s  SHEET" % lab, fill=(235, 235, 235), font=FONT)
        x0 += ncol[k] * (S + PAD) + PAD

    def diag(ver):
        p = os.path.join(R, ver + "_hairdiag.json")
        return json.load(open(p)) if os.path.exists(p) else None
    yl = PAD + LAB + S + PAD
    for k, ver in enumerate((BASE, V)):
        d_ = diag(ver)
        if d_:
            ip = d_["interpenetration"]
            ds.text((PAD + 4, yl + 22 * k), "%s hair diag: %d locks | lock pairs cutting %d (tri pairs %d), top sheets %d / %d | paint patches / lock %.2f | "
                    "top-facet kinks p50 / p90 %.1f / %.1f deg (>20 deg %.1f%%) | slivers %d" % (
                        ver.split("_")[-1], d_["locks"], ip["lock_pairs"], ip["tri_pairs"], ip["top_sheets"]["lock_pairs"],
                        ip["top_sheets"]["tri_pairs"], d_["paint"]["patches_per_lock"], d_["facet_kinks_top_deg"]["p50"],
                        d_["facet_kinks_top_deg"]["p90"], d_["facet_kinks_top_deg"]["share_over_20deg_pct"],
                        d_["faces"]["sliver_aspect_over_20"]), fill=(220, 220, 210), font=FONT_S)
    if os.environ.get("ELIAS_STRIP_NOTE"):
        ds.text((PAD + 4, yl + 44), os.environ["ELIAS_STRIP_NOTE"], fill=(220, 220, 210), font=FONT_S)
    outs = os.path.join(R, V + "_hair_compare.png")
    st.save(outs)
    print("STRIP", outs, st.size)
