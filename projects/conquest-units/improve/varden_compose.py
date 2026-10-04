"""General Varden v1 draft comparison sheet (plain Python + PIL, no Blender; run with the forge service venv):

    python -P varden_compose.py          (env VARDEN_V = the render prefix, default varden_v1)

renders/varden/ inputs: <ver>_<view>.png (front / side / back / threequarter full body; portrait / face_tq / fur / fur_back
/ sword / sword_full / emblem / brooch close-ups) + improved/varden.json + rigged/varden.json + the face probe json + the
hair diag json + the sheet itself (design/reference/varden/varden_sheet.webp, shown small for the side-by-side read).
Output: renders/varden/<ver>_sheet.png -- ONE sheet (render economy): row 1 the full body four ways + the reference,
row 2 the face / fur mantle / sword / emblem / brooch detail, a strip of the measured numbers under it.
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "varden")
V = os.environ.get("VARDEN_V", "varden_v1")
IMP = json.load(open(os.path.join(ROOT, "improved", "varden.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "varden.json")))
FP = os.path.join(R, V + "_face_probe.json")
PROBE = json.load(open(FP)) if os.path.exists(FP) else None
HD = os.path.join(R, V + "_hairdiag.json")
HDIAG = json.load(open(HD)) if os.path.exists(HD) else None
ROW1 = [("front", "front"), ("side", "side (his left)"), ("back", "back"), ("threequarter", "three-quarter")]
ROW2 = [("headc_front", "head front (v5 scalp)"), ("headc_tq", "head 3/4, his right (sheet head panel)"),
        ("headc_side", "head side (his left)"), ("headc_back", "head back"), ("portrait", "face"), ("face_tq", "face 3/4"), ("fur", "fur mantle (clumps)"), ("fur_back", "fur mantle, behind"),
        ("sword", "sword hilt (left hip)"), ("sword_full", "sword + scabbard"), ("emblem", "cloak emblem (back)"),
        ("brooch", "brooch + pendant + chain")]
W1, PAD, LAB = 520, 8, 24
try:
    FONT = ImageFont.truetype("arial.ttf", 18); FONT_S = ImageFont.truetype("arial.ttf", 15)
except Exception:
    FONT = FONT_S = ImageFont.load_default()


def tile(name, w):
    p = os.path.join(R, "%s_%s.png" % (V, name))
    if not os.path.exists(p):
        return Image.new("RGB", (w, w), (60, 30, 30))
    return Image.open(p).convert("RGB").resize((w, w), Image.LANCZOS)


ref = Image.open(os.path.join(ROOT, "design", "reference", "varden", "varden_sheet.webp")).convert("RGB")
ref_w = int(W1 * ref.width / ref.height)
cols2 = 4
Wt = 4 * W1 + ref_w + 6 * PAD
row2_w = (Wt - (cols2 + 1) * PAD) // cols2
rows2 = (len(ROW2) + cols2 - 1) // cols2
H = PAD + LAB + W1 + PAD + rows2 * (LAB + row2_w + PAD) + 210
sheet = Image.new("RGB", (Wt, H), (28, 28, 30))
d = ImageDraw.Draw(sheet)
y = PAD
for k, (v, lab) in enumerate(ROW1):
    x = PAD + k * (W1 + PAD)
    d.text((x + 4, y + 2), lab, fill=(235, 235, 235), font=FONT)
    sheet.paste(tile(v, W1), (x, y + LAB))
x = PAD + 4 * (W1 + PAD)
d.text((x + 4, y + 2), "reference: design/reference/varden/varden_sheet.webp", fill=(235, 235, 235), font=FONT)
sheet.paste(ref.resize((ref_w, W1), Image.LANCZOS), (x, y + LAB))
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
sw = IMP.get("parts", {}).get("sword", {})
fur = IMP.get("parts", {}).get("cloak", {}).get("fur", {})
g = RIG.get("grip", {}).get("sword", {})
lines = [
    "GENERAL VARDEN %s (hero tier)   tris %d (main %d + sword %d + scabbard %d; budget 30-50k)   body %d, scalp locks %d, beard+mustache %d (v4: shells), "
    "fur %d (%d tufts) + roll %d" % (V.split("_")[-1] + " draft", t["total"], t["main"], t["sword"], t["scabbard"], t["body"],
                                     t["scalp_locks"], t.get("beard_mustache", t.get("beard_mustache_locks", 0)), t.get("fur", 0), fur.get("tufts", 0), t.get("furroll", 0)),
    "heads tall %.2f (house rule; Wren 5.94, Elias 5.96)  /  %.2f by chin outline   height %.3f m" %
    (pr.get("heads_tall_house_rule", 0), pr.get("heads_tall_chin_outline", 0), IMP["measure"]["height"]),
    "mouth: v_ratio %.3f (Ashe 0.29), width / eye spacing %.3f (house 0.65-0.73), seam %.1f mm   eyes: iris coverage L %.1f%% (55-65%%)"
    % (mp.get("v_ratio", 0), mp.get("w_eyes", 0), mp.get("seam_width_mm", 0), ep.get("L", {}).get("iris_coverage_pct", 0)),
    "sword %.2f m at the left hip (stand-off %s m, tilt fwd / out %s deg); guard-hold roll %s deg, wrist bend %.1f deg   clips: none (artist-gated)"
    % (sw.get("total_len_m", 0), sw.get("standoff_m"), sw.get("tilt_fwd_out_deg"), g.get("roll_deg"), g.get("wrist_bend_deg", 0)),
]
if HDIAG:
    ip = HDIAG["interpenetration"]
    lines.append("hair diag: %d locks | lock pairs cutting %d (tri pairs %d), top sheets %d / %d | paint patches / lock %.2f | "
                 "top-facet kinks p50 / p90 %.1f / %.1f deg" % (HDIAG["locks"], ip["lock_pairs"], ip["tri_pairs"],
                                                                ip["top_sheets"]["lock_pairs"], ip["top_sheets"]["tri_pairs"],
                                                                HDIAG["paint"]["patches_per_lock"], HDIAG["facet_kinks_top_deg"]["p50"],
                                                                HDIAG["facet_kinks_top_deg"]["p90"]))
if PROBE:
    ue = PROBE.get("undereye", {})
    try:
        lines.append("face probe (under-eye, max): flatness L %.2f / R %.2f mm, lid line L %.2f / R %.2f mm (Wren v6.1: 0.55 / 0.47)"
                     % (ue["L"]["flatness_mm"]["max"], ue["R"]["flatness_mm"]["max"], ue["L"]["lid_line_mm"]["max"],
                        ue["R"]["lid_line_mm"]["max"]))
    except Exception:
        lines.append("face probe: " + ", ".join(sorted(PROBE.keys()))[:160])
for k, ln in enumerate(lines):
    d.text((PAD + 4, y + 6 + 26 * k), ln, fill=(220, 220, 210), font=FONT_S)
out = os.path.join(R, V + "_sheet.png")
sheet.save(out)
print("SHEET", out, sheet.size)
# ---- v2: the ONE before / after strip (BASE | this version): front + three-quarter full body + the frame numbers
BASE = os.environ.get("VARDEN_BASE", "")
if BASE and BASE != V:
    S = 560
    # v4: VARDEN_STRIP picks the strip's views ("fix:<view>" = the fixed full-body frame; else the close-up of that name)
    LABS = {"headc_front": "front", "headc_tq": "3/4 (his right)", "headc_side": "side (his left)", "headc_back": "back", "front": "front", "threequarter": "three-quarter", "portrait": "portrait", "face_tq": "face 3/4",
            "portrait_low": "chin-up"}
    pairs = [(v_, LABS.get(v_.split(":")[-1], v_)) for v_ in os.environ.get("VARDEN_STRIP", "fix:front,fix:threequarter").split(",")]
    if os.environ.get("VARDEN_ONETONE"):              # v5: + the one-tone hair column (every hair region one tone: shading only)
        pairs.append((os.environ["VARDEN_ONETONE"], "hair one-tone"))
    # v5: + the SHEET's own view beside each head pair (the judge: design/reference/varden/varden_sheet.webp crops, 1536 x 1024)
    REF = {"headc_front": (150, 85, 290, 225), "headc_tq": (1073, 53, 1295, 275), "headc_side": (480, 85, 620, 225),
           "headc_back": (820, 90, 960, 230)}
    ncol = [3 if v_.split(":")[-1] in REF else (1 if v_.startswith("onetone") else 2) for v_, _ in pairs]
    st = Image.new("RGB", (PAD + sum(c_ * (S + PAD) + PAD for c_ in ncol), PAD + LAB + S + PAD + 30), (28, 28, 30))
    ds = ImageDraw.Draw(st)
    x0 = PAD
    for k, (v, lab) in enumerate(pairs):
        if v.startswith("onetone"):
            p = os.path.join(R, "%s_%s.png" % (V, v))
            im = Image.open(p).convert("RGB").resize((S, S), Image.LANCZOS) if os.path.exists(p) else Image.new("RGB", (S, S), (60, 30, 30))
            st.paste(im, (x0, PAD + LAB)); ds.text((x0 + 4, PAD + 2), "%s  %s" % (lab, V.split("_")[-1]), fill=(235, 235, 235), font=FONT)
            x0 += S + 2 * PAD
            continue
        if v.split(":")[-1] in REF:
            im = ref.crop(REF[v.split(":")[-1]]).resize((S, S), Image.LANCZOS)
            st.paste(im, (x0 + 2 * (S + PAD), PAD + LAB))
            ds.text((x0 + 2 * (S + PAD) + 4, PAD + 2), "%s  SHEET" % lab, fill=(235, 235, 235), font=FONT)
        for j, ver in enumerate((BASE, V)):
            p = os.path.join(R, ("%sfix_%s.png" if v.startswith("fix:") else "%s_%s.png") % (ver, v.split(":")[-1]))   # (fix: the
            #   SAME fixed frame for both, --fixed; close-ups frame on the same face landmarks both sides)
            im = Image.open(p).convert("RGB").resize((S, S), Image.LANCZOS) if os.path.exists(p) else Image.new("RGB", (S, S), (60, 30, 30))
            st.paste(im, (x0 + j * (S + PAD), PAD + LAB))
            ds.text((x0 + j * (S + PAD) + 4, PAD + 2), "%s  %s" % (lab, ver.split("_")[-1]), fill=(235, 235, 235), font=FONT)
        x0 += ncol[k] * (S + PAD) + PAD
    fr = IMP.get("frame", {})
    ms_ = IMP.get("hair", {}).get("masses")
    if ms_ and os.environ.get("VARDEN_ONETONE"):      # v5: the scalp numbers under the head strip
        ds.text((PAD + 4, PAD + LAB + S + 6), "same head frame both sides (HC / HR box).  %s scalp: %d masses / %d locks, width spread per mass %s (all %.2f), grey locks %d; scalp locks %d tris, total %d" % (
            V.split("_")[-1], ms_["_all"]["masses"], ms_["_all"]["locks"], ", ".join("%s %.2f" % (k_, v_["width_spread"]) for k_, v_ in ms_.items() if k_ != "_all"),
            ms_["_all"]["width_spread"], ms_["_all"]["grey_locks"], IMP["tris"]["scalp_locks"], IMP["tris"]["total"]), fill=(220, 220, 210), font=FONT_S)
    else:
      ds.text((PAD + 4, PAD + LAB + S + 6), "same camera / frame both sides.  %s: height %.3f m, heads %.2f, shoulder joint span %.3f m, outer shoulder width %.3f m" % (
        V.split("_")[-1], IMP["landmarks"]["height_total"], IMP["landmarks"]["heads_tall"], fr.get("shoulder_joint_span_m", 0),
        fr.get("shoulder_outer_width_m", 0)), fill=(220, 220, 210), font=FONT_S)
    outs = os.path.join(R, V + "_compare.png")
    st.save(outs)
    print("STRIP", outs, st.size)
