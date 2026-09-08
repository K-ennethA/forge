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
routes (`/jobs`, `/file/<token>`, `/upload`, `/services/*`, `/flows`, the
part sheet's `/projects`, `/preview`, `/scene`, the library's `/library` and
`/projects/<name>/thumbnail`, and the project file's `/projects/<name>/open` and
`/projects/<name>/save`). They are additive, the panel never calls them, and
they are documented under
[The web UI (Phase 9)](#the-web-ui-phase-9--the-second-surface),
[The part sheet (Phase 11)](#the-workbench-phase-11--the-page-stops-being-a-chat-box),
[The Library (Phase 13)](#the-library-phase-13--a-view-to-see-all-our-3d-models)
and [The project's own `.blend` (Phase 15)](#the-projects-own-blend-phase-15--clicking-a-model-should-open-it).
The page itself is [one screen](#the-page-the-studio): the conversation and the
part's own numbers side by side.

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
`format.js`, `follow.js`. Vanilla, no build step, **no CDN**, no framework: it is
served by a stdlib HTTP server on a machine that may have no internet, and it has
to still open in five years without a toolchain being alive to rebuild it. A test
fetches all five files from a running bridge and fails if any of them names an
`http://` resource. The two small files are the two rules worth running in a test
on their own: `format.js` turns a reply into HTML, `follow.js` decides which part
a finished turn was about.

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

### The page: the Studio

Three tabs — **Studio | Library | Flows** — and the first of them is the whole
working session. The artist's own words for what was wrong with four:

> *"I want to directly edit this from the UI — I should be able to pinch or
> stretch the bottom radius by typing into a param box or flatten the top with
> params rather than going back and forth with the AI. I want fields I can
> directly edit. I don't like the multiple tabs for one workflow session — one
> screen that allows me to control it directly. Things like gallery that are
> separate actions can be a different tab."*

The case that settled it: three edits — shorter, flatter top, top 1.5× the
bottom — asked of the model, **155 seconds and $0.41**. They are three numbers.
So the Chat tab and the Workbench tab are one screen, and the numbers are always
on it:

**Left — the conversation**, unchanged: the thread, the composer, the model
selector (Fast / Smart / Deepest, remembered in `localStorage`), Enter to send
and Shift+Enter for a line break, the attachment chip, the queue notice, four
starter chips on the empty state.

**Right — the part rail**, its own scrolling column:

- the **picker** and a Refresh, with one line under it saying whether the rail
  is following the conversation or pinned to a part chosen by hand;
- the **parameter rows** — slider *and* number box, the unit, a hint, amber
  until Apply — always visible, never a tab away;
- **Apply & rebuild**, **Reset values**, the status, and **how long the last
  rebuild took**. That pill is the argument: the same edit is `1.8 s` here and
  was 155 seconds and 41 cents through the model;
- the **component sheet** (what the part is, what it is made of, what prints
  beside it) from `spec.json`;
- the **preview** with its view selector, refreshed by itself after every Apply;
- a compact **scene strip** — what Blender is holding, with Preview and Scrap.

**Enter in any value box is Apply.** That is the point of the rail: click the
number, type `1.5`, press Enter, watch it rebuild. Reaching for a button between
every field is what made typing it yourself feel no faster than asking.

Below **1100 px** the two columns stack, chat first — a laptop and a second
monitor are not the same window.

The rest, unchanged by the merge:

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
- **The flow row above every panel** — Phase 11, and one click away whichever
  tab is open. It is [below](#the-flow-row--on-every-tab).
- **Library tab**: every project in `projects/` as a card, from a folder read
  alone, plus what Blender is holding right now — a separate errand, so a
  separate tab. Its cards' **Open in Studio** switches the tab, the picker and
  the pin. [Below](#the-library-phase-13--a-view-to-see-all-our-3d-models).
- **Flows tab**: the saved flows with their params, and a Run button that goes
  straight to Blender with no model in the loop. Blender being closed is a
  sentence naming the button to press, not a stack trace.

### Auto-follow: the rail keeps up on its own

A sheet that has to be pointed at the part you just asked for is a tab in
disguise. So the rail tracks the conversation, and the rules are one file —
`follow.js`, no DOM and no fetch in it, so the tests run them under node with
canned `/jobs` entries. In order, and each one cheap:

1. Only a job whose `state` is `"done"` is read at all; a running turn has not
   decided anything yet, and the conversation redrawn at page load is history
   rather than a decision.
2. Its tool activity is read **newest first**, and only three tools count —
   `partforge_new_part`, `partforge_open_in_panel`, `partforge_generate`.
   Everything else (a check, an export, a render) is about a part the rail is
   already on, and following those would walk the sheet through a part's whole
   history.
3. The argument half of the label is resolved against the picker's projects: by
   exact name, then by slug (`"a small magnet holder"` →
   `a-small-magnet-holder`), then as a script filename **exactly one** project
   has. `part.py` is most projects' script, so it deliberately resolves to
   nothing — switching to whichever sorted first would be worse than not
   switching.
4. `partforge_new_part` is special: the part was written *this turn*, so a slug
   matching nothing is still returned and the page refetches `/projects` once.
5. Failing all that, the reply and then the question are read for a
   `projects/<name>` path (either slash) or a known project's name — as a whole
   word, so "cup" does not match inside "cupboard" — and the last mention wins,
   because a reply that names two parts ends on the one it just made. A path
   naming a folder the picker has not heard of is the last resort, for the same
   reason as rule 4.
6. Otherwise the sheet does not move.

Picking from the dropdown by hand, or **Open in Studio** from the Library,
**pins** the rail to that part; the next detection unpins it, because the artist
asked for that part in the message that produced it. On page load the rail opens
on the part it was left on (`forge.project` in `localStorage`), else the most
recently modified project — `/library` is the only route carrying an `mtime`,
and that is the one call it costs.

`localStorage` from before the merge is migrated rather than ignored: a stored
tab of `chat` or `workbench` (and a `#chat` / `#workbench` bookmark) both land on
`studio`, and the stored name is rewritten so the alias is read once per browser.

`format.js` is the reply formatter, ~90 lines and no markdown library: blank-line
paragraphs (single newlines kept as breaks), `- ` and `1. ` lists, `#` headings,
`` `code` ``, ```` ``` ```` fenced blocks, `**bold**`, `_italic_`, and bare URLs
as links. Everything is **escaped first and marked up afterwards**, so no reply
can put a tag on the page — the tests run the real file under node and assert
that the only tags coming out are the ones the formatter itself makes.
`innerHTML` is assigned in exactly one place in `app.js`, from that function; a
test fails if a second one appears.

## The Workbench (Phase 11) — the page stops being a chat box

The artist's own words for what was wrong with Phase 9: *"On the UI it's pretty
much just a chat bot, but I should be given little windows to edit my
components… I'd like to have a sheet of what makes up the object, like CAD,
where I can see the components so I can edit parts of it."*

So the page grew a part sheet, and **none of it spends a model turn** — a slider
that costs money per drag is a slider nobody drags. Every control is a route on
this bridge: a slider move is a `/generate` and a `load_mesh`, a Preview is a
`render_preview`, a Scrap is a `delete_object`. Changing a number costs nothing
and answers in the time a rebuild takes, which is the difference between editing
a part and *asking somebody* to edit a part.

Phase 11 gave it its own tab; **Phase 14 moved it into the Studio's right rail**,
beside the conversation, because a tab away was still away. The machinery below —
the rows, the debounce, the amber-until-Apply, Apply's chain, the preview, the
scene panel — is the same machinery in a different place, plus Enter-to-Apply and
the rebuild-time pill. Where this section says "the Workbench", read "the rail".

### The flow row — on every tab

The row sits above `<main>`, outside every panel, because these are the
things an artist actually presses and they should not be a tab away (a test
asserts the position, not just the markup). The three fixed ones send a **canned
chat message** — the assistant already knows how to do these jobs, and each is
several tools deep, so the button's whole purpose is that the paragraph never
has to be typed again:

| Button | What it sends, verbatim |
|---|---|
| Get ready to print | `Get the current work ready to print: merge what's in the scene if needed, run the checks, fix what you can, segment if it doesn't fit the bed, and export. Tell me what you did.` |
| Send to Godot | `Take the current character through retopo/tags/rig if not done and export it for Godot. Tell me where the files are.` |
| Check my work | `[check-in] Look at my work and tell me what you notice.` |

The last one is *character-for-character* the add-on's `CHECK_IN_LEAD` and the
phrase `system_prompt.md` keys its "checking their work" stance off — a test
asserts that, because a reworded button would be a check-in the model does not
recognise. Saved flows from `/flows` are added to the same row as extra buttons
and run in Blender directly, with no model in the loop.

### The component sheet

The picker lists every folder in `projects/` (`FORGE_PROJECTS_DIR` — the same
variable `partforge_new_part` writes into, so a part the assistant wrote a
minute ago is already there). Picking one draws:

- **what it is** — `spec.json`'s description, the script, and the Blender object
  it builds into;
- **what it is made of** — the spec's `features`, its `components` (read
  liberally: a list of names, a list of objects, or a map, plus `core` /
  `proposals` / `assembly.parts`, so the Phase 11 component tree lands without a
  second reader), and its `companion_parts` under "prints separately";
- **the dimensions** — one row per `PARAMS` entry, resolved by the service's
  `/parse_params`: a labelled slider when the schema gives `min` and `max`
  (stepped by `step`), a number box beside it that is always the authority, a
  checkbox for a `bool`, the unit next to the name and the description under it.
  Both inputs drive each other, debounced, and a row whose value has moved goes
  amber until Apply.

**Apply & rebuild** posts every value to `/projects/<name>/set_params` and the
part comes back changed in place, then renders itself into the preview beside
it. **Reset values** puts the schema's defaults back.

### The scene panel and the preview

`GET /scene` is `get_scene_info` verbatim — what Blender is holding *right now*,
which is the component sheet for work that has no spec (a generated mesh, a
sculpt, the pieces a segment produced). Each object gets its size in millimetres
and two buttons: **Preview** renders that one object, and **Scrap** deletes it
after one confirm. Scrap is the only destructive control on the page, and both
the button and the bridge's own answer say the same true thing — every
state-changing socket command pushes its own undo step, so Ctrl+Z in Blender
puts it back.

`POST /preview` renders to a path **this bridge chooses**, mints a token for it
and hands back the URL; the picture is then served by the same `/file/<token>`
allow-list as everything else. The path is never taken from the client, and the
name is fresh every time — a reused URL is a browser cache showing the artist
the *previous* shape, which is the most misleading thing this feature could do.
The folder keeps its newest 40.

### The workbench's routes

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/projects` | — | `{"dir", "count", "projects": [{"name", "path", "script", "script_path", "scripts", "spec", "has_params", "object"}]}` |
| GET | `/projects/<name>/schema` | `?refresh=1` skips the cache | `{"project", "script", "script_path", "object", "params", "count", "cached", "spec"}`; **503** service down (with `"service": false`), **502** the service refused the script, **422** the project has no script to read, **404** no such project |
| POST | `/projects/<name>/set_params` | `{"overrides": {param: value}, "object"?}` | `{"project", "object", "overrides", "params", "stats", "loaded", "mesh", "notes"}`; **503** service or Blender down, **502** either one refused, **422** nothing to build, **400** a body that is not `{param: value}` |
| POST | `/preview` | `{"objects"?, "view"?, "resolution"?, "shading"?}` | `{"token", "url", "path", "objects", "view", "bounds_mm", ...}`; **503** Blender down, **502** it refused or wrote nothing |
| GET/POST | `/scene` | — | `get_scene_info`, verbatim |
| POST | `/scene/delete` | `{"object"}` | `{"deleted", "result", "undo"}` |

`set_params` is a three-step chain and **the order is not arbitrary**:

1. `POST /generate` on the geometry service, so a stopped service is reported as
   a stopped service rather than as Blender refusing a command it never got;
2. `partforge_open` on the socket, so the artist's Forge panel is pointed at the
   same script the page is — best effort: a panel that will not follow along is
   a smaller problem than a mesh that does not arrive, and it becomes a note;
3. `load_mesh` with `replace: true`, which is what keeps the object's transform,
   its place in the outliner and the artist's selection through a rebuild.

The object name is derived by `bridge.object_name_for_script` — the stem, or the
*folder* when the stem is generic (`part.py`, `main.py`, …). That is
`docs/architecture.md`'s part-object naming convention, implemented here for the
third time (the add-on and the MCP server are the other two) on purpose: get it
wrong and Apply replaces the mesh of an object nobody is looking at, and the
artist watches a slider do nothing.

The bridge **never runs the artist's script**. `has_params` is a read for a
top-level `PARAMS =`, and everything that needs the script executed goes to the
geometry service, in its own process, with its own venv.

### When something is not running

- **Blender closed** — the scene panel, Preview and Apply each answer with the
  *panel's own sentence*: "Blender is not running, or the Forge add-on's server
  is stopped. Open Blender, press N in the 3D view, click the Forge tab, and
  press Start Server — then try again." One line, one thing to do, the same
  words on both surfaces. A rebuild that got as far as the service still returns
  its `stats` alongside the 503: built but not shown, said plainly.
- **Shape service stopped** — the parameters are replaced by one sentence naming
  the button in the top bar ("Press Start services"), and Apply and Reset are
  disabled rather than left to fail.
- **Neither** — the sheet still lists what is in `projects/` and shows every
  spec, because that is a folder read and nothing else.

### A keep-alive bug this tab found

`POST /flows` ignored its request body, and HTTP/1.1 here is keep-alive: the
unread `{}` stayed in the socket and was parsed as the start of the *next*
request on that connection, which arrived as a 501 `Unsupported method
('{}GET')` on whatever innocent route asked second. It was invisible until a
page made two POSTs in a row. Every POST handler now drains, even the ones with
nothing to read, and a test walks each of them over one `http.client` connection
and then asks for `/health`.

## The Library (Phase 13) — a view to see all our 3D models

The Workbench edits *one* part. The Library is the shelf you look along to find
it: a responsive card grid of every folder in `projects/`, plus a second row of
whatever Blender is holding right now.

**Everything on a card is a folder read.** Description, dimensions, components,
exports, when it was last touched — all of it comes off `spec.json` and `stat`,
so the whole page still draws with Blender closed *and* the shape service
stopped. That is the point of the tab: the artist opens it to find their work,
and "find my work" must not be a thing that can be down. The only part that
needs anything running is the picture, and a missing picture is a placeholder
with the project's initial in it, not an error.

A card carries:

- the cached thumbnail, or that initial placeholder;
- `spec.json`'s `description` (or, with no spec, the script and the object name
  it builds into);
- **how many dimensions** it has — read from the spec's own `parameters` block,
  else from a schema the Workbench already parsed this session, else honestly
  unknown. Never by calling the shape service: a library of twenty parts must
  not be twenty `/parse_params` round trips, nor twenty error cards when the
  service is stopped. "Parameters not read yet" is the truth; `0 dimensions`
  about a script with three would not be;
- **component chips**. The shape that matters is the one
  `partforge_new_part` actually writes —
  `"components": {"collection": <slug>, "core": <slug>, "proposals": [names]}`
  (`forge_mcp.util.component_block`). That is a **tree, not a map**, and reading
  it as a map is a real bug this tab found: it produced a chip called
  "collection", a chip called "core", and silently dropped every proposal —
  which is the one thing on the card that says what the part is made of. So the
  tree is recognised by its whole key set and read as core-plus-proposals, the
  collection is skipped (it is where the pieces live in the outliner, not a
  piece), and a test pins the three key names against `util.py` itself so a
  rename on either side fails here rather than quietly emptying every chip row.
  The looser shapes are still read — a list of names, a list of objects, a map
  of name → sentence, plus top-level `core`/`proposals` and `assembly.parts` —
  because `spec.json` is the artist's file and the tree is still growing. A
  `proposal` chip is dashed: it is the piece you are invited to scrap, and it
  should not look as settled as the core does. `companion_parts` are deliberately
  **not** chips — a companion part prints separately and is a sibling, not a
  component;
- **exports** from `projects/<name>/exports/`, newest first, with sizes. The
  full path is the chip's tooltip rather than a link — these are files on this
  machine and the browser is on this machine, so a download route would be a
  second way to read the filesystem for no gain over a path you can paste into
  Explorer;
- **whether it has a scene file of its own** — `scene file 4.2 MB`, or `no scene
  file yet` with the button that makes one in the tooltip (Phase 15, below);
- four buttons: **Open** (the project's `.blend`, which is what "open this
  model" means, and the only one on a card wearing the accent), **Open in
  Studio**, which switches tab, selection *and* pin (a tab switch that left the
  picker on the previous part would be a button that lies), **Save scene**, and
  **Preview**.

The second row is the **works in progress**: a generated mesh, a sculpt, the
pieces a segment produced. None of them has a folder in `projects/` and all of
them are the artist's work, so they get cards too — dashed, with their size in
millimetres and a Preview, and an Open in Studio when the object happens to
be some project's part object. Blender being closed is one sentence in that row,
not a failed request, because the rest of the page is a folder read and must
still draw.

`#library` is a URL: the tab is written to the hash with `replaceState` (so Back
still leaves the page instead of walking the tabs) and a pasted link wins over
whatever was open last time.

### Thumbnails: a photograph, never a build

The cache is one PNG per project in `assistant/thumbs/`, overwritten in place —
so it cannot grow past the number of parts, and a stale picture cannot outlive
the part it is of. It sits **beside `uploads/`, not in the temp dir** like the
previews do: a preview is regenerated on every click, and a thumbnail is what
the library draws *before* anything is running, so it has to survive a reboot.
The folder writes its own `.gitignore` (`*`) on creation rather than making every
clone edit the repo's.

Two ways a picture gets there, and neither of them builds geometry:

1. **The Workbench's own render.** `POST /preview` of exactly one object that is
   some project's part object caches the PNG on the way past, and says which
   project it was in `thumbnail_for`. One render, two uses — the file is already
   on disk, and asking Blender to draw the same shape a second time for a
   240-pixel square would be work nobody asked for. A whole-scene render is
   nobody's thumbnail: a picture captioned with the wrong part is worse than no
   picture, when the library is how the artist finds their work.
2. **The card's Preview button** — `POST /projects/<name>/thumbnail`, which
   photographs the part *as it stands in the scene*.

That second one is deliberately **only** a photograph. It would be easy to make
it open the script and generate the part when it is missing — and then a page of
twelve cards would rebuild twelve parts, spend minutes of the artist's machine
and change a scene they were looking at, all for pictures. So a part that is not
in the scene is a **409** naming the button that builds it ("Open it in the
Workbench and press Apply & rebuild"), and the artist stays the one who decides
when geometry happens. The route asks `get_scene_info` first and only reaches
`render_preview` if the object is there; a test asserts that on a miss Blender
saw exactly one command.

The thumbnail URL is stable, so the card appends the file's mtime as `?t=` — a
browser showing yesterday's shape under today's name is the one bug this feature
must not have.

### The library's routes

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/library` | — | `{"dir", "count", "projects": [...], "scene": {...}}` |
| GET | `/projects/<name>/thumbnail` | — | the cached PNG as `image/png`; **404** with a sentence saying how to get one (which the page draws as a placeholder, not as a fault) |
| POST | `/projects/<name>/thumbnail` | — | `{"project", "object", "token", "url", "path", "cached", "thumbnail_url", "thumbnail_mtime", "bounds_mm"}`; **409** the part is not in the scene, **422** the project has no part script to photograph, **503** Blender down, **502** it refused or wrote nothing, **404** no such project |

Each card in `projects` is:

```
{"name", "path", "script", "script_path", "object", "has_params",
 "description", "param_count", "param_source", "components": [{"name", "role",
 "description"}], "features", "exports": [{"file", "path", "size", "mtime"}],
 "export_count", "mtime", "has_blend", "blend_path", "blend_size",
 "blend_mtime", "has_thumbnail", "thumbnail_mtime", "thumbnail_url", "spec"}
```

`param_count` is `null` when it could not be read without running anything, and
`param_source` says which of the three answers it is (`spec`, `service`,
`unread`). `mtime` is the latest of the folder, its `spec.json`, its script and
its exports. The `scene` block is `get_scene_info`'s objects when Blender is up,
and `{"ok": false, "blender": false, "error": <the panel's own sentence>}` when
it is not — never an exception, because the rest of the answer is a folder read.

`/projects/<name>/thumbnail` puts the project name through the same alphabet gate
as `project_dir` and the asset server, so traversal is refused by the *shape* of
the name before any path arithmetic; the cached PNG is then served straight off
the cache rather than through `/file/<token>`, since the path is one this bridge
chose in a folder this bridge owns.

## The project's own `.blend` (Phase 15) — "clicking a model should open it"

The artist's words, and the gap they expose: everything up to here is
parametric — a script, a spec, an STL — and none of that is where a **sculpt**
lives, or a lighting setup, or six carefully placed reference empties. Those
live in a `.blend`, and a project folder had nowhere to put one.

So a project may now keep one scene file beside its script,
`projects/<name>/<name>.blend`, and the card says whether it has one. That
question is answered by a **`stat`** — `has_blend`, `blend_path`, `blend_size`,
`blend_mtime` — never by asking Blender, which is exactly the thing that may not
be running when somebody opens the Library to find their work. A saved scene
also counts as touching the project, so it moves the card's `mtime`.

### Save is a copy, and that is the whole feature

`POST /projects/<name>/save` goes straight through to the add-on's
`save_project_blend`, which is `wm.save_as_mainfile(..., copy=True)`. Without
`copy=True` Blender **retargets the session**: the file the artist has been
pressing Ctrl+S on all afternoon silently becomes the project file, and their
next save goes somewhere they did not choose. A tool that moves where your work
saves to is a tool nobody should hand a sculpt. The add-on checks
`bpy.data.filepath` before and after and hands it back as `session_file`, this
route passes it on, and the card prints "your own file is untouched" every time
it succeeds — because that is the one thing an artist would reasonably be afraid
of, and reassurance is cheaper said than discovered.

This route never names a path of its own. The folder convention lives in one
place and it is the add-on's; a bridge with its own opinion about where a
project file goes is a second convention waiting to disagree.

### Open has three routes, and a running Blender always wins

`POST /projects/<name>/open` — and the page branches on the **`route`** in the
body, not on the status code, because two of the three are not faults:

1. **`running`** — something is listening on 9876, so the open goes through the
   add-on's `open_project_blend`. If the running scene has unsaved work, the
   add-on answers `needs_confirmation` with a sentence naming what would go and
   a count of it, and **nothing is touched**. That comes back as a **200**: it
   is a question, and the page asks it (`window.confirm`) and comes back with
   `{"confirm": true}`. There is no undo across a file load — Blender resets the
   stack — so this confirmation is the last moment it can be asked, and the
   dialog says exactly that rather than implying a way back.
2. **`spawned`** — nothing is listening and there *is* a `.blend`, so a windowed
   Blender is started on it. The artist clicked Open, so this is **them**
   launching Blender: the no-windowed-Blender law binds agents and verification
   runs, not the person whose machine it is. The spawn is `DETACHED_PROCESS |
   CREATE_NEW_PROCESS_GROUP` on Windows (`start_new_session` elsewhere) so it
   outlives a bridge restart and a Ctrl+C here is not a Ctrl+C there — and
   emphatically **not** `CREATE_NO_WINDOW`, which every *other* spawn in this
   file carries. Those are background helpers nobody should have to look at;
   this is an application the artist is about to work in. The two flags are
   mutually exclusive to `CreateProcess` anyway, so getting it wrong is not a
   cosmetic bug, it is a Blender that never appears. Nothing is piped: a
   detached GUI application with a pipe nobody reads eventually blocks on its
   own stdout.
3. **`no_blend`** — there is nothing to open yet, which is the ordinary state of
   every project until someone saves one. Also a 200, and Blender is never
   contacted: the question was answered by a `stat`. The page falls back to the
   thing that *does* exist — Open in Studio, its dimensions — and prints one
   sentence naming the button that makes the other.

**A running instance always wins.** Two Blenders would fight over port 9876 and
the add-on would end up talking to whichever won the race, so this never spawns
while something is listening — it drives the running session instead. A test
asserts that with both a fake add-on socket *and* a stand-in executable
available, the executable is never launched.

Blender itself is found the way the CLI is: `FORGE_BLENDER_EXE` first (which is
also how the tests point this at a stand-in that only writes down its argv),
then `PATH`, then where the official installer actually unpacks — the Windows
installer does not put Blender on a `PATH` this process inherits, so
`shutil.which` alone would find nothing on the machine this product is built
for. Not found at all is a **501** naming the variable to set, in the same shape
as the "PowerShell was not found" answer.

### The project-file routes

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/projects/<name>/save` | — | `{"project", "saved", "blender", "path", "object_count", "replaced", "session_file", "has_blend", "blend_size", "blend_mtime", "result"}`; **503** Blender down, **502** it refused, **404** no such project |
| POST | `/projects/<name>/open` | `{"confirm"?}` | `{"project", "blend", "has_blend", "route": "running"\|"spawned"\|"no_blend"}` plus, per route: `opened`/`needs_confirmation`/`would_lose`/`hint`/`result` (running), `pid`/`executable`/`note` (spawned), `hint`/`object`/`script_path` (no_blend). **501** Blender is not installed, **500** it would not start, **503**/**502** the running Blender went away or refused, **404** no such project |

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
| `FORGE_PROJECTS_DIR` | `<repo>/projects` | The parts the workbench lists — **the same variable** `partforge_new_part` writes into |
| `FORGE_ASSISTANT_PREVIEWS` | `<temp>/forge-webui-previews` | Where `POST /preview` renders to |
| `FORGE_GENERATE_TIMEOUT` | `300` | Seconds one workbench rebuild may take |
| `FORGE_ASSISTANT_THUMBS` | `assistant/thumbs` | The library's thumbnail cache — beside `uploads`, not in the temp dir, because a card has to draw before anything is running |
| `FORGE_BLENDER_EXE` | discovered | Full path to `blender.exe`, for the one case where this bridge *starts* Blender rather than talking to it (`POST /projects/<name>/open` with nothing listening). Unset = `PATH`, then the official installer's own folders |

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

`assistant/tests/test_webui.py` is the web UI's half of the suite (313 tests
beside `test_bridge.py`'s 90, so **403** in all). It never touches port 8901:
every bridge it starts is on a port the OS handed out, and the Blender socket,
the geometry service, the two downstream health probes and the start script all
have fakes in the file, so nothing in it needs Blender, PowerShell or the
internet. `FORGE_PROJECTS_DIR` is pointed at the test's own `tmp_path` for the
same reason — a workbench test that passes because the machine happens to have
four bowl holders in `projects/` is not a test.

What it pins down for Phase 9: the page and its assets are served and **only**
they are (traversal, encoded traversal, `system_prompt.md` and unknown file
types are all 404); every element id `app.js` reaches for exists in the page it
was served with; the page fetches nothing off this machine and calls no route
this bridge does not serve; tokens are minted inbound and `/file` serves nothing
that was not; upload caps, magic bytes and hostile filenames;
`/services/health` answers with everything down; and `/flows` passing through to
a fake socket, including one that hangs up and one that answers with junk.

And for Phase 11: `object_name_for_script` against the naming convention's own
cases (`part.py` → the folder, `lid.py` → `lid`); `project_dir` refusing
everything that is not one plain name; `scan_projects` reading the spec,
preferring the script the spec names, skipping folders that are not parts and
surviving a `spec.json` with a trailing comma; the schema cache expiring on the
script's mtime; `set_params` calling `/generate` **before** the socket (so a
stopped service never reads as a Blender fault), keeping the numbers when
Blender is closed, and surviving a panel that will not follow along; `/preview`
never rendering to a path the client named and saying so when the render
produced no file; `/scene/delete` promising the undo it actually has; the three
canned flow buttons carrying their message character-for-character (one of them
compared against `addon/forge/tools/buddy.py`'s `CHECK_IN_LEAD`, in that file,
so a reword on either side fails here); the flow row living outside every panel;
and every POST route walked over a single keep-alive connection followed by a
`/health` that must still answer.

And for Phase 13: `/library` carrying every field a card draws (description,
`param_count` and its source, component chips, exports with sizes, the mtime),
listing what is on disk and skipping what is not a project, surviving a
`spec.json` that will not parse, and — the one that matters — **drawing every
card with nothing at all running**, with Blender's absence as the panel's own
sentence inside the `scene` block rather than as a failed request;
`param_count_for` never reaching the shape service, and answering `None` rather
than `0` for a script it could not read; `spec_components` reading the tree the
MCP server actually writes (core + proposals, collection skipped) *and* still
reading a map keyed by name that happens to contain a piece called "core", with
the three key names compared against `mcp/forge_mcp/util.py`'s
`component_block`; `thumbnail_path` refusing everything
that is not one plain name, and defaulting beside `uploads` rather than in the
temp dir; `save_thumbnail` keeping one file per project and writing the folder's
own `.gitignore`; `remember_preview` caching a picture of exactly one part and
of nothing else; the `POST` thumbnail route **never building the part to
photograph it** (a 409 naming Apply & rebuild, with an assertion that Blender saw
only `get_scene_info`), 422 for a project with no script, and 503 with the
panel's sentence when Blender is closed; the Workbench's own render doubling as
the thumbnail while a whole-scene render stays nobody's; and, on the page, the
tab and panel wired to each other in the right order, the two grids, the
responsive `auto-fill` grid rule, the initial placeholder, the `?t=` cache-bust,
Open in Studio switching the tab, the selection and the pin, and the tab
refetching every time it is opened.

And for Phase 14: the Studio panel holding **both** sets of anchors — the
thread, the composer and the model selector *and* the picker, the parameter
rows, Apply, the preview and the scene — so there is no arrangement of tabs in
which the answer about a dimension is visible and the box that changes it is
not; the conversation before the rail in source order, which is the stacking
order at a narrow width; the rail as a grid column that scrolls on its own; the
1100 px breakpoint existing at all; Enter wired to Apply on **both** kinds of
value box, with a held-down Enter unable to queue a second rebuild; the
rebuild-time pill measured across the `set_params` call; the old `chat` /
`workbench` tab names aliased to `studio` and rewritten in `localStorage`; the
rail opening on the remembered part else `/library`'s newest `mtime`; the
page-load redraw marked `historical` so it cannot yank the sheet to an hour-old
turn; and **auto-follow's rules run for real under node** over canned `/jobs`
entries — a slugged new part, a script filename only one project has, the
ambiguous `part.py` resolving to nothing, a `projects/<name>` path in the reply
(both slashes), the newest tool winning, a name that is not a substring
("cupboard"), a folder the picker has not heard of, a job that has not
finished, `/projects` in any of the three shapes it arrives in, and six
malformed job shapes that must each cost a missed switch rather than an
exception.

And for Phase 15: `project_blend` answering off a `stat` alone (absent, then
present with its size and mtime) and a saved scene moving the card's "last
touched"; `resolve_blender` taking the override first and refusing a path that
is not there rather than quietly picking up something else off `PATH`;
`blender_launch_argv` running a `.py` stand-in under this interpreter; the GUI
spawn's creation flags asserted to be `DETACHED_PROCESS |
CREATE_NEW_PROCESS_GROUP` and **not** `CREATE_NO_WINDOW`; `spawn_blender`
actually starting the stand-in, which writes down the argv it was given so the
`.blend` path can be checked; `/projects/<name>/save` driving
`save_project_blend` with the project name and nothing else (the folder
convention lives in the add-on), 503 with the panel's sentence when Blender is
closed, 502 passing the add-on's own refusal through, 404 for a name that is not
a project; `/projects/<name>/open` answering `no_blend` **without contacting
Blender at all**, surfacing the confirmation round trip as a 200 and carrying the
artist's `confirm` back on the second press, spawning a Blender on the file when
none is listening, and **never** spawning one when a socket answers — that last
with a stand-in executable available and a log asserted not to exist; a 501
naming `FORGE_BLENDER_EXE` when Blender is not installed; both new POST routes
draining their body so a 404 cannot poison the next request on a keep-alive
connection; and, on the page, the confirm dialog quoting the add-on's
`would_lose` and saying that undo does not cross a file load, the branch on
`route` rather than on the status code, and the scene-file chip.

The formatter and auto-follow tests run `format.js` and `follow.js` for real
under node when there is one, and skip when there is not.

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
