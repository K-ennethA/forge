"""Lyra palette sampler (plain Python + PIL + numpy; the forge service venv):

    python -P improve/lyra_palette.py          -> palettes/lyra/default.json

Every region tone is PIXEL-SAMPLED from the saved sheet design/reference/lyra/lyra_sheet.webp (1536 x 1024; style-guide
sheet law: never eyeballed). Rule per region (the Elias v5 / shadow-assassin precedent): a pixel BOX on one of the sheet's
views / detail panels, an HSV MASK (keeps only the material's pixels: background, gold trim, other materials excluded as
named), then the median RGB of the pixels inside a LUMINANCE-PERCENTILE BAND of the masked set. The note on each region
records box, mask, band and pixel count. The sheet's 8 palette chips (13 x 13 medians at y = 940) are recorded. Values that
cannot be read off the sheet are marked 'derived' with their rule. Deterministic: same sheet -> same file.
No region emits (L3 default: no magic on the sheet -- _GLOW ships all zero, the Varden pattern).
"""
import json
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SHEET = os.path.join(ROOT, "design", "reference", "lyra", "lyra_sheet.webp")
OUT = os.path.join(ROOT, "palettes", "lyra", "default.json")
# ---- artist-facing knobs
CHIP_Y, CHIP_X = 940, (1047, 1100, 1153, 1205, 1258, 1311, 1363, 1416)   # the 8 palette chips (centres, measured runs)
ROUGHNESS = 0.66

IM = np.asarray(Image.open(SHEET).convert("RGB")).astype(float)
assert IM.shape[:2] == (1024, 1536), IM.shape


def hsv(a):
    a = a / 255.0
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    nz = d > 1e-6
    dd = np.where(nz, d, 1.0)
    h = np.where(mx == r, ((g - b) / dd) % 6.0, np.where(mx == g, (b - r) / dd + 2.0, (r - g) / dd + 4.0)) * 60.0
    h = np.where(nz, h, 0.0)
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1.0), 0.0)
    return h, s, mx


def lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


MASKS = {   # name -> fn(h, s, v) -> bool; described in the notes verbatim
    "skin": (lambda h, s, v: (h > 8) & (h < 38) & (s > 0.25) & (s < 0.70) & (v > 0.40),
             "skin: h 8-38, s 0.25-0.70, v > 0.40"),
    "hair": (lambda h, s, v: ((h < 40) | (h > 340)) & (v < 0.60) & (s > 0.15) & ~((h > 200) & (h < 260)),
             "brown hair: h < 40 or > 340, v < 0.60, s > 0.15 (blue ribbon excluded)"),
    "dark": (lambda h, s, v: (v < 0.38) & ~((h > 195) & (h < 265) & (s > 0.18)),
             "dark: v < 0.38, not navy-blue (h 195-265 & s > 0.18)"),
    "blue": (lambda h, s, v: (h > 195) & (h < 260) & (s > 0.18) & (v > 0.18),
             "blue / navy: h 195-260, s > 0.18, v > 0.18"),
    "navy": (lambda h, s, v: (h > 195) & (h < 265) & (s > 0.12) & (v < 0.50),
             "navy cloth: h 195-265, s > 0.12, v < 0.50 (gold trim / emblem excluded by hue)"),
    "gold": (lambda h, s, v: (h > 22) & (h < 52) & (s > 0.35) & (v > 0.45),
             "gold: h 22-52, s > 0.35, v > 0.45"),
    "leather": (lambda h, s, v: ((h < 42) | (h > 345)) & (s > 0.18) & (s < 0.75) & (v > 0.08) & (v < 0.45),
                "brown leather: h < 42 or > 345, s 0.18-0.75, v 0.08-0.45 (gold highlights v > 0.45 excluded)"),
    "cream": (lambda h, s, v: (s < 0.32) & (v > 0.50) & ((h < 60) | (s < 0.10)),
              "cream / white cloth: s < 0.32, v > 0.50, warm hue (h < 60) or near-grey (s < 0.10)"),
    "lash": (lambda h, s, v: (v < 0.32), "the darkest strokes: v < 0.32"),
    "brow": (lambda h, s, v: (v < 0.50) & ((h < 40) | (h > 340)), "brow strokes: v < 0.50, warm hue"),
    "any": (lambda h, s, v: np.ones_like(v, bool), "all pixels"),
}


