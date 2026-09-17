"""A stand-in for the Claude Code CLI, for the bridge tests.

It does two jobs: **validate** the command line the bridge built (and fail loudly
if the flags that make the assistant work went missing) and **print canned JSON**
— either the single object ``--output-format json`` used to produce, or the
newline-delimited event stream ``--output-format stream-json
--include-partial-messages`` produces now.  Every invocation is appended to
``FAKE_CLAUDE_LOG`` as one JSON line so the tests can assert on argv after the
fact — most usefully that turn two carried ``--resume``.

Behaviour is steered by ``FAKE_CLAUDE_MODE``:

``ok``                 print a normal result object (the default)
``noise``              print a warning line *before* the JSON, to exercise salvage
``garbage``            print no JSON at all
``slow``               sleep ``FAKE_CLAUDE_SLEEP`` seconds first (for cancel)
``reject_permission``  exit 1 complaining about ``--permission-mode`` unless it
                       is ``acceptEdits`` — the older-CLI fallback path
``api_error``          print the CLI's ``is_error`` result shape
``auth_error``         print the ``is_error`` shape a signed-out CLI produces,
                       so ``/health``'s ``last_auth_error`` has something real
                       to read
``stream``             the full event stream: two tool calls (one with its
                       arguments arriving as ``input_json_delta`` chunks), a
                       malformed line, text deltas, then the result event
``stream_noresult``    the same stream with the result event withheld, exit 0 —
                       the salvage path
``stream_slow``        the stream, sleeping ``FAKE_CLAUDE_SLEEP`` seconds after
                       the tool events, so a cancel lands mid-stream
``stream_hang``        text and tools, then silence forever (it sleeps
                       ``FAKE_CLAUDE_SLEEP``, default 600, and never prints a
                       result): the turn the watchdog has to kill.  What it said
                       and did before the silence is what the bridge must still
                       be able to hand back — see B-1 in
                       ``docs/dogfood-litwick-2026-09-16.md``

``FAKE_CLAUDE_STREAM_COST`` makes the stream volunteer a running cost before the
result event, so a turn that is killed can still be billed.

``FAKE_CLAUDE_PREAMBLE`` adds a text block BEFORE the tool calls, which is where
a real model puts its reasoning.  The CLI's ``result`` field only ever carries
the LAST block, so a bridge that reads only that drops this one — the whole of
friction F-1, reproducible in one environment variable.

``FAKE_CLAUDE_EXPECT_IMAGE`` asserts on the Phase 6c attachment block: set to a
path, the prompt must carry that path under ``--- Attached reference image ---``
with the Read instruction and ``Read`` still in ``--allowedTools``; set to the
empty string, the prompt must carry no attachment block at all.

``FAKE_CLAUDE_EXPECT_MODEL`` asserts on the per-request model selector: set to a
name, ``--model`` must carry exactly that; set to the empty string, ``--model``
must be absent altogether (the CLI's own default).  Whatever the flag says is
echoed back as the result's ``model`` field, so a test can read the effective
model off the job as well as off the argv log.

``FAKE_CLAUDE_AUTH_FILE`` names a flag file: while it exists the run behaves as
``auth_error``, and deleting it signs the fake back in.  A mode is fixed for the
life of the bridge process (it is read from the environment it was started with),
so a file is the only way for one test to watch the signed-out signal appear and
then clear.

``FAKE_CLAUDE_REPLY`` replaces the reply text in the result event, and
``FAKE_CLAUDE_RENDER_PATH`` adds a tool call that "wrote" a file at that path
(and a tool result saying so).  Together they are how the Phase 9 tests give
the bridge a real path to mint a ``/file`` token for — one arriving through the
activity, one arriving through the reply.

``FAKE_CLAUDE_STREAM=1`` selects ``stream`` without naming a mode, and
``FAKE_CLAUDE_STREAM_FILE`` names a flag file whose existence does the same.
The *output* shape and the *flags the bridge must pass* are deliberately
independent: the bridge always asks for stream-json, and the json modes prove
the bridge still copes with a build that answers with one object anyway.

The bridge runs this file through ``sys.executable`` because
``FORGE_ASSISTANT_CLAUDE`` points at a ``.py`` path; see ``bridge.launcher``.
"""

import json
import os
import sys
import time

REPLY = "OK - the fake assistant answered."

#: Kept in step with ``bridge.IMAGE_DIVIDER`` on purpose rather than imported:
#: this file stands in for a separate program, and a copy that drifts is exactly
#: the failure the assertion below exists to catch.
IMAGE_DIVIDER = "--- Attached reference image ---"

#: The streamed reply, in the chunks the CLI would emit it in.
STREAM_CHUNKS = [
    "Done - I cut it into 4 wedges, ",
    "each about 120 mm across, ",
    "so every piece fits your 256 mm bed. ",
    "They are laid out the way they will sit on the plate. ",
    "The joints are dovetails, ",
    "so they hold without glue.",
]

STREAM_REPLY = "".join(STREAM_CHUNKS)

