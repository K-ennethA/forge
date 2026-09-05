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
| GET | `/health` | — | `{"status": "ok", "claude_cli": {"found", "path", "version"?, "hint"?}, "cwd", "session", "busy"}` |
| POST | `/ask` | `{"message", "context": {...}, "conversation": "continue"\|"new"}` | `{"job_id", "state": "running"}`; **409** while another job runs; **503** if the CLI is missing; **400** for an empty message |
| GET | `/job/<id>` | — | `{"state": "running"\|"done"\|"error"\|"cancelled", "reply"?, "session_id"?, "cost_usd"?, "duration_ms"?, "model"?, "usage"?, "error"?}` |
| POST | `/cancel/<id>` | — | the job, now `cancelled` |
| POST | `/new` | — | `{"status": "ok", "session": null}` — forget the conversation without asking anything |

One job at a time, on purpose: two turns in flight would fight over the same
resumed session. The last 20 jobs stay in memory; older ids 404.

## The command line it builds

```
<claude> -p "<message>\n\n--- Current Blender context ---\n<context lines>"
         --output-format json
         --append-system-prompt-file assistant/system_prompt.md
         --allowedTools "Read,Glob,Grep,mcp__forge__*"
         --permission-mode auto
         [--model <FORGE_ASSISTANT_MODEL>]
         [--resume <session_id>]          # every turn after the first
```

run with `cwd` = the repo root. Four details are load-bearing:

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
- **Noisy stdout** — a node warning printed before the JSON is salvaged by
  scanning backwards for the last line that parses; failing that, the stderr
  tail is surfaced verbatim rather than a shrug.
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
makes the assistant work (`-p`, `--output-format json`, `--allowedTools`, a real
`--append-system-prompt-file`), and it **logs every argv** to `FAKE_CLAUDE_LOG`
so tests can assert after the fact — most usefully that turn two carried
`--resume`. `FAKE_CLAUDE_MODE` picks the failure being rehearsed: `noise`,
`garbage`, `slow`, `reject_permission`, `api_error`.

One real turn against the live CLI runs only when you ask for it:

```
set FORGE_ASSISTANT_SMOKE=1
service\.venv\Scripts\python.exe -m pytest assistant\tests -q -k smoke
```

It sends five words to Haiku with no tools, so it costs about nothing. Auth and
rate-limit failures skip rather than fail — those are facts about the machine,
not bugs in the bridge.

The panel side is `addon/tests/headless_assistant.py` (Blender `--background`,
port 9882), which drives the operators against a fake bridge written into the
harness. The two suites never overlap: that one is about the panel, this one is
about the command line.

## Running it

`start_forge.cmd` at the repo root starts this and the geometry service, each
only if its port is free. `stop_forge.cmd` stops them again. The Blender side
points at `http://127.0.0.1:8901` by default; the address is an add-on
preference (Edit → Preferences → Add-ons → Forge → Assistant).
