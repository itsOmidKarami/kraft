"""What a `kind: acp` entry declares: what ACP cannot tell Kraft (spec §5).

Imports nothing from `kraft.harness` at import time except inside the functions
that need it, so `harness.py` can import this module at the top without a cycle."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, StrictStr

#: What an agent may report, for the cap refusals (spec §2): per-turn tokens, a cost.
REPORTS = ("tokens", "cost")
#: Kraft options an entry may map onto an agent's session config options (spec §5).
OPTIONS = ("model", "effort")


class AsksEverythingInput(BaseModel):
    """How to make the agent ask before every tool call (spec §6): one of an env
    mapping, session config options, or a mode. Stored here, applied by part 2."""

    model_config = ConfigDict(strict=True, extra="forbid")

    env: dict[StrictStr, StrictStr] | None = None
    config: dict[StrictStr, StrictStr] | None = None
    mode: StrictStr | None = None


class OptionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    config: StrictStr


@dataclass(frozen=True)
class AsksEverything:
    env: dict[str, str]
    config: dict[str, str]
    mode: str | None


@dataclass(frozen=True)
class AcpEntry:
    asks_everything: AsksEverything | None
    #: Subset of `REPORTS`: what this agent sends, so a cap that needs more refuses
    #: the launch before it starts (`acp.launch.cap_refusal`).
    reports: frozenset[str]
    #: ACP tools the client always rejects, whatever the task's policy (part 2).
    always_deny: tuple[str, ...]
    #: Kraft option name -> the agent's session config option id.
    options: dict[str, str]


def build(
    parsed_asks: AsksEverythingInput | None,
    reports: list[str],
    always_deny: list[str],
    options: dict[str, OptionInput],
    *,
    where: str,
) -> AcpEntry:
    """Validate the four entry-only fields (already shape-checked by pydantic) and
    build the `AcpEntry`; raise `HarnessError` prefixed `where`."""
    from kraft.harness import HarnessError

    bad = [r for r in reports if r not in REPORTS]
    if bad:
        raise HarnessError(f"{where}: 'reports' takes only {list(REPORTS)}, not {bad}")
    asks = None
    if parsed_asks is not None:
        given = [k for k in ("env", "config", "mode") if getattr(parsed_asks, k) is not None]
        if len(given) != 1:
            raise HarnessError(
                f"{where}: 'asks_everything' takes exactly one of env, config or mode"
            )
        asks = AsksEverything(
            env=dict(parsed_asks.env or {}),
            config=dict(parsed_asks.config or {}),
            mode=parsed_asks.mode,
        )
    unknown = [k for k in options if k not in OPTIONS]
    if unknown:
        raise HarnessError(f"{where}: 'options' takes only {list(OPTIONS)}, not {unknown}")
    return AcpEntry(
        asks_everything=asks,
        reports=frozenset(reports),
        always_deny=tuple(always_deny),
        options={k: v.config for k, v in options.items()},
    )


def capabilities_for(entry: AcpEntry) -> dict:
    """The `capabilities` an ACP harness answers with. An entry declares none: the
    agent says what it can do at `initialize`, so Kraft synthesizes the ones its
    consumers read (`log_reader`, `build_context`, `supports("resume")`, ...)."""
    from kraft.harness import Capability

    caps = {
        "prompt": Capability(name="prompt"),
        "context": Capability(name="context", channel="system_prompt"),
        "usage": Capability(name="usage", source="envelope", reader="acp"),
        "structured_log": Capability(name="structured_log"),
        "resume": Capability(name="resume"),
    }
    for option in entry.options:
        caps[option] = Capability(name=option)
    return caps
