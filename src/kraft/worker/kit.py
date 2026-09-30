"""A Docker Sandbox Kit read, checked and lowered onto `kind: docker`
(sandbox part 2, P7; spec §9).

Kraft reads exactly one operator-authored Kit, a `kind: workload`, and
composes nothing. Its descriptor comes from the Kit image's manifest
annotation (`fetch`), is cached by the digest the policy pins (`ensure`,
`cached`), decoded strictly (`decode`), judged against what Kraft enforces
(`claims`), and lowered to the `SandboxPolicy` the docker backend already
runs (`lower`).

Kraft's decoder is not upstream's `ValidatePublished`, and it errs closed by
construction: anything it does not model it refuses. Every refusal is a
`KitRefused` naming what it found: a capability by its type, a decode error
by its dotted path, a fetch by what the CLI said. The conformance set in
`tests/fixtures/kit/<tag>/` pins it to upstream at `SPEC_TAG`; bumping the
tag re-copies those fixtures and re-reads the four capability pages Kraft
claims (network-policy@1, credential@1, resources@1, agent-sessions@1).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from kraft.paths import RunDirs, default_run_dir, default_templates_dir
from kraft.policy import HostPattern, SandboxPolicy
from kraft.worker.backends import docker
from kraft.worker.ca import write_whole

#: The upstream `docker/sandbox-kit-spec` release this reader follows (e502d26).
SPEC_TAG = "v3.0.0-m.7"
#: The manifest annotation holding the published descriptor (SPEC §9.3).
ANNOTATION = "vnd.docker.sandbox.kit.descriptor"
#: Kraft's own limit, the number SPEC §9.4 makes a publish-time error;
#: upstream sets no consumer-side one.
MAX_DESCRIPTOR = 512 * 1024
#: The most `manifest inspect` may print: a registry's own ceiling (~4 MB).
MAX_MANIFEST = 4 * 1024 * 1024

_NS = "com.docker.sandbox/"
NETWORK = _NS + "network-policy@1"
CREDENTIAL = _NS + "credential@1"
RESOURCES = _NS + "resources@1"
AGENT_SESSIONS = _NS + "agent-sessions@1"
#: SPEC §7.1: at most one entry each among a descriptor's ordinary entries.
SINGLETONS = frozenset(
    _NS + t
    for t in (
        "network-policy@1 network-policy@2 resources@1 privileged@1 kit-registry@1 "
        "agent-sessions@1 lifecycle@1 agent-context@1 sbx@1 long-running@1 git-identity@1"
    ).split()
)
#: SPEC §11: types that take no config at all, not even `{}`.
CONFIGLESS = frozenset(
    _NS + t for t in ("privileged@1", "kit-registry@1", "sbx@1", "long-running@1", "git-identity@1")
)
#: SPEC §11's capability type grammar.
_TYPE = r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?/[a-z0-9]([a-z0-9-]*[a-z0-9])?@[1-9][0-9]*"
#: credential@1's `service`: a lowercase-kebab handle (upstream `handleName`).
_SERVICE = r"[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?"


class KitRefused(ValueError):
    """Kraft will not run this Kit; the message names what it found."""


class _Strict(BaseModel):
    """Every level Kraft models refuses what it does not know (SPEC §1.2: a
    misspelled key silently ignored is a policy silently absent)."""

    model_config = ConfigDict(strict=True, extra="forbid", alias_generator=to_camel)


# --- the four claimed configs ----------------------------------------------------------


class NetworkRules(_Strict):
    allow: list[HostPattern] = []
    deny: list[HostPattern] = []


class NetworkConfig(_Strict):
    """network-policy@1. A phase left out grants nothing."""

    install: NetworkRules | None = None
    runtime: NetworkRules | None = None


class Inject(_Strict):
    domain: str = Field(min_length=1)
    header: str | None = None
    format: str | None = None
    scheme: str | None = None
    username: str | None = None


class ApiKey(_Strict):
    #: Empty or omitted with `inject` rules: inject-only, no env presence.
    name: str = Field(default="", pattern=r"^([A-Za-z_][A-Za-z0-9_]*)?$")
    proxy_managed: bool = False
    inject: list[Inject] = []

    @model_validator(mode="after")
    def _names_or_injects(self) -> ApiKey:
        if not self.name and not self.inject:
            raise ValueError("apiKey needs a name, inject rules, or both")
        return self


class OAuth(_Strict):
    """Its keys are checked; its values are not, since Kraft applies none of it."""

    token_endpoint: dict[str, Any] | None = None
    resource_hosts: list[str] | None = None
    sentinels: dict[str, Any] | None = None
    credential_file: dict[str, Any] | None = None
    response_fields: dict[str, Any] | None = None
    passthrough: bool = False


class CredentialConfig(_Strict):
    """credential@1. `service` and `phase` are required (m.7)."""

    service: str = Field(pattern=f"^{_SERVICE}$")
    phase: list[Literal["install", "runtime"]] = Field(min_length=1)
    api_key: ApiKey | None = None
    oauth: OAuth | None = None

    @field_validator("phase", mode="before")
    @classmethod
    def _one_or_a_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def _presents_somehow(self) -> CredentialConfig:
        if self.api_key is None and self.oauth is None:
            raise ValueError(f"credential {self.service!r} declares neither apiKey nor oauth")
        return self


class ResourcesConfig(_Strict):
    cpu: int | float | None = Field(default=None, ge=0)
    memory: str | None = None
    gpu: str | None = None


class SessionsConfig(_Strict):
    """agent-sessions@1: its keys checked, nothing applied. Upstream's verb
    and placeholder rules are left out: the harness drives the CLI, so a
    malformed verb here reaches nothing."""

    prompt: list[str] | None = None
    resume: list[str] | None = None
    continue_: list[str] | None = Field(default=None, alias="continue")
    list_: str | list[str] | None = Field(default=None, alias="list")


# --- capability items ------------------------------------------------------------------


class _Item(_Strict):
    name: str | None = None
    optional: bool = False
    description: str | None = None
    #: Diagnostic provenance only, never a trust input (SPEC §7).
    source: dict[str, Any] | None = None


class NetworkCap(_Item):
    type: Literal["com.docker.sandbox/network-policy@1"]
    config: NetworkConfig = NetworkConfig()


class CredentialCap(_Item):
    type: Literal["com.docker.sandbox/credential@1"]
    config: CredentialConfig


class ResourcesCap(_Item):
    type: Literal["com.docker.sandbox/resources@1"]
    config: ResourcesConfig = ResourcesConfig()


class SessionsCap(_Item):
    type: Literal["com.docker.sandbox/agent-sessions@1"]
    config: SessionsConfig


class OtherCap(_Item):
    """A type Kraft does not claim: its config rides opaquely (SPEC §7.3)."""

    type: str = Field(pattern=f"^{_TYPE}$")
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _configless(self) -> OtherCap:
        if self.type in CONFIGLESS and "config" in self.model_fields_set:
            raise ValueError(f"{self.type} takes no config")
        return self


class GroupItem(_Strict):
    """A capability group (SPEC §7.1.1); `claims` refuses every one."""

    group: dict[str, Any]
    source: dict[str, Any] | None = None


_TAGS = {
    NETWORK: "network-policy@1",
    CREDENTIAL: "credential@1",
    RESOURCES: "resources@1",
    AGENT_SESSIONS: "agent-sessions@1",
}


def _tag(item: object) -> str:
    if isinstance(item, dict):
        return "group" if "group" in item else _TAGS.get(item.get("type"), "other")
    if isinstance(item, GroupItem):
        return "group"
    return _TAGS.get(getattr(item, "type", None), "other")


Capability = Annotated[
    Annotated[NetworkCap, Tag("network-policy@1")]
    | Annotated[CredentialCap, Tag("credential@1")]
    | Annotated[ResourcesCap, Tag("resources@1")]
    | Annotated[SessionsCap, Tag("agent-sessions@1")]
    | Annotated[OtherCap, Tag("other")]
    | Annotated[GroupItem, Tag("group")],
    Discriminator(_tag),
]
_TAG_NAMES = {*_TAGS.values(), "other", "group"}


# --- the descriptor --------------------------------------------------------------------


class Arg(_Strict):
    default: str | None = None
    required: bool = False
    description: str | None = None
    enum: list[str] | None = None
    pattern: str | None = None
    env: str | None = None
    build_arg: str | None = None


class KitEntry(_Strict):
    """A published set's record of a Kit it merged; the merge is done."""

    ref: str
    digest: str | None = None
    args: dict[str, str] | None = None


