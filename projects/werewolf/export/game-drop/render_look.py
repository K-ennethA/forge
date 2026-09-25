"""Headless look-dev stills / previews of a game-drop .glb, rendered as the game would see it.

    blender --background --factory-startup --python render_look.py -- <in.glb> <out_prefix> [stills|turntable|walk]

Imports the glb into an empty scene (so what renders is exactly what ships), lights
it with a fixed key/fill/rim + grey world, and renders with EEVEE:
  stills    -> <prefix>_front.png, <prefix>_threequarter.png (1024 px)
  turntable -> <prefix>_turntable.mp4 (72 frames, 360 deg, idle clip playing)
  walk      -> <prefix>_walk.mp4 (the walk clip, two loops, three-quarter camera)
The glb faces glTF +Z-forward-in-Blender-terms: the importer maps glTF -Z (Godot
forward) to Blender +Y, so the "front" camera sits on +Y looking back at -Y.
"""
import bpy, sys, math, os
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, PREFIX = argv[0], argv[1]
MODE = argv[2] if len(argv) > 2 else "stills"

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=SRC)
meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]  # skip the importer's bone-shape icosphere
arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)

lo = Vector((1e9, 1e9, 1e9)); hi = -lo
for o in meshes:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
centre = (lo + hi) / 2
height = hi.z - lo.z

scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1024 if MODE == "stills" else 640
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("look"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6


def light(name, kind, energy, rot_deg, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, kind); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot_deg]
    return o


light("key", "SUN", 3.2, (50, 0, 150))       # from front-left-above (front is +Y)
light("fill", "SUN", 1.0, (65, 0, 215), (0.85, 0.9, 1.0))
light("rim", "SUN", 2.0, (60, 0, 10))         # from behind

floor_me = bpy.data.meshes.new("floor")
floor_me.from_pydata([(-3, -3, 0), (3, -3, 0), (3, 3, 0), (-3, 3, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("floor", floor_me); scene.collection.objects.link(floor)
fm = bpy.data.materials.new("floor"); fm.diffuse_color = (0.1, 0.1, 0.11, 1)
fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
floor_me.materials.append(fm)

cam_d = bpy.data.cameras.new("cam"); cam_d.lens = 50
cam = bpy.data.objects.new("cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
target = Vector((centre.x, centre.y, lo.z + height * 0.5))
dist = height * 1.75   # 50 mm lens: ~1.2x the figure fills the frame height


def aim(angle_deg, elev=0.08):
    a = math.radians(angle_deg)
    cam.location = target + Vector((math.sin(a) * dist, math.cos(a) * dist, height * elev))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()


def play(clip_prefix):
    if arm is None or arm.animation_data is None:
        return None
    for tr in arm.animation_data.nla_tracks:
        tr.mute = True
    act = next((a for a in bpy.data.actions if a.name.split("_")[0].startswith(clip_prefix)
                or a.name.startswith(clip_prefix)), None)
    if act is None:
        return None
    arm.animation_data.action = act
    try:
        arm.animation_data.action_slot = act.slots[0]
    except Exception:
        pass
    return act


def movie(path):
    if hasattr(scene.render.image_settings, "media_type"):  # Blender 5.0 split image/video
        scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.filepath = path
    bpy.ops.render.render(animation=True)
    print("WROTE", path)


if MODE == "stills":
    play("idle")
    scene.frame_set(1)
    for tag, ang in (("front", 0.0), ("threequarter", 40.0)):
        aim(ang)
        scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
        bpy.ops.render.render(write_still=True)
        print("WROTE", scene.render.filepath)
elif MODE == "turntable":
    act = play("idle")
    n = 72
    scene.frame_start, scene.frame_end = 1, n
    scene.render.fps = 24
    pivot = bpy.data.objects.new("pivot", None); scene.collection.objects.link(pivot)
    pivot.location = target
    cam.parent = pivot
    aim(0.0)
    cam.location = cam.location - target
    pivot.rotation_euler = (0, 0, 0); pivot.keyframe_insert("rotation_euler", frame=1)
    pivot.rotation_euler = (0, 0, math.radians(360 * (n - 1) / n))
    pivot.keyframe_insert("rotation_euler", frame=n)
    for fc in pivot.animation_data.action.fcurves if hasattr(pivot.animation_data.action, "fcurves") else []:
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
    movie(PREFIX + "_turntable.mp4")
else:
    act = play("walk")
    f0, f1 = (int(act.frame_range[0]), int(act.frame_range[1])) if act else (1, 32)
    span = f1 - f0
    scene.frame_start, scene.frame_end = f0, f0 + 2 * span - 1
    scene.render.fps = 24
    if act is not None:
        for fc in getattr(act, "fcurves", []) or []:
            fc.modifiers.new("CYCLES")
        try:
            for layer in act.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for fc in bag.fcurves:
                            fc.modifiers.new("CYCLES")
        except AttributeError:
            pass
    aim(40.0)
    movie(PREFIX + "_walk.mp4")
