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

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictStr, ValidationError

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
    "restrict_tools",
    "resume",
    "autocompact",
    "rate_limit_signal",
    # Kraft fills it, never a task: the directories outside the worktree a
    # worker must write -- the one holding $KRAFT_RESULT_PATH, and the
    # worktree's git common dir when it has one -- for a CLI whose own sandbox
    # would otherwise refuse those writes (Kraft-rs9pk). `{value}` is one JSON
    # array of absolute paths, which is also a TOML inline array.
    "writable_dirs",
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
    #: instead of a flag -- `resume: {via: command_resume}` -- or
    #: `permission_hook`: a tool list the harness's pre-tool hook enforces
    #: (Kraft-4in7z), never rendered into argv -- or `permission_rules`: one
    #: written into the CLI's own permission config at launch.
    via: str | None = None
    #: `permission_mode` only: the mode a launch under a tool allowlist runs
    #: in -- one that sends every ask it does not settle itself to the
    #: approval channel, rather than approving it (Kraft-nt6tt). A harness
    #: whose `permission_mode` declares none cannot launch under an allowlist.
    under_allowlist: str | None = None


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
    #: The env var naming the CLI's config directory, when the harness wants
    #: one Kraft owns instead of the user's (Kraft-bosip: Cursor's
    #: `CURSOR_CONFIG_DIR`). None: the CLI reads its own, as ever.
    config_env: str | None = None
    #: File name -> text Kraft writes into that directory before every launch.
    config_files: dict[str, str] = field(default_factory=dict)
    #: The CLI's own tool name -> Kraft's (Cursor's `Shell` -> `Bash`), so a
    #: hooked call is checked against policy under the names policy uses. More
    #: than one when the CLI's tool does both (Cursor's `Write` also edits):
    #: denied if any is, allowed under an allowlist only if all are listed.
    tool_names: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Policy tool names whose calls never reach the hook (Cursor's web
    #: fetch, probed 2026-09-23), so a launch that would have to deny one is
    #: refused rather than run with it unenforced.
    unhooked_tools: tuple[str, ...] = ()
    #: The `permission_hooks.TRANSLATORS` key that answers this CLI's
    #: pre-tool hook, or None when it has none (Kraft-4in7z).
    permission_hook: str | None = None
    #: The `permission_rules.RENDERERS` key that writes policy into this CLI's
    #: own permission config at launch, for a CLI with no hook (Kraft-4in7z.4).
    permission_rules: str | None = None

    @classmethod
    def from_mapping(cls, data: object, *, where: str, path: Path | None = None) -> Harness:
        """One harness file's parsed content, validated, or raise `HarnessError`
        prefixed with `where`."""
        try:
            parsed = HarnessInput.model_validate(data)
        except ValidationError as exc:
            raise HarnessError(f"{where}: {_shape_problem(exc)}") from exc
        return cls.from_input(parsed, where=where, path=path)

    @classmethod
    def from_input(cls, parsed: HarnessInput, *, where: str, path: Path | None = None) -> Harness:
        """A well-shaped harness, checked as a whole: the required capabilities
        are there, every binding is one `kind` can invoke, and the capabilities
        that depend on each other agree."""
        if not parsed.id:
            raise HarnessError(f"{where}: missing a string 'id'")
        kind = parsed.kind
        if kind not in KINDS:
            raise HarnessError(f"{where}: unknown kind {kind!r}; known: {sorted(KINDS)}")
        if not parsed.command:
            raise HarnessError(f"{where}: 'command' must be a non-empty string or list")
        command_resume = tuple(parsed.command_resume)
        if not parsed.capabilities:
            raise HarnessError(f"{where}: 'capabilities' must be a non-empty mapping")

        caps = {name: _capability(name, raw, where) for name, raw in parsed.capabilities.items()}

        for name in REQUIRED:
            if name not in caps:
                raise HarnessError(f"{where}: missing required capability {name!r}")

        for name, cap in caps.items():
            # Every capability is either invocable or one of the known facts
            # about reading results back. Neither -> `capabilities` is a
            # wishlist. `context` is exempt here because its own validation
            # below covers both channels: `channel: prompt` is deliberately
            # argv-less.
            if not cap.argv and not cap.via and name not in (*NON_INVOCABLE, "context"):
                raise HarnessError(
                    f"{where}: capability {name!r} has no 'cli' binding for kind {kind!r} "
                    f"and is not one of {sorted(NON_INVOCABLE)}"
                )
            if cap.via and cap.argv:
                raise HarnessError(
                    f"{where}: capability {name!r} is bound 'via' {cap.via!r}, "
                    "so it must not also carry a 'cli' binding"
                )
            if cap.via in ("permission_hook", "permission_rules"):
                if name not in ("deny_tools", "allowed_tools"):
                    raise HarnessError(
                        f"{where}: capability {name!r} cannot be bound via {cap.via!r}; "
                        "only deny_tools and allowed_tools can"
                    )
                if not getattr(parsed, cap.via):
                    raise HarnessError(
                        f"{where}: capability {name!r} is bound via {cap.via!r}, "
                        "which this harness does not define"
                    )
            elif cap.via and cap.via != "command_resume":
                raise HarnessError(
                    f"{where}: capability {name!r} 'via' must be 'command_resume', "
                    "'permission_hook' or 'permission_rules'"
                )
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

        if parsed.permission_hook is not None:
            from kraft.permission_hooks import TRANSLATORS

            if parsed.permission_hook not in TRANSLATORS:
                raise HarnessError(
                    f"{where}: unknown permission_hook {parsed.permission_hook!r}; "
                    f"known: {sorted(TRANSLATORS)}"
                )

        if parsed.permission_rules is not None:
            from kraft.permission_rules import RENDERERS

            if parsed.permission_rules not in RENDERERS:
                raise HarnessError(
                    f"{where}: unknown permission_rules {parsed.permission_rules!r}; "
                    f"known: {sorted(RENDERERS)}"
                )

        config = parsed.config_dir
        if config is not None:
            if not config.env:
                raise HarnessError(f"{where}: 'config_dir' needs an 'env' naming its variable")
            for name in config.files:
                # Written under a directory Kraft owns: a name must stay in it.
                if name in ("", ".", "..") or Path(name).name != name:
                    raise HarnessError(f"{where}: 'config_dir' file {name!r} is not a plain name")

        return cls(
            id=parsed.id,
            kind=kind,
            command=tuple(parsed.command),
            command_resume=command_resume,
            capabilities=caps,
            path=path,
            config_env=config.env if config is not None else None,
            config_files={
                name: json.dumps(body, indent=2) + "\n"
                for name, body in (config.files if config is not None else {}).items()
            },
            tool_names={
                k: (v,) if isinstance(v, str) else tuple(v) for k, v in parsed.tool_names.items()
            },
            unhooked_tools=tuple(parsed.unhooked_tools),
            permission_hook=parsed.permission_hook,
            permission_rules=parsed.permission_rules,
        )

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


#: A command prefix, written as a string or a list of strings.
Prefix = Annotated[list[StrictStr], BeforeValidator(lambda v: [v] if isinstance(v, str) else v)]


class CapabilityInput(BaseModel):
    """One `capabilities:` entry as written. Shape only: whether its binding
    makes sense for its name is `Harness.from_input`'s to say."""

    # Unknown keys are ignored, as the hand-rolled parser did: refusing them
    # would quarantine an operator overlay that loaded yesterday.
    model_config = ConfigDict(strict=True)

    cli: list[StrictStr] = []
    values: list[StrictStr] = []
    always: StrictStr | list[StrictStr] | None = None
    channel: StrictStr | None = None
    source: StrictStr | None = None
    reader: StrictStr | None = None
    via: StrictStr | None = None
    under_allowlist: StrictStr | None = None


class ConfigDirInput(BaseModel):
    """`config_dir:` as written: the variable the CLI reads its config
    directory from, and each file in it as a mapping Kraft writes as JSON."""

    model_config = ConfigDict(strict=True)

    env: StrictStr = ""
    files: dict[StrictStr, dict[StrictStr, Any]] = {}


class HarnessInput(BaseModel):
    """One harness file as written. Everything is defaulted so that a missing
    key reaches `Harness.from_input`, which refuses it in the same words as a
    wrong one."""

    model_config = ConfigDict(strict=True)

    id: StrictStr = ""
    kind: StrictStr | None = None
    command: Prefix = []
    command_resume: Prefix = []
    capabilities: dict[StrictStr, CapabilityInput] = {}
    config_dir: ConfigDirInput | None = None
    tool_names: dict[StrictStr, StrictStr | list[StrictStr]] = {}
    unhooked_tools: list[StrictStr] = []
    permission_hook: StrictStr | None = None
    permission_rules: StrictStr | None = None


#: What each capability key's shape is, in the words an operator reads.
_SHAPE = {
    "cli": "must be a list of strings",
    "values": "must be a list of strings",
    "always": "must be a string or a list of strings",
}


def _shape_problem(exc: ValidationError) -> str:
    """The first thing `HarnessInput` refused, in the prose the hand-rolled
    parser used rather than pydantic's "Input should be a valid list"."""
    error = exc.errors()[0]
    match error["loc"]:
        case ("id",):
            return "missing a string 'id'"
        case ("kind",):
            return f"unknown kind {error['input']!r}; known: {sorted(KINDS)}"
        case ("command" | "command_resume" as key, *_):
            return f"{key!r} must be a string or a list of strings"
        case ("capabilities",):
            return "'capabilities' must be a non-empty mapping"
        case ("capabilities", name):
            return f"capability {name!r} must be a mapping"
        case ("capabilities", name, key, *_):
            return f"capability {name!r} {key!r} {_SHAPE.get(key, 'must be a string')}"
        case ("tool_names", *_):
            return "'tool_names' must map a CLI's tool name to Kraft's: a string or a list"
        case ("unhooked_tools", *_):
            return "'unhooked_tools' must be a list of strings"
        case ("permission_hook" | "permission_rules" as key,):
            return f"{key!r} must be a string"
        case ("config_dir", *_):
            return "'config_dir' needs an 'env' string and 'files' mapping names to mappings"
    return "expected a top-level mapping"


def _capability(name: str, raw: CapabilityInput, where: str) -> Capability:
    if name not in KNOWN:
        raise HarnessError(f"{where}: unknown capability {name!r}; known: {sorted(KNOWN)}")
    for frag in raw.cli:
        for ph in re.findall(r"\{[a-z_]+\}", frag):
            if ph not in PLACEHOLDERS:
                raise HarnessError(
                    f"{where}: capability {name!r} uses placeholder {ph}; "
                    f"only {', '.join(PLACEHOLDERS)} exist"
                )
    for pattern in raw.values:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HarnessError(
                f"{where}: capability {name!r} value pattern {pattern!r} is not a regex: {exc}"
            ) from None
    return Capability(
        name=name,
        argv=tuple(raw.cli),
        values=tuple(raw.values),
        always=tuple(raw.always) if isinstance(raw.always, list) else raw.always,
        channel=raw.channel,
        source=raw.source,
        reader=raw.reader,
        via=raw.via,
        under_allowlist=raw.under_allowlist,
    )


def load(harnesses_dir: Path | None) -> HarnessSet:
    """Every harness, overlay-first then bundled, by bare name.

    Per-file validity: one malformed harness
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
                h = Harness.from_mapping(data, where=str(path), path=path)
            except HarnessError as exc:
                invalid[stem] = str(exc)
                continue
            if h.id != stem:
                invalid[stem] = f"{path}: id {h.id!r} does not match its file name {stem!r}"
                continue
            valid[stem] = h
    return HarnessSet(valid=valid, invalid=invalid)


# shlex for the binding's `command:` override -- the same treatment agent.py
# gives it today.
def _fill(fragments: tuple[str, ...], value: str | tuple[str, ...]) -> list[str]:
    csv = ",".join(value) if isinstance(value, tuple) else value
    scalar = value if isinstance(value, str) else csv
    out = []
    for frag in fragments:
        out.append(frag.replace("{value}", scalar).replace("{csv}", csv))
    return out


def build_argv(
    h: Harness,
    *,
    command: str | None = None,
    prompt: str,
    context: str,
    options: dict[str, str | tuple[str, ...]] | None = None,
    resume: str | None = None,
    extra: tuple[str, ...] = (),
) -> list[str]:
    """One command line for one dispatch.

    Order is `capabilities:` declaration order, after the command prefix --
    the file reads as the command line it builds. A capability the harness
    does not declare is *skipped*, never emitted flagless: loading the task or
    profile already refused it, and a second line of defence here is what
    makes the old `agent.py:473` bare-positional bug unrepresentable.
    """
    opts = dict(options or {})
    resuming = bool(resume) and h.supports("resume")

    prefix = list(h.command_resume if resuming and h.command_resume else h.command)
    if resuming and h.command_resume:
        prefix = [p.replace("{value}", resume) for p in prefix]
    if command:
        # The executable slot only, shlex.split so a multiword wrapper keeps
        # working (Task 2 note). The harness's own subcommands survive after
        # it, for both the normal and the resume prefix.
        prefix = shlex.split(command) + prefix[1:]

    ctx_cap = h.capabilities["context"]
    prompt_value = f"{context}\n\n{prompt}" if ctx_cap.channel == "prompt" else prompt

    # `extra`: flags Kraft adds itself (a launch's permission rules), right
    # after the prefix so no positional can swallow them.
    argv = [*prefix, *extra]
    for name, cap in h.capabilities.items():
        if not cap.argv:
            continue  # non-invocable, or carried by `via`
        if name == "resume":
            if not resuming or h.command_resume:
                continue  # absent, or already in the prefix
            value: str | tuple[str, ...] | None = resume
        elif name == "prompt":
            value = prompt_value
        elif name == "context":
            value = context if cap.channel == "system_prompt" else None
        else:
            provided = opts.get(name)
            has_placeholder = any(ph in frag for frag in cap.argv for ph in PLACEHOLDERS)
            if isinstance(cap.always, tuple):
                given = provided if isinstance(provided, tuple) else ()
                merged = tuple(dict.fromkeys((*cap.always, *given)))
                value = merged or None
            elif not has_placeholder:
                # No placeholder to fill (e.g. `structured_log`'s
                # `--output-format stream-json`) -- this is a flag every
                # launch gets, unconditionally, from the declaration alone.
                value = ""
            else:
                value = provided if provided is not None else cap.always
        if value is None:
            continue
        argv += _fill(cap.argv, value)
    return argv
