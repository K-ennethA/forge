"""Blender-side helpers for the head bake-off (f-frame: front -y, up +z, head glb units)."""
import bpy, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)


def obj(name, V, F, link=True):
    me = bpy.data.meshes.new(name)
    V = np.asarray(V, np.float64); F = np.asarray(F, np.int64)
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(F.size); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set("loop_start", (np.arange(len(F)) * 3).astype(np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(F), 3, np.int32))
    me.update(); me.validate()
    ob = bpy.data.objects.new(name, me)
    if link:
        bpy.context.scene.collection.objects.link(ob)
    return ob


def arrays(ob, evaluated=False, world=False):
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get(); oe = ob.evaluated_get(dg); me = oe.to_mesh()
    else:
        me = ob.data
    me.calc_loop_triangles()
    V = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
    T = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", T); T = T.reshape(-1, 3)
    if world:
        M = np.array(ob.matrix_world); V = V @ M[:3, :3].T + M[:3, 3]
    if evaluated:
        oe.to_mesh_clear()
    return V, T


def bvh(V, F):
    return BVHTree.FromPolygons(np.asarray(V).tolist(), np.asarray(F).tolist())


def ortho_front(tree, x0, x1, z0, z1, px, y0=-2.0):
    """rays along +y from y0 on a grid (x right, z up; row 0 = top). -> hit (H, W, 3) nan where none, face idx (H, W)"""
    xs = np.arange(x0, x1, px); zs = np.arange(z1, z0, -px)
    H = np.full((len(zs), len(xs), 3), np.nan); FI = np.full((len(zs), len(xs)), -1, np.int64)
    d = Vector((0.0, 1.0, 0.0))
    for i, z in enumerate(zs):
        for j, x in enumerate(xs):
            h = tree.ray_cast(Vector((x, y0, z)), d, 10.0)
            if h[0] is not None:
                H[i, j] = h[0]; FI[i, j] = h[2]
    return H, FI, xs, zs


def nearest_face(tree, P):
    out = np.full(len(P), -1, np.int64); dist = np.full(len(P), np.inf)
    for i, p in enumerate(P):
        if np.isfinite(p[0]):
            h = tree.find_nearest(Vector(p))
            if h[0] is not None:
                out[i] = h[2]; dist[i] = h[3]
    return out, dist


def dists(tree, P):
    return np.array([tree.find_nearest(Vector(p))[3] for p in P])
