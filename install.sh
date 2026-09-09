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

API="https://gitlab.com/api/v4/projects/itsOmidKarami%2Fkraft/releases"

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv (kraft needs it to fetch a Python 3.14)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

# The releases feed is newest-first, so the first .whl in it is the current one.
wheel=$(curl -fsSL "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
if [ -z "$wheel" ]; then
    echo "no released wheel found at $API" >&2
    exit 1
fi

uv tool install --force --from "$wheel" kraft

echo
echo "kraft $(kraft --version) installed."
echo "next: kraft admin init   # register Kraft with your agent"
echo "then: kraft              # start the server"
