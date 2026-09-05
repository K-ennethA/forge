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

``FAKE_CLAUDE_EXPECT_IMAGE`` asserts on the Phase 6c attachment block: set to a
path, the prompt must carry that path under ``--- Attached reference image ---``
with the Read instruction and ``Read`` still in ``--allowedTools``; set to the
empty string, the prompt must carry no attachment block at all.

``FAKE_CLAUDE_AUTH_FILE`` names a flag file: while it exists the run behaves as
``auth_error``, and deleting it signs the fake back in.  A mode is fixed for the
life of the bridge process (it is read from the environment it was started with),
so a file is the only way for one test to watch the signed-out signal appear and
then clear.

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


def run_stream(session_id, model, mode):
    """The event sequence a real turn produces, in the real order."""
    emit({"type": "system", "subtype": "init", "session_id": session_id,
          "tools": ["mcp__forge__partforge_check"], "model": model})

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
        "result": STREAM_REPLY,
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
        "result": failures.get(mode, REPLY),
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
