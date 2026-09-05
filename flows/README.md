# Flows — saved sequences that replay without an AI

A flow is a job you have already had done once, written down so it can be done
again the same way: a list of Forge operations with parameters, stored as JSON in
this folder and run from a button.

**No model is involved when a flow runs.** That is the point. The assistant is
worth a turn the first time, when it has to work out *what* to do; it is a waste
of a turn — and of determinism — the tenth time. Once the sequence is understood
it becomes a file in here, and after that it costs nothing and does exactly the
same thing every time.

Flows are plain text under git, so a flow that worked last month still works, and
`git diff` shows what changed if it stops.

## Running one

- **In Blender:** press <kbd>N</kbd> → **Forge** tab → **Flows** box → the
  refresh button → pick a flow → **Run**. Its parameters appear underneath and
  are editable before you press it.
- **From the assistant / Claude Code:** `flow_list` to see them, `flow_run` to
  run one, `flow_save` to write a new one.
- **Over the socket:** `{"type": "flow_run", "params": {"name": "segment-into-4",
  "params": {"wedges": 6}}}`.

## The format

```json
{
  "name": "segment-into-4",
  "description": "One sentence an artist can read.",
  "params": {
    "wedges": {"value": 4, "unit": "count", "description": "How many wedges"}
  },
  "steps": [
    {"kind": "service", "op": "/segment", "label": "Cut it up",
     "args": {"script_path": "", "mode": {"radial": "{{wedges}}"},
              "include_mesh": true}},
    {"kind": "blender", "op": "load_meshes", "label": "Show the pieces",
     "args": {"meshes": "{{steps.0.result.segments}}",
              "plate": "{{steps.0.result.plate}}"}}
  ]
}
```

| Field | Meaning |
|---|---|
| `name` | The flow's name. The filename is `<name>.json`; keep them the same. |
| `description` | What it does, in one sentence. This is the tooltip in the panel. |
| `params` | `{name: {"value": <default>, "unit"?: "mm", "description"?: "..."}}`. `value` is both the default and the declared type. |
| `steps` | A list, run in order, stopping at the first failure. |
| `steps[].kind` | `"blender"` (a Forge socket command) or `"service"` (a geometry-service endpoint). |
| `steps[].op` | The command name (`load_meshes`, `remesh`, …) or the endpoint (`/segment`, `/check`, …). |
| `steps[].args` | The command's params / the endpoint's request body, with `{{...}}` placeholders. |
| `steps[].label` | What the step is called in the panel and in error messages. Write it for a human. |
| `steps[].args.timeout_s` | Optional, service steps only: seconds this call may take, overriding the endpoint's default budget. |

Every `op` is validated against the real command registry / endpoint list before
a flow runs, so a typo is an error naming what exists rather than a silent skip.

## Placeholders

`{{name}}` inside an argument is filled in when the flow runs.

- **A whole value** — `"mode": {"radial": "{{wedges}}"}` — becomes the *typed*
  value: the number `4`, not the string `"4"`.
- **Inside a longer string** — `"label": "Cut into {{wedges}} pieces"` — is a
  text substitution.
- **An earlier step's result** — `"{{steps.0.result.segments}}"` — is a dotted
  path into the result of a step that has already run. Steps are numbered from
  **0**. Dotted lookup only: field names and list indices, no arithmetic, no
  expressions, no functions. If a path does not exist the flow stops and the
  error says which field was missing and what was there instead.

Unknown placeholders are an error listing the flow's actual parameters — a
misspelled parameter never silently becomes an empty string.

## Two conveniences for service steps

Flow JSON never carries a copy of a part script or a printer profile. Instead:

| Argument | Becomes | Blank means |
|---|---|---|
| `script_path` | `script` (the file's source text) | the script the PartForge panel is currently pointed at |
| `printer_path` | `printer` (the parsed JSON object) | the add-on's **Printer Profile** preference, or nothing at all — then the service uses its own Elegoo Centauri Carbon default |

This is what makes one saved flow work on every part: `"script_path": ""` reads
"whatever I am working on right now".

## Writing one by hand

1. Copy `segment-into-4.json` to `<your-name>.json` in this folder.
2. Change `name` to match the filename, and rewrite `description` for a person.
3. Edit the steps. Command names come from `addon/README.md` (the Commands
   table); endpoints from `docs/architecture.md` (the geometry service API).
4. In Blender, press the refresh button in the Flows box — it re-reads this
   folder. A file that will not parse is *listed with its error* rather than
   quietly disappearing.

Or let the assistant write it: finish a multi-step job, then say "save that as a
flow". It calls `flow_save`, which validates every step and writes the file here
— the only place it is allowed to write.

## Editing one from the panel

The pencil toggle in Blender's **Flows** box edits the selected flow without
opening this folder: its description, its **default** parameter values, and the
order of its steps (up/down arrows and an X per step). Nothing is written until
**Save** — the header says *unsaved* meanwhile, and the loop-back button next to
Save throws the edits away and re-reads the file.

Two things the panel preserves that hand-editing gets wrong: each default is
written back **typed like the one it replaced** (a `wedges` of `4` stays a
number when you type `6`), and the whole document is revalidated before it is
written, so a flow that would not load cannot be saved. Saving fewer than two
steps is refused, exactly as `flow_save` refuses it.

Steps cannot be *created* there — writing a step means knowing a command name
and its arguments, which is what this file and the assistant are for.

## What is here

| Flow | What it does |
|---|---|
| `segment-into-4.json` | Cuts the current part into 4 radial wedges with dovetail joints (`/segment`, `include_mesh: true`) and loads every piece into Blender at its packed plate position. The count, joint type, tolerance and target collection are all parameters, so "cut it into 6 with magnets instead" is the same flow with different numbers. |
