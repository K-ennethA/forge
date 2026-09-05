# Forge Assistant

The chat box at the top of Blender's Forge sidebar, and the small local program
behind it.

## What it is for

Every other panel in this add-on assumes you already know what you want to
press. The assistant does not. It is for the artist who knows what the object
should *be* and does not know — or care — what Blender calls the operation.

The governing rule (from `docs/architecture.md`) is that every reply takes one
of three shapes:

1. **Did it.** The tools could do it, so it did, and it says what changed in
   plain words.
2. **Did 90%, here's the last bit.** It did everything the tools can, then gave
   the remainder as numbered beginner steps naming the exact panel and button.
3. **Can't do it — here's how you do it.** Sculpting, judgement calls, GUI-only
   work: a step-by-step walkthrough with every term explained inline.

The manual panels never go away. The assistant is a layer over them, not a
replacement for them.

That contract lives in `system_prompt.md`, which is appended to the CLI's system
prompt on every turn. **That file is the product.** Changing it changes how the
assistant behaves far more than changing any code here.

## Shape

```
Blender panel  --HTTP-->  assistant/bridge.py  --subprocess-->  claude -p ...
 (urllib,                  (stdlib only,                         (the user's own
  no deps)                  port 8901)                            subscription)
```

The bridge exists because the add-on cannot spawn a minutes-long subprocess and
poll it without freezing Blender's UI, and because the add-on must stay
dependency-free. The bridge is stdlib-only for the same reason in reverse: it
has to run under whatever Python is around, with no install step, so
`start_forge.cmd` can be a double-click.

## Endpoints (127.0.0.1:8901)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status": "ok", "claude_cli": {"found", "path", "version"?, "hint"?}, "cwd", "session", "busy", "queued", "session_cost_usd", "last_auth_error"}` |
| POST | `/ask` | `{"message", "context": {..., "image_path"?}, "conversation": "continue"\|"new"}` | `{"job_id", "state": "running", "queued": false}`, or `{"job_id", "state": "queued", "queued": true}` while a turn is running; **409** only when a message is *already* waiting; **503** if the CLI is missing; **400** for an empty message or an unusable `image_path` |
| GET | `/job/<id>` | — | `{"state": "queued"\|"running"\|"done"\|"error"\|"cancelled", "activity": [...], "session_cost_usd", "reply"?, "session_id"?, "cost_usd"?, "duration_ms"?, "model"?, "usage"?, "error"?}` |
| POST | `/cancel/<id>` | — | the job, now `cancelled` — works on a queued job too, which then never runs |
| POST | `/new` | — | `{"status": "ok", "session": null}` — forget the conversation without asking anything, and zero `session_cost_usd` |

One job at a time, on purpose: two turns in flight would fight over the same
resumed session. The last 20 jobs stay in memory; older ids 404.

## One message may wait its turn

A turn takes tens of seconds, and an artist who has already thought of the next
thing should not have to sit on their hands. So a second `/ask` while a turn is
running is **queued**, not refused:

- It gets a real job id **immediately** and `GET /job/<id>` answers
  `state: "queued"` straight away — the panel polls it like any other job rather
  than being handed an id that 404s.
- A **third** ask is the 409 (`{"error", "job_id", "state": "queued"}`, naming
  the one already waiting). A queue of one is a courtesy; a queue of ten is a
  way to lose track of what you asked for.
- The waiting message starts by itself when the running turn ends — **done,
  errored or cancelled**. Its state goes `queued` → `running` → `done`/`error`.
- It carries the prompt built when Send was pressed (the scene the artist was
  looking at) but resolves the **session id at pickup**, so it continues the
  conversation the turn ahead of it produced. A queued `conversation: "new"`
  drops the session at pickup too, never at enqueue — the running turn must
  still finish in its own session.
- `POST /cancel/<id>` on a queued job takes it out of the queue and lands it
  `cancelled`; it can never start afterwards. Cancelling the *running* job still
  lets the queued one through.

Exactly one turn is ever in flight. The queue is one deep, and that is the whole
of it.

## Cost and the sign-in signal

Both are additive fields for the panel's status row, and neither costs anything
to produce.

- **`session_cost_usd`** — the running total of `cost_usd` over turns that
  finished `done`, since the conversation started. It rides on **both**
  `/health` **and every `/job/<id>` snapshot**, so the panel can redraw the
  number off the poll it is already making. `POST /new` (and any
  `conversation: "new"` ask) resets it to `0.0`, because "this conversation" is
  what the number means. Failed and cancelled turns add nothing.
