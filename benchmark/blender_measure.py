"""Headless-Blender entry for the benchmark's measurement.

Spawned by :func:`benchmark.quality.evaluate`, never windowed::

    blender --background --factory-startup --python benchmark/blender_measure.py \
        -- <task.json> <artifact.glb|.blend|.stl|.obj> <out.json>

Loads the artifact into an empty scene, reads every mesh-like object out in
world-space millimetres with the add-on's own ``evaluated_mesh_mm`` (modifiers
applied — what you see is what is measured), and hands them to
:func:`benchmark.quality.measure`.  Writes ``{"error": ...}`` instead of a
measurement when anything goes wrong, and exits 0 either way: the caller reads
the JSON, not the exit code.
"""

import json
import os
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
ADDON_DIR = os.path.join(REPO_ROOT, "addon")
for path in (REPO_ROOT, ADDON_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

MESHABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def load_artifact(path):
    """Bring the artifact's objects into the current scene. Returns their names."""
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects.keys())
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".blend":
        # Appended, not opened: the session (and this script) stay in place,
        # and the file on disk is never written.
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for obj in dst.objects:
            if obj is not None and obj.name not in bpy.context.scene.objects:
                bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError("cannot load %r (glb, gltf, blend, stl, obj)" % ext)
    bpy.context.view_layer.update()
    return sorted(set(bpy.data.objects.keys()) - before)


def meshes_mm(names, scale):
    """``{name: (vertices_mm, faces)}`` for every mesh-like object named."""
    from forge.tools.common import evaluated_mesh_mm

    out = {}
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type not in MESHABLE:
            continue
        vertices, faces = evaluated_mesh_mm(obj, apply_modifiers=True, scale=scale)
        if faces:
            out[name] = (vertices, faces)
    return out


def measure_artifact(task, artifact):
    """Load + measure; the whole in-Blender half, shared with the headless suite."""
    from benchmark import quality

    ext = os.path.splitext(artifact)[1].lower()
    scale = float(task.get("unit_scale_mm") or quality.DEFAULT_UNIT_SCALE.get(ext, 1000.0))
    names = load_artifact(artifact)
    meshes = meshes_mm(names, scale)
    if not meshes:
        raise ValueError("the artifact has no mesh objects")
    measurement = quality.measure(task, meshes, quality.load_printer(task))
    measurement["artifact"] = artifact
    measurement["unit_scale_mm"] = scale
    return measurement


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 3:
        print("usage: blender --background --python blender_measure.py -- "
              "<task.json> <artifact> <out.json>")
        sys.exit(2)
    task_json, artifact, out_json = (os.path.abspath(p) for p in argv)
    try:
        from benchmark import quality

        task = quality.load_task(os.path.dirname(os.path.abspath(task_json)))
        clear_scene()
        payload = measure_artifact(task, artifact)
    except Exception as exc:  # noqa: BLE001 - reported in the JSON, not the exit code
        traceback.print_exc()
        payload = {"error": "%s: %s" % (type(exc).__name__, exc)}
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=str)
    print("benchmark measure: wrote %s" % out_json)
    sys.exit(0)


if __name__ == "__main__":
    main()
