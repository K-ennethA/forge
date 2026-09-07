from build123d import *
import forge_lib

PARAMS = {
    "part": {"value": 0, "unit": "count", "min": 0, "max": 1, "step": 1,
              "description": "0 = body (holds the LED, battery and switch), 1 = flame (the push plunger)"},
    "total_height": {"value": 75.0, "unit": "mm", "min": 60.0, "max": 200.0, "step": 1.0,
                       "description": "Overall height of the body, not counting the flame"},
    "base_diameter": {"value": 40.0, "unit": "mm", "min": 20.0, "max": 80.0, "step": 1.0,
                        "description": "Diameter of the small circle at the base the lamp stands on. The top opening is always 1.5x this"},
    "wall": {"value": 3.0, "unit": "mm", "min": 2.0, "max": 6.0, "step": 0.2,
              "description": "Shell wall thickness"},
    "foot_count": {"value": 4, "unit": "count", "min": 0, "max": 6, "step": 1,
                     "description": "Little foot nubs around the base (0 removes them)"},
    "flame_height": {"value": 55.0, "unit": "mm", "min": 25.0, "max": 90.0, "step": 1.0,
                        "description": "Height of the oval flame above its flat base disc"},
    "flame_width": {"value": 24.0, "unit": "mm", "min": 16.0, "max": 50.0, "step": 1.0,
                       "description": "Widest diameter of the slim oval flame"},
    "peg_length": {"value": 34.0, "unit": "mm", "min": 10.0, "max": 80.0, "step": 1.0,
                     "description": "How far the flame's plunger stem drops inside the body to reach your switch"},
}


def _body(p):
    H = p["total_height"]
    base_r = p["base_diameter"] / 2.0
    top_r = 1.5 * base_r
    wall = max(p["wall"], forge_lib.min_wall())
    d = top_r - base_r

    points = [
        (base_r, 0.0),
        (base_r + 0.30 * d, 0.20 * H),
        (base_r + 0.55 * d, 0.42 * H),
        (base_r + 0.80 * d, 0.65 * H),
        (top_r, 0.85 * H),
        (top_r, H),
    ]
    body = forge_lib.soft_body(points, wall=wall)

    if int(p["foot_count"]) > 0:
        body += forge_lib.feet_ring(base_r, 6.0, int(p["foot_count"]), style="pad")

    return body


def _flame(p):
    wall = max(p["wall"], forge_lib.min_wall())
    base_r_body = p["base_diameter"] / 2.0
    neck_r = 1.5 * base_r_body
    neck_inner_r = neck_r - wall
    peg_r = max(neck_inner_r - forge_lib.fit_tolerance("slide_fit"), forge_lib.min_feature() * 1.5)
    peg_l = p["peg_length"]

    taper_h = 3.0
    disc_r = peg_r + 6.0
    disc_h = 3.0
    stem_h = 6.0
    oval_r = max(p["flame_width"] / 2.0, forge_lib.min_feature() * 3.0)
    stem_r = max(oval_r * 0.55, forge_lib.min_feature() * 1.5)
    fh = p["flame_height"]

    y_peg_top = peg_l
    y_disc_bottom = y_peg_top + taper_h
    y_disc_top = y_disc_bottom + disc_h
    y_stem_top = y_disc_top + stem_h
    y_oval_mid = y_stem_top + 0.5 * fh
    y_oval_top = y_stem_top + fh

    points = [
        (peg_r, 0.0),
        (peg_r, y_peg_top),
        (disc_r, y_disc_bottom),
        (disc_r, y_disc_top),
        (stem_r, y_stem_top),
        (oval_r, y_oval_mid),
        (0.18 * oval_r, y_oval_top),
    ]
    return forge_lib.soft_body(points)


def build(p):
    if int(p["part"]) == 0:
        return _body(p)
    return _flame(p)