- **`last_auth_error`** — `true` when the most recently finished turn failed
  with a sign-in problem (`not logged in`, `/login`, `invalid api key`,
  `authentication_error` — the first `_FRIENDLY` class). It is **read off the
  error we already have**, never by running `claude -p` to test the login: a
  probe turn costs money and seconds to colour one status line. The next
  successful turn clears it. A cancelled turn leaves it alone — it proved
  nothing either way.

## Live activity (Phase 6b)

A turn takes tens of seconds. A spinner for tens of seconds looks broken. So the
bridge reads the CLI's event stream as it arrives and keeps a running list of
what the model is doing, which the panel draws under the busy indicator.

```jsonc
"activity": [
  {"t": 1764972041.213, "kind": "tool",   "label": "partforge_check: part.py"},
  {"t": 1764972048.771, "kind": "tool",   "label": "partforge_segment: overrides={\"bowl…"},
  {"t": 1764972061.004, "kind": "status", "label": "thinking…"},
  {"t": 1764972061.377, "kind": "text",   "label": "Done - I cut it into 4 wedges,"}
]
```

| kind | When | Label |
|---|---|---|
| `tool` | The model called a tool | The tool name with `mcp__forge__` stripped, plus a **≤60-character** summary of its most identifying argument (a path is shown as its basename). One entry per call: the name arrives first and the arguments are filled into the *same* line when they finish streaming. |
| `status` | Text starts flowing after at least one tool ran | `thinking…` — exactly once per turn |
| `text` | The reply is being written | The text that arrived since the last marker, clipped to 60 characters, at most **one every 2 s** (`FORGE_ASSISTANT_TEXT_INTERVAL`) |

- `GET /job/<id>` returns `activity` **always** — running, done, errored or
  cancelled — and the finished job keeps it, so the last lines stay readable
  after the answer lands. A run that produced no events (an older CLI printing
  one JSON object) returns `[]` rather than failing.
- The list is capped at **200 entries**. Past that the *middle* is dropped —
  the first 20 say how the turn started, the tail says what it is doing now —
  and a synthetic `status` line in their place says how many are missing
  (`activity_dropped` carries the count).
- Parsing is forgiving on purpose. A line that is not JSON, an event shape this
  bridge has never seen, a half-written object: skipped, never fatal. The
  activity list is a nicety; the answer is not.

## Reference images (Phase 6c)

An artist can attach a sketch or a photo in the panel. The **path** travels — the
image itself never passes through this process — as `context.image_path` on
`/ask`, and the bridge appends it to the message body as its own block:

```
what should the wall thickness be?

--- Current Blender context ---
Active object: Cup (MESH, 120 x 120 x 90 mm)
...

--- Attached reference image ---
C:\Users\you\Pictures\bowl-sketch.png
View this image with the Read tool BEFORE answering.
```

Three decisions worth knowing:

- **It is the last block in the prompt**, after the scene context, so the
  instruction to look at the picture is the last thing read before the work
  starts. `image_path` is taken *out* of the context dict first, so it appears
  once as an explained block rather than twice — once as a bare "Image path:"
  line nobody told the model what to do with.
- **The allow-list does not change.** `Read` is already permitted and Claude
  Code's Read tool renders images, so a picture needs no new permission. The
  test suite asserts `--allowedTools` is still `Read,Glob,Grep,mcp__forge__*`
  with an image attached.
- **The path is checked here too**, not only in the panel: it is expanded and
  made absolute, must be a file that exists, and must be one of `.png`, `.jpg`,
  `.jpeg`, `.webp`, `.bmp` (a folder is reported as a folder whatever it is
  called). Anything else is a **400 with a plain sentence** before a turn is
  spent — "I couldn't see your image" three minutes later is the worst possible
  way to learn the path was wrong.

