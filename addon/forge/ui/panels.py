"""Forge sidebar (N-panel) UI: server status, PartForge and RigForge.

Two deliberate conventions in this file:

* the PartForge panels bind their state to a local called ``props``, the
  RigForge ones to ``rf``, the animation ones to ``ra``, the Assistant to
  ``chat`` and Flows to ``fl``.  They are different PropertyGroups on the scene
  and the headless
  panel-wiring tests tell them apart by that name — reuse one and another
  phase's suite will fail on a property that is not on its group;
* nothing here does work.  Every button is an operator that reports back through
  its panel's ``status`` string, so a failure shows up in the sidebar instead of
  in a console the sculptor is not looking at.
"""

import bpy
from bpy.types import Panel

from .. import server
from ..prefs import get_prefs, service_url
from ..tools import assistant, flows, partforge, rigforge, rigforge_anim, rigforge_rig

CATEGORY = "Forge"


class _ForgePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


class VIEW3D_PT_forge_assistant(_ForgePanel, Panel):
    """The beginner's door into everything below it.

    Deliberately first in the sidebar: someone who has never opened Blender
    should be able to type a sentence here and never touch the boxes underneath.
    """

    bl_idname = "VIEW3D_PT_forge_assistant"
    bl_label = "Assistant"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        chat = assistant.get_props(context)
        if chat is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return

        if len(chat.log):
            box = layout.box()
            column = box.column(align=True)
            for entry in chat.log:
                you = entry.role == "you"
                header = column.row()
                header.active = you
                header.label(text="You:" if you else "Forge:",
                             icon="USER" if you else "LIGHT")
                body = column.column(align=True)
                body.scale_y = 0.75
                for line in _wrap(entry.text, 38)[:24]:
                    body.label(text=line)
                column.separator()

        column = layout.column(align=True)
        column.enabled = not chat.busy
        column.label(text="What do you want to make or fix?")
        column.prop(chat, "message", text="")
        if chat.message:
            sub = column.column(align=True)
            sub.active = False
            sub.scale_y = 0.7
            for line in _wrap(chat.message, 38)[1:5]:
                sub.label(text=line)

        row = layout.row(align=True)
        if chat.busy:
            row.label(text=chat.status or "Thinking ...", icon="SORTTIME")
            row.operator("forge.assistant_cancel", text="Stop", icon="CANCEL")
            # What it is doing, live, under the spinner: a job that is thinking
            # in silence looks broken, and this is the difference between
            # "it's working" and "is it working?".
            if len(chat.activity):
                box = layout.box()
                column = box.column(align=True)
                column.scale_y = 0.75
                for entry in list(chat.activity)[-assistant.ACTIVITY_LINES:]:
                    column.label(text=_wrap(entry.label, 36)[0],
                                 icon=assistant.ACTIVITY_ICONS.get(entry.kind, "DOT"))
        else:
            send = row.row(align=True)
            send.enabled = bool(chat.message.strip())
            send.operator("forge.assistant_send", text="Send", icon="PLAY")
            row.operator("forge.assistant_new", text="", icon="FILE_NEW")
            row.operator("forge.assistant_health", text="", icon="URL")

        if not chat.busy and chat.status:
            status = layout.box() if chat.status_is_error else layout.column(align=True)
            status.alert = bool(chat.status_is_error)
            lines = _wrap(chat.status, 38)
            status.label(text=lines[0],
                         icon="ERROR" if chat.status_is_error else "INFO")
            for line in lines[1:4]:
                status.label(text=line)
        if chat.last_cost:
            row = layout.row()
            row.active = False
            row.label(text=chat.last_cost)