def _references(value: object, at: str) -> str | None:
    """The dotted path of the first `${{` in `value`, keys included."""
    if isinstance(value, str):
        return at if "${{" in value else None
    items = (
        value.items()
        if isinstance(value, dict)
        else enumerate(value)
        if isinstance(value, list)
        else ()
    )
    for key, inner in items:
        if isinstance(key, str) and "${{" in key:
            return f"{at}.{key}"
        if found := _references(inner, f"{at}.{key}"):
            return found
    return None


def _allowed(network: NetworkConfig | None, phase: str, domain: str) -> bool:
    """SPEC §11: by name, port ignored, or through a bare `*`/`**`."""
    rules = getattr(network, phase) if network else None
    hosts = {h.split(":")[0] for h in (rules.allow if rules else ())}
    return bool(hosts & {domain.split(":")[0], "*", "**"})


class Descriptor(_Strict):
    """A published descriptor: every SPEC §4 field, with SPEC §11's
    descriptor-level rules. Whether Kraft runs it is `claims`' question."""

    schema_version: Literal["3"]
    #: `sandbox` is the pre-rename spelling of `workload`; `set` is never published.
    kind: Literal["workload", "mixin", "sandbox"]
    display_name: str | None = None
    author: str | None = None
    description: str | None = None
    source_url: str | None = None
    icon_url: str | None = None
    version: str | None = None
    licenses: list[str] | None = None
    provides: list[str] | None = None
    requires: list[str] | None = None
    integrates: list[str] | None = None
    conflicts: list[str] | None = None
    capabilities: list[Capability] = []
    args: dict[str, Arg] | None = None
    build: str | None = None
    dockerfile: str | None = None
    kits: list[KitEntry] | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_references(cls, data: object) -> object:
        """A create-phase `${{ kit.args.* }}` or `${{ kit.env.* }}` in a
        capability must be expanded before validating (SPEC §6, §6.1), and
        Kraft expands none: refused before a typed check misreads it."""
        if isinstance(data, dict) and (at := _references(data.get("capabilities"), "capabilities")):
            raise ValueError(f"{at} holds a `${{{{` reference, which Kraft never expands")
        return data

    @model_validator(mode="after")
    def _arity_and_cross_entry(self) -> Descriptor:
        ordinary = [(i, c) for i, c in enumerate(self.capabilities) if not isinstance(c, GroupItem)]
        seen: dict[str, int] = {}
        dumps: dict[str, int] = {}
        for i, cap in ordinary:
            if cap.type in SINGLETONS and cap.type in seen:
                raise ValueError(
                    f"capabilities.{i}: {cap.type} is also at capabilities.{seen[cap.type]}"
                )
            seen.setdefault(cap.type, i)
            dump = json.dumps(cap.model_dump(by_alias=True), sort_keys=True)
            if dump in dumps:
                raise ValueError(f"capabilities.{i} repeats capabilities.{dumps[dump]} exactly")
            dumps[dump] = i
        if NETWORK in seen and _NS + "network-policy@2" in seen:
            raise ValueError("network-policy@1 and network-policy@2 are both declared")
        network = next((c.config for _, c in ordinary if isinstance(c, NetworkCap)), None)
        credentials = [(i, c) for i, c in ordinary if isinstance(c, CredentialCap)]
        owners: dict[tuple[str, str], int] = {}
        for i, cap in credentials:
            for phase in cap.config.phase:
                key = (cap.config.service, phase)
                if key in owners:
                    raise ValueError(
                        f"capabilities.{i}: credential {key[0]!r} in phase {phase} "
                        f"is also at capabilities.{owners[key]}"
                    )
                owners[key] = i
        for i, cap in credentials:
            for phase in cap.config.phase:
                for j, rule in enumerate(cap.config.api_key.inject if cap.config.api_key else ()):
                    if not _allowed(network, phase, rule.domain):
                        raise ValueError(
                            f"capabilities.{i}.config.apiKey.inject.{j}.domain: {rule.domain!r} "
                            f"is not in the network policy's {phase} allow list"
                        )
        return self


