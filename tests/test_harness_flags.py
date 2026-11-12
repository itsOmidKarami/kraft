"""Every flag and subcommand a bundled harness file names exists in its agent CLI.

Each `src/kraft/harnesses/*.yaml` of `kind: cli` binds flags and subcommands that
were checked by hand once. A vendor that renames or drops one breaks every launch
of that harness, and the first to find out is a user's work item. This reads
`<prefix> --help` (and `<binary> --version`) for each prefix the file launches
with, and fails on a flag or a subcommand the CLI no longer lists.

It sends no prompt and needs no credential, so its marks say `spends_tokens=False`
and it runs without KRAFT_E2E=1. A CLI that isn't installed skips, naming itself.
`just smoke-flags` runs it; the `harness flags` workflow runs it daily against the
latest release of each CLI.

Not covered: Codex's `-c` config keys (`--help` doesn't list them), the shape of a
CLI's output, and `kind: acp` entries (`kraft admin acp-probe` covers those).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from kraft import harness as _harness
from kraft.harness import Capability, Harness

BUNDLED = _harness.load(Path(__file__).resolve().parent / "no-overlay")  # bundled files only

#: Flags Kraft's own code adds outside any `cli:` list, by the harness-file field
#: that makes it add them; `build_argv(extra=...)` puts them after every prefix.
KRAFT_ADDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("permission_rules", "amp"): ("--settings-file",),  # permission_rules._amp
    ("permission_rules", "opencode"): ("--standalone",),  # permission_rules._opencode
}

#: (harness id, flag) -> why: a flag the CLI accepts but no help page lists. None today.
HIDDEN: dict[tuple[str, str], str] = {}

FLAG = re.compile(r"--?[A-Za-z][\w-]*")
TIMEOUT = 30


def _words(prefix: tuple[str, ...]) -> tuple[str, ...]:
    """The leading words of a command, before the first flag or placeholder."""
    words = []
    for tok in prefix:
        if tok.startswith("-") or "{" in tok:
            break
        words.append(tok)
    return tuple(words)


def _flags(cap: Capability) -> list[str]:
    flags = []
    for tok in cap.argv:
        if tok == "--{value}":
            always = (cap.always,) if isinstance(cap.always, str) else cap.always or ()
            # shortcut: a regex value under --{value} isn't checked; none exists today,
            # check one by hand if it ever does
            flags += [f"--{v}" for v in (*cap.values, *always) if re.fullmatch(r"[\w-]+", v)]
        elif FLAG.fullmatch(name := tok.split("=", 1)[0]):
            flags.append(name)
    return flags


def _own(command: tuple[str, ...]) -> list[str]:
    return [tok for tok in command if FLAG.fullmatch(tok)]


def _prefixes(h: Harness) -> dict[tuple[str, ...], list[str]]:
    """Each prefix the harness launches with -> the flags put after it."""
    shared = [
        flag
        # `build_argv` never emits `resume`'s own flags when the file has a `command_resume`.
        for name, cap in h.capabilities.items()
        if not (name == "resume" and h.command_resume)
        for flag in _flags(cap)
    ]
    for field in ("permission_rules", "permission_hook"):
        shared += KRAFT_ADDS.get((field, getattr(h, field)), ())
    prefixes = {_words(h.command): [*_own(h.command), *shared]}
    if h.command_resume:
        resume = prefixes.setdefault(_words(h.command_resume), [*shared])
        resume += _own(h.command_resume)
    return prefixes


def _env(home: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(home)}


def _help(words: tuple[str, ...], home: Path) -> tuple[int | None, str]:
    try:
        done = subprocess.run(
            [*words, "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=_env(home),
        )
    except subprocess.TimeoutExpired:
        return None, ""
    return done.returncode, done.stdout + done.stderr


def _version(binary: str, home: Path) -> str:
    try:
        done = subprocess.run(
            [binary, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=_env(home),
        )
    except subprocess.TimeoutExpired:
        return "version unknown"
    lines = (done.stdout + done.stderr).strip().splitlines()
    return lines[0].strip() if lines else "version unknown"


def _listed(flag: str, text: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text) is not None


@pytest.mark.parametrize(
    "h",
    [
        pytest.param(h, id=h.id, marks=pytest.mark.e2e(h.command[0], spends_tokens=False))
        for h in BUNDLED.valid.values()
        if h.kind == "cli"
    ],
)
def test_every_flag_and_subcommand_is_in_its_clis_help(h, tmp_path):
    # One HOME for every call of this case: Amp's help embeds the HOME path, so help
    # from two HOMEs never compares equal and a misspelt subcommand would pass.
    home = tmp_path / "home"
    home.mkdir()
    helps: dict[tuple[str, ...], tuple[int | None, str]] = {}
    problems: list[str] = []

    def at(level: tuple[str, ...]) -> tuple[int | None, str]:
        if level not in helps:
            helps[level] = _help(level, home)
        return helps[level]

    for words, flags in _prefixes(h).items():
        levels = [words[:n] for n in range(1, len(words) + 1)]
        for level in levels:
            rc, text = at(level)
            if rc != 0:
                got = "timed out after 30 s" if rc is None else f"exited {rc}"
                problems.append(f"`{' '.join(level)} --help` {got}")
            elif len(level) > 1 and text == at(level[:-1])[1]:
                # Codex, Amp and OpenCode print the parent's help and exit 0 for an
                # unknown subcommand, so a successful call proves nothing by itself.
                problems.append(
                    f"subcommand {level[-1]!r}: `{' '.join(level)} --help` prints the same "
                    f"as `{' '.join(level[:-1])} --help`"
                )
        # Amp accepts `-x` after `threads continue` but lists it only in `amp --help`.
        known = "\n".join(at(level)[1] for level in levels)
        for flag in dict.fromkeys(flags):
            if (h.id, flag) not in HIDDEN and not _listed(flag, known):
                problems.append(
                    f"flag {flag} (after `{' '.join(words)}`) is in no help "
                    f"from `{words[0]} --help` down"
                )

    problems = list(dict.fromkeys(problems))
    assert not problems, (
        f"{h.path} against {h.command[0]} {_version(h.command[0], home)}:\n" + "\n".join(problems)
    )
