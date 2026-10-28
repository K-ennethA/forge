"""vampwarrior_rawhead_sheet.py -- <render_dir> <out.png> <left_item> <right_item> <tag1,tag2,...> [cols] [tile] :
side-by-side review sheet: each tag gives a pair (left | right) with labels; pairs laid out 'cols' pairs per row."""
import sys, os
from PIL import Image, ImageDraw
d, outp, L, Rr, tags = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5].split(",")
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 2
T = int(sys.argv[7]) if len(sys.argv) > 7 else 512
rows = (len(tags) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 2 * T + (cols - 1) * 12, rows * (T + 22)), (30, 30, 32))
dr = ImageDraw.Draw(sheet)
for k, tag in enumerate(tags):
    r, c = divmod(k, cols)
    x0 = c * (2 * T + 12); y0 = r * (T + 22)
    for j, it in enumerate((L, Rr)):
        p = os.path.join(d, "%s_%s.png" % (it, tag))
        if os.path.exists(p):
            sheet.paste(Image.open(p).convert("RGB").resize((T, T)), (x0 + j * T, y0 + 22))
        dr.text((x0 + j * T + 6, y0 + 4), "%s  %s" % (("RAW" if j == 0 else "NEW"), tag), fill=(255, 255, 120))
sheet.save(outp)
print("SHEET", outp, sheet.size)
