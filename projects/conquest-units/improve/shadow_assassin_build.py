"""SHADOW ASSASSIN (Varrick's ally) -- hero tier (Conquest roster), v1 DRAFT (lane-conventions "Two speeds": one clean
headless build + probes-as-sanity + one comparison strip; no gate wall, no determinism twin, NO clips).

    blender --background --factory-startup --python improve/shadow_assassin_build.py -- \
        [--preview <out.blend>]          (body + outfit + cloak + blade + regions + palette only: no bake, no rig)
        [--scratch <dir>]                (exploration: every output goes to <dir>, nothing in the project is written)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--glb]                          (also export rigged/shadow_assassin.glb through the shared _GLOW path)

Spec (binding): design/review-log.md 2026-10-05 "NEW UNIT: Shadow Assassin" + design/OPEN-QUESTIONS.md S1-S5 (the live
defaults built here). SHAPE AUTHORITY: design/reference/shadow_assassin/shadow_assassin_sheet.webp (front / side / back
figures + head, necklace, belt, blade, cloak panels) -- silhouette-matched to the VIEWS, never a text transcription.
Hooded figure, NO FACE (S5: dark face wrap + near-black hood void, zero face stack, no eyes, no emissive), layered
tattered charcoal cloak + hood with gold trim, purple scarf + gold diamond pendant, purple sash, the purple diamond sigil
on the back cloak; leather shoulder plates, crossed chest straps + round gold brooch, belt with gold rings + pouches,
tattered layered skirt over dark trousers, knee guards, buckled boots; ONE large curved dark blade with purple sigils in
the RIGHT hand, the reverse hold BAKED INTO THE BIND POSE (S1; clipless law, the Elias v7 precedent).
House style: design/character-style-guide.md (the face / eye / mouth / hair stacks do not apply: no visible face, no
visible hair). Reference implementation: improve/elias_* (read-only): s1 = the MPFB body (face ops removed, a stance knob
added), s2 = the body paint, s3 = the outfit, s4 = hood / capes / cloak / shoulder plates, (no s5: no hair), s6 = blade +
assembly + bake, s7 = rig + the baked blade hold + save + glb.
UNITS: metres, floor z = 0 at the boot soles, front -Y, left +X ("his left" = +X).
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile, addon_utils
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K                     # noqa: E402  (read-only use)
import palettes as PAL                 # noqa: E402  (read-only use)
import shadow_assassin_parts as VP     # noqa: E402

# =========================================================================== TUNABLE CONSTANTS (artist-facing names)
UNIT = "shadow_assassin"
CHAR_ID = "shadow_assassin"
TRI_BUDGET = [30000, 50000]           # declared tier: HERO / named (quality-tier law: hero = 30-50k; Elias precedent)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
# ---- body (MPFB2). MakeHuman age macro 0.5 = 25 yr; "age unknown" -> a prime-age adult (~30 yr = 0.5 + 5/65 x 0.5)
HEIGHT = 1.80                         # "height" (S4 default): boot sole to the crown of the head (m) -- the hood rises above it
SOLE_T = 0.024                        # "boot sole": the foot sits this far above the floor inside the boot
BODY_H = HEIGHT - SOLE_T              # (derived: the barefoot MPFB body height)
MACRO = {"gender": 1.0, "age": 0.54, "muscle": 0.62, "weight": 0.38, "proportions": 0.75, "height": 0.5,
         "cupsize": 0.5, "firmness": 0.5}                                  # "build": lithe, athletic, lean (infiltrator)
RACE = {"caucasian": 0.6, "asian": 0.2, "african": 0.2}
TARGETS = {                           # "head size": the house-style head-to-body read (5.5-6 heads, house rule); no face
    "head/head-scale-vert-incr": 0.40, "head/head-scale-horiz-incr": 0.30, "head/head-scale-depth-incr": 0.30,   # dials:
    "nose/nose-scale-horiz-decr": 0.40, "nose/nose-volume-decr": 0.50,     #   the face is never seen (S5) -- only a smaller
    "nose/nose-point-width-decr": 0.40,                                    #   nose so the face wrap drapes smooth
}
REST_ARM_DOWN = 28.0                  # "rest arm drop": the MPFB A-pose arms lowered this much (deg) in the bind pose (left arm;
                                      #   the right arm is then IK-solved onto the blade grip in s7)
REST_ELBOW_OPEN = 24.0                # the MPFB rest elbow opened this much (deg)
STANCE_DEG = 2.0                      # "stance width": each thigh swung out this much (deg) about the forward axis, the foot
                                      #   counter-rotated level (the sheet FRONT view: boot centres ~0.47 m apart; MPFB A-stance 0.40 + 2 deg -> 0.456)
NEAREST_TIE = 1e-6                    # tie-invariant BVH nearest band (m; lane-conventions NEAREST_TIE)
# ---- boots (Elias's boot stack: foot shell + sole + shaft + folded cuff) + the sheet's buckled straps
BOOT_MARGIN = (0.011, 0.013)
TOE_EXT = 0.024
BOOT_CLEAR = (0.012, 0.018)           # shaft clearance at the ankle / at the top (m)
BOOT_TOP = 0.16                       # "boot height": the shaft reaches this far down the shin from the knee (knee 0 .. ankle 1)
CUFF = (0.040, 0.012, 0.016, 0.0045)  # "boot cuff": height, clearance, flare, thickness (m)
BOOT_STRAPS = (0.30, 0.52, 0.76)      # "boot straps": strap centres down the shaft (shaft top 0 .. ankle 1); each a leather band
BOOT_STRAP = (0.016, 0.0030, (0.012, 0.004, 0.010))   # strap height, thickness (m), gold buckle half sizes (across, out, up)
KNEE_GUARD = {"t": (-0.10, 0.17), "half_deg": 70.0, "clear": 0.012, "t_plate": 0.005, "nu": 9, "nv": 5, "strap": 0.20}   # "knee
                                      #   guards": span along the shin (knee 0 .. ankle 1, - = above the knee), half angle round
                                      #   the front, clearance, thickness, grid, the strap ring below at this t
# ---- belt + pouches + gold rings (trunk-hung: geometry from the TRUNK, style-guide law)
BELT = {"h": 0.052, "clear": 0.010, "t": 0.006, "dz": -0.020}   # "belt": height, clearance over the skirt top, thickness,
                                      #   offset of its centre from the waist line (m)
RING_BUCKLE = (0.024, 0.0042)         # "ring buckle": the gold ring at the belt front -- ring radius, wire radius (m)
BELT_RINGS = ((-48.0, 0.016), (38.0, 0.014))   # "belt rings": gold rings hanging off the belt (azimuth deg, + his left; ring radius m)
POUCHES = ((58.0, (0.034, 0.020, 0.036)), (-60.0, (0.030, 0.019, 0.032)), (-128.0, (0.028, 0.018, 0.030)))   # "pouches":
                                      #   (azimuth from the front deg, + his left; half sizes across / depth / height)
# ---- tattered skirt panels below the belt (two layers) + the purple sash at the front
SKIRT = {"len": (0.43, 0.47), "clear": 0.010, "flare": 0.085, "t": 0.0032, "nu": 40, "nv": 9,
         "tear": (0.20, 0.045, 0.22), "slits": (0, 10, 20, 30), "slit_rows": 5}   # "skirt": length below the belt front / back,
                                      #   clearance, flare, thickness, grid, tatter (long point, typical tooth, share of long
                                      #   points), the columns split open (front, his left, back, his right) and how many rows up
SKIRT_UNDER = {"len": (0.36, 0.40), "clear": 0.004, "flare": 0.055, "tear": (0.10, 0.030, 0.15)}   # the inner layer (shorter)
SASH = {"x": -0.028, "w": 0.085, "len": 0.50, "trim": 0.008, "chevron": (0.05, 0.010), "nv": 10, "t": 0.0030}   # "purple
                                      #   sash": centre x (m, - = his right), width, length below the belt, gold edge trim
                                      #   width, the gold chevron at the bottom (height, stroke half width), rows, thickness
# ---- crossed chest straps + round gold brooch + the scarf + the gold diamond pendant
STRAPS = (((-0.55, 0.10), (0.85, -0.12)), ((0.55, 0.10), (-0.85, -0.10)))   # "chest straps": per strap (shoulder anchor
                                      #   (x share of the shoulder joint x, dz above it m), hip anchor (x share of the hip joint
                                      #   x, dz vs the waist m)); strap 0 = his right shoulder -> left hip, 1 = left shoulder ->
                                      #   right hip (the brooch strap)
STRAP_W, STRAP_T = 0.030, 0.0028      # strap width / half thickness (m)
BROOCH = {"r": 0.026, "drop": 0.140, "x": 0.120, "gem": (0.012, 0.005)}   # "brooch": plate radius, drop below the neck base,
                                      #   x (m, + his left), the dark centre (radius, height)
SCARF = {"loops": ((0.010, 0.019, 0.016, 0.014), (-0.008, 0.022, 0.044, 0.026), (-0.024, 0.021, 0.074, 0.040)),
         "seg": 36, "sides": 8, "flat": 2.0}   # "purple scarf": per fold loop (z vs
                                      #   the neck base m, tube radius m, front dip m, stand-off from the neck m), ring segments,
                                      #   tube sides, flattening
PENDANT = {"drop": 0.160, "size": (0.020, 0.032, 0.006), "gem": 0.008}   # "gold pendant": below the neck base (m), half width /
                                      #   half height / depth, the dark gem
# ---- forearm bracers (leather, gold rims) + leather shoulder plates (layered lames, shoulder-hung)
BRACER = {"t": (0.05, 0.62), "clear": 0.006, "t_leather": 0.0042, "nu": 14, "nv": 6, "trim_w": 0.007, "straps": (0.30, 0.62)}
SHOULDER_PLATES = ((-0.26, 0.20, 0.004, 100.0), (-0.02, 0.38, 0.008, 95.0), (0.18, 0.54, 0.012, 88.0))   # "shoulder plates"
                                      #   (top -> bottom lame): span along the upper arm (shoulder joint 0 .. elbow 1, - = above
                                      #   the joint), clearance over the arm / cape, half angle round the outside (deg)
PLATE_T = 0.0050                      # plate thickness (m)
PLATE_TRIM = 0.008                    # gold edge band on each plate's lower hem (m)
# ---- face: NO FACE (S5). A smooth dark WRAP shell over the face; the eye band painted near-black (the hood void)
FACE_WRAP = {"az": 100.0, "top": 0.030, "bottom": 0.040, "clear": 0.0045, "smooth": 60, "t": 0.0020, "void_dz": -0.040,
             "nu": 40, "nv": 24}       # "face wrap": half span round the front (deg), top above the eye centres, bottom below the
                                      #   chin (m), clearance, envelope smoothing passes, thickness, the void band's lower edge
                                      #   vs the eye centres (above it = hood_void), grid
# ---- hood (deep cowl, pointed peak, gold-trimmed opening; interior near-black)
HOOD = {"clear": 0.034, "brim_fwd": 0.085, "brim_drop": 0.045, "peak": (0.035, 64.0, 16.0, 42.0), "nu": 44, "nv": 18,
        "t": 0.0045, "trim": 0.011, "fold": (0.006, 7.0), "el_top": 86.0, "el_bot": -62.0, "bot_flare": 0.015}   # "hood":
                                      #   clearance over the head / wrap / scarf, brim pushed forward + down at the opening (m),
                                      #   peak (height m, centre elevation deg, elevation sigma deg, azimuth sigma deg) at the top-back, grid,
                                      #   thickness, gold trim width, fold amplitude / count, elevation range of the rows (deg
                                      #   about the head centre), extra flare at the lower rows (over the scarf / shoulders)
HOOD_OPEN = ((-62.0, 146.0), (-30.0, 134.0), (0.0, 128.0), (18.0, 138.0), (28.0, 160.0), (35.0, 180.0))   # "hood opening":
                                      #   (elevation deg, front-edge azimuth from the BACK deg): the face opening's outline
# ---- shoulder capes (two ragged layers, shoulder-hung) + the long back cloak (trunk-hung) + its sigil
CAPES = ({"drop": (0.13, 0.31, 0.22), "clear": 0.010, "flare": 0.015, "front": 148.0, "tear": (0.090, 0.030, 0.20), "nu": 44, "nv": 9, "t": 0.0040},
         {"drop": (0.08, 0.20, 0.14), "clear": 0.017, "flare": 0.010, "front": 156.0, "tear": (0.060, 0.022, 0.18), "nu": 44, "nv": 7, "t": 0.0040})
                                      # "capes" (lower -> upper layer): length below the neck-line top (m) at the back / over the
                                      #   shoulders / at the front edge (the sheet: short at the back -- the BACK view's gold
                                      #   ornament shows under it -- long over the shoulders), clearance, flare, front-edge
                                      #   azimuth from the BACK (deg), tatter (long point, tooth, long share), grid, thickness
CLOAK = {"top_dz": 0.020, "az": ((0.0, 76.0), (0.55, 84.0), (1.0, 104.0)), "hem_z": 0.36, "clear": 0.022, "flare": 0.14,
         "tear": (0.21, 0.050, 0.24), "nu": 34, "nv": 20, "t": 0.0045, "folds": (0.014, 6.0), "slits": (5, 11, 16, 22, 28),
         "slit_rows": 7}               # "back cloak": top edge above the shoulder joints (m), half span round the back (v down the
                                      #   cloak, deg from the back centre: behind the arms at the top, wrapping out below the
                                      #   hands), hem height (m), clearance, flare at the hem, tatter, grid, thickness, folds,
                                      #   tear slits (columns) running this many rows up from the hem
CLOAK_SIGIL = {"h": 0.46, "z": 1.24, "w": 0.0040, "ornament": (0.034, 0.012)}   # "back sigil": total height, the diamond centre
                                      #   height (m; sheet BACK view), stroke half width, the gold ornament above it (height,
                                      #   half width)
# ---- the BLADE (own object + bone 'blade', child of hand_r) and the baked reverse hold
BLADE = {"grip_r": 0.0135, "grip_len": 0.110, "pommel_h": 0.012, "guard_h": 0.016, "guard_r": 0.021,
         "ctrl": ((0.0, 0.0), (0.020, 0.090), (0.060, 0.220), (0.070, 0.330), (0.040, 0.450), (-0.010, 0.535), (-0.060, 0.590)),
         "width": ((0.0, 0.040), (0.10, 0.046), (0.35, 0.044), (0.70, 0.032), (0.90, 0.016), (1.0, 0.002)),
         "spine_share": 0.40, "barbs": ((0.10, 0.16, 0.014), (0.19, 0.24, 0.010)), "thick": (0.0040, 0.0022), "bevel": 0.30,
         "stations": 34, "sigil_span": (0.14, 0.82), "sigil_frac": 0.55, "sigil_w": 0.0014,
         "sigil_diamonds": (0.26, 0.44, 0.62), "diamond": (0.016, 0.0065)}   # "blade": grip radius / length, pommel, guard;
                                      #   the crescent centreline (x = convex side out, z = along the blade from the guard, m;
                                      #   sheet FRONT view + BLADE panel: bulging out ~0.07 m, tip curling back in), width profile
                                      #   (share along -> m), spine share of the width, spine barbs (s0, s1, height m), half
                                      #   thickness spine / bevel line, bevel share, stations, the sigil vein span + its place
                                      #   across the flat, stroke half width, the sigil diamonds (share along), diamond half
                                      #   length / half width
BLADE_GRIP = {"out": -0.120, "fwd": -0.060, "drop": 0.480}   # "grip place": the fist centre vs the right shoulder joint (x m, - =
                                      #   out to his right; y m, - = forward; down m) -- the sheet FRONT view: fist at hip height
BLADE_TILT = {"out": 2.0, "fwd": -35.0, "yaw": 0.0}   # "blade lean": the blade axis (fist -> blade) leans this much off straight
                                      #   down, outward (his right) / forward (deg); yaw turns the blade plane about that axis off
                                      #   the frontal plane (deg, + = convex side forward)
                                      #   v1: fwd -35 = the tip leans BACK (invisible in the sheet FRONT view, which fixes only
                                      #   the side-to-side read): a reverse hold on a hanging forearm sends the blade down-back;
                                      #   measured sweep (wrist bend): fwd +4 -> 57-77 deg, -20 -> 48, -35 -> 27 (with GRIP_DIAG 10)
HOLD_ELBOW_POLES = ((-0.85, 0.30, -0.10), (-0.70, 0.60, -0.20), (-0.95, -0.10, -0.15), (-0.40, 0.85, -0.20),
                    (-0.20, 0.95, -0.10), (0.00, 1.00, 0.00), (-0.55, 0.80, 0.20))   # elbow
                                      #   directions tried (out, back, down) with the hand-roll search
GRIP_DIAG = 10.0                      # "diagonal grip": the handle crosses the palm this far (deg) off the knuckle line, its blade
                                      #   end toward the wrist (a loose reverse hold); the sign is picked by the least wrist bend
HOLD_TWIST_SHARE = 0.5                # "forearm twist share": the forearm takes this share of the grip's twist about its own axis
HOLD_CURL = ((58.0, 72.0, 50.0), "wrap")   # "grip fingers": curl per joint (knuckle, middle, tip; deg; Wren's HAND_POSES grip) +
                                      #   the thumb solved closed over the fingers round the grip ("wrap", Elias v7)
HAS_BLADE2 = False                    # "second blade" (S1 knob, STUBBED in v1: the sheet's back view silhouettes a blade at each
                                      #   side; True is not built yet -- a new lane adds it on the same grip machinery, left hand)
# ---- glow gate (S2): only the sigils glow, subtle (Elias-orb tier), purple
GLOW_REGIONS = ("blade_sigil", "cloak_sigil")
GLOW_MAX_SCALE = 0.35                 # emission_scale cap (Elias orb 0.22); emission strength cap 2.0 (phone)
GLOW_HUE = (275.0, 40.0)              # the glow hue window (deg centre, +-): purple
# ---- bake + material
HAND_DECIMATE = {"L": 0.45, "R": 0.55}   # "hand detail" (Elias v7.1 / Varden glove pattern): each gloved hand's interior collapse-
                                      #   decimated to this share of its tris (the right hand grips: more kept)
BAKE_RES = (2048, 2048)          # (normal, AO) texture sizes
CUT_SNAP = 0.12
SLIVER_AREA = 5e-8
AO_SAMPLES = 32
AO_FLOOR = {"default": 0.50, "cloth": 0.58, "leather": 0.55, "metal": 0.62}   # AO floors per region class (no black grime)
AO_FLOOR_REGIONS = {"cloth": ["cloak", "cloak_inner", "cape", "cape_inner", "hood", "skirt", "skirt_inner", "sash", "sash_inner",
                              "scarf", "scarf_shade", "tunic", "tunic_shade", "trousers", "sleeve", "facewrap"],
                    "leather": ["leather", "leather_dark", "boot", "boot_cuff", "boot_strap", "glove"],
                    "metal": ["gold", "gold_dark", "hood_trim"]}
SMOOTH_NORMAL_REGIONS = ("glove",)    # normal-map texels kept only here (the MPFB hands' smooth high); everything else flat

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
WANT_GLB = "--glb" in argv
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
BODY_H = HEIGHT - SOLE_T
assert not HAS_BLADE2, "HAS_BLADE2: the second blade is stubbed in v1 (S1 default = one blade)"
SCRATCH = argv[argv.index("--scratch") + 1] if "--scratch" in argv else None
_OUT_ROOT = SCRATCH or ROOT
OUT_IMPROVED = os.path.join(_OUT_ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(_OUT_ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(_OUT_ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(_OUT_ROOT, "improved", "textures")
TAG = "scratch" if SCRATCH else "main"
report = {"unit": UNIT, "conquest_character_id": CHAR_ID,
          "version": "v1 draft (hood void + face wrap, layered tattered cloak, one curved blade held in the bind pose)",
          "name_status": "named by the sheet (SHADOW ASSASSIN - VARRICK'S ALLY)",
          "source": "design/reference/shadow_assassin/shadow_assassin_sheet.webp (review-log 2026-10-05)", "tier": "hero",
          "tri_budget": TRI_BUDGET, "units": "metres; floor z = 0 at the soles", "overrides": OVERRIDES}
DIG = {}
SECTIONS = ["shadow_assassin_s1_body.py", "shadow_assassin_s2_regions.py", "shadow_assassin_s3_outfit.py",
            "shadow_assassin_s4_cloak.py", "shadow_assassin_s6_assemble.py", "shadow_assassin_s7_rig.py"]
UNTIL = argv[argv.index("--until") + 1] if "--until" in argv else None   # (exploration: stop after the named section)
for sec_ in SECTIONS:
    _p = os.path.join(HERE, sec_)
    print("SECTION", sec_, round(time.time() - T0, 1)); sys.stdout.flush()
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
    if UNTIL and UNTIL in sec_:
        if "--debug-blend" in argv and "PARTS" in globals():   # (exploration: body + parts, palette-painted, for quick stills)
            _pal = PAL.load(UNIT, "default")
            _isl = [{"name": "body", "V": CV, "F": CF, "R": list(reg)}] + PARTS
            _V = np.vstack([p_["V"] for p_ in _isl]); _F, _R, _o = [], [], 0
            for p_ in _isl:
                _F += [[i + _o for i in f] for f in p_["F"]]; _R += list(p_["R"]); _o += len(p_["V"])
            _names = sorted(set(_R))
            _me = bpy.data.meshes.new(UNIT); _me.from_pydata(_V.tolist(), [], _F); _me.update()
            _ob = bpy.data.objects.new(UNIT, _me); scene.collection.objects.link(_ob)
            PAL.store_regions(_me, _names, [_names.index(r_) for r_ in _R], np.ones(len(_R)))
            PAL.paint(_me, _pal)
            _mt = bpy.data.materials.new("dbg"); _mt.use_nodes = True
            _vc = _mt.node_tree.nodes.new("ShaderNodeVertexColor"); _vc.layer_name = "Col"
            _mt.node_tree.links.new(_vc.outputs["Color"], _mt.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
            _me.materials.append(_mt); _me.shade_flat()
            _ob["conquest_focus"] = json.dumps({})
            bpy.ops.wm.save_as_mainfile(filepath=argv[argv.index("--debug-blend") + 1], copy=True, compress=True)
            print("DEBUG_BLEND", len(_F), "faces", tri_count_F(_F), "tris")
        print("UNTIL", sec_, round(time.time() - T0, 1)); sys.stdout.flush(); os._exit(0)
