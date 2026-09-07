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
from ..tools import (
    assistant,
    buddy,
    flows,
    model,
    partforge,
    rigforge,
    rigforge_anim,
    rigforge_rig,
    services,
)

CATEGORY = "Forge"


class _ForgePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


def _empty(layout, sentence, icon="INFO"):
    """One plain sentence saying what to do when a box has nothing in it.

    Every box in this file that can be empty says something here.  An empty box
    with no words in it is the single most common way a beginner concludes the
    tool is broken, and one sentence is cheaper than any amount of documentation
    they will not read.
    """
    column = layout.column(align=True)
    column.scale_y = 0.8
    lines = _wrap(sentence, 36)
    column.label(text=lines[0], icon=icon)
    for line in lines[1:4]:
        column.label(text=line)
    return column


class VIEW3D_PT_forge_health(_ForgePanel, Panel):
    """Four dots at the very top: is everything Forge needs actually running?

    Deliberately above the Assistant, because "why did nothing happen when I
    pressed Send" is answered here and nowhere else.  Nothing in this panel does
    any work — the states are whatever the last refresh found, and the command
    socket is read straight out of this process.
    """

    bl_idname = "VIEW3D_PT_forge_health"
    bl_label = "Forge Status"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        sv = services.get_props(context)

        column = layout.column(align=True)
        for label, state, detail in services.rows(sv):
            row = column.row(align=True)
            row.label(text=label,
                      icon=services.STATE_ICONS.get(state, "RADIOBUT_OFF"))
            note = row.row()
            note.active = state == services.UP
            note.alignment = "RIGHT"
            note.label(text=_wrap(detail, 30)[0])

        row = layout.row(align=True)
        row.operator("forge.services_start", text="Start services", icon="PLAY")
        row.operator("forge.services_refresh", text="", icon="FILE_REFRESH")

        if sv is None:
            return
        if sv.busy:
            layout.label(text=sv.status or "Checking ...", icon="SORTTIME")
        elif sv.status:
            box = layout.box() if sv.status_is_error else layout.column(align=True)
            box.alert = bool(sv.status_is_error)
            for line in _wrap(sv.status, 38)[:3]:
                box.label(text=line, icon="ERROR" if sv.status_is_error else "INFO")
        elif not sv.checked:
            _empty(layout, "Press the refresh arrows to check.")


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
            for index, entry in enumerate(chat.log):
                you = entry.role == "you"
                lines = _wrap(entry.text, assistant.LOG_LINE_WIDTH)
                header = column.row(align=True)
                header.active = you
                # A check-in says so. An answer nobody asked for reads as a bug
                # unless the log names where it came from.
                if entry.check_in:
                    header.label(text="Check-in:" if not you else "You asked:",
                                 icon="HIDE_OFF")
                else:
                    header.label(text="You:" if you else "Forge:",
                                 icon="USER" if you else "LIGHT")
                if len(lines) > assistant.LOG_MAX_LINES:
                    # Truncated below: give them the whole thing in a window
                    # rather than a message that stops mid-sentence.
                    expand = header.row(align=True)
                    expand.active = True
                    expand.operator("forge.assistant_show_reply", text="",
                                    icon="ZOOM_IN").index = index
                body = column.column(align=True)
                body.scale_y = 0.75
                for line in lines[:assistant.LOG_MAX_LINES]:
                    body.label(text=line)
                if len(lines) > assistant.LOG_MAX_LINES:
                    more = column.row(align=True)
                    more.active = False
                    more.label(text="... %d more lines"
                               % (len(lines) - assistant.LOG_MAX_LINES))
                column.separator()
        else:
            _empty(layout,
                   "Type what you want in your own words - 'make me a phone stand'.",
                   icon="LIGHT")

        # The three jobs that come up over and over, as buttons. They send the
        # sentence an artist would have typed, so nothing about the answer is
        # different for having pressed a button.
        chips = layout.row(align=True)
        chips.enabled = not chat.busy
        for key, label, icon, _text in assistant.QUICK_ACTIONS:
            chips.operator("forge.assistant_quick", text=label, icon=icon).action = key

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

        # Attach a sketch or photo (Phase 6c). The folder icon is Blender's own
        # file browser; once a file is picked the row becomes a chip with an X,
        # because a 90-character path in a 40-character sidebar tells you
        # nothing and the filename tells you everything.
        attach = layout.column(align=True)
        attach.enabled = not chat.busy
        label = assistant.image_label(chat.image_path)
        if label:
            chip = attach.row(align=True)
            chip.label(text=label, icon="IMAGE_DATA")
            chip.operator("forge.assistant_clear_image", text="", icon="X")
            note = attach.row()
            note.active = False
            note.scale_y = 0.7
            note.label(text="sent with your next message")
        else:
            row = attach.row(align=True)
            row.prop(chat, "image_path", text="Picture")

        # How hard to think about this message. One compact row, right above
        # Send, because it is a property of the message being sent - not a mode
        # buried in preferences. The labels say the trade-off ("Fast", "Smart
        # (recommended)", "Deepest"); nothing here names a model.
        speed = layout.row(align=True)
        speed.enabled = not chat.busy
        speed.prop(chat, "model", text="")

        row = layout.row(align=True)
        if chat.busy:
            row.label(text=chat.status or "Thinking ...",
                      icon="SORTTIME" if not chat.queued else "TIME")
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

        # Buddy mode: a teacher's eyes on the work in progress. The button is
        # the whole feature for most people ("look at this now"); the toggle
        # underneath is the same thing on a timer, off by default, and it says
        # in words that each look costs a turn.
        bud = buddy.get_props(context)
        if bud is not None:
            box = layout.box()
            row = box.row(align=True)
            row.enabled = not chat.busy
            row.operator("forge.buddy_check", text="Check my work",
                         icon="HIDE_OFF")
            watch = box.row(align=True)
            watch.prop(bud, "enabled", text="Look every", toggle=True,
                       icon="TIME")
            sub = watch.row(align=True)
            sub.enabled = bud.enabled
            sub.prop(bud, "interval_minutes", text="")
            cost = box.row()
            cost.active = False
            cost.scale_y = 0.7
            cost.label(text="%s min. %s"
                            % (bud.interval_minutes, buddy.TURN_COST_NOTE))
            if bud.status:
                note = box.row()
                note.active = False
                note.scale_y = 0.7
                note.label(text=_wrap(bud.status, 36)[0], icon="INFO")

        # Undo, in words, for someone who does not know Ctrl+Z is undo: every
        # command the assistant ran pushed a named checkpoint before it ran.
        revert = layout.row(align=True)
        revert.enabled = not chat.busy
        revert.operator("forge.revert_ai", text="Revert last AI action",
                        icon="LOOP_BACK")

        footer = layout.row(align=True)
        footer.active = False
        total = assistant.cost_footer(chat)
        if total:
            footer.label(text=total)
        if chat.last_cost:
            last = footer.row()
            last.alignment = "RIGHT"
            last.label(text=chat.last_cost)


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

        if not str(props.script_path or "").strip():
            _empty(layout,
                   "No part yet - ask the Assistant for one, or set a script "
                   "path below.")

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
            _empty(layout, "Sliders appear here once a part is loaded.")
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
            _empty(layout, "Press Run Checks after generating a part.")
            return

        # The service's own verdict is kept as it sent it; what the panel SHOWS
        # discounts a bed_fit row that only needs cutting up, because a part
        # bigger than the plate is print planning and not a design failure.
        overall = partforge.design_overall(props)
        box = layout.box()
        box.alert = overall == "fail"
        box.label(
            text="Overall: %s" % (overall or "?").upper(),
            icon=partforge.CHECK_ICONS.get(overall, "QUESTION"),
        )
        if props.check_summary:
            box.label(text=props.check_summary)

        for item in props.checks:
            column = layout.column(align=True)
            column.label(text=item.name, icon=item.icon())
            sub = column.column(align=True)
            sub.active = item.status == "pass" or item.split
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

        if not props.segment_summary:
            _empty(layout,
                   "Cut a part into printable pieces here, or ask the Assistant.")

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
            # Not "your part is too big" — the part is the size it should be,
            # and this is simply how it comes off the plate.
            row = column.row()
            row.active = False
            row.label(text=partforge.suggested_split_label(props),
                      icon="MOD_BOOLEAN")

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


