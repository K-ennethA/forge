# composite_back_r2.py: back = r1 base + the r2 edit inside three feathered boxes only (upper back, back of hips, backs of knees); the r2 'loin panel seen from behind' strip is dropped (it painted over the legs = a tail).
import numpy as np
from PIL import Image
from scipy import ndimage
b = np.asarray(Image.open("base_back.png").convert("RGB")).astype(float); e = np.asarray(Image.open("edited_back.png").convert("RGB")).astype(float)
m = np.zeros(b.shape[:2])
for x0, y0, x1, y1 in [(430, 285, 595, 420), (395, 515, 630, 650), (370, 915, 460, 1020), (565, 915, 655, 1020)]:
    m[y0:y1, x0:x1] = 1
m = ndimage.gaussian_filter(m, 6)[..., None]
Image.fromarray((b * (1 - m) + e * m).round().clip(0, 255).astype(np.uint8)).save("body_back.png")
