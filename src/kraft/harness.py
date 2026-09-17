"""A harness is one agent runtime, described as data.

Sibling of `skill.py` and `steering.py`, and deliberately shaped like them:
shipped files live in the package, `$KRAFT_HOME` is an optional overlay
resolved first by bare name, and the value round-trips through config as a
name rather than as its contents.

`capabilities` is the *interface* Kraft expects; the binding under each
capability is an implementation detail of `kind`. Some capabilities have no
binding at all -- `usage` and `rate_limit_signal` describe what Kraft can read
back out, which is not argv and would mean the same thing for a future
non-CLI kind. That is why capability and invocation are separate axes here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kraft.paths import default_harnesses_dir

#: Harnesses that ship with Kraft. Read from the package, never from
#: `$KRAFT_HOME/templates/`, because `cli.seed_home` copies templates once and
#: never again -- a seeded harness would freeze at install time.
BUNDLED = Path(__file__).parent / "harnesses"

#: The only `kind` implemented. An unknown kind quarantines the file rather
#: than half-loading it; a second kind arrives with its own adapter.
KINDS = ("cli",)

#: Every capability Kraft knows how to ask for. A harness answers these; it
#: does not invent new names.
KNOWN = (
    "prompt",
    "context",
    "usage",
    "structured_log",
    "model",
    "effort",
    "permission_mode",
    "approval_channel",
    "deny_tools",
    "allowed_tools",
    "resume",
    "autocompact",
    "rate_limit_signal",
)

#: Without these three, no agent dispatch can be built at all.
REQUIRED = ("prompt", "context", "usage")

#: Legal with no `cli:` binding -- they describe what Kraft reads back.
NON_INVOCABLE = ("usage", "rate_limit_signal")

#: The whole placeholder language. See the plan's global constraints: a
#: harness needing a third is a signal to move invocation out of YAML, not to
#: add one here.
PLACEHOLDERS = ("{value}", "{csv}")


class HarnessError(Exception):
    pass


@dataclass(frozen=True)
class Capability:
    name: str
    #: The `cli:` fragment, `()` for a non-invocable capability or one bound
    #: `via` a command prefix.
    argv: tuple[str, ...] = ()
    #: `re.fullmatch` patterns. Empty means "any value".
    values: tuple[str, ...] = ()
    #: Applied on every launch whether or not a binding asked. A string for a
    #: scalar capability, a tuple for a list one.
    always: str | tuple[str, ...] | None = None
    #: `context` only: `system_prompt` (out-of-band, privileged) or `prompt`
    #: (folded into the task instruction -- a weaker channel, declared so it
    #: is visible rather than inferred).
    channel: str | None = None
    #: `usage` only: `envelope` (parse the log with `reader`) or `result_file`
    #: (the agent reports its own numbers into $KRAFT_RESULT_PATH).
    source: str | None = None
    #: Names a parser in Python. Never a parser body: an operator must not be
    #: able to break a log reader in YAML.
    reader: str | None = None
    #: Names a command prefix key that carries this capability's value
    #: instead of a flag -- `resume: {via: command_resume}`.
    via: str | None = None


@dataclass(frozen=True)
class Harness:
    id: str
    kind: str
    command: tuple[str, ...]
    #: Replaces `command` entirely when a resume is requested. `()` when the
    #: harness spells resume as a flag, or not at all.
    command_resume: tuple[str, ...]
    #: Insertion-ordered: this is argv order (PyYAML preserves it).
    capabilities: dict[str, Capability] = field(default_factory=dict)
    path: Path | None = None

    def supports(self, name: str) -> bool:
        return name in self.capabilities

    def value_ok(self, name: str, value: str) -> bool:
        """`value` is acceptable for `name`. A capability with no `values:`
        accepts anything; patterns are `fullmatch`, so `low` means only `low`.
        """
        cap = self.capabilities.get(name)
        if cap is None:
            return False
        if not cap.values:
            return True
        return any(re.fullmatch(p, value) for p in cap.values)


@dataclass(frozen=True)
class HarnessSet:
    valid: dict[str, Harness]
    invalid: dict[str, str]


def _capability(name: str, raw: object, where: str) -> Capability:
    if name not in KNOWN:
        raise HarnessError(f"{where}: unknown capability {name!r}; known: {sorted(KNOWN)}")
    if not isinstance(raw, dict):
        raise HarnessError(f"{where}: capability {name!r} must be a mapping")
    cli = raw.get("cli", ())
    if cli and not (isinstance(cli, list) and all(isinstance(x, str) for x in cli)):
        raise HarnessError(f"{where}: capability {name!r} 'cli' must be a list of strings")
    for frag in cli:
        for ph in re.findall(r"\{[a-z_]+\}", frag):
            if ph not in PLACEHOLDERS:
                raise HarnessError(
                    f"{where}: capability {name!r} uses placeholder {ph}; "
                    f"only {', '.join(PLACEHOLDERS)} exist"
                )
    values = raw.get("values", ())
    if values and not (isinstance(values, list) and all(isinstance(x, str) for x in values)):
        raise HarnessError(f"{where}: capability {name!r} 'values' must be a list of strings")
    for pattern in values:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HarnessError(
                f"{where}: capability {name!r} value pattern {pattern!r} is not a regex: {exc}"
            ) from None
    always = raw.get("always")
    if isinstance(always, list):
        always = tuple(always)
    return Capability(
        name=name,
        argv=tuple(cli),
        values=tuple(values),
        always=always,
        channel=raw.get("channel"),
        source=raw.get("source"),
        reader=raw.get("reader"),
        via=raw.get("via"),
    )


def _prefix(raw: object, key: str, where: str) -> tuple[str, ...]:
    """A command prefix, accepted as a string or a list of strings."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return tuple(raw)
    raise HarnessError(f"{where}: {key!r} must be a string or a list of strings")