The panel sends one message per attachment and clears the field afterwards, and
`system_prompt.md`'s **Reference images** section is what the model does with it:
extract the feature list, the proportions and the style intent into parameters,
anchor them to one real dimension, and **never trace pixels**. `load_reference`
(the MCP tool, backed by the add-on's socket command of the same name) is the
other half — it puts the same picture in the viewport so the artist can compare
the model against it.

## The command line it builds

```
<claude> -p "<message>\n\n--- Current Blender context ---\n<context lines>\n\n--- Attached reference image ---\n<path>\n<read instruction>"
         --output-format stream-json --verbose --include-partial-messages
         --append-system-prompt-file assistant/system_prompt.md
         --allowedTools "Read,Glob,Grep,mcp__forge__*"
         --permission-mode auto
         [--model <FORGE_ASSISTANT_MODEL>]
         [--resume <session_id>]          # every turn after the first
```

run with `cwd` = the repo root. Five details are load-bearing:

- **`stream-json`, not `json`.** This is what makes the activity list possible:
  the CLI prints one JSON event per line as it works instead of a single object
  at the end. `--verbose` is required by the CLI alongside it in `-p` mode, and
  `--include-partial-messages` is what turns the reply into text deltas — drop
  it and the panel goes silent while the model writes. The final
  `type: "result"` line carries exactly the fields `--output-format json` used
  to (`result`, `session_id`, `total_cost_usd`, `usage`, `model`), so nothing
  downstream changed.

- **cwd is the repo root** so `.mcp.json` is discovered. Project-scope MCP
  servers load automatically in `-p` mode with no approval gate, and CLI
  sessions are scoped per directory — run it elsewhere and both the Forge tools
  and `--resume` quietly stop working.
- **The context goes in the message, not the system prompt.** The scene changes
  every turn; the philosophy does not.
- **`mcp__forge__*`** — `forge` is the server name in `.mcp.json`. Rename the
  server there and this must follow.
- **No `--dangerously-skip-permissions`.** `--permission-mode auto` plus an
  explicit allow-list is the whole permission story. If an older CLI rejects
  `auto`, the bridge retries with `acceptEdits`, then with the flag omitted.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `FORGE_ASSISTANT_PORT` | `8901` | Listen port (loopback only) |
| `FORGE_ASSISTANT_CLAUDE` | discovered | Full path to the CLI, skipping discovery |
| `FORGE_ASSISTANT_MODEL` | unset | `--model` value; unset = the user's configured default |
| `FORGE_ASSISTANT_TIMEOUT` | `600` | Seconds one turn may take |
| `FORGE_ASSISTANT_TOOLS` | the list above | `--allowedTools` value; set it to `""` for a no-tools run |
| `FORGE_ASSISTANT_CWD` | the repo root | Working directory for the CLI |
| `FORGE_ASSISTANT_TEXT_INTERVAL` | `2.0` | Seconds between `text` activity markers; `0` records every chunk (the tests use it) |
| `FORGE_ASSISTANT_VERBOSE` | unset | Print an access log |

## Finding the CLI on Windows

In order: `FORGE_ASSISTANT_CLAUDE`, then `shutil.which` for `claude` /
`claude.cmd` / `claude.exe`, then the known install locations — newest version
first under `%APPDATA%\Claude\claude-code\<version>\claude.exe`, then
`%APPDATA%\npm\claude.cmd`, `%LOCALAPPDATA%\Programs\claude\claude.exe`,
`~/.local/bin/claude`.

On this machine the CLI is the native install and is **not on PATH**, which is
why the discovery list exists rather than a bare `which`.

Two Windows facts the bridge is built around:

- `CREATE_NO_WINDOW` on every spawn, or each turn flashes a console at the
  artist.
- Cancel is `proc.terminate()`. `SIGINT` is not reliably deliverable to a child
  on Windows.

One caveat worth knowing: if the resolved CLI is an npm `claude.cmd` shim, the
prompt travels through `cmd.exe`, which cannot carry a newline inside a single
argument — so the context block could be mangled. A native `claude.exe` (which
the discovery order prefers over an npm shim only when PATH does not shadow it)
has no such problem.

## Robustness

- **CLI missing** — `/health` says `found: false` with an install hint, and
  `/ask` answers 503 with the same hint instead of starting a job.
- **Noisy stdout** — a node warning printed between events is skipped by the
  line parser; a build that answers with one plain JSON object instead of a
  stream is still read (the object *is* the last parseable line). Failing all
  of that, the stderr tail is surfaced verbatim rather than a shrug.
- **No result event** — if the stream ends with the process exiting 0 and no
  `type: "result"` line, the last parseable line is used, and if that carries no
  reply the text that streamed past becomes the reply. An answer the artist can
  read beats a correct complaint about a missing event.
- **Cancel mid-stream** — `POST /cancel/<id>` terminates the process; the pipe
  closes, the read loop ends, and the job finishes `cancelled` **keeping the
  activity it had gathered**, so it is still visible what it got through.
- **Timeout** — a watchdog thread, not a read deadline, because `readline`
  blocks: it terminates the process and the loop ends on its own.
- **A stranded queue** — the pickup runs in a `finally` on the worker thread, so
  however the turn ended (including a crash the bridge never anticipated) the
  message waiting behind it still gets its turn. A turn whose thread somehow
  left the job `running` is landed as an error rather than holding the slot
  forever.
- **Not signed in / rate limited** — rewritten into a sentence an artist can act
  on, with the CLI's own words kept in parentheses.
- **`pythonw.exe`** — `sys.stderr` is `None` there, so every diagnostic goes
  through `log()`. Writing to `sys.stderr` directly killed the process on its
  first line during development, and a headless test now pins that down.

## Tests

```
service\.venv\Scripts\python.exe -m pytest assistant\tests -q
```

`assistant/tests/fake_claude.py` stands in for the CLI. `FORGE_ASSISTANT_CLAUDE`
points at it, and because it is a `.py` path the bridge runs it under its own
interpreter (`bridge.launcher`) rather than through a `.cmd` wrapper — deliberate,
since `cmd.exe` cannot carry the multi-line prompt.

The fake does two jobs: it **fails loudly** if the command line lost a flag that
makes the assistant work (`-p`, `--output-format stream-json`, `--verbose`,
`--include-partial-messages`, `--allowedTools`, a real
`--append-system-prompt-file`), and it **logs every argv** to `FAKE_CLAUDE_LOG`
so tests can assert after the fact — most usefully that turn two carried
`--resume`. `FAKE_CLAUDE_MODE` picks what is being rehearsed:

| Mode | What it prints |
|---|---|
| `ok` (default) | one `--output-format json`-shaped result object |
| `noise` | a node warning line, then that object |
| `garbage` | no JSON at all, exit 1 |
| `slow` | sleeps `FAKE_CLAUDE_SLEEP` first (cancel, busy) |
| `reject_permission` | exits 1 on anything but `--permission-mode acceptEdits` |
| `api_error` | the CLI's `is_error` result shape |
| `auth_error` | the `is_error` shape a signed-out CLI produces, so `last_auth_error` has something real to read |
| `stream` | the real event sequence: two tool calls (one with its arguments arriving as `input_json_delta` chunks), a deliberately malformed line, text deltas, then the result event |
| `stream_noresult` | that stream with the result withheld, exit 0 — the salvage path |
| `stream_slow` | that stream, sleeping after the tool events so a cancel lands mid-stream |

`FAKE_CLAUDE_AUTH_FILE` names a flag file: while it exists every run behaves as
`auth_error`, and deleting it signs the fake back in. A mode is fixed for the
life of a bridge process, so a file is the only way one test can watch
`last_auth_error` go true and then clear again.

`FAKE_CLAUDE_EXPECT_IMAGE` asserts on the attachment block: set to a path, the
prompt must carry it under `--- Attached reference image ---` with the Read
instruction and `Read` still in `--allowedTools`; set to the empty string, the
prompt must carry no attachment block at all.

`FAKE_CLAUDE_STREAM=1` (or a flag file named by `FAKE_CLAUDE_STREAM_FILE`)
turns streaming on without naming a mode. The output shape and the flags the
bridge must pass are deliberately independent: the bridge always asks for
stream-json, and the json modes prove it still copes with a build that answers
with one object anyway.

One real turn against the live CLI runs only when you ask for it:

```
set FORGE_ASSISTANT_SMOKE=1
service\.venv\Scripts\python.exe -m pytest assistant\tests -q -k smoke
```

It sends five words to Haiku with no tools, so it costs about nothing. Auth and
rate-limit failures skip rather than fail — those are facts about the machine,
not bugs in the bridge.

The panel side is `addon/tests/headless_assistant.py` (Blender `--background`,
port 9882) and `addon/tests/headless_reference.py` (port 9886 — the attach
field, its validation, and the `load_reference` command), which drives the operators against a fake bridge written into the
harness. The two suites never overlap: that one is about the panel, this one is
about the command line.

## Running it

`start_forge.cmd` at the repo root starts this and the geometry service, each
only if its port is free. `stop_forge.cmd` stops them again. The Blender side
points at `http://127.0.0.1:8901` by default; the address is an add-on
preference (Edit → Preferences → Add-ons → Forge → Assistant).
