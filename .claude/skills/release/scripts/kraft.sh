#!/usr/bin/env bash
# Run the smoke venv's kraft with smoke.sh's isolation: its own KRAFT_HOME and
# port, and no other KRAFT_* var from the calling shell. A leftover one can point
# `admin stop` at a real instance, and zsh has no compgen to unset them inline.
# Usage: kraft.sh WORKDIR PORT ARGS...
set -u
W="${1:?usage: kraft.sh WORKDIR PORT ARGS...}"; P="${2:?usage: kraft.sh WORKDIR PORT ARGS...}"; shift 2
for v in $(compgen -e | grep '^KRAFT_'); do unset "$v"; done
export KRAFT_HOME="$W/home" KRAFT_PORT="$P"
exec "$W/venv/bin/kraft" "$@"
