#!/usr/bin/env python3
"""A stand-in for the `claude` and `codex` command lines in tests.

It records what it was called with (argv, stdin, a few environment variables) as one JSON line in $FAKE_CLI_LOG and
answers in the real tool's output format. $FAKE_CLI_REPLY (JSON) sets the answer: {"result": ..., "structured": ...,
"logged_in": true, "is_error": false, "error": "..."}.
"""
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
reply = json.loads(os.environ.get("FAKE_CLI_REPLY") or "{}")
logged_in = reply.get(f"{name}_logged_in", reply.get("logged_in", True))
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": name, "argv": args, "stdin": stdin, "cwd": os.getcwd(),
                            "env": {k: os.environ.get(k) for k in ("HTTPS_PROXY", "CLAUDE_CODE_OAUTH_TOKEN")}}) + "\n")

# An agent's tool calls: {"plans": {"<text in the prompt>": [["search", "q", "--kw", "k"], ...]}} runs those memex
# commands ($FAKE_MEMEX, a JSON list) in order, as the agent would, before answering.
ran = []
for key, calls in (reply.get("plans") or {}).items():
    if key in stdin:
        import subprocess
        memex = json.loads(os.environ.get("FAKE_MEMEX") or "[]")
        for c in calls:
            subprocess.run(memex + c, capture_output=True, text=True)
            ran.append(" ".join(memex + c))
        break

if name == "claude":
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai"}))
        sys.exit(0)
    out = {"type": "result", "is_error": reply.get("is_error", False), "result": reply.get("result", "done"),
           "num_turns": 2, "total_cost_usd": 0.0123, "permission_denials": reply.get("denials", []),
           "modelUsage": {"claude-test-model": {"inputTokens": 100, "outputTokens": 20, "cacheReadInputTokens": 0,
                                                "cacheCreationInputTokens": 0}}}
    if "structured" in reply:
        out["structured_output"] = reply["structured"]
    print(json.dumps(out))
    sys.exit(0)

if name == "codex":
    if args[:2] == ["login", "status"]:
        if logged_in:
            print("Logged in using ChatGPT")
            sys.exit(0)
        print("Not logged in", file=sys.stderr)
        sys.exit(1)
    if reply.get("error"):
        print(json.dumps({"type": "error", "message": reply["error"]}))
        print(reply["error"], file=sys.stderr)
        sys.exit(1)
    o = args[args.index("-o") + 1] if "-o" in args else None
    text = json.dumps(reply["structured"]) if "structured" in reply else reply.get("result", "done")
    if o:
        Path(o).write_text(text, encoding="utf-8")
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20,
                                                          "reasoning_output_tokens": 5}}))
    for c in ran:
        print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": c}}))
    for c in reply.get("other_commands", []):
        print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": c}}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
    sys.exit(0)
sys.exit(2)
