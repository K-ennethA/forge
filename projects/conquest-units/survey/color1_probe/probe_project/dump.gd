extends SceneTree

func _fmt_names(fmt: int) -> String:
	var out := []
	var names := {Mesh.ARRAY_FORMAT_VERTEX:"VERTEX", Mesh.ARRAY_FORMAT_NORMAL:"NORMAL", Mesh.ARRAY_FORMAT_TANGENT:"TANGENT",
		Mesh.ARRAY_FORMAT_COLOR:"COLOR", Mesh.ARRAY_FORMAT_TEX_UV:"TEX_UV", Mesh.ARRAY_FORMAT_TEX_UV2:"TEX_UV2",
		Mesh.ARRAY_FORMAT_CUSTOM0:"CUSTOM0", Mesh.ARRAY_FORMAT_CUSTOM1:"CUSTOM1", Mesh.ARRAY_FORMAT_CUSTOM2:"CUSTOM2",
		Mesh.ARRAY_FORMAT_CUSTOM3:"CUSTOM3", Mesh.ARRAY_FORMAT_BONES:"BONES", Mesh.ARRAY_FORMAT_WEIGHTS:"WEIGHTS",
		Mesh.ARRAY_FORMAT_INDEX:"INDEX"}
	for k in names:
		if fmt & k: out.append(names[k])
	for c in 4:
		var cf := (fmt >> (Mesh.ARRAY_FORMAT_CUSTOM_BASE + c * Mesh.ARRAY_FORMAT_CUSTOM_BITS)) & Mesh.ARRAY_FORMAT_CUSTOM_MASK
		if fmt & (Mesh.ARRAY_FORMAT_CUSTOM0 << c):
			out.append("CUSTOM%d_type=%d" % [c, cf])
	return ", ".join(out)

const ARR := ["VERTEX","NORMAL","TANGENT","COLOR","TEX_UV","TEX_UV2","CUSTOM0","CUSTOM1","CUSTOM2","CUSTOM3","BONES","WEIGHTS","INDEX"]

func _uniq(arr) -> Array:
	var s := {}
	for v in arr:
		s[str(v)] = true
	var k := s.keys()
	k.sort()
	return k

func _dump_mesh(label: String, mesh: Mesh) -> void:
	print("== ", label, " mesh class=", mesh.get_class(), " surfaces=", mesh.get_surface_count())
	for si in mesh.get_surface_count():
		var arrays: Array = mesh.surface_get_arrays(si)
		if mesh is ArrayMesh:
			print("  surface ", si, " format flags: ", _fmt_names((mesh as ArrayMesh).surface_get_format(si)))
		for i in ARR.size():
			var a = arrays[i]
			if a == null:
				print("  ARRAY_", ARR[i], ": null")
				continue
			print("  ARRAY_", ARR[i], ": ", type_string(typeof(a)), " size=", a.size())
			if i in [Mesh.ARRAY_COLOR, Mesh.ARRAY_TEX_UV, Mesh.ARRAY_TEX_UV2] or (i >= Mesh.ARRAY_CUSTOM0 and i <= Mesh.ARRAY_CUSTOM3):
				print("    unique values: ", _uniq(a))
				if i == Mesh.ARRAY_CUSTOM0:
					var bad := 0
					var vs: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
					for vi in vs.size():
						var p := vs[vi]
						var idx := 4 * int(p.x > 0) + 2 * int(p.z < 0) + int(p.y > 0)
						if absf(a[vi * 4] - idx / 7.0) > 0.01: bad += 1
					print("    CUSTOM0.r == blender_vertex_index/7 by position: mismatches=", bad, "/", vs.size())
		var bs = mesh.surface_get_blend_shape_arrays(si) if mesh is ArrayMesh else []
		print("  blend shapes: ", bs.size())

func _walk(n: Node, label: String) -> void:
	if n is MeshInstance3D and n.mesh:
		_dump_mesh(label + ":" + str(n.name), n.mesh)
	if n is ImporterMeshInstance3D and n.mesh:
		print("ImporterMesh on ", n.name)
		_dump_mesh(label + ":importer->" + str(n.name), n.mesh.get_mesh())
	for c in n.get_children():
		_walk(c, label)

func _init() -> void:
	print("GODOT ", Engine.get_version_info().string)
	# Path A: editor-imported scene (res://probe.glb via .import)
	var ps: PackedScene = load("res://probe.glb")
	if ps:
		var inst := ps.instantiate()
		_walk(inst, "IMPORTED")
		inst.free()
	else:
		print("IMPORTED: load failed")
	# Path B: runtime GLTFDocument
	if FileAccess.file_exists("res://addons/color1/color1_ext.gd"):
		GLTFDocument.register_gltf_document_extension(load("res://addons/color1/color1_ext.gd").new(), true)
		print("runtime: Color1Ext registered")
	var doc := GLTFDocument.new()
	var st := GLTFState.new()
	var err := doc.append_from_file(ProjectSettings.globalize_path("res://probe.glb"), st)
	print("RUNTIME append err=", err)
	for gm in st.get_meshes():
		var im: ImporterMesh = gm.mesh
		print("GLTFMesh additional_data/instance materials ok; importer surfaces=", im.get_surface_count())
		_dump_mesh("RUNTIME_IMPORTERMESH", im.get_mesh())
	var root := doc.generate_scene(st)
	_walk(root, "RUNTIME_SCENE")
	root.free()
	quit()