class VIEW3D_PT_forge_model(_ForgePanel, Panel):
    """Downloaded models: the same two questions, asked of geometry we did not make.

    Sits under PartForge because it answers the same things — will it print, and
    how do I cut it up — for a file the artist got from somewhere else.  The
    results land in the very same Print Checks and Segments boxes above.
    """

    bl_idname = "VIEW3D_PT_forge_model"
    bl_label = "Model (downloaded / imported)"

    def draw(self, context):
        layout = self.layout
        md = model.get_props(context)
        if md is None:
            layout.label(text="Scene properties unavailable", icon="ERROR")
            return
        layout.enabled = not md.busy

        column = layout.column(align=True)
        column.prop(md, "import_units", text="File is in")
        column.operator("forge.model_import", icon="IMPORT", text="Import Model")

        # Picture to 3D. One button, and it takes the picture from wherever the
        # artist put it: this field, or the one attached in the Assistant box.
        picture = layout.column(align=True)
        chat = assistant.get_props(context)
        attached = assistant.image_label(getattr(chat, "image_path", "")) if chat else ""
        own = assistant.image_label(md.image_path)
        if own:
            row = picture.row(align=True)
            row.label(text=own, icon="IMAGE_DATA")
        elif attached:
            row = picture.row(align=True)
            row.active = False
            row.label(text="%s (from the Assistant box)" % attached, icon="IMAGE_DATA")
            picture.prop(md, "image_path", text="Or")
        else:
            picture.prop(md, "image_path", text="Picture")
        generate = picture.row(align=True)
        generate.enabled = bool(own or attached) and not md.busy
        generate.operator("forge.model_generate3d", icon="SHADERFX",
                          text="Generate 3D from Picture")
        note = picture.row()
        note.active = False
        note.scale_y = 0.7
        note.label(text="about 5 minutes, then repaired")
        if md.busy and md.gen_stage:
            stage = picture.column(align=True)
            stage.scale_y = 0.8
            stage.label(text=_wrap(md.gen_stage, 36)[0], icon="SORTTIME")
            stage.label(text="the bar is this step, not the whole job")

        if not str(md.object_name or "").strip():
            _empty(layout,
                   "Import an STL you downloaded, or select your own mesh, then "
                   "press Check.")
        else:
            row = layout.row(align=True)
            row.label(text=md.object_name, icon="OUTLINER_OB_MESH")
            if md.face_count:
                note = row.row()
                note.active = False
                note.alignment = "RIGHT"
                note.label(text="%d faces" % md.face_count)

        column = layout.column(align=True)
        column.operator("forge.model_check", icon="CHECKMARK",
                        text="Check imported model")
        column.operator("forge.model_segment", icon="MOD_BOOLEAN",
                        text="Segment imported model")

        # Phase 11: the core and the proposals the artist kept are separate
        # objects right up until the slicer, where they have to be one.
        merge = layout.column(align=True)
        selected = [obj for obj in (getattr(context, "selected_objects", None) or [])
                    if obj.type == "MESH"]
        row = merge.row(align=True)
        row.enabled = bool(selected) and not md.busy
        row.operator("forge.model_merge", icon="MOD_OPACITY",
                     text="Merge for Print")
        hint = merge.row()
        hint.active = False
        hint.scale_y = 0.7
        hint.label(text=("%d selected -> one sealed shell" % len(selected))
                   if selected else "select the pieces to merge them")

        repair = layout.box()
        repair.alert = bool(md.needs_repair)
        if md.needs_repair:
            repair.label(text="This model has holes in it", icon="ERROR")
            sub = repair.column(align=True)
            sub.scale_y = 0.8
            for line in _wrap("Voxel Repair closes them by rebuilding the "
                              "surface, then check it again.", 36):
                sub.label(text=line)
        row = repair.row(align=True)
        row.prop(md, "voxel_size_mm")
        row.operator("forge.model_repair", icon="MOD_REMESH", text="Voxel Repair")

        if md.busy:
            layout.label(text=md.status or "Working ...", icon="SORTTIME")
        elif md.status:
            box = layout.box()
            box.alert = bool(md.status_is_error)
            for line in _wrap(md.status, 38)[:4]:
                box.label(text=line, icon="ERROR" if md.status_is_error else "INFO")
        if md.summary:
            row = layout.row()
            row.active = False
            row.label(text=md.summary[:80], icon="MESH_DATA")


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
        row.prop(fl, "editing", text="", icon="GREASEPENCIL", toggle=True)
        row.operator("forge.flow_refresh", text="", icon="FILE_REFRESH")

        if not fl.loaded:
            _empty(layout, "Press the refresh arrows to list them.")
        elif not len(fl.flows):
            _empty(layout,
                   "Saved one-button jobs appear here. The Assistant offers to "
                   "save repeatable work.")
            note = layout.row()
            note.active = False
            note.label(text=flows.flows_dir() or "(no flows folder set)")

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
            box.label(text="Parameters" if not fl.editing else "Default values",
                      icon="PRESET")
            for item in fl.params:
                box.prop(item, "value", text=item.label_text())
                if item.description:
                    sub = box.column(align=True)
                    sub.active = False
                    sub.scale_y = 0.7
                    for line in _wrap(item.description, 40)[:2]:
                        sub.label(text=line)

        if fl.editing and fl.selected:
            self._draw_editor(fl)

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

    def _draw_editor(self, fl):
        """The modest editor: what it says, what it does, in what order.

        Steps can be reordered and removed but not written from scratch — that
        is what the assistant and the JSON are for, and a half-built step editor
        would only be a worse way to reach the same file.
        """
        layout = self.layout
        box = layout.box()
        box.enabled = not fl.busy
        header = box.row(align=True)
        header.label(text="Editing %s" % fl.selected, icon="GREASEPENCIL")
        if fl.dirty:
            unsaved = header.row()
            unsaved.alert = True
            unsaved.label(text="unsaved", icon="ERROR")

        column = box.column(align=True)
        column.label(text="What it does:")
        column.prop(fl, "edit_description", text="")

        steps = box.column(align=True)
        steps.label(text="Steps", icon="SEQUENCE")
        if not len(fl.steps):
            _empty(steps, "This flow has no steps to show.")
        for index, item in enumerate(fl.steps):
            row = steps.row(align=True)
            row.label(text="%d. %s" % (index + 1, item.line()))
            up = row.row(align=True)
            up.enabled = index > 0
            move = up.operator("forge.flow_step_move", text="", icon="TRIA_UP")
            move.index = index
            move.direction = "UP"
            down = row.row(align=True)
            down.enabled = index < len(fl.steps) - 1
            move = down.operator("forge.flow_step_move", text="", icon="TRIA_DOWN")
            move.index = index
            move.direction = "DOWN"
            row.operator("forge.flow_step_delete", text="", icon="X").index = index

        note = box.row()
        note.active = False
        note.label(text="Ask the Assistant to add steps")

        row = box.row(align=True)
        save = row.row(align=True)
        save.enabled = len(fl.steps) >= 2
        save.operator("forge.flow_save", text="Save", icon="FILE_TICK")
        row.operator("forge.flow_revert", text="", icon="LOOP_BACK")
        if len(fl.steps) < 2:
            warn = box.row()
            warn.alert = True
            warn.label(text="A flow needs at least two steps", icon="ERROR")


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
        if obj is None:
            _empty(layout, "Select your sculpt and add a tag to start.")
            return
        row = layout.row(align=True)
        row.label(text=obj.name, icon="OUTLINER_OB_MESH")
        row.operator("forge.rf_sync", text="", icon="FILE_REFRESH")

        box = layout.box()
        box.label(text="Tags", icon="GROUP_VERTEX")
        tags = rigforge.tag_groups(obj)
        if not tags:
            _empty(box, "Select your sculpt and add a tag to start.")
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
    VIEW3D_PT_forge_health,
    VIEW3D_PT_forge_assistant,
    VIEW3D_PT_forge_server,
    VIEW3D_PT_forge_partforge,
    VIEW3D_PT_forge_parameters,
    VIEW3D_PT_forge_checks,
    VIEW3D_PT_forge_segments,
    VIEW3D_PT_forge_export,
    VIEW3D_PT_forge_model,
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