class VIEW3D_PT_forge_server(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_server"
    bl_label = "Forge Server"

    def draw(self, context):
        layout = self.layout
        status = server.get_status()
        prefs = get_prefs()

        row = layout.row(align=True)
        if status["running"]:
            row.label(text="Listening on %s:%d" % (status["host"], status["port"]), icon="CHECKMARK")
        else:
            row.label(text="Stopped (port %d)" % status["port"], icon="RADIOBUT_OFF")

        row = layout.row(align=True)
        row.operator("forge.start_server", icon="PLAY", text="Start")
        row.operator("forge.stop_server", icon="PAUSE", text="Stop")

        if status["running"]:
            box = layout.box()
            column = box.column(align=True)
            column.label(text="Connections: %d open / %d total"
                         % (status["active_connections"], status["total_connections"]))
            column.label(text="Commands handled: %d" % status["commands_handled"])
            if status["queued"]:
                column.label(text="Queued: %d" % status["queued"], icon="SORTTIME")
            if status["last_command"]:
                column.label(text="Last: %s" % status["last_command"])

        if status["last_error"]:
            box = layout.box()
            box.alert = True
            column = box.column(align=True)
            column.label(text="Last error:", icon="ERROR")
            for line in _wrap(status["last_error"], 44)[:4]:
                column.label(text=line)
            column.operator("forge.clear_last_error", text="Dismiss", icon="X")

        if not status["running"] and not getattr(prefs, "autostart", False):
            layout.label(text="Enable autostart in add-on preferences", icon="INFO")


class VIEW3D_PT_forge_partforge(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_partforge"
    bl_label = "PartForge"

    def draw(self, context):
        layout = self.layout
        props = partforge.get_props(context)
        if props is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return

        column = layout.column(align=True)
        column.prop(props, "script_path", text="")
        row = column.row(align=True)
        row.enabled = not props.busy
        row.operator("forge.pf_load_script", icon="FILE_REFRESH", text="Load Script")
        row.operator("forge.pf_health", icon="URL", text="", emboss=True)

        column = layout.column(align=True)
        column.prop(props, "object_name")
        column.prop(props, "collection_name")

        if props.busy:
            layout.label(text=props.status or "Working ...", icon="SORTTIME")
        elif props.status:
            box = layout.box()
            box.alert = bool(props.status_is_error)
            box.label(
                text=props.status,
                icon="ERROR" if props.status_is_error else "INFO",
            )

        if props.stats:
            layout.label(text=props.stats, icon="MESH_DATA")


class VIEW3D_PT_forge_parameters(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_parameters"
    bl_parent_id = "VIEW3D_PT_forge_partforge"
    bl_label = "Parameters"

    def draw_header_preset(self, context):
        props = partforge.get_props(context)
        if props is not None:
            self.layout.prop(props, "show_descriptions", text="", icon="INFO", emboss=False)

    def draw(self, context):
        layout = self.layout
        props = partforge.get_props(context)
        if props is None:
            return
        layout.enabled = not props.busy

        if not len(props.params):
            layout.label(text="No parameters loaded", icon="INFO")
            layout.label(text="Pick a script and press Load Script")
            return

        for item in props.params:
            column = layout.column(align=True)
            if item.kind == "BOOL":
                column.prop(item, "bool_value", text=item.label or item.name)
            elif item.kind == "INT":
                column.prop(item, "int_value", text=item.label or item.name)
            else:
                column.prop(item, "float_value", text=item.label or item.name)
            if props.show_descriptions:
                sub = column.column(align=True)
                sub.active = False
                range_text = item.range_text()
                if range_text:
                    sub.label(text=range_text)
                if item.description:
                    for line in _wrap(item.description, 40)[:3]:
                        sub.label(text=line)

        row = layout.row(align=True)
        row.operator("forge.pf_regenerate", icon="FILE_REFRESH", text="Regenerate")
        row.operator("forge.pf_reset_params", icon="LOOP_BACK", text="", emboss=True)


class VIEW3D_PT_forge_checks(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_checks"
    bl_parent_id = "VIEW3D_PT_forge_partforge"
    bl_label = "Print Checks"

    def draw(self, context):
        layout = self.layout
        props = partforge.get_props(context)
        if props is None:
            return
        layout.enabled = not props.busy

        layout.operator("forge.pf_check", icon="CHECKMARK", text="Run Checks")

        if not len(props.checks):
            layout.label(text="Not checked yet", icon="INFO")
            return

        overall = (props.check_overall or "").lower()
        box = layout.box()
        box.alert = overall == "fail"
        box.label(
            text="Overall: %s" % (props.check_overall or "?").upper(),
            icon=partforge.CHECK_ICONS.get(overall, "QUESTION"),
        )
        if props.check_summary:
            box.label(text=props.check_summary)

        for item in props.checks:
            column = layout.column(align=True)
            column.label(text=item.name, icon=item.icon())
            sub = column.column(align=True)
            sub.active = item.status == "pass"
            for line in _wrap(item.details, 40)[:3]:
                sub.label(text=line)
            if item.hint:
                for line in _wrap(item.hint, 40)[:2]:
                    column.label(text=line, icon="MOD_BEVEL")


class VIEW3D_PT_forge_segments(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_segments"
    bl_parent_id = "VIEW3D_PT_forge_partforge"
    bl_label = "Segments"

    def draw(self, context):
        layout = self.layout
        props = partforge.get_props(context)
        if props is None:
            return
        layout.enabled = not props.busy

        column = layout.column(align=True)
        column.prop(props, "joint_type")
        column.prop(props, "joint_tolerance")

        column = layout.column(align=True)
        column.prop(props, "segment_mode")
        if props.segment_mode == "RADIAL":
            column.prop(props, "segment_radial")
        elif props.segment_mode == "PLANAR":
            column.prop(props, "segment_planar")
        elif props.suggested_mode:
            row = column.row()
            row.active = False
            row.label(text="Suggested: %s" % props.suggested_mode)

        layout.prop(props, "segment_collection")
        layout.operator("forge.pf_segment", icon="MOD_BOOLEAN", text="Segment")

        box = layout.box()
        box.label(text="Export Segments", icon="EXPORT")
        box.prop(props, "segment_export_dir", text="")
        box.prop(props, "segment_basename")
        box.label(text="Format: %s (see Export panel)" % props.export_format.upper())
        box.operator("forge.pf_export_segments", icon="FILE_TICK", text="Export Segments")

        if props.segment_summary:
            layout.label(text=props.segment_summary, icon="MESH_DATA")


class VIEW3D_PT_forge_export(_ForgePanel, Panel):
    bl_idname = "VIEW3D_PT_forge_export"
    bl_parent_id = "VIEW3D_PT_forge_partforge"
    bl_label = "Export"

    def draw(self, context):
        layout = self.layout
        props = partforge.get_props(context)
        if props is None:
            return
        layout.enabled = not props.busy
        layout.prop(props, "export_format", expand=True)
        layout.prop(props, "export_path", text="")
        layout.operator("forge.pf_export", icon="EXPORT", text="Export")
        layout.label(text="Service: %s" % service_url(), icon="URL")


class VIEW3D_PT_forge_flows(_ForgePanel, Panel):
    """Saved sequences of Forge operations, replayed with no AI in the loop.

    Sits directly under PartForge because that is where its steps land: a flow
    is what a job the assistant worked out once becomes afterwards.
    """

    bl_idname = "VIEW3D_PT_forge_flows"
    bl_label = "Flows"

    def draw(self, context):
        layout = self.layout
        fl = flows.get_props(context)
        if fl is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return

        row = layout.row(align=True)
        row.label(text="Saved sequences", icon="SEQUENCE")
        row.operator("forge.flow_refresh", text="", icon="FILE_REFRESH")

        if not fl.loaded:
            layout.label(text="Press the refresh button to list them", icon="INFO")
        elif not len(fl.flows):
            layout.label(text="No flows in %s" % (flows.flows_dir() or "(unset)"),
                         icon="INFO")

        column = layout.column(align=True)
        for entry in fl.flows:
            row = column.row(align=True)
            chosen = entry.name == fl.selected
            pick = row.operator(
                "forge.flow_select", text=entry.name,
                icon="RADIOBUT_ON" if chosen else "RADIOBUT_OFF",
                emboss=False, depress=chosen)
            pick.name = entry.name
            if entry.error:
                row.label(text="", icon="ERROR")
            else:
                run = row.operator("forge.flow_run", text="Run", icon="PLAY")
                run.name = entry.name
            if chosen and entry.description:
                sub = column.column(align=True)
                sub.active = False
                sub.scale_y = 0.75
                for line in _wrap(entry.description, 38)[:3]:
                    sub.label(text=line)
            if chosen and entry.error:
                sub = column.box()
                sub.alert = True
                for line in _wrap(entry.error, 38)[:3]:
                    sub.label(text=line)

        if len(fl.params):
            box = layout.box()
            box.enabled = not fl.busy
            box.label(text="Parameters", icon="PRESET")
            for item in fl.params:
                box.prop(item, "value", text=item.label_text())
                if item.description:
                    sub = box.column(align=True)
                    sub.active = False
                    sub.scale_y = 0.7
                    for line in _wrap(item.description, 40)[:2]:
                        sub.label(text=line)

        if fl.busy:
            layout.label(text=fl.status or "Running ...", icon="SORTTIME")
        elif fl.status:
            box = layout.box()
            box.alert = bool(fl.status_is_error)
            for line in _wrap(fl.status, 38)[:4]:
                box.label(text=line,
                          icon="ERROR" if fl.status_is_error else "CHECKMARK")
        if fl.summary:
            row = layout.row()
            row.active = False
            row.label(text=fl.summary[:80], icon="MESH_DATA")


class VIEW3D_PT_forge_rigforge(_ForgePanel, Panel):
    """Stage 1 of RigForge: what the parts of the sculpt are called."""

    bl_idname = "VIEW3D_PT_forge_rigforge"
    bl_label = "RigForge"

    def draw(self, context):
        layout = self.layout
        rf = rigforge.get_props(context)
        if rf is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return

        obj = rigforge.active_mesh(context)
        row = layout.row(align=True)
        if obj is None:
            row.label(text="Select a mesh object", icon="INFO")
            return
        row.label(text=obj.name, icon="OUTLINER_OB_MESH")
        row.operator("forge.rf_sync", text="", icon="FILE_REFRESH")

        box = layout.box()
        box.label(text="Tags", icon="GROUP_VERTEX")
        tags = rigforge.tag_groups(obj)
        if not tags:
            box.label(text="No tags yet - name one below", icon="INFO")
        else:
            face_map = rigforge._face_tag_map(obj)
            for group in tags:
                name = rigforge.tag_display_name(group.name)
                verts = len(rigforge._group_vertices(obj, group.index))
                faces = sum(1 for marks in face_map.values() if group.index in marks)
                row = box.row(align=True)
                row.label(text="%s   %d f / %d v" % (name, faces, verts))
                row.operator("forge.rf_select_tag", text="", icon="RESTRICT_SELECT_OFF").tag = name
                op = row.operator("forge.rf_assign_tag", text="", icon="IMPORT")
                op.tag = name
                op.replace = False
                row.operator("forge.rf_remove_tag", text="", icon="X").tag = name

        row = box.row(align=True)
        row.prop(rf, "new_tag_name", text="")
        row.operator("forge.rf_new_tag", text="", icon="ADD")

        box = layout.box()
        box.label(text="Character", icon="ARMATURE_DATA")
        box.prop(rf, "character_name")
        box.prop(rf, "archetype")
        column = box.column(align=True)
        column.label(text="Motion notes:")
        column.prop(rf, "motion_notes", text="")
        if rf.motion_notes:
            sub = column.column(align=True)
            sub.active = False
            for line in _wrap(rf.motion_notes, 40)[:4]:
                sub.label(text=line)

        box = layout.box()
        box.label(text="Manifest", icon="FILE_TEXT")
        box.prop(rf, "manifest_path", text="")
        row = box.row(align=True)
        row.operator("forge.rf_manifest_save", text="Save", icon="FILE_TICK")
        row.operator("forge.rf_manifest_load", text="Load", icon="IMPORT")

        if rf.status:
            box = layout.box()
            box.alert = bool(rf.status_is_error)
            box.label(text=rf.status, icon="ERROR" if rf.status_is_error else "INFO")
        if rf.summary:
            layout.label(text=rf.summary, icon="MESH_DATA")


class VIEW3D_PT_forge_retopo(_ForgePanel, Panel):
    """Stage 2: sculpt in, game mesh out."""

    bl_idname = "VIEW3D_PT_forge_retopo"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "Retopo"

    def draw(self, context):
        layout = self.layout
        rf = rigforge.get_props(context)
        if rf is None:
            return

        column = layout.column(align=True)
        column.prop(rf, "platform", expand=True)
        column.prop(rf, "target_faces")
        if rf.target_faces <= 0:
            row = column.row()
            row.active = False
            row.label(text="Preset: %d faces" % rigforge.PLATFORM_TARGETS[rf.platform])
        column.prop(rf, "lods")

        box = layout.box()
        box.prop(rf, "bake_normals")
        sub = box.row()
        sub.active = rf.bake_normals
        sub.prop(rf, "bake_resolution")
        if rf.bake_normals:
            row = box.row()
            row.active = False
            row.label(text="Needs Cycles; skipped with a note if it fails")

        layout.operator("forge.rf_retopo", icon="MOD_REMESH", text="Retopologise")


class VIEW3D_PT_forge_uv(_ForgePanel, Panel):
    """Stage 3: seams from the tags, unwrap, pack."""

    bl_idname = "VIEW3D_PT_forge_uv"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "UV"

    def draw(self, context):
        layout = self.layout
        rf = rigforge.get_props(context)
        if rf is None:
            return

        column = layout.column(align=True)
        column.prop(rf, "uv_seams_from_tags")
        column.prop(rf, "uv_margin")
        sub = column.row()
        sub.active = not rf.uv_seams_from_tags
        sub.prop(rf, "uv_angle_limit")
        layout.operator("forge.rf_auto_uv", icon="UV", text="Unwrap")


class VIEW3D_PT_forge_rig(_ForgePanel, Panel):
    """Stage 4: metarig from the tags, Rigify generate, weight cleanup."""

    bl_idname = "VIEW3D_PT_forge_rig"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "Rig"

    def draw(self, context):
        layout = self.layout
        rf = rigforge.get_props(context)
        if rf is None:
            return
        obj = rigforge.active_mesh(context)

        column = layout.column(align=True)
        # the archetype lives on the object and in the manifest; this is the
        # same property the Character box edits, shown again where it decides
        # which Rigify template gets built.
        column.prop(rf, "archetype")
        column.prop(rf, "rig_preset")

        column = layout.column(align=True)
        column.operator("forge.rf_metarig", icon="ARMATURE_DATA", text="Place Metarig")
        row = column.row(align=True)
        metarig = str(rigforge._prop(obj, rigforge_rig.PROP_METARIG, "")) if obj else ""
        row.enabled = bool(metarig and metarig in bpy.data.objects)
        row.operator("forge.rf_generate_rig", icon="OUTLINER_OB_ARMATURE",
                     text="Generate Rig")
        if obj is not None and metarig:
            sub = column.row()
            sub.active = False
            sub.label(text="Metarig: %s" % metarig)

        box = layout.box()
        box.label(text="Weights", icon="MOD_VERTEX_WEIGHT")
        box.prop(rf, "max_influences")
        row = box.row(align=True)
        row.operator("forge.rf_weights", text="Report").action = "report"
        row.operator("forge.rf_weights", text="Cleanup").action = "cleanup"
        row.operator("forge.rf_weights", text="Normalize").action = "normalize"


class VIEW3D_PT_forge_cloth(_ForgePanel, Panel):
    """Stage 5: a garment grown from the tagged faces of the body."""

    bl_idname = "VIEW3D_PT_forge_cloth"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "Cloth"

    def draw(self, context):
        layout = self.layout
        ra = rigforge_anim.get_props(context)
        if ra is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return
        obj = rigforge.active_mesh(context)

        layout.prop(ra, "cloth_use_selection")
        box = layout.box()
        box.active = not ra.cloth_use_selection
        box.label(text="Covered tags", icon="GROUP_VERTEX")
        tags = rigforge.tag_groups(obj) if obj is not None else []
        chosen = set(rigforge_anim._selected_tags(ra))
        if not tags:
            box.label(text="No tags on this mesh yet", icon="INFO")
        else:
            column = box.column(align=True)
            for group in tags:
                name = rigforge.tag_display_name(group.name)
                row = column.row(align=True)
                row.operator(
                    "forge.rf_cloth_tag",
                    text=name,
                    icon="CHECKBOX_HLT" if name in chosen else "CHECKBOX_DEHLT",
                    depress=name in chosen,
                ).tag = name

        column = layout.column(align=True)
        column.prop(ra, "cloth_name")
        column.prop(ra, "cloth_output")
        sub = column.column(align=True)
        sub.active = ra.cloth_output == "shapekeys"
        sub.prop(ra, "cloth_preset")
        sub.prop(ra, "cloth_frames")
        sub.prop(ra, "cloth_self_collision")

        column = layout.column(align=True)
        column.prop(ra, "cloth_offset_mm")
        column.prop(ra, "cloth_thickness_mm")

        layout.operator("forge.rf_cloth", icon="MOD_CLOTH", text="Make Garment")
        row = layout.row()
        row.active = False
        row.label(text="The sim never ships: it bakes to a shape key")


class VIEW3D_PT_forge_actions(_ForgePanel, Panel):
    """Stage 6: the Godot action library."""

    bl_idname = "VIEW3D_PT_forge_actions"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "Actions"

    def draw(self, context):
        layout = self.layout
        ra = rigforge_anim.get_props(context)
        if ra is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return

        rig = rigforge_anim._rig_for(rigforge.active_mesh(context), {})
        current = None
        if rig is not None and rig.animation_data is not None:
            current = rig.animation_data.action
        row = layout.row(align=True)
        row.active = False
        row.label(text="Rig: %s" % (rig.name if rig is not None else "none found"),
                  icon="ARMATURE_DATA")

        box = layout.box()
        if not len(bpy.data.actions):
            box.label(text="No actions yet - name one below", icon="INFO")
        for action in sorted(bpy.data.actions, key=lambda a: a.name.lower()):
            start, end = action.frame_range
            row = box.row(align=True)
            row.operator(
                "forge.rf_action_select", text="",
                icon="RADIOBUT_ON" if action is current else "RADIOBUT_OFF",
                emboss=False,
            ).name = action.name
            row.label(
                text="%s   %d-%d" % (action.name, int(start), int(end)),
                icon="ACTION" if not rigforge_anim.is_loop(action.name) else "FILE_REFRESH",
            )
            if rigforge_anim.nla_usage(action):
                row.label(text="", icon="NLA")
            row.operator("forge.rf_action", text="", icon="NLA_PUSHDOWN"
                         ).action = "push_nla"
            op = row.operator("forge.rf_action", text="", icon="X")
            op.action = "delete"
            op.name = action.name

        column = layout.column(align=True)
        row = column.row(align=True)
        row.prop(ra, "action_name", text="")
        row.prop(ra, "action_loop", text="", icon="FILE_REFRESH", toggle=True)
        column.prop(ra, "action_source")
        row = column.row(align=True)
        row.operator("forge.rf_action", text="New", icon="ADD").action = "new"
        row.operator("forge.rf_action", text="Duplicate",
                     icon="DUPLICATE").action = "duplicate"

        box = layout.box()
        box.label(text="Retarget", icon="ANIM")
        box.prop(ra, "retarget_path", text="")
        row = box.row(align=True)
        row.prop(ra, "retarget_name", text="")
        row.prop(ra, "retarget_loop", text="", icon="FILE_REFRESH", toggle=True)
        box.operator("forge.rf_retarget", icon="IMPORT", text="Retarget Clip")
        row = box.row()
        row.active = False
        row.label(text=".bvh / .fbx you supply; FK chains receive it")

        if ra.status:
            box = layout.box()
            box.alert = bool(ra.status_is_error)
            box.label(text=ra.status, icon="ERROR" if ra.status_is_error else "INFO")
        if ra.summary:
            layout.label(text=ra.summary, icon="MESH_DATA")


class VIEW3D_PT_forge_godot(_ForgePanel, Panel):
    """Stage 7: deform-only bake and glTF for Godot."""

    bl_idname = "VIEW3D_PT_forge_godot"
    bl_parent_id = "VIEW3D_PT_forge_rigforge"
    bl_label = "Godot Export"

    def draw(self, context):
        layout = self.layout
        rf = rigforge.get_props(context)
        if rf is None:
            return

        layout.prop(rf, "export_path", text="")
        column = layout.column(align=True)
        column.prop(rf, "export_actions", expand=True)
        sub = column.row()
        sub.active = rf.export_actions == "selected"
        sub.prop(rf, "export_action_names", text="")

        column = layout.column(align=True)
        column.prop(rf, "export_lods")
        column.prop(rf, "root_motion")

        layout.operator("forge.rf_export_godot", icon="EXPORT", text="Export to Godot")
        row = layout.row()
        row.active = False
        row.label(text="Control bones are stripped; -loop clips auto-loop")


def _wrap(text, width):
    """Very small word wrapper - layout.label() does not wrap by itself."""
    lines = []
    for paragraph in str(text).splitlines():
        current = ""
        for word in paragraph.split():
            candidate = (current + " " + word).strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [""]


_CLASSES = (
    VIEW3D_PT_forge_assistant,
    VIEW3D_PT_forge_server,
    VIEW3D_PT_forge_partforge,
    VIEW3D_PT_forge_parameters,
    VIEW3D_PT_forge_checks,
    VIEW3D_PT_forge_segments,
    VIEW3D_PT_forge_export,
    VIEW3D_PT_forge_flows,
    VIEW3D_PT_forge_rigforge,
    VIEW3D_PT_forge_retopo,
    VIEW3D_PT_forge_uv,
    VIEW3D_PT_forge_rig,
    VIEW3D_PT_forge_cloth,
    VIEW3D_PT_forge_actions,
    VIEW3D_PT_forge_godot,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
