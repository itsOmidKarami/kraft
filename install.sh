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
# `uv tool install kraft-sdlc` from PyPI is the other supported door, and needs
# none of this.
set -eu

# /releases/latest, not /releases: this endpoint already excludes drafts and
# prereleases, so there is no feed to filter here the way kraft.update has to.
API="https://api.github.com/repos/itsOmidKarami/kraft/releases/latest"
# The caller's PATH, before this script adds uv's own directory to it.
user_path=$PATH

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv (kraft needs it to fetch a Python 3.12 or newer)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

wheel=$(curl -fsSL "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
if [ -z "$wheel" ]; then
    echo "no released wheel found at $API" >&2
    echo "if this persists, install from PyPI instead: uv tool install kraft-sdlc" >&2
    exit 1
fi

# uv can fetch $wheel itself, but pulling it here keeps one download path and
# one error message for both this script and `kraft admin update`.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
wheel_file="$tmpdir/$(basename "$wheel")"
curl -fsSL "$wheel" > "$wheel_file"

uv tool install --force --from "$wheel_file" kraft-sdlc

# Where uv put the command, which a first uv install has not added to the
# caller's PATH yet: run it from there rather than trust `kraft` to resolve.
bin_dir=$(uv tool dir --bin 2>/dev/null) || bin_dir="$HOME/.local/bin"

echo
if [ -x "$bin_dir/kraft" ]; then
    echo "$("$bin_dir/kraft" --version) installed in $bin_dir."
    case ":$user_path:" in
        *":$bin_dir:"*) ;;
        *)
            echo "$bin_dir is not on your PATH: run  uv tool update-shell  and open a new"
            echo "terminal, or call kraft as $bin_dir/kraft"
            ;;
    esac
else
    # A uv too old for `tool dir --bin`, with its bin directory moved: there is
    # no path to name, only the fix.
    echo "kraft installed. If \`kraft\` is not found, run  uv tool update-shell  and open"
    echo "a new terminal."
fi
echo "next: kraft              # start the server"
echo "then, in Claude Code:  /plugin marketplace add itsOmidKarami/kraft"
echo "                       /plugin install kraft@kraft, then /kraft:onboard in your repo"
echo "or, terminal only:     kraft admin init, then kraft repo connect in your repo"
