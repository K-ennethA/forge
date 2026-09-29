"""Wren image assembly (plain Python + PIL, no Blender): run with the forge service venv.
v7 (review-log 2026-09-29 "Wren v7 hair feedback": the ribbon locks): every model input / output is v7-prefixed (V = "wren_v7");
the v6 stills (wren_v6_*.png) + the v6 diagnosis renders / metrics (wren_v6_diag_*, wren_v6_hairdiag.json, wren_v6_ribbon.json,
made once off the committed v6 rig) are the baseline; the v7 composites: wren_v7_hair_compare (v6 | v7, five views),
_hair_vs_ref (the v7 close-up | the FE archer + Byleth figures), _diagnosis (lock-id renders + metrics, v6 | v7),
_portrait_compare (v6 | v7: the face untouched, + the probe equality lines FACEPROBE / MPROBE_SAME), _hair, _winter, _contact.
The v6-round composites (v5.1 | v6) are not rebuilt.
v6 (review-log 2026-09-29 "Wren v6 mouth feedback" + "addendum"): every model input / output is v6-prefixed (V = "wren_v6");
the v5 stills (wren_v5_*.png) + the v5.1 mouth probe (wren_v5_mprobe.*, run once off the committed v5.1 rig) are the
comparison baseline; the v6 composites: wren_v6_mouth_compare (v5 | v6 close-ups + the probe's orthographic renders with the
line repainted skin: the second feature), wren_v6_face_vs_refs (v6 portrait | Ashe portrait | Alicia front, the placement
ratios measured on each), _portrait_compare, _undereye_untouched + _body_untouched (v5 | v6), _contact. The v5-round
composites (v4 | v5) are not rebuilt.
v5 (review-log 2026-09-29 "Wren v5 feedback + FE reference set"): every model input / output is v5-prefixed (V = "wren_v5");
the v4 stills (wren_v4_*.png, + wren_v4_undereye / _undereye_tq rendered once off the committed v4 rig) are the comparison
baseline; + the v5 composites wren_v5_undereye_compare (v4 | v5), _mouth_compare (v4 | v5 | the FE mouths), _face_vs_fe
(v5 portrait | Ashe portrait | Alear art crop), _relief_maps (the face probe's mouth relief + under-eye flatness maps,
v4 | v5), _body_untouched (v4 | v5), _contact. The v4-round composites (v3 | v4) are not rebuilt.
v4 (review-log 2026-09-29 "Wren v4 feedback"): every model input / output is v4-prefixed (V = "wren_v4"); the v3 stills
(wren_v3_*.png) are the comparison baseline; + the v4 composites wren_v4_eyes_compare (v3 | v4 iris fit), _face_threeup
(v3 | v4 | sheet), _nose_side_compare (the orbit-blend cleanup), _mouth_compare (the 2D line), _hair (four sides + top),
_hair_layers (the layer shadows, v3 | v4), _winter, _body_untouched, _contact.
v3 (the Fire Emblem round): every model input / output is v3-prefixed (V = "wren_v3"); the v2 stills (wren_v2_*.png) are
the comparison baseline; + the FE-round composites wren_v3_face_threeup (v2 | v3 | sheet), _eyes_compare, _hair (front /
side / back / three-quarter back + the close-up), _hair_shading_ab (the hair strip flat = v2 shading | the smooth-proxy
bake), _winter (the hair tiers repainted through the winter skin), _body_untouched.

    python -P wren_compose.py

renders/wren/ (inputs: wren_<view>.png stills, wren_winter_<view>.png, wren_idle_sheet.png, wren_walk_sheet.png):
  wren_sheet_vs_model.png   the sheet's front / side / back figures | the model's orthographic front / side / back (idle f1)
  wren_face_vs_sheet.png    the sheet's head panel | portrait | three-quarter face | eyes close-up, + the skin numbers
  wren_details_vs_sheet.png the sheet's necklace / bracer / cloak + patch / staff panels | the matching close-ups
  wren_hair.png             hair from the front, side, three-quarter back, back
  wren_contact.png          the main stills, close-ups, the winter skin + the idle / walk 8-frame sheets
  wren_skin_tone.json       sampled lit skin: the sheet vs the render
(v2: all of the above as wren_v2_*)
"""
import colorsys
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "wren")
SHEET = os.path.join(ROOT, "design", "reference", "wren-character-sheet.webp")
LAB = (235, 235, 235, 255)
IMP = json.load(open(os.path.join(ROOT, "improved", "wren.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "wren.json")))
PAL = json.load(open(os.path.join(ROOT, "palettes", "wren", "default.json")))
SH = Image.open(SHEET).convert("RGB")
V = "wren_v7"
V6 = "wren_v6"
V5 = "wren_v5"
V4 = "wren_v4"
V3 = "wren_v3"
V2 = "wren_v2"
VL = V.replace("wren_", "")          # the round label in titles
FE = os.path.join(ROOT, "design", "reference", "fe-style")
CROPS = {"eyes": (1178, 152, 1292, 200), "mouth": (1200, 208, 1262, 244), "brows": (1172, 140, 1298, 196),
         "front": (20, 90, 470, 970), "side": (470, 90, 710, 970), "back": (730, 90, 1030, 970),
         "head": (1116, 90, 1318, 322), "necklace": (1344, 98, 1497, 282), "bracer": (1118, 357, 1287, 560),
         "cloak": (1318, 335, 1500, 528), "patch": (1320, 568, 1500, 744), "staff": (1120, 598, 1290, 840),
         "chips": (1300, 790, 1480, 975)}


def font(sz):
    for f in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def label(im, text, xy=(8, 6), sz=16):
    d = ImageDraw.Draw(im)
    f = font(sz)
    y = xy[1]
    for line in text.split("\n"):
        w = d.textlength(line, font=f)
        d.rectangle([xy[0] - 4, y - 3, xy[0] + w + 6, y + sz + 4], fill=(0, 0, 0, 185))
        d.text((xy[0], y), line, fill=LAB, font=f)
        y += sz + 8
    return im


def load(fn):
    if isinstance(fn, Image.Image):
        return fn.convert("RGBA")
    return Image.open(fn if os.path.isabs(fn) else os.path.join(R, fn)).convert("RGBA")


def fit(im, W, H):
    k = min(W / im.width, H / im.height)
    im2 = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    t = Image.new("RGBA", (W, H), (28, 28, 30, 255))
    t.paste(im2, ((W - im2.width) // 2, (H - im2.height) // 2), im2)
    return t


def grid(rows, W, H, title, out, head_h=46):
    nc = max(len(r) for r in rows)
    cs = Image.new("RGBA", (nc * (W + 6), len(rows) * (H + 6) + head_h), (28, 28, 30, 255))
    d = ImageDraw.Draw(cs)
    d.text((10, 10), title, fill=(255, 255, 255, 255), font=font(22))
    for i, r in enumerate(rows):
        for j, (src, lab) in enumerate(r):
            if src is None:
                continue
            t = fit(load(src), W, H)
            if lab:
                label(t, lab)
            cs.paste(t, (j * (W + 6), head_h + i * (H + 6)))
    cs.convert("RGB").save(os.path.join(R, out))
    print("WROTE", out)


def crop(k):
    return SH.crop(CROPS[k])


def lit_skin(img, box):
    """median of the lit warm-skin pixels in box (hue 12-40 deg, saturation 0.25-0.65, value > 0.45)."""
    a = np.array(img.convert("RGB")).astype(float)[box[1]:box[3], box[0]:box[2]].reshape(-1, 3)
    hsv = np.array([colorsys.rgb_to_hsv(*(p / 255.0)) for p in a])
    m = (hsv[:, 0] * 360 > 12) & (hsv[:, 0] * 360 < 40) & (hsv[:, 1] > 0.25) & (hsv[:, 1] < 0.65) & (hsv[:, 2] > 0.45)
    med = np.median(a[m], 0) if m.any() else np.zeros(3)
    h, s, v = colorsys.rgb_to_hsv(*(med / 255.0))
    return {"rgb": med.astype(int).tolist(), "hue_deg": round(h * 360, 1), "sat": round(s, 3), "val": round(v, 3), "pixels": int(m.sum())}


# ---- sheet vs model
grid([[(crop("front"), "SHEET front"), (V + "_ortho_front.png", "model, orthographic front (idle f1)"),
       (crop("side"), "SHEET side"), (V + "_ortho_side.png", "model, orthographic side"),
       (crop("back"), "SHEET back"), (V + "_ortho_back.png", "model, orthographic back")]],
     300, 580, "Wren - the sheet vs the model (front / side / back)", V + "_sheet_vs_model.png")

# ---- face + skin numbers
sk_sheet_front = lit_skin(SH, (228, 190, 262, 232))
sk_sheet_head = lit_skin(SH, (1190, 200, 1300, 300))
por = load(V + "_portrait.png")
W_, H_ = por.size
sk_render = lit_skin(por, (int(W_ * 0.28), int(H_ * 0.45), int(W_ * 0.72), int(H_ * 0.75)))
SKIN = {"palette_skin_rgb": PAL["regions"]["skin"]["rgb"], "sheet_front_face_lit": sk_sheet_front,
        "sheet_head_panel_lit": sk_sheet_head, "render_portrait_lit": sk_render,
        "rule": "median of lit warm-skin pixels (hue 12-40 deg, sat 0.25-0.65, value > 0.45) in a face box"}
json.dump(SKIN, open(os.path.join(R, V + "_skin_tone.json"), "w"), indent=1)
grid([[(crop("head"), "SHEET head panel\nlit skin %s (hue %.0f, sat %.2f)" % (sk_sheet_head["rgb"], sk_sheet_head["hue_deg"], sk_sheet_head["sat"])),
       (V + "_portrait.png", "model portrait\nlit skin %s (hue %.0f, sat %.2f)" % (sk_render["rgb"], sk_render["hue_deg"], sk_render["sat"])),
       (V + "_face.png", "three-quarter"), (V + "_eyes.png", "eyes: lash band, iris shade, highlight")]],
     420, 460, "Wren - face vs the sheet (palette skin %s, sampled off the sheet's lit face)" % (PAL["regions"]["skin"]["rgb"],),
     V + "_face_vs_sheet.png")

# ---- details
grid([[(crop("necklace"), "SHEET necklace"), (V + "_necklace.png", "pendant + cord + clasp"),
       (crop("bracer"), "SHEET bracer"), (V + "_bracer.png", "bracer (left forearm), teal diamond")],
      [(crop("cloak"), "SHEET cloak + brass boss"), (V + "_cloak.png", "cloak from behind: hood, patches, ragged hem"),
       (crop("patch"), "SHEET cloth patch"), (V + "_patches.png", "patches + cross stitches")],
      [(crop("staff"), "SHEET staff"), (V + "_fork_head.png", "pitchfork head (rest pose): tines, brass collar, wrap"),
       (crop("chips"), "SHEET palette chips"), (V + "_fork_full.png", "pitchfork, full (idle f1, planted)")]],
     360, 360, "Wren - detail panels vs the model", V + "_details_vs_sheet.png")

# ---- hair (v7: the ribbon locks)
HI = IMP["hair"]
TK = HI.get("tuck_shade", {})
RB = HI.get("ribbon", {})
LR = HI.get("layer_resolve", {})
grid([[(V + "_head_front.png", "front"), (V + "_head_side.png", "side (his left: the part side)"),
       (V + "_head_tq_back.png", "three-quarter back"), (V + "_head_back.png", "back: nape tail + pointed ribbons")],
      [(V + "_hair_close.png", "close-up: ribbons, tuck shade, angel ring"), (V + "_head_top.png", "from above: the side part"),
       (V + "_hair_part.png", "over the part (his left, above)"), (V + "_hair_sweep.png", "the sweep side (his right)")]],
     420, 420, "Wren " + VL + " - hair: %d ribbon locks (%s-%s sections), side part on his left; tuck shade on %s segments; "
     "crown %.1f mm above the scalp" % (HI["clumps"], RB.get("stations_per_lock_min_mean_max", ["?"] * 3)[0],
                                        RB.get("stations_per_lock_min_mean_max", ["?"] * 3)[2], TK.get("segments"),
                                        HI["crown"]["crown_above_scalp_mm"]), V + "_hair.png")
grid([[(V + "_winter_front.png", "winter front"), (V + "_winter_threequarter.png", "winter three-quarter"),
       (V + "_winter_back.png", "winter back")],
      [(V + "_winter_portrait.png", "winter portrait"), (V + "_winter_hair_close.png", "winter: hair tiers + tuck shade"),
       (V + "_winter_head_tq_back.png", "winter: hair three-quarter back")]],
     420, 460, "Wren " + VL + " - the winter skin (a pure palette swap: the hair tiers, layer shadows + eye regions repaint through it)",
     V + "_winter.png")

# ---- contact
T = 400
items = [(V + "_front.png", "front (idle f1)"), (V + "_threequarter.png", "three-quarter"), (V + "_side.png", "side"),
         (V + "_back.png", "back"), (V + "_tactical.png", "tactical (256 px)"), (V + "_portrait.png", "face"),
         (V + "_undereye.png", "under the eyes: flat skin, the liner only"), (V + "_hair_close.png", "hair: ribbon locks (v7)"),
         (V + "_mouth.png", "mouth: one line (v6, untouched)"), (V + "_head_top.png", "hair from above: side part"),
         (V + "_winter_front.png", "winter skin (swap proof)"), (V + "_winter_hair_close.png", "winter: hair tiers")]
tiles = [label(fit(load(fn), T, T), lab) for fn, lab in items]
Wc = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (leaning on the planted fork, weight shift, cloak / fringe follow-through)"),
                  ("walk", "walk: 8 frames / %.2f s, %.0f steps/min (bounce, the fork carried, cloak / tail / ties lag)"
                   % (RIG["clips"]["walk"]["seconds"], RIG["clips"]["walk"]["cadence_steps_per_min"]))):
    s_ = load(V + "_%s_sheet.png" % clip)
    s_ = s_.resize((Wc, int(s_.height * Wc / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (Wc, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, V + "_contact.png"))
print("WROTE", V + "_contact.png", json.dumps(SKIN))

# ---- v7 round: v6 | v7 (| the figure references)
DG = {v: json.load(open(os.path.join(R, v + "_hairdiag.json"))) for v in (V6, V)}
RB6 = json.load(open(os.path.join(R, V6 + "_ribbon.json")))


def dg_lab(v, rb):
    d = DG[v]
    ip = d["interpenetration"]
    return ("locks cutting through each other: %d pairs / %d triangle pairs (top sheets %d)\npaint patches %.1f per lock, "
            "top-facet kinks p50 %.0f / p90 %.0f deg\nspine turn p90 %.0f deg, silhouette-edge turn p90 %.0f deg (%d%% > 20)"
            % (ip["lock_pairs"], ip["tri_pairs"], ip.get("top_sheets", {}).get("tri_pairs", -1), d["paint"]["patches_per_lock"],
               d["facet_kinks_top_deg"]["p50"], d["facet_kinks_top_deg"]["p90"], rb["spine_turn_deg_p50_p90_max"][1],
               rb["edge_turn_deg_p50_p90_max"][1], round(100.0 * rb["edge_turns_over_20deg"] / max(rb["edge_points"], 1))))


grid([[(V6 + "_hair_close.png", "v6 close-up"), (V + "_hair_close.png", "v7 close-up")],
      [(V6 + "_head_front.png", "v6 front"), (V + "_head_front.png", "v7 front")],
      [(V6 + "_head_side.png", "v6 side (his left)"), (V + "_head_side.png", "v7 side")],
      [(V6 + "_head_back.png", "v6 back"), (V + "_head_back.png", "v7 back")],
      [(V6 + "_head_top.png", "v6 from above"), (V + "_head_top.png", "v7 from above")]],
     640, 640, "Wren v7 - hair: v6 clumps (chopped) | v7 ribbon locks (same cameras, same light, idle f1)", V + "_hair_compare.png")
ARCH = Image.open(os.path.join(FE, "fe-archer-figure-hair.webp")).convert("RGBA")
BYL = Image.open(os.path.join(FE, "fe-byleth-figure-hair.png")).convert("RGBA")
grid([[(V + "_large_hair_close.png", "Wren v7 (1600 px close-up)\n" + dg_lab(V, RB)),
       (ARCH.crop((200, 0, 760, 560)), "reference: the silver-haired archer figure (the bar)"),
       (BYL, "reference: the Byleth figure")]],
     700, 700, "Wren v7 - hair vs the figure references: one smooth ribbon per lock, crisp edge, sharp tip, feathered layers",
     V + "_hair_vs_ref.png")
grid([[(V6 + "_diag_lockids_hair_close.png", "v6, every clump its own hue\n" + dg_lab(V6, RB6)),
       (V + "_lockids_hair_close.png", "v7, every ribbon its own hue\n" + dg_lab(V, RB))],
      [(V6 + "_diag_lockids_head_top.png", "v6 from above: the fringe / sweep patchwork (clumps cutting through clumps)"),
       (V + "_lockids_head_top.png", "v7 from above: one layer order, ribbons over / under each other")],
      [(V6 + "_diag_flatnormal_head_side.png", "v6 facets (hair normal strip flat): 12 lens sections per clump"),
       (V + "_large_head_side.png", "v7 side (1600 px): %s-%s sections per ribbon, whole-segment paint"
        % (RB.get("stations_per_lock_min_mean_max", ["?"] * 3)[0], RB.get("stations_per_lock_min_mean_max", ["?"] * 3)[2]))]],
     700, 700, "Wren v7 - the chopped-read diagnosis: v6 | v7 (resolve: %s -> %s top-sheet triangle pairs in the build)"
     % (LR.get("before", {}).get("top", {}).get("tri_pairs"), LR.get("after", {}).get("top", {}).get("tri_pairs")),
     V + "_diagnosis.png")
# the face untouched: the probes on both delivered meshes
FP = {v: json.load(open(os.path.join(R, v + "_face_probe.json"))) for v in (V6, V)}
MP = {v: json.load(open(os.path.join(R, v + "_mprobe.json"))) for v in (V6, V)}
_fp_same = all(FP[V6][k] == FP[V][k] for k in ("undereye", "mouth"))
print("FACEPROBE identical=%s (undereye + mouth sections, byte-equal json values)" % _fp_same)
print("MPROBE_SAME geometry=%s line=%s features=%s" % (MP[V6].get("geometry") == MP[V].get("geometry"),
                                                       MP[V6].get("line") == MP[V].get("line"),
                                                       MP[V6].get("features") == MP[V].get("features")))
grid([[(V6 + "_portrait.png", "v6 portrait"), (V + "_portrait.png", "v7 portrait")],
      [(V6 + "_front.png", "v6 front (idle f1)"), (V + "_front.png", "v7 front (idle f1)")]],
     620, 620, "Wren v7 - portrait + body (v6 | v7): face probe identical=%s, mouth probe geometry identical=%s"
     % (_fp_same, MP[V6].get("geometry") == MP[V].get("geometry")), V + "_portrait_compare.png")
