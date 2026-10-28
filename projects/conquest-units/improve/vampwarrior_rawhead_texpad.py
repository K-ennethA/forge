"""vampwarrior_rawhead_texpad.py -- <in.png> <coverage_lab.npy> <out.png> : edge padding. Every texel that no raw UV triangle
covers (lab == -1, from head_graft/head7/hybrid/work_v2/s2/edit_pos_lab.npy, same UVs) takes the colour of the nearest
covered texel. Covered texels are byte-identical; only gutters change (so a decimated face whose UVs dip into a gutter
shows the neighbouring island colour instead of the gutter's peach)."""
import sys, numpy as np
from PIL import Image
from scipy import ndimage
T = np.asarray(Image.open(sys.argv[1]).convert("RGB")); LB = np.load(sys.argv[2])
cov = LB != -1
_, (gy, gx) = ndimage.distance_transform_edt(~cov, return_indices=True)
O = T[gy, gx]
assert (O[cov] == T[cov]).all()
Image.fromarray(O).save(sys.argv[3])
print("PAD gutters", int((~cov).sum()), "->", sys.argv[3])
