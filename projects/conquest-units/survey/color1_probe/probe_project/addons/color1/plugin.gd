@tool
extends EditorPlugin
var ext: Color1Ext
func _enter_tree() -> void:
	ext = Color1Ext.new()
	GLTFDocument.register_gltf_document_extension(ext, true)
func _exit_tree() -> void:
	GLTFDocument.unregister_gltf_document_extension(ext)
