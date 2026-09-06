"""Convert ComfyUI's official 3d_pixal3d_trellis2_image_to_model template from
the UI 'workflow' format into the API 'prompt' format /prompt accepts, keeping
only the nodes that actually feed the save node.

Run once with ComfyUI listening on 8188 - /object_info is the authority on
input declaration order, which inputs are widgets, and how dynamic combos
expand.

Three rules the naive converters get wrong, all of them load-bearing here:

1. A widget that has been dragged out into a socket STILL occupies its slot in
   `widgets_values`.  It is marked by a `widget: {name}` key on the socket.
   Skipping its slot shifts every later value by one.
2. `COMFY_DYNAMICCOMBO_V3` consumes its own slot plus one per sub-input of the
   SELECTED option, and serialises flat (sibling keys) in the API format.
3. An INT with `control_after_generate` eats an extra frontend-only slot.

The script asserts every widgets_values entry is consumed, so a future template
that breaks these assumptions fails loudly instead of producing a subtly wrong
graph.
"""
import json
import sys
import urllib.request
from pathlib import Path

TEMPLATE = Path(sys.argv[1])
OUT_PATH = Path(sys.argv[2])
OBJ_URL = "http://127.0.0.1:8188/object_info"

# Frontend-only annotation nodes the backend does not know at all.
ANNOTATION = {"Note", "MarkdownNote", "Reroute"}
# The node the headless service wants.  NOTE: do NOT prune by "looks like a
# preview node" - in this template PreviewImage / MaskPreview / GetMeshInfo are
# wired as PASS-THROUGH nodes (ApplyTextureToMesh reads base_color straight out
# of a PreviewImage), so reachability is the only safe rule.
KEEP_ROOT = "322"  # Save3DAdvanced

PRIMITIVE_WIDGETS = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"


def opts_of(definition):
    if isinstance(definition, list) and len(definition) > 1 and isinstance(definition[1], dict):
        return definition[1]
    return {}


def type_of(definition):
    return definition[0] if isinstance(definition, list) and definition else definition


def main():
    with urllib.request.urlopen(OBJ_URL, timeout=180) as r:
        obj = json.loads(r.read().decode("utf-8"))

    wf = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    link_src = {link[0]: (link[1], link[2]) for link in wf["links"]}

    api = {}
    problems = []
    for node in wf["nodes"]:
        ntype = node["type"]
        if ntype in ANNOTATION:
            continue
        if node.get("mode") in (2, 4):
            print(f"  skip muted/bypassed {node['id']} {ntype}")
            continue
        spec = obj.get(ntype)
        if spec is None:
            problems.append(f"unknown node type {ntype} (id {node['id']})")
            continue

        # sockets: pure link inputs (never consume a widget slot)
        # converted: widget-turned-socket (consumes a slot AND takes the link)
        sockets, converted = {}, {}
        for slot in node.get("inputs") or []:
            target = converted if "widget" in slot else sockets
            target[slot["name"]] = slot.get("link")

        widgets = list(node.get("widgets_values") or [])
        cursor = [0]

        def take():
            if cursor[0] >= len(widgets):
                return None, False
            value = widgets[cursor[0]]
            cursor[0] += 1
            return value, True

        inputs = {}
        declared = {}
        input_spec = spec.get("input") or {}
        for section in ("required", "optional"):
            for name, definition in (input_spec.get(section) or {}).items():
                declared[name] = definition

        for name, definition in declared.items():
            kind = type_of(definition)
            options = opts_of(definition)

            if name in sockets:  # pure link, no widget slot
                link = sockets[name]
                if link is not None:
                    origin, slot_idx = link_src[link]
                    inputs[name] = [str(origin), slot_idx]
                continue

            is_widget = (
                isinstance(kind, list)
                or kind in PRIMITIVE_WIDGETS
                or kind == DYNAMIC_COMBO
                or name in converted
            )
            if not is_widget:
                # A custom widget type we do not model (e.g. LOAD_3D viewport
                # state).  It still owns a slot in widgets_values.
                if name not in sockets and cursor[0] < len(widgets):
                    value, ok = take()
                    if ok:
                        inputs[name] = value
                continue

            value, ok = take()
            if not ok:
                continue

            if kind == DYNAMIC_COMBO:
                inputs[name] = value
                selected = None
                for option in options.get("options") or []:
                    if option.get("key") == value:
                        selected = option
                        break
                if selected is None:
                    problems.append(f"{ntype}#{node['id']}: dynamic combo {name}={value!r} has no option")
                else:
                    # Sub-inputs are namespaced "<combo>.<sub>" in the API format
                    # (ComfyUI's validator reports them as sign_mode.qef).
                    sub = selected.get("inputs") or {}
                    for sub_section in ("required", "optional"):
                        for sub_name in (sub.get(sub_section) or {}):
                            sub_value, sub_ok = take()
                            if sub_ok:
                                inputs[f"{name}.{sub_name}"] = sub_value
                continue

            if name in converted and converted[name] is not None:
                origin, slot_idx = link_src[converted[name]]
                inputs[name] = [str(origin), slot_idx]  # link beats the stored widget value
            else:
                inputs[name] = value

            # Frontend-only companion widgets that own a slot with no backend
            # input: the seed's 'fixed'/'randomize' control, and LoadImage's
            # upload-type selector ('image'/'mask').
            if options.get("control_after_generate") or options.get("image_upload"):
                take()

        leftover = widgets[cursor[0]:]
        if leftover:
            problems.append(
                f"{ntype}#{node['id']}: {len(leftover)} unconsumed widgets_values {leftover!r}"
            )

        api[str(node["id"])] = {
            "class_type": ntype,
            "inputs": inputs,
            "_meta": {"title": node.get("title") or ntype},
        }

    print(f"converted {len(api)} nodes")

    assert KEEP_ROOT in api, f"{KEEP_ROOT} missing from the converted graph"
    keep, stack = set(), [KEEP_ROOT]
    while stack:
        nid = stack.pop()
        if nid in keep:
            continue
        keep.add(nid)
        for value in api[nid]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                stack.append(value[0])

    for nid in sorted(set(api) - keep, key=int):
        print(f"  drop (not feeding {KEEP_ROOT}) {nid} {api[nid]['class_type']}")
        del api[nid]

    print(f"pruned graph: {len(api)} nodes")
    if problems:
        print("\n!! PROBLEMS")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(api, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", OUT_PATH)


main()
