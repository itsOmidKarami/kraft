#!/usr/bin/env bash
# Install a published kraft-sdlc from PyPI into a throwaway venv and exercise it.
# Usage: smoke.sh VERSION [PORT]
# Prints each check's output and exit code for you to read. Exits 1 when the
# install or the server start fails, or the web UI has no <title>. Stops its
# server on exit and leaves its work dir (printed first, under $TMPDIR): venv/,
# home/ (its KRAFT_HOME) and server.log. Delete it when you are done.
set -u
V="${1:?usage: smoke.sh VERSION [PORT]}"; PORT="${2:-8799}"
URL="http://127.0.0.1:$PORT/"
# A server already on the port would answer every check below for this one.
if curl -s -o /dev/null "$URL"; then echo "port $PORT is already serving; pass another"; exit 1; fi
W="$(mktemp -d "${TMPDIR:-/tmp}/kraft-smoke.XXXXXX")"
echo "work dir: $W"
uv venv -q --python 3.12 "$W/venv" || exit 1
VIRTUAL_ENV="$W/venv" uv pip install -q --no-cache "kraft-sdlc[vector]==$V" || exit 1
K="$W/venv/bin/kraft"
echo "version: $("$K" --version)"
# Its own home and port, and no other KRAFT_* var: a worker or dev shell sets
# ones that override KRAFT_HOME (the run dir, so `admin stop` would kill a real
# instance) or point at real repos, beads and work items.
for v in $(compgen -e | grep '^KRAFT_'); do unset "$v"; done
export KRAFT_HOME="$W/home" KRAFT_PORT="$PORT"
"$K" admin start --port "$PORT" > "$W/server.log" 2>&1 &
trap '"$K" admin stop >/dev/null 2>&1' EXIT
for _ in $(seq 1 60); do curl -sf -o /dev/null "$URL" && break; sleep 1; done
if ! curl -sf -o /dev/null "$URL"; then
  echo "server did not answer on :$PORT within 60s; end of $W/server.log:"; tail -20 "$W/server.log"; exit 1
fi
echo "--- health"; "$K" admin health; echo "health exit $?"
echo "--- doctor"; "$K" admin doctor; echo "doctor exit $?"
echo "--- templates lint"; "$K" admin templates lint; echo "lint exit $?"
echo "--- spa"; curl -s "$URL" | grep -o '<title>[^<]*'
