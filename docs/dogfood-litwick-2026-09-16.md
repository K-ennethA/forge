# Dogfood run — the Litwick lamp, end to end (2026-09-16)

An artist-voice run through Forge's own front door: every instruction below went in
as a chat message to the assistant bridge at `http://127.0.0.1:8901/ask`, polled at
`/job/<id>`. No MCP tool was called by hand, no repo source was edited, and no bug
was fixed mid-run — each one is logged as a finding and worked around the way an
artist would (rephrase, retry once, or drop the thread).

## Rig

| piece | state at start |
| --- | --- |
| bridge `127.0.0.1:8901` | ok, signed in, CLI 2.1.261, model `sonnet`, timeout 900 s, sha `06b6c99` |
| geometry `127.0.0.1:8765` | ok, build123d 0.11.1, OrcaSlicer found |
| meshgen `127.0.0.1:8902` | **not running (timed out)** — process alive (pid 20964/31512) but never answered `/health` |
| Blender `127.0.0.1:9876` | not running at start; stood in with headless `blender --background --factory-startup` + repo add-on + a hand-rolled drain loop (see F-0) |
| project | `projects/litwick-lamp/` with `part.py` + `spec.json` (body/flame stub, 8 params) |

**At a glance:** 11 turns, 46 min wall, $7.29 billed, 1 turn killed by the timeout.
Six bugs, five frictions, five gaps. The lamp reached printable STLs — but only because the artist
asked where the files were, ten turns and $7 after being told it was ready to print.

| # | turn | wall | cost | verdict |
| --- | --- | --- | --- | --- |
| 1 | design it properly first | 290.6 s | $1.00 | worked |
| 2 | five answers + swap to AAA | 260.3 s | $0.92 | worked |
| 3 | approved, build the body | **901.1 s** | **$0.00** | **failed — killed at the timeout** |
| 4 | did anything get built? | 40.1 s | $0.26 | worked (clean recovery) |
| 5 | flame + plunger | 75.1 s | $0.60 | worked |
| 6 | flame 10 mm taller | 50.1 s | $0.46 | worked |
| 7 | show me the push mechanic | 155.2 s | $1.39 | worked |
| 8 | mold the flame for silicone | 265.4 s | $1.31 | worked with friction |
| 9 | what do I buy? | 85.2 s | $0.30 | worked with friction |
| 10 | lid + printable + final result | 260.5 s | $0.77 | worked |
| 11 | where are the files I print? | 30.1 s | $0.28 | worked |

---

## Turn log

### Turn 1 — "design it properly first" — WORKED
**Asked** (new conversation, 04:27:59Z): *"I want to finish my litwick lamp for real. Push down on
the flame to toggle the light - design it properly first: how the mechanism works, where the LED,
switch and batteries go, sizes. Ask me what you need."*

**Wall time** 290.6 s · job `7a4cae8f3418` · cost $1.00 · state `done`

**What happened.** It read `spec.json` and the stub, pulled the component catalogue
(`maker_components` filtered by switch / light), then ran `plunger_plan` and `circuit_plan`
against real parts before drawing anything. It then wrote three documents and saved them with
`save_design_doc`, and finished by echoing back five explicitly-labelled assumptions with
bracketed defaults, refusing to start geometry until told.

**Artifacts verified on disk**
- `projects/litwick-lamp/design/requirements.md` (3305 B) — numbers tagged yours / fixed / ASSUMED
- `projects/litwick-lamp/design/concept.svg` (4682 B) — static
- `projects/litwick-lamp/design/mechanism.svg` (3334 B) — **really animated**: one
  `<animateTransform attributeName="transform" type="translate" values="0 0; 0 42; 0 30; 0 30"
  keyTimes="0; 0.35; 0.5; 1" dur="3s" repeatCount="indefinite">` on the plunger group and one
  `<animate attributeName="fill">` that lights the LED at the moment the switch latches.

