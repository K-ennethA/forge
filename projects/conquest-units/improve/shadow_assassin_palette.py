"""Shadow Assassin palette sampler (plain Python + PIL + numpy; the forge service venv):

    python -P improve/shadow_assassin_palette.py          -> palettes/shadow_assassin/default.json

Every region tone is PIXEL-SAMPLED from the saved sheet design/reference/shadow_assassin/shadow_assassin_sheet.webp
(1536 x 1024; style-guide sheet law: never eyeballed). Rule per region (the Elias v5 precedent): a pixel BOX on one of the
sheet's views / detail panels, an HSV MASK (keeps only the material's pixels: background, gold trim, purple accents
excluded as named), then the median RGB of the pixels inside a LUMINANCE-PERCENTILE BAND of the masked set. The note on
each region records box, mask, band and pixel count. The sheet's 5 palette chips (13 x 13 medians at y = 907) are recorded.
Values that cannot be read off the sheet are marked 'derived' with their rule. Deterministic: same sheet -> same file.
"""
import json
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SHEET = os.path.join(ROOT, "design", "reference", "shadow_assassin", "shadow_assassin_sheet.webp")
OUT = os.path.join(ROOT, "palettes", "shadow_assassin", "default.json")
# ---- artist-facing knobs
GLOW_SCALE = 0.12          # "sigil glow strength": emission_scale of the blade + back-cloak sigils (Elias orb tier 0.22; gate <= 0.35)
GLOW_SAT_K = 3.0           # the glow hue's saturation = the sampled sigil saturation x this (capped at 1), value 1.0
EMISSION_STRENGTH = 2.0    # material emission strength (phone cap 2.0)
CHIP_Y, CHIP_X = 907, (1077, 1128, 1179, 1229, 1281)

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
    "dark_cloth": (lambda h, s, v: (v < 0.45) & (s < 0.32) & ~((h > 225) & (h < 310) & (s > 0.14)) & ~((h > 15) & (h < 55) & (s > 0.30) & (v > 0.32)),
                   "v < 0.45, s < 0.32, not purple (h 225-310 & s > 0.14), not gold (h 15-55 & s > 0.30 & v > 0.32)"),
    "purple": (lambda h, s, v: (h > 225) & (h < 310) & (s > 0.12) & (v > 0.18),
               "purple: h 225-310, s > 0.12, v > 0.18"),
    "gold": (lambda h, s, v: (h > 15) & (h < 55) & (s > 0.30) & (v > 0.35),
             "gold: h 15-55, s > 0.30, v > 0.35"),
    "leather": (lambda h, s, v: (((h < 45) | (h > 340)) & (s > 0.10) & (s < 0.62) & (v > 0.10) & (v < 0.42)),
                "brown leather: h < 45 or > 340, s 0.10-0.62, v 0.10-0.42 (gold highlights v > 0.42 excluded)"),
    "figure": (lambda h, s, v: (v < 0.50) | (s > 0.28),
               "not sheet background (background v ~0.80 s ~0.17): v < 0.50 or s > 0.28"),
    "blade_dark": (lambda h, s, v: (v < 0.40),
                   "blade body: v < 0.40 (panel background grey-green v ~0.49)"),
    "blade_hi": (lambda h, s, v: (v > 0.53) & (s < 0.20),
                 "blade highlight: v > 0.53, s < 0.20 (lighter than the panel background)"),
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
    assert len(px) >= 20, (boxes, mask, len(px))
    L = lum(px)
    lo, hi = np.percentile(L, band[0]), np.percentile(L, band[1])
    sel = px[(L >= lo) & (L <= hi)]
    rgb = [int(round(c)) for c in np.median(sel, 0)]
    note = "SAMPLED #%02x%02x%02x: %s, box%s %s, mask [%s], luminance %d-%dth pct band median (%d of %d px)" % (
        rgb[0], rgb[1], rgb[2], view, "es" if len(boxes) > 1 else "", "; ".join("x%d-%d y%d-%d" % (b[0], b[2], b[1], b[3]) for b in boxes),
        MASKS[mask][1], band[0], band[1], len(sel), len(px))
    return rgb, note


