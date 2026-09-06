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
| POST | `/ask` | `{"message", "context": {..., "image_path"?}, "conversation": "continue"\|"new", "model"?: "haiku"\|"sonnet"\|"opus"}` | `{"job_id", "state": "running", "queued": false}`, or `{"job_id", "state": "queued", "queued": true}` while a turn is running; **409** only when a message is *already* waiting; **503** if the CLI is missing; **400** for an empty message or an unusable `image_path` |
| GET | `/job/<id>` | — | `{"state": "queued"\|"running"\|"done"\|"error"\|"cancelled", "activity": [...], "session_cost_usd", "reply"?, "session_id"?, "cost_usd"?, "duration_ms"?, "model"?, "requested_model"?, "usage"?, "error"?}` |
| POST | `/cancel/<id>` | — | the job, now `cancelled` — works on a queued job too, which then never runs |
| POST | `/new` | — | `{"status": "ok", "session": null}` — forget the conversation without asking anything, and zero `session_cost_usd` |

One job at a time, on purpose: two turns in flight would fight over the same
resumed session. The last 20 jobs stay in memory; older ids 404.

The bridge serves a second surface as well — a web UI at `/`, with its own
routes (`/jobs`, `/file/<token>`, `/upload`, `/services/*`, `/flows`). They are
additive, the panel never calls them, and they are documented under
[The web UI (Phase 9)](#the-web-ui-phase-9--the-second-surface).

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

## Choosing the model per message

`POST /ask` takes an optional `"model"`, one of **`haiku`**, **`sonnet`** or
**`opus`** — the panel's Fast / Smart (recommended) / Deepest selector, which
rides with every message including the quick-action chips. It is validated here:
anything else (a model id, a typo, a number) is a **400 naming the three on
offer**, before a turn is spent, and the bridge is not left busy. Whitespace and
case are forgiven; an absent or empty value means "nothing was asked for". What
wins, in order:

1. the request's `model`,
2. `FORGE_ASSISTANT_MODEL` (the escape hatch, and what a terminal user sets),
3. nothing — no `--model` flag at all, so the CLI uses the user's own default.

The choice belongs to the *message*, not to the bridge: it is stored on the job,
so a message queued as Fast still runs as Fast even if the panel's selector
moved while it waited. `GET /job/<id>` echoes it back as **`requested_model`**
(readable while the job is still queued or running) alongside `model`, which is
what the CLI says it actually ran.

**Switching model mid-conversation is fine and needs no special handling.** The
next turn still carries `--resume`, and the CLI continues the same conversation
under the newly named model — so an artist can spend four Haiku turns exporting
and segmenting, then hand the same thread to Opus for the part that is actually
hard, without losing what was already said.

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

## The web UI (Phase 9) — the second surface

`http://127.0.0.1:8901/` in a browser is the same assistant as the Blender
panel: the same session, the same job list, the same `/ask`. Ask something in
the panel and it appears in the page; ask it in the page and the panel's poll
picks it up. There is no second history — `/jobs` **is** the history.

It exists because Blender's sidebar is 300 px wide and a conversation with
pictures in it is not. The page is what you leave open on the second monitor.

The whole of it is `assistant/webui/` — `index.html`, `app.css`, `app.js`,
`format.js`. Vanilla, no build step, **no CDN**, no framework: it is served by a
stdlib HTTP server on a machine that may have no internet, and it has to still
open in five years without a toolchain being alive to rebuild it. A test fetches
all four files from a running bridge and fails if any of them names an
`http://` resource.

### Its routes

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/` | — | the page (`assistant/webui/index.html`) |
| GET | `/webui/<asset>` | — | one file from `assistant/webui/`, and nothing else |
| GET | `/jobs` | — | `{"jobs": [<every job in memory, oldest first>], "session_cost_usd", "busy", "queued", "limit"}` |
| GET | `/file/<token>` | — | the bytes of one file **this bridge recorded**; 404 for anything else |
| GET | `/services/health` | — | `{"services": [{"key", "label", "ok", "detail", "optional"?, "url"?, "address"?, "data"?}], "busy", "queued", "session_cost_usd", "claude_cli", ...}` |
| POST | `/upload` | `{"name", "data": "<base64 or data: URL>"}`, or a multipart form | `{"path", "name", "bytes", "token", "url"}` — **400** for a non-image or bad base64, **413** over the cap |
| POST | `/services/start` | — | `{"ok", "returncode", "script", "output": [<last 60 lines>]}`; **404** if the script is missing, **409** if a start is already running, **504** on timeout |
| POST | `/flows` | — | Blender's `flow_list`, verbatim |
| POST | `/flows/run` | `{"name", "params"?}` | Blender's `flow_run` report, verbatim; **503** Blender down, **502** the add-on said no |

`/job/<id>` and `/jobs` gained the fields a page that has just been opened needs
in order to draw a conversation it never saw happen: `message` (what was asked),
`created_at` / `started_at` / `finished_at`, and `files`. `created_at` is when
Send was pressed and never moves; `started_at` moves when a queued job is picked
up, so `duration_ms` means the same thing on every job. The panel ignores all of
them.

### File serving is the bridge's own history, not the filesystem

`GET /file/<token>` is the only way the page can see a file on the artist's
disk, and **paths are never accepted from the client**. A token is minted
*inbound*, when a path enters a job through this process:

- the attachment on an `/ask` (`source: "attachment"`),
- a path in a tool's arguments or in a tool result (`source: "activity"`) — how
  a render arrives,
- a path in the reply (`source: "reply"`) — "I saved it to `C:\...\cup.png`".

Minting also requires the extension to be one of `.png .jpg .jpeg .webp .bmp
.glb .gltf` **and** the file to exist right then; a path the model only
*mentioned* never becomes a broken image in the conversation. The same path
noted three times is one token, so a reload gets the URLs it had. 400 tokens
live at once; past that the oldest is forgotten and its `/file` 404s.

Everything else is a 404 with the same words — never minted, expired, or minted
and since deleted all read alike, because a client that can tell those apart can
use the endpoint to ask questions about the filesystem. A perfectly readable
`.png` whose absolute path you type in is refused exactly like `..\bridge.py`.

`/webui/<asset>` is the same idea for the page's own files: the name must match
`^[A-Za-z0-9._-]+$` (one plain segment — no slash, no `..`, and a percent sign
is not one of the allowed characters, so an encoded traversal fails on the
alphabet rather than on path arithmetic), the extension must be one a browser
has a use for, and the resolved path must still be inside the folder. Three
gates, all of which must pass. `assistant/system_prompt.md` is a 404 by all
three.

### Uploads: browser bytes in, a path on this machine out

A file input hands JavaScript the file's **content**, never its path, so there
is nothing to put in `context.image_path` until the bytes have been written
down. `POST /upload` does that; the path it hands back then rides exactly the
road the panel's attachment takes — a path in the context, a block in the
prompt, a Read by the model.

What it refuses, before writing anything: a non-image extension; an empty file;
anything over **20 MB** (`FORGE_ASSISTANT_MAX_UPLOAD_MB`), checked against
`Content-Length` before a byte is read as well as against the decoded bytes; and
an extension that lies — the first bytes must actually be a PNG, JPEG, WEBP or
BMP, because this writes a file to the artist's disk that the model is then told
to open. The name is sanitised to a filename with a random prefix, so
`../../../evil.png` lands in the uploads folder like everything else and two
sketches called `ref.png` are two files. The folder keeps its newest 200 files
and writes itself a `.gitignore` on creation — dropped sketches are not source.

Drag-and-drop and paste-a-screenshot go through the same `/upload`.

### The page

- **Chat** the width of the window, with the model selector (Fast / Smart /
  Deepest, remembered in `localStorage`), Enter to send, Shift+Enter for a line
  break, and four starter chips on the empty state.
- **Live activity** while a turn runs — the last four lines under a pulse, with
  a Stop button; the full list collapses into a `N steps` disclosure when it
  finishes. Polling is 800 ms and only while something is unsettled.
- **Pictures in the conversation**: the attachment under the question, renders
  and `.glb` files under the answer, each fetched by token.
- **Per-job footer**: model, duration, cost, time of day. A queued job says
  "waiting its turn", and the composer says so too — the queue is surfaced on
  both surfaces.
- **Health strip + Start services + New conversation + session cost** in the top
  bar. The strip is fanned out server-side by `/services/health`, because a page
  served from 8901 cannot ask 8765 itself; Blender and image-to-3D are allowed
  to be down on a working machine, the shape service is not.
- **Flows tab**: the saved flows with their params, and a Run button that goes
  straight to Blender with no model in the loop. Blender being closed is a
  sentence naming the button to press, not a stack trace.

`format.js` is the reply formatter, ~90 lines and no markdown library: blank-line
paragraphs (single newlines kept as breaks), `- ` and `1. ` lists, `#` headings,
`` `code` ``, ```` ``` ```` fenced blocks, `**bold**`, `_italic_`, and bare URLs
as links. Everything is **escaped first and marked up afterwards**, so no reply
can put a tag on the page — the tests run the real file under node and assert
that the only tags coming out are the ones the formatter itself makes.
`innerHTML` is assigned in exactly one place in `app.js`, from that function; a
test fails if a second one appears.

## The command line it builds

```
<claude> -p "<message>\n\n--- Current Blender context ---\n<context lines>\n\n--- Attached reference image ---\n<path>\n<read instruction>"
         --output-format stream-json --verbose --include-partial-messages
         --append-system-prompt-file assistant/system_prompt.md
         --allowedTools "Read,Glob,Grep,mcp__forge__*"
         --permission-mode auto
         [--model <the request's model, else FORGE_ASSISTANT_MODEL>]
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
| `FORGE_ASSISTANT_MODEL` | unset | Fallback `--model` value, used only when the request named no model; unset = the CLI's configured default |
| `FORGE_ASSISTANT_TIMEOUT` | `600` | Seconds one turn may take |
| `FORGE_ASSISTANT_TOOLS` | the list above | `--allowedTools` value; set it to `""` for a no-tools run |
| `FORGE_ASSISTANT_CWD` | the repo root | Working directory for the CLI |
| `FORGE_ASSISTANT_TEXT_INTERVAL` | `2.0` | Seconds between `text` activity markers; `0` records every chunk (the tests use it) |
| `FORGE_ASSISTANT_VERBOSE` | unset | Print an access log |

The web UI's own, all optional:

| Variable | Default | What it does |
|---|---|---|
| `FORGE_ASSISTANT_UPLOADS` | `assistant/uploads` | Where `POST /upload` writes |
| `FORGE_ASSISTANT_MAX_UPLOAD_MB` | `20` | The upload cap |
| `FORGE_START_SCRIPT` | `<repo>/start_forge.ps1` | What `POST /services/start` shells (hidden, via PowerShell; a `.py` path runs under this interpreter, which is how the tests exercise the route without PowerShell) |
| `FORGE_SERVICE_URL` | `http://127.0.0.1:8765` | Geometry service, for the health strip |
| `FORGE_MESHGEN_URL` | `http://127.0.0.1:8902` | Image-to-3D service, likewise |
| `FORGE_BLENDER_HOST` / `FORGE_BLENDER_PORT` | `127.0.0.1` / `9876` | The add-on socket the flows passthrough talks to |
| `FORGE_FLOW_RUN_TIMEOUT` | `900` | Seconds a `/flows/run` may take (a segment is minutes) |

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

`FAKE_CLAUDE_EXPECT_MODEL` asserts on the selector: set to a name, `--model`
must carry exactly that; set to the empty string, `--model` must be **absent**
(the "nobody chose anything" case, which has to keep working unchanged). The
flag's value is echoed back as the result's `model`, so a test can read the
effective model off the job as well as off the argv log.

`FAKE_CLAUDE_EXPECT_IMAGE` asserts on the attachment block: set to a path, the
prompt must carry it under `--- Attached reference image ---` with the Read
instruction and `Read` still in `--allowedTools`; set to the empty string, the
prompt must carry no attachment block at all.

`FAKE_CLAUDE_REPLY` replaces the reply text and `FAKE_CLAUDE_RENDER_PATH` adds a
tool call that "wrote" a file at that path (as both a tool input and a tool
result). Together they give the web-UI tests a real path to mint a `/file` token
for — one arriving through the activity, one through the reply, and both at once
to prove they collapse to a single token.

`FAKE_CLAUDE_STREAM=1` (or a flag file named by `FAKE_CLAUDE_STREAM_FILE`)
turns streaming on without naming a mode. The output shape and the flags the
bridge must pass are deliberately independent: the bridge always asks for
stream-json, and the json modes prove it still copes with a build that answers
with one object anyway.

`assistant/tests/test_webui.py` is the web UI's half of the suite (124 tests
beside `test_bridge.py`'s 90). It never touches port 8901: every bridge it
starts is on a port the OS handed out, and the Blender socket, the two
downstream services and the start script all have fakes in the file, so nothing
in it needs Blender, PowerShell or the internet. What it pins down: the page and
its assets are served and **only** they are (traversal, encoded traversal,
`system_prompt.md` and unknown file types are all 404); every element id
`app.js` reaches for exists in the page it was served with; the page fetches
nothing off this machine and calls no route this bridge does not serve; tokens
are minted inbound and `/file` serves nothing that was not; upload caps, magic
bytes and hostile filenames; `/services/health` answers with everything down;
and `/flows` passing through to a fake socket, including one that hangs up and
one that answers with junk. The formatter tests run `format.js` for real under
node when there is one, and skip when there is not.

One real turn against the live CLI runs only when you ask for it:

```
set FORGE_ASSISTANT_SMOKE=1
service\.venv\Scripts\python.exe -m pytest assistant\tests -q -k smoke
```

It sends five words to Haiku with no tools, so it costs about nothing. Auth and
rate-limit failures skip rather than fail — those are facts about the machine,
not bugs in the bridge.

The panel side is `addon/tests/headless_assistant.py` (Blender `--background`,
port 9882), `addon/tests/headless_model.py` (port 9889 — the speed selector),
and `addon/tests/headless_reference.py` (port 9886 — the attach
field, its validation, and the `load_reference` command), which drives the operators against a fake bridge written into the
harness. The two suites never overlap: that one is about the panel, this one is
about the command line.

## Running it

`start_forge.cmd` at the repo root starts this and the geometry service, each
only if its port is free. `stop_forge.cmd` stops them again. The Blender side
points at `http://127.0.0.1:8901` by default; the address is an add-on
preference (Edit → Preferences → Add-ons → Forge → Assistant).

The web UI is the same address in a browser: **http://127.0.0.1:8901/**. It
needs nothing else running — Blender down just means the flows tab and the
scene-context tools have nothing to talk to, which the page says in words.
