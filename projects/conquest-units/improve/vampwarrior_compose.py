"""vampwarrior image assembly (scaffolded from firefly_compose.py; plain Python + PIL, no Blender): run with the forge service venv.

    python -P vampwarrior_compose.py

renders/vampwarrior/vampwarrior_sheet_vs_model.png
    one row per TRUE-ortho sheet view (intake.json ortho_frame.views); columns: the artist's sheet view (cropped by design/reference/vampwarrior/intake.json 'ortho_frame', at
    the sheet's own scale) | the build, orthographic at the SAME scale and floor line | overlay: the sheet with the build's
    silhouette outline (cyan) | one column per extra skin at the same framing. Proportions compare 1:1.
renders/vampwarrior/vampwarrior_contact.png
    every still that exists (views, close-ups, skin fronts) + every clip contact sheet, labelled.
renders/vampwarrior/vampwarrior_sheet_gate.json + stdout 'GATE ...' lines: the NUMERIC sheet gate (the lane quotes them)
    per true-ortho view: IoU of the build's ortho silhouette against the sheet's (best translation - the same
    sheet_proxy.view_metrics the proxy was scored with - and as rendered, in frame), row width error %, and the
    proxy's IoU next to it when intake.json has a `proxy` block; a view more than 0.05 IoU below the proxy is FLAGGED.
    Also 'GATE bake_reference': which shading the build bakes its normal map against (contract: FLAT).
"""
import glob, json, os, re, sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

UNIT = "vampwarrior"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", UNIT)
INTAKE = json.load(open(os.path.join(ROOT, "design", "reference", UNIT, "intake.json")))
SHEET = os.path.join(ROOT, INTAKE["sheet"])
OF = INTAKE["ortho_frame"]
BG = (42, 42, 42, 255)
LAB = (235, 235, 235, 255)
SKINS = sorted({os.path.basename(p)[len(UNIT) + 1:-len("_ortho_front.png")]
                for p in glob.glob(os.path.join(R, UNIT + "_*_ortho_front.png"))})


def over_bg(path, bg=BG):
    im = Image.open(path).convert("RGBA")
    b = Image.new("RGBA", im.size, bg)
    b.alpha_composite(im)
    return b, np.array(im)[:, :, 3]


def outline(alpha):
    m = Image.fromarray(((alpha > 40) * 255).astype(np.uint8))
    e = np.array(m.filter(ImageFilter.MaxFilter(3))).astype(int) - np.array(m.filter(ImageFilter.MinFilter(3))).astype(int)
    return e > 0


def label(im, text, xy=(8, 6)):
    d = ImageDraw.Draw(im)
    d.rectangle([xy[0] - 4, xy[1] - 3, xy[0] + 7 * len(text) + 6, xy[1] + 13], fill=(0, 0, 0, 170))
    d.text(xy, text, fill=LAB)
    return im


sh = Image.open(SHEET).convert("RGBA")
W, H = OF["width_px"], OF["height_px"]
rows = []
for view in OF.get("views", ["front", "side"]):          # only views the sheet draws as TRUE orthos
    p = os.path.join(R, "%s_ortho_%s.png" % (UNIT, view))
    if view not in OF["crops"] or not os.path.exists(p):
        continue
    crop = OF["crops"][view]
    ref = sh.crop(tuple(crop))
    # blank whatever of the neighbouring views / detail panels the shared-size box picks up (only this view's own
    # bbox + a small margin stays): a crop centred on an off-centre axis otherwise shows slices of the next view
    vb = INTAKE["views"][view]["bbox_px"]
    keep = Image.new("L", ref.size, 0)
    ImageDraw.Draw(keep).rectangle([vb[0] - crop[0] - 12, vb[1] - crop[1] - 12, vb[2] - crop[0] + 12, vb[3] - crop[1] + 12], fill=255)
    ref = Image.composite(ref, Image.new("RGBA", ref.size, BG), keep)
    mod, a = over_bg(p)
    ov = ref.copy()
    px = np.array(ov)
    px[outline(a)] = (0, 230, 255, 255)
    ov = Image.fromarray(px)
    tiles = [label(ref, "SHEET %s" % view.upper()), label(mod, "BUILD %s" % view), label(ov, "SHEET + build outline")]
    for s in SKINS:
        q = os.path.join(R, "%s_%s_ortho_%s.png" % (UNIT, s, view))
        if os.path.exists(q):
            tiles.append(label(over_bg(q)[0], "BUILD %s (%s skin)" % (view, s)))
    row = Image.new("RGBA", (len(tiles) * (W + 6) - 6, H), (255, 255, 255, 255))
    for i, t in enumerate(tiles):
        row.paste(t, (i * (W + 6), 0))
    rows.append(row)
if rows:
    out = Image.new("RGBA", (max(r.width for r in rows), len(rows) * (H + 6) - 6), (255, 255, 255, 255))
    for i, r in enumerate(rows):
        out.paste(r, (0, i * (H + 6)))
    out.convert("RGB").save(os.path.join(R, UNIT + "_sheet_vs_model.png"))
    print("WROTE", os.path.join(R, UNIT + "_sheet_vs_model.png"))