# region -> (boxes, mask, band, view label)
SPEC = {
    "cloak":        ([(775, 290, 890, 620)], "dark_cloth", (40, 60), "BACK view, the long back cloak"),
    "cloak_inner":  ([(775, 290, 890, 620)], "dark_cloth", (8, 22), "BACK view cloak, its shadow folds (lining / inner side)"),
    "cloak_sigil":  ([(818, 300, 880, 510)], "purple", (80, 97), "BACK view, the diamond sigil strokes on the cloak (thin strokes: the lit core band, edges mix with the cloak)"),
    "hood":         ([(1065, 55, 1145, 250)], "dark_cloth", (45, 65), "HEAD DETAIL panel, the hood cloth (his right side)"),
    "hood_trim":    ([(1060, 50, 1285, 257)], "gold", (60, 88), "HEAD DETAIL panel, the gold trim along the hood edge"),
    "hood_inner":   ([(1160, 100, 1235, 165)], "any", (3, 15), "HEAD DETAIL panel, the hood interior over the face (darkest)"),
    "hood_void":    ([(1160, 100, 1235, 165)], "any", (10, 30), "HEAD DETAIL panel, the face void under the brim (eye band)"),
    "facewrap":     ([(1170, 165, 1235, 215)], "dark_cloth", (40, 60), "HEAD DETAIL panel, the face wrap over nose + mouth"),
    "scarf":        ([(1062, 290, 1252, 345)], "purple", (45, 65), "NECKLACE DETAIL panel, the purple scarf folds"),
    "scarf_shade":  ([(1062, 290, 1252, 345)], "purple", (8, 25), "NECKLACE DETAIL panel, the scarf fold shadows"),
    "gold":         ([(1128, 352, 1178, 428), (1360, 305, 1400, 350)], "gold", (70, 92), "NECKLACE panel pendant + BELT panel ring, the lit metal"),
    "gold_dark":    ([(1128, 352, 1178, 428), (1360, 305, 1400, 350)], "gold", (8, 30), "NECKLACE panel pendant + BELT panel ring, the shaded gold"),
    "gem_dark":     ([(1146, 378, 1160, 402)], "any", (5, 35), "NECKLACE panel, the pendant's dark centre"),
    "leather":      ([(1282, 298, 1478, 336), (300, 252, 360, 300)], "leather", (45, 65), "BELT panel belt band + FRONT view shoulder plate"),
    "leather_dark": ([(1282, 298, 1478, 336), (300, 252, 360, 300)], "leather", (10, 25), "BELT panel + FRONT shoulder plate, the dark leather edges"),
    "boot":         ([(102, 805, 190, 885), (302, 805, 382, 885)], "figure", (40, 60), "FRONT view, both boots"),
    "boot_strap":   ([(102, 805, 190, 885), (302, 805, 382, 885)], "figure", (18, 32), "FRONT view boots, the strap / buckle shadow band"),
    "boot_sole":    ([(102, 870, 190, 892), (302, 870, 382, 892)], "figure", (3, 15), "FRONT view, the boot soles"),
    "trousers":     ([(150, 700, 215, 740), (300, 700, 360, 740)], "figure", (40, 60), "FRONT view, the trousers between skirt hem and boot top"),
    "tunic":        ([(190, 395, 275, 520)], "dark_cloth", (40, 60), "FRONT view, the tunic over the belly (straps excluded by mask)"),
    "cape":         ([(128, 245, 172, 335), (300, 230, 340, 250)], "dark_cloth", (45, 65), "FRONT view, the shoulder cape layers"),
    "cape_inner":   ([(128, 245, 172, 335), (300, 230, 340, 250)], "dark_cloth", (8, 22), "FRONT view shoulder capes, the fold shadows"),
    "skirt":        ([(150, 580, 205, 660), (270, 580, 320, 660)], "dark_cloth", (40, 60), "FRONT view, the tattered skirt panels"),
    "skirt_inner":  ([(150, 580, 205, 660), (270, 580, 320, 660)], "dark_cloth", (8, 22), "FRONT view skirt, the shadowed under-layers"),
    "sash":         ([(1335, 345, 1395, 432)], "purple", (45, 65), "BELT DETAIL panel, the purple sash"),
    "sash_inner":   ([(1335, 345, 1395, 432)], "purple", (8, 25), "BELT DETAIL panel, the sash folds"),
    "sash_trim":    ([(1325, 340, 1405, 435)], "gold", (50, 85), "BELT DETAIL panel, the gold trim along the sash edges"),
    "glove":        ([(326, 466, 352, 506), (40, 480, 75, 515)], "dark_cloth", (40, 60), "FRONT view, both gloved hands"),
    "sleeve":       ([(300, 340, 330, 410)], "dark_cloth", (40, 60), "FRONT view, his left upper sleeve"),
    "blade":        ([(1065, 472, 1250, 620)], "blade_dark", (35, 55), "BLADE DETAIL panel, the blade body"),
    "blade_edge":   ([(1065, 472, 1250, 620)], "blade_hi", (50, 75), "BLADE DETAIL panel, the steel edge highlight"),
    "blade_sigil":  ([(1065, 472, 1250, 620), (30, 520, 110, 790)], "purple", (80, 97), "BLADE DETAIL panel + FRONT view blade, the purple sigils (thin strokes: the lit core band)"),
    "grip":         ([(62, 495, 92, 530)], "figure", (30, 50), "FRONT view, the blade's wrapped grip under the fist"),
    "guard":        ([(55, 520, 95, 545)], "figure", (15, 35), "FRONT view, the blade's dark guard / collar"),
}
DERIVED_SHADE = {"tunic_shade": ("tunic", 0.78), "boot_cuff": ("boot", 0.88), "hood_inner_trim": ("hood_trim", 0.70)}