def parse(data: object, *, where: str, path: Path | None = None) -> Harness:
    """One harness definition, validated, or raise `HarnessError`."""
    if not isinstance(data, dict):
        raise HarnessError(f"{where}: expected a top-level mapping")
    hid = data.get("id")
    if not isinstance(hid, str) or not hid:
        raise HarnessError(f"{where}: missing a string 'id'")
    kind = data.get("kind")
    if kind not in KINDS:
        raise HarnessError(f"{where}: unknown kind {kind!r}; known: {sorted(KINDS)}")
    command = _prefix(data.get("command"), "command", where)
    if not command:
        raise HarnessError(f"{where}: 'command' must be a non-empty string or list")
    command_resume = _prefix(data.get("command_resume"), "command_resume", where)
    raw_caps = data.get("capabilities")
    if not isinstance(raw_caps, dict) or not raw_caps:
        raise HarnessError(f"{where}: 'capabilities' must be a non-empty mapping")

    caps: dict[str, Capability] = {}
    for name, raw in raw_caps.items():
        caps[name] = _capability(name, raw, where)

    for name in REQUIRED:
        if name not in caps:
            raise HarnessError(f"{where}: missing required capability {name!r}")

    for name, cap in caps.items():
        # Every capability is either invocable or one of the known facts about
        # reading results back. Neither -> `capabilities` is a wishlist.
        # `context` is exempt here because its own validation below covers
        # both channels: `channel: prompt` is deliberately argv-less.
        if not cap.argv and not cap.via and name not in (*NON_INVOCABLE, "context"):
            raise HarnessError(
                f"{where}: capability {name!r} has no 'cli' binding for kind {kind!r} "
                f"and is not one of {sorted(NON_INVOCABLE)}"
            )
        if cap.via and cap.via != "command_resume":
            raise HarnessError(f"{where}: capability {name!r} 'via' must be 'command_resume'")
        if cap.via == "command_resume" and not command_resume:
            raise HarnessError(
                f"{where}: capability {name!r} is bound via 'command_resume', "
                "which this harness does not define"
            )
        # A default the harness's own CLI would reject is worse than no
        # default: it fails every launch, with no binding to blame.
        for one in (cap.always,) if isinstance(cap.always, str) else (cap.always or ()):
            if cap.values and not any(re.fullmatch(p, one) for p in cap.values):
                raise HarnessError(
                    f"{where}: capability {name!r} 'always' value {one!r} "
                    "is not accepted by its own 'values'"
                )

    ctx = caps["context"]
    if ctx.channel not in ("system_prompt", "prompt"):
        raise HarnessError(
            f"{where}: capability 'context' needs channel 'system_prompt' or 'prompt', "
            f"not {ctx.channel!r}"
        )
    if ctx.channel == "system_prompt" and not ctx.argv:
        raise HarnessError(f"{where}: context channel 'system_prompt' needs a 'cli' binding")
    if ctx.channel == "prompt" and ctx.argv:
        raise HarnessError(
            f"{where}: context channel 'prompt' is folded into the prompt and "
            "must not also carry a 'cli' binding"
        )

    usage = caps["usage"]
    if usage.source not in ("envelope", "result_file"):
        raise HarnessError(
            f"{where}: capability 'usage' needs source 'envelope' or 'result_file', "
            f"not {usage.source!r}"
        )
    # Nothing for a reader to read without a machine-readable stream.
    for name in ("usage", "rate_limit_signal"):
        cap = caps.get(name)
        if cap is None:
            continue
        if name == "usage" and cap.source != "envelope":
            continue
        if not cap.reader:
            raise HarnessError(f"{where}: capability {name!r} needs a 'reader'")
        if "structured_log" not in caps:
            raise HarnessError(
                f"{where}: capability {name!r} reads the log, so 'structured_log' "
                "must be declared"
            )

    # A bare positional would be swallowed by a preceding flag.
    for name, cap in caps.items():
        if cap.argv == ("{value}",) and name != next(reversed(caps)):
            raise HarnessError(
                f"{where}: capability {name!r} is a bare positional, so it must be "
                "the last declared capability (argv order is declaration order)"
            )

    return Harness(
        id=hid,
        kind=kind,
        command=command,
        command_resume=command_resume,
        capabilities=caps,
        path=path,
    )


def load(harnesses_dir: Path | None) -> HarnessSet:
    """Every harness, overlay-first then bundled, by bare name.

    Per-file validity, like `templates.load_templates`: one malformed harness
    is quarantined by name with its reason and the rest still load. An
    unusable harness nobody references is not an outage.
    """
    overlay = Path(harnesses_dir) if harnesses_dir is not None else default_harnesses_dir()
    valid: dict[str, Harness] = {}
    invalid: dict[str, str] = {}
    # Overlay first: a name present in both wins from $KRAFT_HOME, and the
    # operator thereby owns it -- the same trade `skill._local_path` makes.
    for directory in (overlay, BUNDLED):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            stem = path.stem
            if stem in valid or stem in invalid:
                continue
            try:
                data = yaml.safe_load(path.read_text())
            except (OSError, ValueError, yaml.YAMLError) as exc:
                invalid[stem] = f"{path}: cannot read/parse: {exc}"
                continue
            try:
                h = parse(data, where=str(path), path=path)
            except HarnessError as exc:
                invalid[stem] = str(exc)
                continue
            if h.id != stem:
                invalid[stem] = f"{path}: id {h.id!r} does not match its file name {stem!r}"
                continue
            valid[stem] = h
    return HarnessSet(valid=valid, invalid=invalid)