# --- decode ----------------------------------------------------------------------------


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if dup := next((k for k in keys if keys.count(k) > 1), None):
        raise KitRefused(f"the JSON descriptor repeats the key {dup!r}")
    return dict(pairs)


class _StrictYaml(yaml.SafeLoader):
    """A SafeLoader that refuses a mapping stating one key twice, and any
    alias: a few hundred bytes of nested aliases expand to billions of nodes,
    and a descriptor is decoded again on every read."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise KitRefused("the YAML descriptor uses an alias (`*name`); Kraft reads none")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=True) for k, _ in node.value]
        if dup := next((k for k in keys if keys.count(k) > 1), None):
            raise KitRefused(f"the YAML descriptor repeats the key {dup!r}")
        return super().construct_mapping(node, deep)


def _path(loc: tuple) -> str:
    return ".".join(str(p) for p in loc if p not in _TAG_NAMES)


def _explained(exc: ValidationError) -> str:
    return "; ".join(
        # A rule on the whole descriptor names its own path.
        ": ".join(filter(None, (_path(e["loc"]), e["msg"].removeprefix("Value error, "))))
        for e in exc.errors()
    )


def decode(text: str) -> Descriptor:
    """The annotation's text as a `Descriptor`, or `KitRefused`. Compact
    JSON is the published form; only text that is not JSON at all is read
    as YAML, which Kits published before the switch carry (SPEC §9.3). A
    JSON duplicate key never falls through to YAML, which would keep the
    last one."""
    if len(text.encode()) > MAX_DESCRIPTOR:
        raise KitRefused(f"the descriptor is over {MAX_DESCRIPTOR // 1024} KiB")
    try:
        try:
            data = json.loads(text, object_pairs_hook=_json_pairs)
        except json.JSONDecodeError:
            try:
                data = yaml.load(text, _StrictYaml)  # noqa: S506 -- a SafeLoader
            except yaml.YAMLError as exc:
                raise KitRefused(f"the descriptor is neither JSON nor YAML: {exc}") from exc
        return Descriptor.model_validate(data)
    except ValidationError as exc:
        raise KitRefused(_explained(exc)) from exc
    except RecursionError as exc:
        raise KitRefused("the descriptor is nested too deeply") from exc


# --- claims ----------------------------------------------------------------------------


class Claims(BaseModel):
    """What Kraft enforces of a Kit, and what it skipped (optional entries
    it does not enforce) or ignored (accepted, not applied) -- the record
    SPEC §7.3's "skipped and recorded" asks for."""

    model_config = ConfigDict(frozen=True)

    network: NetworkConfig | None = None
    credentials: tuple[CredentialCap, ...] = ()
    resources: ResourcesConfig | None = None
    skipped: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()


