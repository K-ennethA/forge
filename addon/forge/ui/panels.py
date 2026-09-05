"""Forge sidebar (N-panel) UI: server status and the PartForge parameter panel."""

import bpy
from bpy.types import Panel

from .. import server
from ..prefs import get_prefs, service_url
from ..tools import partforge

CATEGORY = "Forge"


class _ForgePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


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
    VIEW3D_PT_forge_server,
    VIEW3D_PT_forge_partforge,
    VIEW3D_PT_forge_parameters,
    VIEW3D_PT_forge_checks,
    VIEW3D_PT_forge_segments,
    VIEW3D_PT_forge_export,
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
