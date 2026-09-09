#!/bin/sh
# Install the newest released Kraft.
#
#   curl -fsSL https://gitlab.com/itsOmidKarami/kraft/-/raw/main/install.sh | sh
#
# This is a piped-shell install, with the objections that implies. What reduces
# them: this script is short, committed, and reviewable at the URL above before
# you run it, and the wheel it fetches is an asset of a tagged release rather
# than something from a mutable location. What does not reduce them is arguing
# the pattern is fine.
set -eu

API_ROOT="https://gitlab.com/api/v4"
API="$API_ROOT/projects/itsOmidKarami%2Fkraft/releases"

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv (kraft needs it to fetch a Python 3.14)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

# The project is private, so anonymous curl 404s on both the releases list and
# the wheel itself. Prefer an explicit token (works anywhere, including CI);
# fall back to an already-authenticated glab (the common case on a dev
# machine); fall back to plain curl last, in case the project ever goes
# public and this whole branch stops mattering.
fetch() {
    if [ -n "${GITLAB_TOKEN:-}" ]; then
        curl -fsSL --header "PRIVATE-TOKEN: $GITLAB_TOKEN" "$1"
    elif command -v glab >/dev/null 2>&1 && glab auth status >/dev/null 2>&1; then
        glab api "${1#"$API_ROOT"/}"
    else
        curl -fsSL "$1"
    fi
}

# The releases feed is newest-first, so the first .whl in it is the current one.
wheel=$(fetch "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
if [ -z "$wheel" ]; then
    echo "no released wheel found at $API" >&2
    echo "the project is private: set GITLAB_TOKEN, or run 'glab auth login' first" >&2
    exit 1
fi

# uv would fetch $wheel itself unauthenticated, so pull it down here instead
# and hand uv the local file.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
wheel_file="$tmpdir/$(basename "$wheel")"
fetch "$wheel" > "$wheel_file"

uv tool install --force --from "$wheel_file" kraft

echo
echo "$(kraft --version) installed."
echo "next: kraft admin init   # register Kraft with your agent"
echo "then: kraft              # start the server"