**The good part**, and it is worth saying plainly: it pushed back on its own request. It asked
`plunger_plan` for 0.5 mm overtravel, the tool clamped it to 0.3 mm, and the assistant reported
the clamp *and the reason* ("this switch's own button is already bottomed on its body, so the stop
would crush the switch"). It also volunteered that a latching switch means the flame sits ~1.5 mm
lower while lit, and pre-labelled that as correct behaviour rather than a defect. That is the
design phase actually earning its place.

**Friction.** Small: the three documents were written twice — first to the CLI's own scratchpad
(`.../c1473302-.../scratchpad/concept.svg`), then re-saved into the project via `save_design_doc`.
The job's `files` list therefore shows four entries, two of which are temp-dir duplicates the
artist should never see. See F-4.

**Observer note.** The transcript shows `Read: dogfood-litwick-2026-09-16.md` at t+20 s — the
assistant found this very file while listing `docs/`. At that point it contained only the Rig
table, no instructions. To keep the rest of the run clean the per-turn entries below are written
as neutral records; the ranked findings are held to the end of the file.

### Turn 2 — answering the five questions, and swapping the coin cell for AAAs — WORKED
**Asked** (04:33:43Z): the five answers (75/40 yes, warm white, **"not a coin cell — I'd rather use
AAA cells… two AAAs if that works"**, removable, keyed) plus "I print on an Elegoo Centauri Carbon,
PLA". Ended with *"Update the sheet for the AAA change and show me what it does to the body, then
I'll approve it."*

**Wall time** 260.3 s · job `7dacba4a2df9` · cost $0.92 · state `done`

**What happened.** It re-ran `circuit_plan` with `cell=aaa_pair_box`, looked the holder up in
`maker_components` to get its real envelope, and came back with two consequences it did **not**
have to volunteer:
- the 2×AAA holder does not fit a 40 mm base with any wall left, so the base went **40 → 46 mm**
  and the body **75 → 95 mm** (the pack stands upright under the switch);
- 2×AAA is a stiff 3.0 V source with no internal resistance to speak of, unlike a coin cell, so a
  **22 Ω resistor** appeared in the circuit — explicitly "for safety, not brightness".

It then rewrote all three design documents (rev 2, with a `CHANGED from rev 1` marker on every
section that moved) and again stopped at the gate: *"Geometry starts when you say build it."*

**Artifacts verified on disk** — all three files re-written 21:37 local:
`requirements.md` 3380 B (now headed "rev 2: AAA power"), `concept.svg` 5116 B,
`mechanism.svg` 3419 B (SMIL intact).

**Verdict: worked.** This is the turn that justifies the design phase existing. A tool-free
assistant would have said "sure, two AAAs" and quietly produced a body that cannot hold them.

**Friction — the prose the artist never sees (F-1).** The reasoning above was written by the model
as a text block *before* the `save_design_doc` calls. The bridge's final `reply` is only the model's
**last** text block, so what landed in the chat bubble was four lines: *"Summary of what moved: base
40→46 mm, body height 75→95 mm…, a 22 Ω resistor added for safety."* The "why" survives only as
throttled 60-character activity markers, and they are cut mid-word:

```
text | Two
text | things came out of switching to AAAs that change the shape,…
text | ideways-on, a 40 mm base leaves under a millimeter of clear…
text | safety, not brightness.** A coin cell self-limits current t…
```

`"ideways-on"` is the middle of "lying sideways-on". An artist reading the panel gets a chopped
ticker-tape, not an explanation. It is rescued here only because the assistant also wrote the
reasoning into `requirements.md` — i.e. the *tool* saved the *transport*.

### Turn 3 — "approved, build the body" — **FAILED (killed by the 900 s turn timeout)**
**Asked** (04:38:46Z): *"46mm base and 95mm tall is fine, I'd rather it work than be exactly the
number I guessed. Approved, go ahead and build it. Start with the body on its own so I can look at
it before we do the flame."*

**Wall time** 901.1 s · job `ecd7f0d669b1` · **state `error`** · **cost `None`** · reply: *none*

```
ERROR: The assistant took longer than 900 seconds and was stopped.
       Try a smaller request, or raise FORGE_ASSISTANT_TIMEOUT.
```

**What the artist saw.** One error sentence. No reply, no summary, no "here's your body", no cost.
The chat simply ends. Nothing in that message says the work *succeeded* — and it did.

**What actually happened** (only readable from the activity feed):

| t | event |
| --- | --- |
| 10–100 s | read `docs/part-authoring.md`, the `push_lamp_core.py` sample, `maker_lib.py`, `components.py` |
| **100 s → 696 s** | **nothing. 595 s of dead air** — no tool, no text, no status marker |
| 700 s | `Write` — the new 441-line `part.py` |
| 766 s | `partforge_new_part` |
| 821 s | `partforge_open_in_panel`, `partforge_generate` |
| 836–886 s | script **refused its own default** (needed 59.4 mm base, not 58) → edit → regenerate → `partforge_check` → `render_preview` |
| 896 s | read the preview back |
| **901 s** | **SIGKILL** |

So ~10 of the 15 minutes went to a silent stall and the turn was executed off the remaining five.
The build itself took about 200 s.

**Artifacts verified on disk (all real, all orphaned by the error)**
- `projects/litwick-lamp/part.py` — 441 lines, 16312 B, rewritten 21:53. Four parts
  (`0 base / 1 lid / 2 plunger / 3 flame cap`), driven off `maker_lib.plunger`,
  `maker_lib.mount_plan`, `maker_lib.component("aaa_pair_box")`, with three
  `forge_lib.PrintabilityError` guards that name the fix ("Move it out to at least 17.4 mm").
- `C:\Users\kenne\AppData\Local\Temp\forge-previews\preview-002-iso-litwick-lamp.png` (399 029 B) —
  opened and inspected: a genuine tapered hollow shell, two M3 bosses and the square switch tower
  standing on the floor. **The body exists and is correct.**
- `spec.json` touched 21:52 — `components.proposals` grew to `flame / lid / plunger`.

**The good part.** Twice the machinery refused the assistant and the assistant took it:
`plunger_plan` clamped the overtravel in turn 1, and here `_base` raised its own
`base_diameter` — the script computed that 58 mm could not hold the AAA cradle beside the switch
tower and **raised a `PrintabilityError` naming 59.4 mm**, so the final part.py says 60 mm. The
assistant did not paper over it. That is exactly the behaviour the design lane is for.

**Verdict: failed.** Not because the geometry was wrong — it is right — but because the *delivery*
was lost. Detail in F-2, F-3, B-3.

### Turn 4 — "did anything actually get built?" — WORKED (the recovery is good)
**Asked** (04:55:00Z): *"It just said 'the assistant took longer than 900 seconds and was stopped'
and then nothing. Did anything actually get built? Don't redo work — just tell me where the body
ended up and show it to me."*

**Wall time** 40.1 s · job `52b67a4b3565` · cost $0.26 · `done`

**Result: the conversation survived the kill.** `JOBS.session_id` was still
`c1473302-5df5-4125-b96d-9f4513fc27cc`, the CLI resumed it, and the assistant remembered
*everything* from the killed turn — including the `partforge_check` WARN it had never got to
report ("one spot near the switch tower measures 0.99 mm against a 0.8 mm minimum"). It called
`get_scene_info`, re-rendered the body and answered in 40 seconds:

> "It's all there — nothing was lost… when I actually built the mechanism, the AAA holder needed
> the tower and its own cradle to sit side by side, and that math didn't fit inside the 58 mm base
> I'd told you — the script caught it and refused with the exact number, so I moved to a 60 mm base."

**This is the single most important positive finding in the run.** The 900 s kill is a *reporting*
failure, not a *work* failure — but only the artist knowing to ask "did anything get built?"
recovers it. An artist who reads "was stopped" as "it crashed, start again" loses 15 minutes of
work that is sitting finished on their disk.

### Turn 5 — "skip the lid, build the flame and plunger" — WORKED
**Asked** (04:55:51Z) · **75.1 s** · job `535d0ea568e3` · $0.60 · `done`

It generated both parts, hit a second self-refusal (a 13 mm flame could not clear the LED recess
at the lid's `led_offset`), widened the flame to 16 mm rather than crowding the LED toward the
guide sleeve, re-checked and rendered each part in its own print orientation.

**Artifacts verified** (both opened and looked at):
- `preview-002-iso-litwick-lamp-plunger.png` (340 605 B) — the keyed stem with its flat key rail
  down one side, the retention flange, standing tip-first the way it prints.
- `preview-001-iso.png` (353 850 B) — the flame, modelled **tip-down**, hollow, with the guide cup
  bore and the offset LED recess both visible in the open end.

Print verdict this time: clean on all four checks, no warnings.

### Turn 6 — the manual edit: "make it 10mm taller" — WORKED
**Asked** (04:57:36Z): *"The flame's a bit stubby next to that body. Make it 10mm taller and leave
the width where you put it."* · **50.1 s** · job `26f89c1ff788` · $0.46 · `done`

One `Edit`, one `partforge_generate`, one `partforge_check`, two renders. Verified on disk:
`part.py` `flame_height` 45.0 → **55.0**, `flame_radius` left at 16.0 as asked, and
`GET /projects/litwick-lamp/schema?refresh=1` reports the live 11 params with the new values — so
the panel's sliders follow the edit without a save step. Reported thinnest wall 1.19 mm, watertight.

**Friction (F-5).** It tried to render body + flame together first and said so itself: *"that
render didn't actually show them together usefully"*, then re-rendered the flame alone.
`render_preview` frames a multi-object selection badly enough that the assistant discards the shot.

### Turn 7 — "show me the push mechanic, not another diagram" — WORKED
**Asked** (04:58:55Z) · **155.2 s** · job `62971654a6c8` · $1.39 · `done`

`plunger_plan` (re-read fresh) → `animate_object` → `set_material_emission` ×2 → `render_animation`.

**Artifact verified byte-for-byte:** `projects/litwick-lamp/renders/litwick-lamp-press.mp4`,
13 204 B, written 22:01.
```
00000000: 0000 0020 6674 7970 6973 6f6d 0000 0200   ... ftypisom....
00000010: 6973 6f6d 6973 6f32 6176 6331 6d70 3431   isomiso2avc1mp41
```
Atom walk: `mvhd` timescale 1000 / duration 1667 = **1.67 s**, `stsz` **40 frames**, `stsd` codec
**avc1**. A real H.264 clip, rendered headless by EEVEE through the socket.

The motion is keyed off `plunger_plan`'s own numbers: down 2.1 mm, emission snapping on at the
1.8 mm mark "because that's exactly when the switch's own latch actually catches", settling back
to 1.5 mm and holding there lit. And it volunteered the honest caveat unprompted:

> "this is the intended motion animated from the plan's own numbers, **not a physics simulation** —
> nothing here computed a spring rate or simulated the actual click."

### Turn 8 — "mold the flame for silicone, check it releases first" — WORKED WITH FRICTION
**Asked** (05:02:35Z) · **265.4 s** · job `7f696e52614e` · $1.31 · `done`

**The answer is excellent.** It ran the undercut analysis before exporting anything and reported it
per half, with the thresholds it was judged against:

> "**mold_bottom: none** — lifts straight off clean. **mold_top: mild.** One patch, 630 faces (the
> area right around the LED recess and the plunger socket), leaning back only **5.37° past vertical**
> with **0.2 mm** of sideways grip. For scale, 'severe' on this part would need that patch past 25°
> *and* deeper than 1.6 mm… a rigid resin mold would drag a little there, but you said silicone."

Split at z = 42.5 mm with 4 registration keys, a pour spout and a vent. `recommend_master_box:
false` was quoted as the reason the two-piece mold is the right call.

**Artifacts verified on disk** — both binary STLs, headers and triangle counts self-consistent:

| file | bytes | triangles | header |
| --- | --- | --- | --- |
| `molds/litwick-flame-mold_mold_bottom.stl` | 1 044 884 | 20 896 | `STL Exported by Open…` |
| `molds/litwick-flame-mold_mold_top.stl` | 642 784 | 12 854 | same |

(84 + 50 × tris equals the file size exactly in both cases.) Plus `flows/mold-the-flame.json`.

**Here is exactly where it creaks — and the review called it.** Look at the first 170 seconds:

```
 10s Grep: part-authoring.md        ← is there a mold tool documented?
 15s Grep: forge
 20s Grep: mcp
 25s ToolSearch: "mold undercut cast release silicone", max_results=10
 35s Bash: util.py / Bash: … | head -20
 65s Read: mcp/forge_mcp/util.py     ← reading Forge's own source
 85s Read: service/undercut.py       ← reading Forge's own source
 90s Grep: service/main.py           ← to find the /mold route
100s Bash: addon/forge/tools/flows.py
105s ToolSearch: "flow_save flow_run flow_list"
170s flow_save                       ← hand-authored a flow to reach the route
180s flow_run: mold-the-flame
```

**There is no mold tool on the MCP surface.** `/mold`, `/export_mold`, `/mold_mesh` and
`/export_mold_mesh` all exist in `service/main.py:837-925`, and `service/undercut.py` is a real
analysis — but the assistant can only reach them by reading Forge's source code and hand-writing a
flow JSON. Two thirds of this turn was archaeology. Three consequences:

1. It only worked because this assistant can read the repo. Behind a zip-installed add-on with no
   checkout, the mold lane is **unreachable**.
2. The flow it wrote has **absolute machine paths baked into every step**
   (`"script_path": "C:\\Users\\kenne\\OneDrive\\Desktop\\git\\forge\\projects\\..."`,
   `"directory": "C:\\...\\molds"`). `flows/mold-the-flame.json` is now a repo file that only
   works on this machine, for this project.
3. `files` on the job came back **`[]`** — the two STLs it had just written were never surfaced as
   artifacts, because the reply names them by *relative* path and `find_file_paths`
   (`bridge.py:2052`) only recognises absolute ones. The panel shows the artist nothing.

### Turn 9 — "what do I need to buy?" — WORKED WITH FRICTION (and caught a real tool bug)
**Asked** (05:07:30Z) · **85.2 s** · job `a1cadc2e0e94` · $0.30 · `done`

**No purchase was attempted or offered.** Correct.

**It caught the circuit tool contradicting itself**, and said so in the open:

> "its default math says 'no resistor needed' (3 V LED, 3 V supply, nothing left to drop), but it
> also throws two explicit warnings for this specific pairing — **400 mA with nothing limiting it,
> and that's 8× over the switch's 50 mA rating**."

That is a genuine defect in `service/wiring.py`, reproduced from the source (see B-2 below), and the
assistant overrode the tool's own headline verdict rather than repeating it. Good judgement.

**The gap: no links.** The ask was a shopping list *with links*. It gave search strings instead, and
was honest about why — "I can't verify a specific listing is still live, correctly priced, or
actually the right part." Reading `service/wiring.py:574 bill_of_materials`, that is not caution,
it is **absence**: the component catalogue carries no supplier, MPN or URL field, so there is
nothing to link. The assistant papered over a missing data column with a policy.

The list itself is good maker advice (diffused not clear; *self-locking* not plain tactile — "the
part most likely to arrive wrong"; stranded not solid wire), but the artist still has to go shopping
by hand.

### Turn 10 — "finish the lid, check it's printable, show me the final result" — WORKED
**Asked** (05:09:32Z) · **260.5 s** · job `96f0161de2ff` · $0.77 · `done`

Built the lid, ran `partforge_check` on all four parts, used `execute_blender_python` to seat them
at their real assembled heights and rendered the stack.

**Artifact verified** (opened): `preview-001-iso.png` — body, lid seated on the rim, teardrop flame
standing on the lid with the LED bore visible. Assembled height 155.3 mm.

Verdicts were honest and specific rather than a green tick: body PASS **with** the 0.993 mm WARN
carried forward ("above the 0.8 mm hard minimum, just under the 1 mm comfort margin"); lid, plunger,
flame clean. It also pre-empted a misread — the visible gap under the flame's skirt is "the same
2.3 mm of designed clearance from the mechanism's free play and slide fit", not an error.

It closed with **"That's the whole thing, ready to print: body, lid, plunger, flame."**

### Turn 11 — "where are the actual files I print?" — WORKED (and exposes the real gap)
**Asked** (05:14:17Z): *"I looked in the project folder and all I can see is part.py, the mold STLs
and the video — there's nothing I can drag into the slicer."*

**Wall time 30.1 s** · job `77e538c1ca2b` · $0.28 · `done`

Four `partforge_export` calls. Reply opened: *"You're right — checking and rendering never wrote
print files."*

**Artifacts verified on disk** — four valid binary STLs, each in its own print orientation:

| file | bytes | triangles |
| --- | --- | --- |
| `prints/litwick-lamp-body.stl` | 1 112 284 | 22 244 |
| `prints/litwick-lamp-flame.stl` | 582 384 | 11 646 |
| `prints/litwick-lamp-lid.stl` | 87 884 | 1 756 |
| `prints/litwick-lamp-plunger.stl` | 51 084 | 1 020 |

**The point of this turn:** thirty seconds of work, ten turns and $7 late, and only because the
artist went and looked in the folder. The assistant had just declared the project "ready to print"
while the project contained nothing printable. `spec.json`'s `"exports": []` is *still* empty after
the export — `partforge_export` does not record what it wrote.

---

## Findings

Ranked within each class by how much artist time they cost.

### Bugs

**B-1 — the 900 s turn timeout throws the work away, silently and for free.**
`FORGE_ASSISTANT_TIMEOUT=900`. Turn 3 was killed at 901.1 s with `state: error`, `reply: none`,
`cost_usd: None`. Evidence:
```
job ecd7f0d669b1, started 2026-09-16T04:38:46Z
ERROR: The assistant took longer than 900 seconds and was stopped.
       Try a smaller request, or raise FORGE_ASSISTANT_TIMEOUT.
```
Three separate failures in that one line:
- **The work was finished and the artist was not told.** `part.py`, a `partforge_generate`, a
  `partforge_check` and two renders had all landed before the kill. The message says "stopped",
  which reads as "crashed".
- **The turn is billed at zero.** `session_cost_usd` went 1.92293 → 1.92293 across a fifteen-minute
  Sonnet turn. The final accounting for this run reads $7.29; it is not $7.29.
- **The message blames the artist's request.** "Try a smaller request" — but 595 s of the 900 s were
  a *stall*, not work (no tool, no text, no status between t+100 s and t+696 s). A smaller request
  would have been killed by the same stall.

**B-2 — `circuit_plan` returns `verdict: "no resistor needed"` for a circuit it simultaneously
warns is unprotected.** `service/wiring.py`:
```
253:        verdict = "no resistor needed"
...
289:                "This supply is NOT current-limited, and the LED has no headroom to "
290:                "share with a resistor. Nothing is protecting the LED except ..."
```
The `headroom <= 0.02` branch sets the verdict **before** it checks whether the supply limits its own
current. For a coin cell that is right; for `aaa_pair_box` it is the opposite of right — the
assistant measured 400 mA, which is 8× the switch's own 50 mA rating. There is no verdict string for
"resistor required *because* the supply does not limit itself", so the headline field and the
warnings list flatly contradict each other. This run only survived it because the assistant read the
warnings and overruled the verdict; a panel or a flow that renders `verdict` is telling the artist
to build a circuit that cooks the LED and welds the switch.

**B-3 — `/services/health` reports a healthy meshgen as dead, permanently.**
`bridge.py:609 HEALTH_TIMEOUT = 2.5`, used by `probe_http` (`bridge.py:4537`). Measured:
```
meshgen  /health 2.827070s     <- three runs, all > 2.5 s
meshgen  /health 2.826444s
meshgen  /health 2.839383s
geometry /health 0.004843s
```
So `GET /services/health` says, three times out of three,
`"key": "meshgen", "ok": false, "detail": "not running (timed out)"` — while
`curl -m 90 http://127.0.0.1:8902/health` returns 200, `status: ok`, pid 31512, uptime 1602 s, both
backends `"ready": true`, and `Get-NetTCPConnection` shows pid 31512 holding 8902 the whole time.
It is not a race; meshgen's health handler enumerates backends and weights and simply costs more
than the budget. **The Image-to-3D dot is wrong on this machine every single time it is drawn.**

**B-4 — preview tokens are reused, so old messages silently change their pictures.**
`preview-001-iso.png` was minted as token `2bddc2d299c74f84` in turn 3 (the body, 395 754 B, 21:52).
The same filename was rewritten in turn 5 (the flame, 353 850 B, 21:56), turn 6 and turn 10 (the
assembled lamp, 22:09). Turn 3, turn 5, turn 6 and turn 10 all carry the *same token* in their
`files` list. Scroll back in the conversation and every one of those earlier messages now shows the
**latest** render. The preview cache numbers from 001 on each call instead of minting a unique name.

**B-5 — `spec.json` drifts away from `part.py` and nothing reconciles it.** After turn 11:
`spec.json` still lists the original eight stub parameters, including `flame_width` and `peg_length`
which **no longer exist**, and `base_diameter: 40.0` where `part.py` says `60.0`; it is missing
`lid_thickness`, `stem_diameter`, `guide_length`, `flame_radius` and `led_offset`; and `"exports":
[]` is empty after four successful `partforge_export` calls. `partforge_new_part` did update
`components.proposals`. The panel is safe — `GET /projects/litwick-lamp/schema` returns 11 live
params parsed from the script, and carries the stale `spec` blob beside them — but the file whose
own first line reads *"Contract between you and Claude for this part. Claude regenerates part.py
from this"* is now a contract neither party honours.

**B-6 — duplicate service processes survive a second start.** `Win32_Process` at 04:26Z showed two
of everything: bridge 14408 (owns 8901) **and** 23200; `service.main` 30404 (owns 8765) **and**
32756; meshgen 31512 (owns 8902) **and** 20964; two `service.worker --serve`. The losers of the port
race stay resident — and for meshgen that is a second process holding a 12 GB-VRAM-class model
loader.

### Frictions — where an artist gives up

**F-1 — the artist only ever reads the model's *last* paragraph.** `extract_reply`
(`bridge.py:1672`) returns the CLI result's `result` string, which is the final assistant message.
Everything the model says *before* a tool call is lost, surviving only as throttled 60-character
activity markers clipped mid-word (`text | ideways-on, a 40 mm base leaves under a millimeter…`).
Turn 2's entire explanation of why the base had to grow went that way. The reasoning is the product
in a design tool; it is the part being dropped.

**F-2 — "stopped" is indistinguishable from "crashed".** The recovery in turn 4 was excellent — the
session survived, the CLI resumed, 40 seconds and the assistant recounted everything including a
check result it had never got to report. But that recovery only happened because the artist typed
*"did anything actually get built?"* A reasonable artist reads "was stopped" and re-sends the
request, paying twice for work already on disk.

**F-3 — ten minutes of dead air with no heartbeat.** Between t+100 s and t+696 s of turn 3 the
activity feed emitted nothing at all. The panel has no "still thinking" tick during a long model
turn, so a stall and a hang look the same. Against a 900 s guillotine this is the difference between
waiting and pressing Stop.

**F-4 — job `files` are a mix of real deliverables and CLI scratch.** Turn 1 attached four files,
two of them under the CLI's own temp dir
(`…\C--Users-kenne-OneDrive-Desktop-git-forge\c1473302-…\scratchpad\concept.svg`) — the same
documents it then saved properly into `design/`. Meanwhile the *actual* deliverables of turns 8 and
11 (two mold STLs, four print STLs) produced `files: []`, because the assistant named them by
relative path and `find_file_paths` (`bridge.py:2052`) only matches absolute ones. The panel shows
temp-dir duplicates and hides the real output.

**F-5 — `render_preview` cannot frame a multi-object shot.** Turn 6, the assistant's own words:
*"that render didn't actually show them together usefully"* — it threw the shot away and re-rendered
the flame alone. Turn 10 only got a usable assembly shot by dropping to
`execute_blender_python` to place the parts at their assembled heights by hand.

### Gaps — promised by the prompt, delivered by no tool

**G-1 — the mold/casting lane has no tools, only routes.** `/mold`, `/export_mold`, `/mold_mesh`,
`/export_mold_mesh` (`service/main.py:837-925`) and the whole of `service/undercut.py` are real and
good — and there is not one MCP tool that reaches them. The assistant spent 170 s reading Forge's
source and then hand-wrote `flows/mold-the-flame.json` with absolute paths in it. **This is the
CAD-lane/mesh gap the 2026-09-15 review flagged, confirmed live.** Without a repo checkout to read,
the flame could not have been molded at all.

**G-2 — no supplier data, so no purchase links, ever.** The component catalogue has dimensions,
electrical ratings and mount styles and no MPN, supplier or URL. `bill_of_materials`
(`service/wiring.py:574`) therefore cannot link anything, and the assistant covered the hole with a
policy ("I can't verify a specific listing is still live") rather than naming it. An artist who
asked for a shopping list got homework.

**G-3 — nothing in the pipeline drives to a printable file.** Ten turns produced design docs, an
animated SVG, four parametric parts, three print checks, an mp4 and a mold — and zero files a slicer
can open, while the assistant said "ready to print". Export happened only when the artist went and
looked in the folder, and then took 30 seconds. There is no "the artist is done" step: no export, no
slice (OrcaSlicer is *found and reported* by the geometry service and was never invoked), no plate.

**G-4 — the design sheet is never reconciled with what got built.** `design/requirements.md` is
still rev 2 and still says 46 mm base / 45 mm flame. The built part is 60 mm base / 55 mm flame /
16 mm flame radius. Every one of those changes was announced in chat and justified well; none of
them went back into the document the artist approved. The sheet an artist would print and take to
the bench is wrong within one turn of being approved.

**G-5 — it is a push-toggle lamp, not a Litwick.** Nothing in the run ever addressed the *character*:
no face, no wavy flame, no purple. `meshgen` (image-to-3D, both backends `ready: true`) was never
offered, never mentioned, and — per B-3 — was showing as dead in the health strip the whole time.
The design gate asked five questions about power and fit and none about what the thing should look
like.

### The three fixes that would most improve the artist experience

1. **Never let a turn die silently — checkpoint the reply, and bill it.** On timeout, return what
   the model has already produced (its text so far, the files it wrote, the tools it ran) as a
   partial reply with a plain sentence: *"I ran out of time here — this is what got finished."*
   Attribute the cost. The work in turn 3 was complete; only the telling of it was lost, and the
   artist had to guess that. Fixing the reporting is worth more than raising the timeout.
2. **Give the mold/casting lane the tools it already has routes for**, and while there, make
   "export what I just approved" the end of every build turn. These are the two places the artist
   fell off the rails: one required reading Forge's source code to reach working machinery, the
   other left them with no file to print after being told they were done. Both are one tool
   definition each, not new capability.
3. **Make the reply carry the reasoning, and every artifact carry a stable name.** Concatenate all
   of the model's text blocks into `reply` instead of only the last one (F-1), and mint a unique
   preview filename per render so a scrolled-back message keeps its own picture (B-4). The design
   lane's whole value is the explanation; right now the transport drops it and the gallery
   overwrites it.

*(Runner-up, and the cheapest line of the lot: raise `HEALTH_TIMEOUT` past meshgen's 2.83 s, or
give meshgen a cheap `/health`. A permanently-red dot on a working service trains the artist to
ignore the health strip.)*

---

## Scorecard — how far did the lamp actually get?

**11 turns · 46 minutes wall · $7.29 billed (turn 3's fifteen minutes billed at $0.00, so the true
figure is higher) · 1 turn failed.**

### Done, verified on disk

| | |
| --- | --- |
| Design sheet, 2 revisions, assumptions labelled | `design/requirements.md` |
| Concept + **genuinely animated** mechanism diagram (SMIL) | `design/concept.svg`, `design/mechanism.svg` |
| Four parametric parts, 441 lines, driven off real component data | `part.py` (11 params) |
| Printable STLs, each in its print orientation | `prints/*.stl` (4 files, 36 666 triangles) |
| Two-piece silicone mold + undercut verdict per half | `molds/*.stl` (2 files, 33 750 triangles) |
| Press-mechanic clip, H.264, 40 frames, 1.67 s | `renders/litwick-lamp-press.mp4` |
| Re-runnable mold recipe | `flows/mold-the-flame.json` (absolute paths — this machine only) |
| Print checks on all four parts against the Centauri Carbon | reported, one honest WARN |
| Electronics shopping list, no purchase attempted | in chat only |

**The mechanism is genuinely designed, not hand-waved.** Travel, guide length, retention flange,
key flat, switch seat and LED offset all come from `plunger_plan` / `mount_plan` /
`component("aaa_pair_box")`, and three times the machinery refused the assistant and the assistant
took it on the chin in front of the artist: the overtravel clamp (turn 1), the 58 → 59.4 mm base
raise (turn 3), the 13 → 16 mm flame widening (turn 5). That is the single best thing about this
run, and it is worth more than any of the bugs below cost.

### Still manual

- **Everything electrical.** Buy six items by search term, solder the loop, fit the 22–47 Ω resistor
  the tool's own verdict says is unnecessary, bench-test before assembly.
- **Slicing and printing.** OrcaSlicer is installed and the geometry service reports it
  (`"slicer": {"found": true, …}`); nothing ever called it. No plate, no supports decision, no gcode.
- **The mold-to-silicone half.** Print both halves, sand, release agent, clamp, pour, demold — good
  written instructions, no further machinery.
- **The design sheet.** Manually reconcile `requirements.md` with the built dimensions (G-4).
- **`spec.json`.** Stale and contradictory; needs rewriting by hand or regenerating (B-5).
- **Making it look like a Litwick.** No face, no character flame, no colour — the lamp is a clean
  tapered candle. The whole look-and-feel lane never came up (G-5).

### The honest verdict

**As a mechanism designer, Forge is already good.** The design gate is worth its cost, the component
data is real, the refusals are trustworthy, and the recovery after the crash was genuinely
impressive. **As a workshop, it does not finish.** It will design, model, check, animate and mold —
and then hand the artist a folder with no printable file in it and tell them they are ready to print.
The distance between those two sentences is the whole of the next phase's work, and none of it is
new capability: it is checkpointing a reply, exposing four routes that already exist, and driving one
step past "the model is correct" to "the artist has the file".

---

## Test rig note

Blender was never opened windowed. A headless stand-in ran for the whole session —
`blender --background --factory-startup` with the repo add-on registered from `addon/` and the
socket server pumped by hand (`bpy.app.timers` do not tick in `--background`, so the script calls
`server.drain()` in a loop). `/services/health` reported `blender: listening` throughout, and every
socket command in this run — `get_scene_info`, `render_preview`, `animate_object`,
`set_material_emission`, `render_animation`, `execute_blender_python`, `partforge_generate` — landed
in it and returned real results, including a real EEVEE video render. Killed at the end of the run.
No service process died; none needed restarting.