def _unenforceable(config: CredentialConfig) -> str | None:
    """Why the egress proxy cannot present this credential, or None: it
    puts a named, proxy-managed key in a header, nothing else."""
    key = config.api_key
    if key is None:
        return "oauth only"
    if not key.proxy_managed:
        return "not proxyManaged"
    if not key.name:
        return "inject-only, no apiKey.name"
    if not key.inject:
        return "no inject rules"
    if any(r.header is None or r.scheme or r.username for r in key.inject):
        return "injects by scheme or username, not a header"
    return None


def capability_claims(capabilities: list) -> Claims:
    """The capability layer alone: each entry enforced, ignored, skipped
    (optional) or refused by its type (required). `claims` runs it after the
    descriptor-level checks; the conformance tests run it on upstream's
    fixtures directly, nearly all of which are mixins."""
    found: dict[str, Any] = {"credentials": [], "skipped": [], "ignored": []}

    def unless_optional(cap, what: str) -> None:
        if not cap.optional:
            raise KitRefused(f"requires {what}; Kraft does not enforce it")
        found["skipped"].append(what)

    for cap in capabilities:
        if isinstance(cap, GroupItem):
            raise KitRefused(
                f"has a capability group {cap.group.get('name')!r}; Kraft selects none"
            )
        if isinstance(cap, NetworkCap):
            found["network"] = cap.config
        elif isinstance(cap, SessionsCap):
            # The harness drives the CLI; it grants nothing (open question 7).
            found["ignored"].append(cap.type)
        elif isinstance(cap, CredentialCap):
            if why := _unenforceable(cap.config):
                unless_optional(cap, f"{cap.type} {cap.config.service}: {why}")
                continue
            found["credentials"].append(cap)
            if cap.config.oauth is not None:
                found["ignored"].append(f"{cap.type} {cap.config.service} oauth")
        elif isinstance(cap, ResourcesCap):
            if cap.config.gpu is not None:
                unless_optional(cap, f"{cap.type} gpu")
            found["resources"] = cap.config
        else:
            unless_optional(cap, cap.type)
    return Claims(**found)


