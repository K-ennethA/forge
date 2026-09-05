"""A stand-in for the Claude Code CLI, for the bridge tests.

It does two jobs: **validate** the command line the bridge built (and fail loudly
if the flags that make the assistant work went missing) and **print canned JSON**
in the shape ``--output-format json`` produces.  Every invocation is appended to
``FAKE_CLAUDE_LOG`` as one JSON line so the tests can assert on argv after the
fact — most usefully that turn two carried ``--resume``.

Behaviour is steered by ``FAKE_CLAUDE_MODE``:

``ok``                 print a normal result (the default)
``noise``              print a warning line *before* the JSON, to exercise salvage
``garbage``            print no JSON at all
``slow``               sleep ``FAKE_CLAUDE_SLEEP`` seconds first (for cancel)
``reject_permission``  exit 1 complaining about ``--permission-mode`` unless it
                       is ``acceptEdits`` — the older-CLI fallback path
``api_error``          print the CLI's ``is_error`` result shape

The bridge runs this file through ``sys.executable`` because
``FORGE_ASSISTANT_CLAUDE`` points at a ``.py`` path; see ``bridge.launcher``.
"""

import json
import os
import sys
import time

REPLY = "OK - the fake assistant answered."


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

    # --- the contract the bridge must keep -------------------------------
    if "-p" not in argv:
        fail("no -p flag: this was not a headless one-shot run")
    prompt = flag(argv, "-p")
    if not prompt:
        fail("-p carried no prompt")
    if flag(argv, "--output-format") != "json":
        fail("--output-format json is required; the bridge parses JSON")

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

    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": mode == "api_error",
        "result": REPLY if mode != "api_error" else "the model refused",
        "session_id": session_id,
        "total_cost_usd": 0.0123,
        "num_turns": 1,
        "duration_ms": 42,
        "model": flag(argv, "--model") or "default-model",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }

    if mode == "noise":
        # exactly the failure salvage_json() exists for
        sys.stdout.write("(node:1234) ExperimentalWarning: something\n")
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
