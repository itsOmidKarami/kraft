#!/bin/sh
# Install the newest released Kraft.
#
#   curl -fsSL https://raw.githubusercontent.com/itsOmidKarami/kraft/main/install.sh | sh
#
# This is a piped-shell install, with the objections that implies. What reduces
# them: this script is short, committed, and reviewable at the URL above before
# you run it, and the wheel it fetches is an asset of a tagged release rather
# than something from a mutable location. What does not reduce them is arguing
# the pattern is fine.
#
# `uv tool install kraft` from PyPI is the other supported door, and needs none
# of this.
set -eu

# /releases/latest, not /releases: this endpoint already excludes drafts and
# prereleases, so there is no feed to filter here the way kraft.update has to.
API="https://api.github.com/repos/itsOmidKarami/kraft/releases/latest"

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv (kraft needs it to fetch a Python 3.14)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

wheel=$(curl -fsSL "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
if [ -z "$wheel" ]; then
    echo "no released wheel found at $API" >&2
    echo "if this persists, install from PyPI instead: uv tool install kraft" >&2
    exit 1
fi

# uv can fetch $wheel itself, but pulling it here keeps one download path and
# one error message for both this script and `kraft admin update`.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
wheel_file="$tmpdir/$(basename "$wheel")"
curl -fsSL "$wheel" > "$wheel_file"

uv tool install --force --from "$wheel_file" kraft

echo
echo "$(kraft --version) installed."
echo "next: kraft admin init   # register Kraft with your agent"
echo "then: kraft              # start the server"