def claims(descriptor: Descriptor) -> Claims:
    """What Kraft enforces of `descriptor`, or `KitRefused` naming why it
    will not run it (spec §9.1)."""
    if descriptor.kind == "mixin":
        raise KitRefused("is a kind: mixin; Kraft composes no Kits, so runs only a workload")
    if descriptor.requires:
        raise KitRefused(f"requires {descriptor.requires}; Kraft composes no Kit to provide it")
    for name, arg in (descriptor.args or {}).items():
        if arg.env:
            raise KitRefused(
                f"arg {name!r} exports {arg.env} to the container; Kraft never supplies a value"
            )
    found = capability_claims(descriptor.capabilities)
    if found.network is None:
        raise KitRefused(f"declares no {NETWORK}; Kraft runs a Kit only under its egress policy")
    return found


# --- lower -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Lowered:
    """A Kit as the `kind: docker` policy that runs it."""

    policy: SandboxPolicy
    kit: str
    skipped: tuple[str, ...]
    ignored: tuple[str, ...]


#: resources@1's IEC spellings, which docker writes without the `ib` (its
#: units are binary already).
_IEC = re.compile(r"([1-9][0-9]*)([kmg])ib")


def _memory(value: str) -> str:
    """Kraft's own spelling as it is; `2gib` as `2g`. `SandboxResources`
    refuses anything else."""
    iec = _IEC.fullmatch(value.strip().lower())
    return f"{iec[1]}{iec[2]}" if iec else value