#: Deliberately longer than the 60-character argument budget, and made of keys
#: with no short identifying value, so the tests can prove the label is clipped
#: rather than dumped whole into a 40-character-wide sidebar.
LONG_SEGMENT_ARGS = {
    "overrides": {"bowl_diameter": 152.4, "wall_thickness": 3.2, "feet": 4},
    "joint": {"type": "dovetail", "tolerance": 0.2},
    "include_mesh": True,
}


def fail(message):
    sys.stderr.write("fake_claude: %s\n" % message)
    sys.stderr.write("argv: %s\n" % json.dumps(sys.argv[1:]))
    sys.exit(3)


def flag(argv, name):
    """The value after ``name``, or ``None`` if the flag is absent."""
    if name not in argv:
        return None
    index = argv.index(name)
    return argv[index + 1] if index + 1 < len(argv) else ""


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def emit_raw(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def stream_event(event):
    emit({"type": "stream_event", "event": event})


def tool_block(index, tool_id, name, chunks):
    """A tool call the way the CLI streams one: name first, arguments after."""
    stream_event({
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "tool_use", "id": tool_id, "name": name,
                          "input": {}},
    })
    for chunk in chunks:
        stream_event({
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": chunk},
        })
    stream_event({"type": "content_block_stop", "index": index})


def reply_text(default):
    """What the result event says — overridable so a test can plant a path."""
    return os.environ.get("FAKE_CLAUDE_REPLY") or default


def render_events():
    """A tool call that wrote a file, if ``FAKE_CLAUDE_RENDER_PATH`` names one.

    Emitted as both a tool *input* and a tool *result*, because those are the
    two places a real render's path shows up and the bridge mints tokens from
    each of them.
    """
    path = os.environ.get("FAKE_CLAUDE_RENDER_PATH")
    if not path:
        return
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_03", "name": "mcp__forge__render_view",
         "input": {"path": path}}]}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_03",
         "content": "wrote %s" % path}]}})


def preamble_events():
    """The paragraph a model writes BEFORE it touches a tool, if asked for."""
    text = os.environ.get("FAKE_CLAUDE_PREAMBLE")
    if not text:
        return
    stream_event({"type": "content_block_start", "index": 9,
                  "content_block": {"type": "text", "text": ""}})
    # In two pieces, so a bridge that glued the chunks together without
    # knowing where the block ended would be indistinguishable from one that
    # kept them apart — the split is mid-sentence on purpose.
    half = max(1, len(text) // 2)
    for chunk in (text[:half], text[half:]):
        stream_event({"type": "content_block_delta", "index": 9,
                      "delta": {"type": "text_delta", "text": chunk}})
    stream_event({"type": "content_block_stop", "index": 9})


def run_stream(session_id, model, mode):
    """The event sequence a real turn produces, in the real order."""
    emit({"type": "system", "subtype": "init", "session_id": session_id,
          "tools": ["mcp__forge__partforge_check"], "model": model})

    preamble_events()

    # Tool 1: arguments stream in as partial JSON, so the label starts as the
    # bare tool name and is completed in place.
    tool_block(0, "toolu_01", "mcp__forge__partforge_check",
               ['{"script_', 'path": "C:\\\\parts\\\\', 'part.py"}'])
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_01",
         "content": "overall: warn"}]}})

    # A line no parser should ever accept. It must be skipped, not fatal.
    emit_raw('{"type": "stream_event", "event": {"type": "content_block_')

    # Tool 2: the assembled assistant message carries the whole input at once.
    tool_block(1, "toolu_02", "mcp__forge__partforge_segment", [])
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_02",
         "name": "mcp__forge__partforge_segment",
         "input": LONG_SEGMENT_ARGS}]}})

    render_events()

    # A build that volunteers the running cost mid-stream.  Its whole purpose
    # is the turn that never reaches its result event: that is the one the
    # dogfood run billed at $0.00 after fifteen minutes of Sonnet.
    cost = os.environ.get("FAKE_CLAUDE_STREAM_COST")
    if cost:
        stream_event({"type": "message_delta", "delta": {"stop_reason": None},
                      "usage": {"input_tokens": 99, "output_tokens": 5},
                      "total_cost_usd": float(cost)})

    if mode == "stream_hang":
        # Nothing more, ever: no text, no tool, no result.  The bridge has to
        # notice by itself and keep what is already above.
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "600")))
        return 0

    if mode == "stream_slow":
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "30")))

    stream_event({"type": "content_block_start", "index": 2,
                  "content_block": {"type": "text", "text": ""}})
    for chunk in STREAM_CHUNKS:
        stream_event({"type": "content_block_delta", "index": 2,
                      "delta": {"type": "text_delta", "text": chunk}})
        time.sleep(float(os.environ.get("FAKE_CLAUDE_TEXT_SLEEP", "0.02")))
    stream_event({"type": "content_block_stop", "index": 2})

    if mode == "stream_noresult":
        return 0

    emit({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": reply_text(STREAM_REPLY),
        "session_id": session_id,
        "total_cost_usd": 0.0123,
        "num_turns": 2,
        "duration_ms": 42,
        "model": model,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    })
    return 0


