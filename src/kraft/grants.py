"""Which named grant, if any, a tool call is an instance of (Kraft-4in7z).

A grant is a named operation (`kraft.policy.GRANTS`), never a pattern. It
matches only a shell call that is exactly one plain git invocation of that
subcommand, because the grant is an `allow` that outranks a classifier.
Anything else -- a shell operator, substitution, redirection, glob, env
prefix, an unknown git option, or an option that makes git run a command of
the caller's choosing -- matches nothing and falls to the classifier. When in
doubt this refuses: a refusal costs a classifier call, a false match costs
an arbitrary command.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable

#: Characters that make a command line more than one literal invocation:
#: operators, substitution and quoting tricks, redirection, subshells, brace
#: and glob expansion (a glob can expand into an option named by a file),
#: and `!`, which an interactive shell expands from its history.
#: `#` stays allowed: a comment only drops trailing words, never adds one.
_SHELL_META = frozenset(";&|<>`$\\({*?[!")

#: git's own options allowed before the subcommand. Not `-C`: it points git
#: at another repository, whose own config and hooks would run.
_GIT_FLAGS = frozenset({"--no-pager", "-P"})

#: Keys `git -c` may set. Most config can run a command (`core.sshCommand`,
#: `core.hooksPath`, `protocol.ext.allow`, ...), so only these ride a grant.
_SAFE_CONFIG = frozenset({"user.name", "user.email"})

#: Per subcommand: long options that make git run a caller-chosen command
#: (git accepts any unambiguous prefix, so a prefix of these is refused too),
#: and short-option letters doing the same. A push is also held to the
#: branch it names: nothing that deletes or pushes every ref (`--delete`,
#: `--mirror`, `--all`/`--branches`, `--prune`), names the destination by
#: option (`--repo`), or takes a separate value that would pass for the
#: remote (`-o`/`--push-option`). `--force` stays allowed.
_UNSAFE_LONG = {
    "push": (
        "receive-pack",
        "exec",
        "delete",
        "mirror",
        "all",
        "branches",
        "prune",
        "repo",
        "push-option",
    ),
    "rebase": ("exec", "strategy"),
    "commit": (),
}
_UNSAFE_SHORT = {"push": "do", "rebase": "xs", "commit": ""}

#: What a push's first positional must be: a configured remote's name, never
#: a URL or path (`host:repo`, `/tmp/r`, `ext::cmd`) git would push to instead.
_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _git_subcommand(command: str) -> str | None:
    """The subcommand of `command` if it is one plain, safe git call."""
    if _SHELL_META & set(command) or any(not c.isprintable() for c in command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] != "git":
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):
        opt = words[i]
        if opt in _GIT_FLAGS:
            i += 1
        elif opt == "-c" and i + 1 < len(words):
            key = words[i + 1].split("=", 1)[0].lower()
            if key not in _SAFE_CONFIG:
                return None
            i += 2
        else:
            return None
    if i >= len(words) or words[i] not in _UNSAFE_LONG:
        return None
    sub = words[i]
    for arg in words[i + 1 :]:
        if arg.startswith("--"):
            name = arg[2:].split("=", 1)[0]
            if name and any(u.startswith(name) for u in _UNSAFE_LONG[sub]):
                return None
        elif arg.startswith("-") and set(arg[1:]) & set(_UNSAFE_SHORT[sub]):
            return None
    if sub == "push":
        args = words[i + 1 :]
        end = args.index("--") if "--" in args else len(args)
        positional = [a for a in args[:end] if not a.startswith("-")] + args[end + 1 :]
        if positional and not _REMOTE.fullmatch(positional[0]):
            return None
    return sub


def matching(grants: Iterable[str], tool: str, input: dict) -> str | None:
    """The first of `grants` that `(tool, input)` is an instance of, else None.

    Only `Bash` (Kraft's policy name for a shell) can match a git grant.
    """
    if tool != "Bash":
        return None
    command = input.get("command")
    sub = _git_subcommand(command) if isinstance(command, str) else None
    if sub is None:
        return None
    return next((g for g in grants if g == f"git-{sub}"), None)