def lower(ref: str, descriptor: Descriptor, bindings: Mapping[str, str]) -> Lowered:
    """The `kind: docker` policy `descriptor` lowers to (spec §9.4), run from
    the Kit image `ref`. `bindings` maps a credential's `service` to the
    daemon's own variable holding its value: an unbound required one
    refuses the Kit, an unbound optional one is skipped."""
    found = claims(descriptor)
    skipped = list(found.skipped)
    credentials = []
    for cap in found.credentials:
        config = cap.config
        if (source := bindings.get(config.service)) is None:
            if not cap.optional:
                raise KitRefused(
                    f"credential service {config.service!r} has no binding: add "
                    f"`{config.service}: <DAEMON_ENV_NAME>` under sandbox.yaml's credentials"
                )
            skipped.append(f"{cap.type} {config.service}: no binding")
            continue
        credentials.append(
            {
                "env": config.api_key.name,
                "service": config.service,
                "phase": config.phase,
                "source": source,
                "inject": [
                    {
                        k: v
                        for k, v in r.model_dump(include={"domain", "header", "format"}).items()
                        if v
                    }
                    for r in config.api_key.inject
                ],
            }
        )
    network = {
        phase: (rules.model_dump() if (rules := getattr(found.network, phase)) else {})
        for phase in ("install", "runtime")
    }
    resources = found.resources
    try:
        policy = SandboxPolicy.model_validate(
            {
                "kind": "docker",
                "image": ref,
                # Both phases written out: one left out denies everything, and
                # must not dump as `network: {}`, which reloads as open.
                "network": network,
                "credentials": credentials or None,
                "resources": {
                    k: v
                    for k, v in {
                        # SPEC: unset, and so 0, is no constraint.
                        "cpu": resources.cpu or None if resources else None,
                        "memory": _memory(resources.memory)
                        if resources and resources.memory
                        else None,
                    }.items()
                    if v is not None
                },
            }
        )
    except ValidationError as exc:
        raise KitRefused(f"lowers to a sandbox Kraft refuses: {_explained(exc)}") from exc
    return Lowered(policy, ref, tuple(skipped), found.ignored)


# --- fetch and cache -------------------------------------------------------------------


@dataclass(frozen=True)
class Fetched:
    """A Kit's descriptor text and the manifest digest it was read from."""

    ref: str
    manifest: str
    text: str

    def descriptor(self) -> Descriptor:
        """Decoded afresh on every read, so a Kraft upgrade's rules reach
        Kits already cached."""
        return decode(self.text)


def _digest(ref: str) -> str:
    name, _, digest = ref.rpartition("@")
    if not name or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise KitRefused(f"{ref!r} is not pinned: it needs @sha256:<64 hex>")
    return digest


#: What podman (6.1) answers `manifest inspect` of a single manifest, which
#: it reads only as an index.
_SINGLE = "Treating single images as manifest lists is not implemented"
#: A pull's bound: an image, not a manifest.
PULL_TIMEOUT_S = 600


async def _ask(*args: str, timeout: float | None = None) -> str:
    """The CLI's stdout for `args`, or `KitRefused` quoting what it said."""
    said = f"`{' '.join(args[:2])} {args[-1]}`"
    answer = await docker.docker_ask(*args, timeout=timeout, limit=MAX_MANIFEST)
    if answer is None:
        raise KitRefused(f"{said} did not answer")
    code, out, err = answer
    # First: past the limit the client was killed, and whether it had
    # exited 0 before the kill landed is only timing.
    if len(out) > MAX_MANIFEST:
        raise KitRefused(f"{said} answered over {MAX_MANIFEST} bytes")
    if code != 0:
        raise KitRefused(f"{said} failed: {(err or out).strip() or f'exit {code}'}")
    return out


def _json_mapping(out: str, what: str, noun: str) -> dict:
    try:
        value = json.loads(out)
    except (ValueError, RecursionError):
        value = None
    if not isinstance(value, dict):
        raise KitRefused(f"{what} answered no {noun}")
    return value


async def _inspect(ref: str) -> dict | None:
    """`ref`'s manifest, or None where the CLI reads only an index (podman)."""
    try:
        out = await _ask("manifest", "inspect", ref)
    except KitRefused as exc:
        if _SINGLE in str(exc):
            return None
        raise
    return _json_mapping(out, f"`manifest inspect {ref}`", "manifest")


async def _pulled_annotation(ref: str) -> str | None:
    """A single manifest's annotation where the CLI will not inspect one
    (spec §9.2): the image pulled by digest, whose annotations are its
    manifest's."""
    await _ask("pull", "-q", ref, timeout=PULL_TIMEOUT_S)
    out = await _ask("image", "inspect", "--format", "{{json .Annotations}}", ref)
    return _annotation({"annotations": _json_mapping(out, f"`image inspect {ref}`", "annotations")})