def sample(boxes, mask, band, view):
    px = []
    for (x0, y0, x1, y1) in boxes:
        a = IM[y0:y1, x0:x1]
        h, s, v = hsv(a)
        m = MASKS[mask][0](h, s, v)
        px.append(a[m])
    px = np.vstack(px)
    assert len(px) >= 12, (boxes, mask, len(px))
    L = lum(px)
    lo, hi = np.percentile(L, band[0]), np.percentile(L, band[1])
    sel = px[(L >= lo) & (L <= hi)]
    rgb = [int(round(c)) for c in np.median(sel, 0)]
    note = "SAMPLED #%02x%02x%02x: %s, box%s %s, mask [%s], luminance %d-%dth pct band median (%d of %d px)" % (
        rgb[0], rgb[1], rgb[2], view, "es" if len(boxes) > 1 else "", "; ".join("x%d-%d y%d-%d" % (b[0], b[2], b[1], b[3]) for b in boxes),
        MASKS[mask][1], band[0], band[1], len(sel), len(px))
    return rgb, note


HEAD = (1150, 135, 1205, 188)          # head-detail panel: the lit cheek between the eye and the jaw
# region -> (boxes, mask, band, view label)
SPEC = {
    "skin":          ([(200, 165, 232, 200)], "skin", (60, 90), "FRONT view, the lit face (cheeks / forehead / nose; the head panel sits under a darker panel overlay: #a07359 there)"),
    "skin_shadow":   ([HEAD, (1130, 185, 1175, 215)], "skin", (8, 22), "HEAD DETAIL panel cheek + jaw / neck, the shadow side (authored jaw shadow)"),
    "lips":          ([(1186, 172, 1203, 183)], "skin", (40, 60), "HEAD DETAIL panel, the lips (the sheet's lip tint, a touch deeper than skin)"),
    "brow":          ([(1160, 98, 1210, 112)], "brow", (10, 40), "HEAD DETAIL panel, the near brow"),
    "lash":          ([(1158, 112, 1202, 124), (1200, 122, 1225, 132)], "lash", (5, 40), "HEAD DETAIL panel, the upper lash lines of both eyes"),
    "eye_iris":      ([(1166, 117, 1184, 131)], "any", (35, 60), "HEAD DETAIL panel, the near iris"),
    "eye_iris_dark": ([(1166, 117, 1184, 131)], "any", (5, 20), "HEAD DETAIL panel, the near iris' upper shade"),
    "hair":          ([(1105, 40, 1185, 95), (1020, 45, 1060, 70)], "hair", (40, 62), "HEAD DETAIL panel, the swept-back top + ponytail"),
    "hair_shade":    ([(1105, 40, 1185, 95), (1020, 45, 1060, 70)], "hair", (12, 28), "HEAD DETAIL panel hair, the strand shadows"),
    "hair_inner":    ([(1105, 40, 1185, 95), (1020, 45, 1060, 70)], "hair", (2, 10), "HEAD DETAIL panel hair, the darkest gaps (the dark inner cap)"),
    "hair_root":     ([(1105, 40, 1185, 95), (1020, 45, 1060, 70)], "hair", (22, 40), "HEAD DETAIL panel hair, the shadowed roots band"),
    "hair_tip":      ([(1105, 40, 1185, 95), (500, 95, 600, 200)], "hair", (72, 88), "HEAD DETAIL panel + SIDE view ponytail, the lit strand ends"),
    "hair_ring":     ([(1105, 40, 1185, 95), (500, 95, 600, 200)], "hair", (90, 98), "HEAD DETAIL panel + SIDE view ponytail, the brightest highlight strokes (angel ring)"),
    "ribbon":        ([(1035, 60, 1085, 110), (1030, 170, 1060, 210)], "blue", (40, 62), "HEAD DETAIL panel, the blue hair ribbon"),
    "ribbon_shade":  ([(1035, 60, 1085, 110), (1030, 170, 1060, 210)], "blue", (8, 25), "HEAD DETAIL panel, the ribbon folds"),
    "ribbon_gold":   ([(1052, 120, 1080, 185)], "gold", (45, 70), "HEAD DETAIL panel, the gold ribbon tails"),
    "gold":          ([(1093, 20, 1128, 50), (785, 262, 880, 302)], "gold", (65, 90), "HEAD DETAIL hairpiece + BACK view capelet emblem, the lit gold"),
    "gold_dark":     ([(1093, 20, 1128, 50), (785, 262, 880, 302)], "gold", (8, 28), "HEAD DETAIL hairpiece + BACK view emblem, the shaded gold"),
    "capelet":       ([(700, 250, 780, 322), (860, 300, 895, 335)], "navy", (40, 62), "BACK view, the navy capelet"),
    "capelet_lining": ([(700, 250, 780, 322), (860, 300, 895, 335)], "navy", (6, 20), "BACK view capelet, the fold shadows (lining / underside)"),
    "shirt":         ([(205, 300, 235, 352), (690, 352, 735, 402)], "cream", (72, 92), "FRONT view shirt front + BACK view her right sleeve"),
    "shirt_shade":   ([(205, 300, 235, 352), (690, 352, 735, 402)], "cream", (25, 45), "FRONT shirt + BACK sleeve, the fold shadows"),
    "tie":           ([(183, 250, 207, 290)], "blue", (40, 62), "FRONT view, the blue necktie ribbon"),
    "tie_shade":     ([(183, 250, 207, 290)], "blue", (8, 25), "FRONT view necktie, the fold shadows"),
    "corset":        ([(175, 374, 245, 396), (755, 394, 805, 412)], "dark", (40, 62), "FRONT + BACK views, the dark corset band"),
    "belt":          ([(1028, 470, 1192, 625)], "leather", (40, 62), "BELT DETAIL panel, the brown belts"),
    "belt_edge":     ([(1028, 470, 1192, 625)], "leather", (8, 25), "BELT DETAIL panel, the belts' dark edges"),
    "strap":         ([(1028, 470, 1192, 625), (1215, 470, 1365, 625)], "leather", (28, 48), "BELT + BAG DETAIL panels, the strap leather (a step darker than the belts)"),
    "satchel":       ([(870, 462, 940, 545), (1240, 500, 1340, 600)], "leather", (40, 62), "BACK view + BAG DETAIL panel, the satchel"),
    "satchel_flap":  ([(870, 462, 940, 545), (1240, 500, 1340, 600)], "leather", (60, 80), "BACK view + BAG DETAIL panel, the satchel's lit flap"),
    "trousers":      ([(180, 560, 222, 640), (245, 560, 275, 640)], "dark", (40, 62), "FRONT view, the black trousers between the panels"),
    "skirt":         ([(745, 450, 812, 640)], "cream", (70, 90), "BACK view, the cream skirt panel (its lit band: the sheet's cream chip is lighter than the mid band)"),
    "skirt_shade":   ([(745, 450, 812, 640)], "cream", (20, 40), "BACK view cream panel, the fold shadows (inner side)"),
    "skirt_navy":    ([(822, 430, 846, 600), (272, 600, 300, 690)], "navy", (40, 62), "BACK view centre strip + FRONT view her left panel, the navy skirt panels"),
    "boot":          ([(90, 820, 132, 885), (240, 820, 282, 885)], "leather", (40, 62), "FRONT view, both boot shafts"),
    "boot_strap":    ([(90, 820, 132, 885), (240, 820, 282, 885)], "leather", (12, 28), "FRONT view boots, the strap / lace shadow band"),
    "boot_sole":     ([(95, 903, 140, 915), (245, 903, 295, 915)], "any", (3, 18), "FRONT view, the boot soles"),
    "book_cover":    ([(76, 300, 140, 352)], "leather", (35, 60), "FRONT view, the front book's cover"),
    "pages":         ([(142, 302, 165, 380)], "cream", (60, 85), "FRONT view, the books' page edges"),
    "scroll_paper":  ([(610, 435, 640, 470)], "cream", (45, 70), "SIDE view, the rolled scroll at her hip"),
}
DERIVED = {    # name -> (source, factor, rule)
    "mouth": ("skin", 0.52, "the one mouth line = skin x 0.52 (the house mouth / skin ratio, Wren / Elias)"),
    "liner": ("lash", 1.35, "thin lower lid line = lash x 1.35 (lighter than the upper lash band)"),
    "eye_pupil": ("eye_iris_dark", 0.55, "pupil = the iris shade x 0.55"),
    "hair_crevice": ("hair_shade", 0.85, "tuck shade = hair_shade x 0.85"),
    "shirt_roll": ("shirt", 0.94, "rolled sleeve cuff = shirt x 0.94 (a fold reads a step down)"),
    "collar": ("shirt", 1.02, "collar = shirt x 1.02 (the lit collar band)"),
    "corset_lace": ("belt_edge", 1.0, "corset lacing = belt_edge"),
    "boot_cuff": ("boot", 0.90, "boot cuff fold = boot x 0.90"),
    "book_cover2": ("book_cover", 0.78, "middle book = book_cover x 0.78 (the sheet's stack reads in two browns)"),
    "book_spine": ("book_cover", 0.70, "book spines = book_cover x 0.70"),
    "pouch": ("satchel", 0.92, "belt pouch = satchel x 0.92"),
    "pouch_flap": ("satchel_flap", 0.95, "pouch flap = satchel_flap x 0.95"),
    "scroll_tube": ("strap", 1.0, "scroll case leather = strap"),
    "scroll_cap": ("gold_dark", 1.0, "scroll case caps = gold_dark"),
    "scroll_end": ("scroll_paper", 0.80, "scroll ends = scroll_paper x 0.80"),
    "bracelet": ("belt_edge", 1.0, "wrist bracelets = belt_edge (the dark bands on both wrists)"),
    "cork": ("belt", 1.15, "vial corks = belt x 1.15"),
    "tassel": ("gold", 0.92, "tassels = gold x 0.92"),
    "skirt_band": ("skirt", 0.86, "the patterned hem band of the cream panels = skirt x 0.86 (its gold pattern is the gold motifs)"),
    "skirt_navy_inner": ("skirt_navy", 0.72, "navy panel inner side = skirt_navy x 0.72"),
    "eye_sclera": (None, None, "sclera = warm off-white [236, 228, 218] (the sheet's whites read ~ this on the head panel; too small to sample cleanly)"),
    "eye_hilite": (None, None, "eye highlight = [250, 248, 244] (house)"),
    "glass": (None, None, "vial glass = [168, 196, 196] pale teal (the BAG DETAIL vials; a 4 px read, so derived)"),
}
FIXED = {"eye_sclera": [236, 228, 218], "eye_hilite": [250, 248, 244], "glass": [168, 196, 196]}


