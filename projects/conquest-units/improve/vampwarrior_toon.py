"""Vampire Warrior v2 render-side TOON PREVIEW: in-memory material swap used by vampwarrior_render.py / vampwarrior_clips.py
(--toon). It stands in for the proposed Godot toon shader (ramped lighting) so the renders show what the game path would
show; the asset itself keeps plain Principled materials (glTF-safe). Nothing is saved.

  body / sword materials:  Diffuse(white) -> Shader to RGB -> constant ColorRamp (RAMP: 3 light bands) x Col (the vertex
                           colour, which already carries the asset's baked AO tone bands) -> Emission, + Glow x strength
  outline material:        Emission(Col): the shell renders as a flat unlit line colour (backface culling kept)
"""

RAMP = ((0.0, 0.58), (0.30, 0.84), (0.80, 1.0))   # (light value at or above, brightness) -- shadow / mid / lit


def toon_preview(bpy):
    done = []
    for mat in bpy.data.materials:
        if not mat.use_nodes or "col" not in mat.node_tree.nodes:
            continue
        nt = mat.node_tree
        out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        strength = bsdf.inputs["Emission Strength"].default_value if bsdf else 1.0
        col = nt.nodes["col"].outputs["Color"]
        if mat.name.endswith("_outline"):
            em = nt.nodes.new("ShaderNodeEmission")
            nt.links.new(col, em.inputs["Color"]); em.inputs["Strength"].default_value = 1.0
            nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
            done.append(mat.name)
            continue
        dif = nt.nodes.new("ShaderNodeBsdfDiffuse")
        dif.inputs["Color"].default_value = (1, 1, 1, 1)
        s2r = nt.nodes.new("ShaderNodeShaderToRGB")
        nt.links.new(dif.outputs["BSDF"], s2r.inputs["Shader"])
        bw = nt.nodes.new("ShaderNodeRGBToBW")
        nt.links.new(s2r.outputs["Color"], bw.inputs["Color"])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        cr = ramp.color_ramp
        cr.interpolation = "CONSTANT"
        while len(cr.elements) < len(RAMP):
            cr.elements.new(0.5)
        for el, (pos, v) in zip(cr.elements, RAMP):
            el.position = pos; el.color = (v, v, v, 1.0)
        nt.links.new(bw.outputs["Val"], ramp.inputs["Fac"])
        mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"
        mul.inputs["Factor"].default_value = 1.0
        ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
        oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
        nt.links.new(col, ia); nt.links.new(ramp.outputs["Color"], ib)
        em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Strength"].default_value = 1.0
        nt.links.new(oc, em.inputs["Color"])
        eg = nt.nodes.new("ShaderNodeEmission"); eg.inputs["Strength"].default_value = strength
        nt.links.new(nt.nodes["glow"].outputs["Color"], eg.inputs["Color"])
        add = nt.nodes.new("ShaderNodeAddShader")
        nt.links.new(em.outputs["Emission"], add.inputs[0]); nt.links.new(eg.outputs["Emission"], add.inputs[1])
        nt.links.new(add.outputs["Shader"], out.inputs["Surface"])
        done.append(mat.name)
    return done


def hide_outlines(bpy):
    hidden = []
    for o in bpy.context.scene.objects:
        if o.type == "MESH" and o.name.endswith("_outline"):
            o.hide_render = True
            hidden.append(o.name)
    return hidden
