#!/bin/sh
# Install the newest released Kraft.
#
#   curl -fsSL https://raw.githubusercontent.com/itsOmidKarami/kraft/main/install.sh | sh
#
# This is a piped-shell install, with the objections that implies. What reduces
# them: this script is short, committed, and reviewable at the URL above before
# you run it, and what it installs is a tagged release, by its exact version
# from PyPI or as the wheel attached to it, rather than something from a
# mutable location. What does not reduce them is arguing the pattern is fine.
#
# `uv tool install kraft-sdlc` from PyPI is the other supported door, and needs
# none of this.
#
# No `pipefail`: dash, the sh on Debian and Ubuntu, refuses it and would stop
# here. Nothing below lets a pipeline's failure through unchecked instead.
set -eu

# Everything is in main(), run by the last line: piped into sh, a download
# cut short is a script whose function never closes, so sh refuses all of it
# instead of running the part that arrived.
main() {
    # /releases/latest, not /releases: this endpoint already excludes drafts and
    # prereleases, so there is no feed to filter here the way kraft.update has to.
    API="https://api.github.com/repos/itsOmidKarami/kraft/releases/latest"
    # The caller's PATH, before this script adds uv's own directory to it.
    user_path=$PATH

    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT

    uv_help="install uv yourself (https://docs.astral.sh/uv/getting-started/installation/), then run this again"
    if ! command -v uv >/dev/null 2>&1; then
        echo "installing uv (kraft needs it to fetch a Python 3.12 or newer)..."
        # Saved, then run: piped straight into sh, a failed download is an empty
        # script that sh runs happily, and this one went on to "uv: not found".
        if ! curl -LsSf https://astral.sh/uv/install.sh > "$tmpdir/uv-install.sh"; then
            echo "could not download the uv installer from https://astral.sh/uv/install.sh" >&2
            echo "$uv_help" >&2
            exit 1
        fi
        # Not on this script's stdin: under `curl ... | sh` that is the rest of
        # this script, and an installer that reads stdin would swallow it.
        if ! sh "$tmpdir/uv-install.sh" </dev/null; then
            echo "the uv installer failed; $uv_help" >&2
            exit 1
        fi
        PATH="$HOME/.local/bin:$PATH"
        export PATH
        if ! command -v uv >/dev/null 2>&1; then
            echo "the uv installer ran, but uv is not in $HOME/.local/bin or on your PATH" >&2
            echo "$uv_help" >&2
            exit 1
        fi
    fi

    wheel=$(curl -fsSL "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
    if [ -z "$wheel" ]; then
        echo "no released wheel found at $API" >&2
        echo "if this persists, install from PyPI instead: uv tool install kraft-sdlc" >&2
        exit 1
    fi

    # The release's version, from its wheel's name (kraft_sdlc-1.5.0-py3-none-any.whl).
    wheel_name=${wheel##*/}
    case "$wheel_name" in
        kraft_sdlc-*) version=${wheel_name#kraft_sdlc-}; version=${version%%-*} ;;
        *) version="" ;;
    esac

    # From PyPI by version, as `kraft admin update` does: uv records the request,
    # so a later `uv tool upgrade` can read it back. A wheel in a temporary
    # directory left a record naming a file that was gone. The GitHub release is
    # created a few minutes before PyPI has it, so in that window, or for a wheel
    # whose name says no version, install the wheel by its URL instead.
    if [ -z "$version" ] || ! uv tool install --force "kraft-sdlc==$version"; then
        echo "installing the release's wheel: $wheel"
        uv tool install --force "kraft-sdlc @ $wheel"
    fi

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
}

main "$@"