def main():
    chips = {}
    for x in CHIP_X:
        p = IM[CHIP_Y - 6:CHIP_Y + 7, x - 6:x + 7].reshape(-1, 3)
        chips["x%d" % x] = "#%02x%02x%02x" % tuple(int(round(c)) for c in np.median(p, 0))
    regions = {}
    for name, (boxes, mask, band, view) in SPEC.items():
        rgb, note = sample(boxes, mask, band, view)
        regions[name] = {"rgb": rgb, "note": note}
    for name, (src, k, rule) in DERIVED.items():
        if src is None:
            regions[name] = {"rgb": FIXED[name], "note": "derived: " + rule}
        else:
            regions[name] = {"rgb": [int(min(255, round(c * k))) for c in regions[src]["rgb"]], "note": "derived: " + rule}
    desc = ("v1 draft. Every region PIXEL-SAMPLED from design/reference/lyra/lyra_sheet.webp (1536x1024) by "
            "improve/lyra_palette.py: box + HSV mask + luminance-percentile band median, noted per region; 'derived' values say "
            "their rule. Palette chips (13x13 medians at y=%d): %s -- navy, slate blue, grey, cream, tan, brown, dark brown, olive "
            "grey. No glow (L3 default: no magic on the sheet; _GLOW ships all zero)." % (CHIP_Y, ", ".join(chips.values())))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write(' "unit": "lyra",\n "skin": "default",\n')
        f.write(' "description": %s,\n' % json.dumps(desc))
        f.write(' "chips": %s,\n' % json.dumps(chips))
        f.write(' "regions": {\n')
        items = list(regions.items())
        for i, (k, v) in enumerate(items):
            f.write("  %-20s %s%s\n" % (json.dumps(k) + ":", json.dumps(v), "," if i < len(items) - 1 else ""))
        f.write(" },\n")
        f.write(' "material": %s\n}\n' % json.dumps({"roughness": ROUGHNESS, "emission_strength": 2.0,
                                                    "note": "the house shaded material: Col x baked AO, baked normal map (skin only); "
                                                            "no cel bands, no outline shells; no emitting region"}))
    print("PALETTE", OUT, len(regions), "regions; chips", chips)
    for k, v in regions.items():
        print("  %-16s %s" % (k, v["rgb"]))


if __name__ == "__main__":
    main()