def main():
    import colorsys
    chips = {}
    for x in CHIP_X:
        p = IM[CHIP_Y - 6:CHIP_Y + 7, x - 6:x + 7].reshape(-1, 3)
        chips["x%d" % x] = "#%02x%02x%02x" % tuple(int(round(c)) for c in np.median(p, 0))
    regions = {}
    for name, (boxes, mask, band, view) in SPEC.items():
        rgb, note = sample(boxes, mask, band, view)
        regions[name] = {"rgb": rgb, "note": note}
    for name, (src, k) in DERIVED_SHADE.items():
        regions[name] = {"rgb": [int(round(c * k)) for c in regions[src]["rgb"]], "note": "derived: %s x %.2f" % (src, k)}
    for name in ("blade_sigil", "cloak_sigil"):
        r, g, b = (c / 255.0 for c in regions[name]["rgb"])
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        e = colorsys.hsv_to_rgb(h, min(1.0, s * GLOW_SAT_K), 1.0)
        regions[name]["emission"] = [int(round(c * 255)) for c in e]
        regions[name]["emission_scale"] = GLOW_SCALE
        regions[name]["note"] += ("; GLOW (S2 default, subtle Elias-orb tier): emission = the sampled hue %.0f deg, saturation x %.1f, "
                                  "value 1.0, x %.2f" % (h * 360.0, GLOW_SAT_K, GLOW_SCALE))
    pal = {"unit": "shadow_assassin", "skin": "default",
           "description": ("v1 draft. Every region PIXEL-SAMPLED from design/reference/shadow_assassin/shadow_assassin_sheet.webp "
                           "(1536x1024) by improve/shadow_assassin_palette.py: box + HSV mask + luminance-percentile band median, "
                           "noted per region; 'derived' values say their rule. Palette chips (13x13 medians at y=%d): %s -- "
                           "charcoal, dark grey, muted purple, taupe grey, gold. Glow: the blade sigils + the back-cloak sigil "
                           "only (S2 default: subtle)." % (CHIP_Y, ", ".join(chips.values()))),
           "chips": chips, "regions": regions,
           "material": {"roughness": 0.66, "emission_strength": EMISSION_STRENGTH,
                        "note": "the house shaded material: Col x baked AO, baked normal map (gloves only); no cel bands, no outline shells"}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write(' "unit": "shadow_assassin",\n "skin": "default",\n')
        f.write(' "description": %s,\n' % json.dumps(pal["description"]))
        f.write(' "chips": %s,\n' % json.dumps(chips))
        f.write(' "regions": {\n')
        items = list(regions.items())
        for i, (k, v) in enumerate(items):
            f.write("  %-18s %s%s\n" % (json.dumps(k) + ":", json.dumps(v), "," if i < len(items) - 1 else ""))
        f.write(" },\n")
        f.write(' "material": %s\n}\n' % json.dumps(pal["material"]))
    print("PALETTE", OUT, len(regions), "regions; chips", chips)
    for k, v in regions.items():
        print("  %-14s %s %s" % (k, v["rgb"], v.get("emission", "")))


if __name__ == "__main__":
    main()