def numeric_gate():
    """Per ortho view: build silhouette vs sheet silhouette, the proxy's IoU beside it (sheet_proxy.view_metrics)."""
    sys.path.insert(0, HERE)
    import sheet_proxy as SP
    intake_dir = os.path.join(ROOT, "design", "reference", UNIT)
    masks = SP.view_masks(INTAKE, intake_dir)
    SP.clip_below_floor(INTAKE, masks)     # the build stands on its floor; the proxy was scored on the same clip
    proxy = INTAKE.get("proxy") or {}
    pscores = proxy.get("scores") or {}
    out = {"unit": UNIT, "views": {}, "proxy_sha256": proxy.get("sha256"),
           "proxy_gate": (proxy.get("gate") or {}).get("verdict"), "flagged": []}
    for view in OF.get("views", ["front", "side"]):
        p = os.path.join(R, "%s_ortho_%s.png" % (UNIT, view))
        if view not in OF["crops"] or view not in masks or not os.path.exists(p):
            continue
        x0, y0, x1, y1 = OF["crops"][view]
        full = masks[view]
        ref = np.zeros((y1 - y0, x1 - x0), bool)
        sy0, sx0, sy1, sx1 = max(y0, 0), max(x0, 0), min(y1, full.shape[0]), min(x1, full.shape[1])
        ref[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = full[sy0:sy1, sx0:sx1]
        build = np.array(Image.open(p).convert("RGBA"))[:, :, 3] > 40
        if not build.any() or not ref.any():
            continue
        met, _A, _B = SP.view_metrics(build, ref, in_frame=True)
        pv = (pscores.get(view) or {}).get("iou")
        met["proxy_iou"] = pv
        met["flag"] = bool(pv is not None and met["iou"] < pv - 0.05)
        if met["flag"]:
            out["flagged"].append(view)
        out["views"][view] = met
        print("GATE view=%-5s iou=%.3f in_frame=%.3f row_err=%.1f%% | proxy iou=%s%s"
              % (view, met["iou"], met.get("iou_in_frame", float("nan")), met["row_err_pct"],
                 "%.3f" % pv if pv is not None else "-",
                 "  FLAG: more than 0.05 below the proxy" if met["flag"] else ""))
    if out["views"]:
        out["mean_iou"] = round(float(np.mean([v["iou"] for v in out["views"].values()])), 4)
        print("GATE mean_iou=%.3f flagged=%s proxy=%s" % (out["mean_iou"], ",".join(out["flagged"]) or "none",
                                                        out["proxy_gate"] or "none"))
    json.dump(out, open(os.path.join(R, UNIT + "_sheet_gate.json"), "w"), indent=1)


def bake_reference():
    """Which shading the build's normal-map bake runs against: the last shade_flat / shade_smooth call before the
    first bpy.ops.object.bake in <unit>_build.py. The contract default is FLAT (BUILD_CONTRACT step 5)."""
    p = os.path.join(HERE, UNIT + "_build.py")
    if not os.path.exists(p):
        return
    src = open(p, encoding="utf-8").read()
    i = src.find("object.bake(")
    if i < 0:
        print("GATE bake_reference=none (no object.bake call found)")
        return
    calls = [(m.start(), m.group(1)) for m in re.finditer(r"\.shade_(flat|smooth)\(", src[:i])]
    ref = calls[-1][1] if calls else "unset"
    print("GATE bake_reference=%s%s" % (ref.upper() if ref == "smooth" else ref,
                                        "  CONTRACT: bake against the FLAT low (BUILD_CONTRACT step 5) - the normal map "
                                        "then carries flat face -> smooth high; a smooth reference leaves ribs"
                                        if ref != "flat" else ""))


try:
    numeric_gate()
except Exception as exc:                       # the images above are still written; the gate says why it did not run
    print("GATE skipped: %s: %s" % (type(exc).__name__, exc))
bake_reference()

T = 400
fixed = ["front", "threequarter", "side", "back", "tactical"]
stills = [(UNIT + "_%s.png" % v, v) for v in fixed]
known = {fn for fn, _ in stills}
for p in sorted(glob.glob(os.path.join(R, UNIT + "_*.png"))):
    fn = os.path.basename(p)
    tag = fn[len(UNIT) + 1:-4]
    if fn in known or "ortho" in tag or "sheet" in tag or tag == "contact" or tag.startswith("_"):
        continue
    stills.append((fn, tag))
tiles = []
for fn, lab in stills:
    p = os.path.join(R, fn)
    if os.path.exists(p):
        im = Image.open(p).convert("RGBA").resize((T, T), Image.NEAREST if lab == "tactical" else Image.LANCZOS)
        tiles.append(label(im, lab))
cols = 3
Wc = cols * T + (cols - 1) * 6
strips = []
for p in sorted(glob.glob(os.path.join(R, UNIT + "_*_sheet.png"))):
    if p.endswith("_vs_model.png"):
        continue
    im = Image.open(p).convert("RGBA")
    im = im.resize((Wc, int(im.height * Wc / im.width)), Image.LANCZOS)
    strips.append(label(im, "CLIP " + os.path.basename(p)[len(UNIT) + 1:-4]))
nrow = (len(tiles) + cols - 1) // cols
Hc = nrow * (T + 6) + sum(s.height + 6 for s in strips)
cs = Image.new("RGBA", (Wc, max(Hc, 1)), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % cols) * (T + 6), (i // cols) * (T + 6)))
y = nrow * (T + 6)
for s in strips:
    cs.paste(s, (0, y)); y += s.height + 6
cs.convert("RGB").save(os.path.join(R, UNIT + "_contact.png"))
print("WROTE", os.path.join(R, UNIT + "_contact.png"))
