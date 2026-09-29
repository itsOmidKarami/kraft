"""A stand-in for `codex app-server` (codex-cli 0.155.0's stdio JSON lines),
enough for `hook_install.codex_hook_flags`: `hooks/list` lists the
`-c hooks.PreToolUse=` hook as a session-flags hook whose hash is its
command's sha256, trusted only when a `-c hooks.state=` names that hash.

FAKE_CODEX_MODE: ok (default), exit (dies before answering), hang (never
answers), unlisted (lists no Kraft hook), distrust (never trusted),
stuck-disabled (listed disabled whatever the flags say).
FAKE_CODEX_PLANTED: what a worker wrote into the config under its HOME, a
layer below `-c` (as codex-cli 0.155.0 merges them, measured):
`disabled` (the hook's state `enabled = false`), `hooks-off`
(`features.hooks = false`, which lists no hook at all), `trusted-hook` (a
PreToolUse hook of its own with its `trusted_hash`, listed enabled and
trusted beside Kraft's).
FAKE_CODEX_LOG: a file each spawn appends its argv to."""

import hashlib
import json
import os
import sys
import time

KEY = "/<session-flags>/config.toml:pre_tool_use:0:0"
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if log := os.environ.get("FAKE_CODEX_LOG"):
    with open(log, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\n")
assert sys.argv[1] == "app-server", sys.argv
flags = sys.argv[3::2]
hook = next(f for f in flags if f.startswith("hooks.PreToolUse="))
command = json.loads(hook.split("command=", 1)[1].split(",timeout=", 1)[0])
digest = "sha256:" + hashlib.sha256(command.encode()).hexdigest()
planted = os.environ.get("FAKE_CODEX_PLANTED")
state = next((f for f in flags if f.startswith("hooks.state=")), "")
trusted = f'"{KEY}"=' in state and f'trusted_hash="{digest}"' in state and mode != "distrust"
enabled = mode != "stuck-disabled" and ("enabled=true" in state or planted != "disabled")
hooks_on = "features.hooks=true" in flags or planted != "hooks-off"


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    msg = json.loads(line)
    if mode == "exit":
        sys.exit(1)
    if mode == "hang":
        time.sleep(60)
    if msg.get("id") == 1:
        send({"id": 1, "result": {"userAgent": "fake"}})
        send({"method": "remoteControl/status/changed", "params": {}})
    elif msg.get("id") == 2:
        hooks = [
            {
                "key": "/repo/.codex/hooks.json:pre_tool_use:0:0",
                "source": "project",
                "command": command,
                "currentHash": "sha256:other",
                "trustStatus": "untrusted",
            }
        ]
        if planted == "trusted-hook" and hooks_on:
            hooks.append(
                {
                    "key": "/home/w/.codex/config.toml:pre_tool_use:0:0",
                    "eventName": "preToolUse",
                    "source": "user",
                    "command": "echo allow",
                    "enabled": True,
                    "currentHash": "sha256:planted",
                    "trustStatus": "trusted",
                }
            )
        if mode != "unlisted" and hooks_on:
            hooks.append(
                {
                    "key": KEY,
                    "eventName": "preToolUse",
                    "source": "sessionFlags",
                    "command": command,
                    "timeoutSec": 10,
                    "currentHash": digest,
                    "enabled": enabled,
                    "trustStatus": "trusted" if trusted else "untrusted",
                }
            )
        send({"id": 2, "result": {"data": [{"cwd": msg["params"]["cwds"][0], "hooks": hooks}]}})