def streaming(mode):
    if mode.startswith("stream"):
        return True
    if os.environ.get("FAKE_CLAUDE_STREAM") == "1":
        return True
    flag_file = os.environ.get("FAKE_CLAUDE_STREAM_FILE")
    return bool(flag_file and os.path.isfile(flag_file))


def main():
    argv = sys.argv[1:]

    if "--version" in argv:
        sys.stdout.write("9.9.9 (Fake Claude)\n")
        return 0

    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    if log_path:
        record = {"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(),
                  "time": time.time()}
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")

    # A flag file, not a mode: the bridge process carries one environment for
    # its whole life, and a test that watches the signed-out signal clear needs
    # to change the answer between two turns of the same bridge.
    auth_file = os.environ.get("FAKE_CLAUDE_AUTH_FILE")
    if auth_file and os.path.isfile(auth_file):
        mode = "auth_error"

    # --- the contract the bridge must keep -------------------------------
    if "-p" not in argv:
        fail("no -p flag: this was not a headless one-shot run")
    prompt = flag(argv, "-p")
    if not prompt:
        fail("-p carried no prompt")
    output_format = flag(argv, "--output-format")
    if output_format != "stream-json":
        fail("--output-format stream-json is required; the bridge parses the "
             "event stream to show the artist what it is doing (got %r)"
             % (output_format,))
    if "--verbose" not in argv:
        fail("--verbose is required alongside stream-json in -p mode")
    if "--include-partial-messages" not in argv:
        fail("--include-partial-messages is required: without it there are no "
             "text deltas and the activity list goes silent while it writes")

    tools = flag(argv, "--allowedTools")
    if tools is None:
        fail("--allowedTools missing: the run would be unable to touch Blender")
    if os.environ.get("FAKE_CLAUDE_EXPECT_TOOLS") is not None:
        expected = os.environ["FAKE_CLAUDE_EXPECT_TOOLS"]
        if tools != expected:
            fail("--allowedTools was %r, expected %r" % (tools, expected))

    prompt_file = flag(argv, "--append-system-prompt-file")
    if not prompt_file or not os.path.isfile(prompt_file):
        fail("--append-system-prompt-file must point at a real file, got %r"
             % (prompt_file,))

    # Phase 6c: an attached reference image is a block in the prompt, and the
    # allow-list must NOT have grown to carry it (Read already renders images).
    expect_image = os.environ.get("FAKE_CLAUDE_EXPECT_IMAGE")
    if expect_image is not None:
        has_block = IMAGE_DIVIDER in prompt
        if expect_image.strip():
            if not has_block:
                fail("the prompt carries no %r block, so the model would never "
                     "look at the attached image" % IMAGE_DIVIDER)
            if expect_image not in prompt:
                fail("the attachment block does not name %r; prompt tail: %r"
                     % (expect_image, prompt[-300:]))
            if "Read tool" not in prompt:
                fail("the attachment block must tell the model to Read the image")
            if "Read" not in (tools or ""):
                fail("Read must stay in --allowedTools or the image cannot be "
                     "opened at all (got %r)" % (tools,))
        elif has_block:
            fail("no image was attached, but the prompt carries a %r block"
                 % IMAGE_DIVIDER)

    # The per-request model selector: the panel's choice has to survive all the
    # way onto the command line, and "no choice anywhere" has to mean no flag.
    expect_model = os.environ.get("FAKE_CLAUDE_EXPECT_MODEL")
    if expect_model is not None:
        got_model = flag(argv, "--model")
        if expect_model.strip():
            if got_model != expect_model:
                fail("--model was %r, expected %r" % (got_model, expect_model))
        elif got_model is not None:
            fail("no model was chosen and none is configured, so --model must "
                 "be absent; got %r" % (got_model,))

    if mode == "reject_permission":
        mode_value = flag(argv, "--permission-mode")
        if mode_value != "acceptEdits":
            sys.stderr.write(
                "error: unknown option '--permission-mode %s'\n" % mode_value)
            return 1

    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "30")))

    if mode == "garbage":
        sys.stdout.write("this is not json at all\nnor is this\n")
        sys.stderr.write("something went sideways\n")
        return 1

    session_id = flag(argv, "--resume") or os.environ.get(
        "FAKE_CLAUDE_SESSION", "sess-fake-0001")
    model = flag(argv, "--model") or "default-model"

    if streaming(mode):
        return run_stream(session_id, model,
                          mode if mode.startswith("stream") else "stream")

    failures = {
        "api_error": "the model refused",
        # The CLI's own words when nobody has run /login on this machine; the
        # bridge rewrites them for the artist and reads the class off them.
        "auth_error": "Invalid API key · Please run /login",
    }

    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": mode in failures,
        "result": failures.get(mode, reply_text(REPLY)),
        "session_id": session_id,
        "total_cost_usd": 0.0123,
        "num_turns": 1,
        "duration_ms": 42,
        "model": model,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }

    if mode == "noise":
        # exactly the failure salvage_json() exists for
        sys.stdout.write("(node:1234) ExperimentalWarning: something\n")
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
