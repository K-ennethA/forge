"""Forge - Blender side of the Claude-driven 3D pipeline.

Exposes a newline-delimited JSON command socket (default ``127.0.0.1:9876``)
that the Forge MCP server drives, plus the PartForge panel that talks to the
Build123d geometry service over HTTP.

Zero third-party dependencies: Python standard library + bpy/bmesh only.
"""

bl_info = {
    "name": "Forge",
    "author": "Forge",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Forge",
    "description": (
        "Command socket for Claude/MCP plus the PartForge parametric panel "
        "(Build123d geometry service)."
    ),
    "warning": "",
    "doc_url": "",
    "category": "3D View",
}

if "bpy" in locals():  # re-enable / reload inside a running Blender
    import importlib

    prefs = importlib.reload(prefs)  # noqa: F821
    tools = importlib.reload(tools)  # noqa: F821
    server = importlib.reload(server)  # noqa: F821
    ui = importlib.reload(ui)  # noqa: F821
else:
    from . import prefs
    from . import tools
    from . import server
    from . import ui

import bpy  # noqa: E402
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty  # noqa: E402
from bpy.types import AddonPreferences  # noqa: E402


class ForgePreferences(AddonPreferences):
    bl_idname = __package__

    host: StringProperty(
        name="Host",
        description="Interface the command socket binds to. Keep this on loopback",
        default="127.0.0.1",
    )
    port: IntProperty(
        name="Port",
        description="TCP port for the Forge command socket (restart the server after changing)",
        default=9876,
        min=1024,
        max=65535,
    )
    autostart: BoolProperty(
        name="Start Server With Blender",
        description="Start the command socket automatically when the add-on loads",
        default=False,
    )
    service_url: StringProperty(
        name="Geometry Service URL",
        description="Base URL of the Build123d geometry service used by the PartForge panel",
        default="http://127.0.0.1:8765",
    )
    printer_path: StringProperty(
        name="Printer Profile",
        description=(
            "printer.json used by the Print Checks and Segments panels. "
            "Blank = the geometry service's built-in Elegoo Centauri Carbon profile"
        ),
        default=prefs.default_printer_path(),
        subtype="FILE_PATH",
    )
    request_timeout: FloatProperty(
        name="Service Timeout (s)",
        description="How long a PartForge HTTP request may take before it is abandoned",
        default=120.0,
        min=1.0,
        max=3600.0,
    )
    job_timeout: FloatProperty(
        name="Command Timeout (s)",
        description="How long a socket command may wait for Blender's main thread",
        default=600.0,
        min=5.0,
        max=7200.0,
    )
    verbose: BoolProperty(
        name="Verbose Logging",
        description="Print every command to the system console",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        status = server.get_status()

        box = layout.box()
        box.label(text="Command Socket", icon="PLUGIN")
        row = box.row(align=True)
        row.prop(self, "host")
        row.prop(self, "port")
        row = box.row(align=True)
        row.operator("forge.start_server", icon="PLAY")
        row.operator("forge.stop_server", icon="PAUSE")
        if status["running"]:
            box.label(text="Listening on %s:%d" % (status["host"], status["port"]), icon="CHECKMARK")
            if (status["host"], status["port"]) != (self.host, self.port):
                box.label(text="Restart the server to use the new host/port", icon="INFO")
        else:
            box.label(text="Not running", icon="RADIOBUT_OFF")
        box.prop(self, "autostart")
        box.prop(self, "job_timeout")

        box = layout.box()
        box.label(text="Geometry Service (PartForge)", icon="URL")
        box.prop(self, "service_url")
        box.prop(self, "printer_path")
        box.prop(self, "request_timeout")

        layout.prop(self, "verbose")


_CLASSES = (ForgePreferences,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    tools.register()
    server.register()
    ui.register()


def unregister():
    ui.unregister()
    server.unregister()
    tools.unregister()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
