"""Derive the werewolf_escaped palette from the reference picture, deterministically.

    blender --background --factory-startup --python sample_palette.py -- [annotated_out.png]

Source: design/refs/form-a-front.png (500 x 1024), the front crop the Form A mesh
was generated from (requirements.md section 7; the mesh IS Form A: jacket, tee,
jeans, boots, bare head and hands). Every region below is a fixed pixel box
(x0, y0, x1, y1; origin top-left, x1/y1 exclusive); a region's colour is the
LIT TONE of all its boxes' pixels: the per-channel mean of the pixels at or above
the region's 75th luminance percentile, as sRGB 0-255. Why not the plain median:
the reference is a low-key studio render, so a region's median is albedo times
the photo's own shadowing; the game relights the model, and shipping the
shadowed median would darken it twice (the medians of jacket/jeans/boots/hair
all sit at 21-32 out of 255 - one black blob). The median is still printed and
stored for comparison. No colour is invented or hand-adjusted: palette.json is
exactly these numbers. Writes palette.json next
to this file and, if asked, a copy of the picture with the boxes outlined.
"""
import bpy, os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "design", "refs", "form-a-front.png"))

BOXES = {
    "hair":   [(232, 40, 282, 64)],
    "skin":   [(238, 112, 272, 140),            # cheeks + nose, below the brows
               (98, 480, 122, 510), (384, 480, 408, 510)],  # backs of both hands
    "jacket": [(160, 235, 200, 300), (305, 245, 340, 300),   # chest panels
               (108, 320, 140, 400), (370, 320, 402, 400)],  # sleeves
    "shirt":  [(232, 200, 282, 380)],
    "jeans":  [(175, 520, 230, 640), (290, 520, 340, 640)],  # thighs
    "boots":  [(100, 890, 165, 950), (340, 890, 400, 950)],
}


def load(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)[::-1]  # top row first
    return img, px


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    img, px = load(REF)
    h, w = px.shape[:2]
    print("REF", REF, "%dx%d" % (w, h))
    palette = {}
    for name, boxes in BOXES.items():
        chunk = np.concatenate([px[y0:y1, x0:x1, :3].reshape(-1, 3) for x0, y0, x1, y1 in boxes])
        med = np.median(chunk, axis=0)
        lum = chunk @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        lit = chunk[lum >= np.percentile(lum, 75)].mean(axis=0)
        to255 = lambda v: [int(round(float(c) * 255)) for c in v]
        rgb = to255(lit)
        palette[name] = {"srgb": rgb, "median_srgb": to255(med), "boxes": boxes,
                         "pixels": int(len(chunk))}
        print("PALETTE %-6s lit_srgb=%s median_srgb=%s pixels=%d boxes=%s"
              % (name, rgb, to255(med), len(chunk), boxes))
    out = {"source": os.path.relpath(REF, HERE).replace(os.sep, "/"),
           "method": "lit tone: per-channel mean of pixels >= the region's 75th luminance percentile, over fixed pixel boxes, sRGB 0-255",
           "regions": palette}
    with open(os.path.join(HERE, "palette.json"), "w") as f:
        json.dump(out, f, indent=1)
    if argv:
        ann = px.copy()
        for name, boxes in BOXES.items():
            for x0, y0, x1, y1 in boxes:
                for x in (x0, x1 - 1):
                    ann[y0:y1, x, :3] = (1, 0.1, 0.8)
                for y in (y0, y1 - 1):
                    ann[y, x0:x1, :3] = (1, 0.1, 0.8)
        out_img = bpy.data.images.new("annotated", w, h, alpha=True)
        out_img.pixels[:] = ann[::-1].ravel()
        out_img.filepath_raw = argv[0]
        out_img.file_format = "PNG"
        out_img.save()
        print("WROTE", argv[0])


main()