def _mapping(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise KitRefused(f"{what} is not a mapping: {str(value)[:80]!r}")
    return value


def _annotation(manifest: dict) -> str | None:
    text = _mapping(manifest.get("annotations") or {}, "a manifest's annotations").get(ANNOTATION)
    return text if isinstance(text, str) else None


async def fetch(ref: str) -> Fetched:
    """The descriptor on `ref`'s index or manifest, through the runtime's
    own CLI and registry login. An index without it (docker 29's `manifest
    inspect` drops index annotations) falls back to a platform manifest, by
    digest, as SPEC §9.3 makes mandatory: the first that is not an
    attestation (`unknown` os), since the frontend annotates every platform's
    alike. A CLI that will not inspect a single manifest (podman) has the
    image pulled by digest and its annotations read. Kraft relies on the
    CLI's own by-digest verification of what the registry served, and does
    not check the digest again."""
    manifest = _digest(ref)
    top = await _inspect(ref)
    if top is None:
        text = await _pulled_annotation(ref)
    elif (text := _annotation(top)) is None:
        repository = ref.rpartition("@")[0]
        for entry in top.get("manifests") or ():
            entry = _mapping(entry, "an index entry")
            if _mapping(entry.get("platform") or {}, "an index entry's platform").get("os") in (
                None,
                "unknown",
            ):
                continue
            manifest = entry.get("digest")
            if not isinstance(manifest, str):
                raise KitRefused(f"{ref}'s index names a platform manifest with no digest")
            platform = await _inspect(f"{repository}@{manifest}")
            text = (
                _annotation(platform)
                if platform is not None
                else await _pulled_annotation(f"{repository}@{manifest}")
            )
            break
    if text is None:
        raise KitRefused(f"{ref} is not a Kit: no {ANNOTATION} annotation")
    return Fetched(ref, manifest, text)


def _cache(ref: str) -> Path:
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    return RunDirs(run_dir).kit / f"{_digest(ref).removeprefix('sha256:')}.json"


def cached(ref: str) -> Fetched | None:
    """`ref`'s descriptor as `ensure` stored it, or None. Never stale: the
    key is the digest `ref` pins."""
    # Anything unreadable is a miss, so `ensure` fetches it again rather
    # than keep a corrupted entry for good.
    try:
        stored = json.loads(_cache(ref).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or not all(
        isinstance(stored.get(k), str) for k in ("manifest", "descriptor")
    ):
        return None
    return Fetched(ref, stored["manifest"], stored["descriptor"])


async def ensure(ref: str) -> Fetched:
    """`cached(ref)`, fetching and storing it first when it is not there."""
    if (hit := cached(ref)) is not None:
        return hit
    fetched = await fetch(ref)
    path = _cache(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_whole(
        path, json.dumps({"manifest": fetched.manifest, "descriptor": fetched.text}).encode(), 0o644
    )
    return fetched


# --- resolve ---------------------------------------------------------------------------


def bindings() -> Mapping[str, str]:
    """`sandbox.yaml`'s `credentials:`, each service's daemon variable.
    Raises `ConfigError` for a `sandbox.yaml` that does not parse."""
    from kraft import config

    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    return config.SandboxHost.load(templates / config.SandboxHost.FILE).credentials


def lowered(ref: str) -> Lowered | None:
    """`ref` lowered from the cache alone, or None when it was never
    fetched. `KitRefused` and `ConfigError` as `lower` and `bindings`."""
    hit = cached(ref)
    return None if hit is None else lower(ref, hit.descriptor(), bindings())


async def resolve(ref: str) -> tuple[Fetched, Lowered]:
    """`ref` fetched when it is not cached, then lowered: what the walk,
    dispatch and doctor ask before a Kit may run."""
    fetched = await ensure(ref)
    return fetched, lower(ref, fetched.descriptor(), bindings())
