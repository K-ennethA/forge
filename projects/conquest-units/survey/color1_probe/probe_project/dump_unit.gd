extends SceneTree
# Skinned-unit gate (import wave 2026-10-02). For res://<unit>.glb: dumps every MeshInstance3D surface of
# (A) the editor-imported scene and (B) the runtime GLTFDocument path with Color1Ext registered, as JSON:
# per-surface format flags, vertex/normal/uv/custom0 arrays, bones/weights presence, blend-shape count,
# skin/skeleton binding, AnimationPlayer clips. compare_unit.py matches it vertex-by-vertex against the glb.
#   godot --headless --path probe_project --script res://dump_unit.gd -- <unit>

func _flat(a) -> Array:
	var out := []
	if a == null:
		return out
	for v in a:
		if v is Vector3:
			out.append_array([v.x, v.y, v.z])
		elif v is Vector2:
			out.append_array([v.x, v.y])
		else:
			out.append(v)
	return out

func _surfaces(mesh: Mesh) -> Array:
	var res := []
	for si in mesh.get_surface_count():
		var arr: Array = mesh.surface_get_arrays(si)
		var fmt := (mesh as ArrayMesh).surface_get_format(si) if mesh is ArrayMesh else 0
		var c0type := (fmt >> Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT) & Mesh.ARRAY_FORMAT_CUSTOM_MASK
		res.append({
			"surface": si,
			"name": (mesh as ArrayMesh).surface_get_name(si) if mesh is ArrayMesh else "",
			"has_custom0": bool(fmt & Mesh.ARRAY_FORMAT_CUSTOM0), "custom0_type": c0type,
			"has_color": bool(fmt & Mesh.ARRAY_FORMAT_COLOR), "has_bones": bool(fmt & Mesh.ARRAY_FORMAT_BONES),
			"has_weights": bool(fmt & Mesh.ARRAY_FORMAT_WEIGHTS),
			"compressed": bool(fmt & Mesh.ARRAY_FLAG_COMPRESS_ATTRIBUTES),
			"blend_shapes": (mesh as ArrayMesh).get_blend_shape_count() if mesh is ArrayMesh else 0,
			"n": (arr[Mesh.ARRAY_VERTEX] as PackedVector3Array).size(),
			"pos": _flat(arr[Mesh.ARRAY_VERTEX]), "nrm": _flat(arr[Mesh.ARRAY_NORMAL]),
			"uv": _flat(arr[Mesh.ARRAY_TEX_UV]),
			"custom0": Array(arr[Mesh.ARRAY_CUSTOM0]) if arr[Mesh.ARRAY_CUSTOM0] != null else [],
		})
	return res

func _walk(n: Node, out: Dictionary) -> void:
	if n is MeshInstance3D and n.mesh:
		var mi := n as MeshInstance3D
		out["meshes"].append({"node": str(mi.name), "skin": mi.skin != null,
			"skeleton_path": str(mi.skeleton), "skeleton_ok": mi.get_node_or_null(mi.skeleton) is Skeleton3D,
			"surfaces": _surfaces(mi.mesh)})
	if n is Skeleton3D:
		out["skeleton_bones"] = (n as Skeleton3D).get_bone_count()
	if n is AnimationPlayer:
		out["animations"] = Array((n as AnimationPlayer).get_animation_list())
	for c in n.get_children():
		_walk(c, out)

func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var unit: String = args[0] if args.size() > 0 else "geode"
	var path := "res://%s.glb" % unit
	var result := {"godot": Engine.get_version_info().string, "unit": unit}
	var ps: PackedScene = load(path)
	var a := {"meshes": []}
	if ps:
		var inst := ps.instantiate()
		_walk(inst, a)
		inst.free()
	else:
		a["error"] = "load failed"
	result["imported"] = a
	GLTFDocument.register_gltf_document_extension(load("res://addons/color1/color1_ext.gd").new(), true)
	var doc := GLTFDocument.new()
	var st := GLTFState.new()
	var err := doc.append_from_file(ProjectSettings.globalize_path(path), st)
	var b := {"meshes": [], "append_err": err}
	if err == OK:
		var root := doc.generate_scene(st)
		_walk(root, b)
		root.free()
	result["runtime"] = b
	var f := FileAccess.open("res://dump_%s.json" % unit, FileAccess.WRITE)
	f.store_string(JSON.stringify(result))
	f.close()
	print("DUMP_UNIT wrote res://dump_%s.json" % unit)
	quit()
