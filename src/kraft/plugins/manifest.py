"""A collection's and a plugin's manifests, and the fixed layout a plugin's
components sit in beside its `.kraft/` folder.

Both manifests are JSON an author wrote. A top-level key Kraft does not read is
ignored, so a manifest written for Claude Code or for a newer Kraft still
loads; `unknown_keys` lists them for `validate` to warn about. An unknown key
inside `requires` is refused: a requirement this Kraft cannot check must not
pass as met.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kraft import update
from kraft.config import bounded_yaml, first_error
from kraft.plugins.config import _SEMVER, RESERVED_NAMESPACE, Name, source_problem

COLLECTION_JSON = ".kraft/collection.json"
PLUGIN_JSON = ".kraft/plugin.json"

_NAME = r"[a-z][a-z0-9_-]*"
#: The only files of a plugin directory Kraft extracts, hashes and reviews.
_LAYOUT = re.compile(
    rf"^(library\.yaml|profiles\.yaml|\.kraft/plugin\.json"
    rf"|chains/{_NAME}\.yaml|skills/{_NAME}/SKILL\.md)$"
)
#: A path that could stand in for part of the layout if it were followed: a
#: symlink or a submodule here is refused, not skipped.
_SHADOWS = re.compile(r"^(chains|skills|\.kraft|skills/[^/]+)$")
#: A file an author most likely meant as part of the layout.
_NEAR = re.compile(
    r"^(library|profiles)\.ya?ml$|^chains/[^/]+\.ya?ml$|^skills/[^/]+/skill\.md$", re.IGNORECASE
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\Z")
_HTTPS = re.compile(r"^https://[^\s/]+\S*\Z")
_REQUIRES_KRAFT = r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?$"


class ManifestError(Exception):
    pass


def _refuse(message: str, **context: object) -> PydanticCustomError:
    return PydanticCustomError("plugins", message, context)


def in_layout(rel: str) -> bool:
    return _LAYOUT.match(rel) is not None


def shadows_layout(rel: str) -> bool:
    return in_layout(rel) or _SHADOWS.match(rel) is not None


def near_layout(rel: str) -> bool:
    return not in_layout(rel) and _NEAR.match(rel) is not None


def hidden_character(text: str) -> str | None:
    """The first character in `text` an operator reading a review would not
    see while a model still reads it, named; or None. Control characters other
    than newline and tab, and every format character (bidi controls,
    zero-width characters, Unicode tags, a byte-order mark)."""
    for ch in text:
        if ch in "\n\t":
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return f"U+{ord(ch):04X} ({unicodedata.name(ch, 'a control character')})"
    return None


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def parse(text: str, where: str) -> dict:
    """A manifest's JSON as a mapping. A string that hides text is refused
    here too: a JSON escape puts one in a file whose bytes are plain ASCII."""
    try:
        data = json.loads(text)
        strings = list(_strings(data))
    except ValueError as exc:
        raise ManifestError(f"{where}: is not JSON: {exc}") from exc
    except RecursionError:
        raise ManifestError(f"{where}: is nested deeper than a manifest goes") from None
    if not isinstance(data, dict):
        raise ManifestError(f"{where}: expected a JSON object")
    for string in strings:
        if (bad := hidden_character(string)) is not None:
            raise ManifestError(f"{where}: a string carries {bad}, which a reader cannot see")
    return data


class Contact(BaseModel):
    """`owner` of a collection, `author` of a plugin."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    name: Annotated[StrictStr, Field(min_length=1)]
    email: StrictStr
    url: StrictStr | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, email: str) -> str:
        if not _EMAIL.match(email):
            raise _refuse("must be an address like platform@acme.dev")
        return email

    @field_validator("url")
    @classmethod
    def _url(cls, url: str | None) -> str | None:
        if url is not None and not _HTTPS.match(url):
            raise _refuse("must be an absolute https:// URL")
        return url


class CollectionEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    name: Name
    source: StrictStr
    description: StrictStr | None = None

    @field_validator("source")
    @classmethod
    def _source(cls, source: str) -> str:
        if problem := source_problem(source):
            raise _refuse("{problem}; a plugin lives in the collection's own repo", problem=problem)
        return source


class CollectionManifest(BaseModel):
    """`.kraft/collection.json`, at the collection's root."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    name: Name
    description: StrictStr | None = None
    owner: Contact
    plugins: Annotated[list[CollectionEntry], Field(min_length=1)]

    @model_validator(mode="after")
    def _entries(self) -> CollectionManifest:
        names = [e.name for e in self.plugins]
        for name in names:
            if names.count(name) > 1:
                raise _refuse("plugins: {name} is listed twice", name=name)
        sources = [e.source for e in self.plugins]
        for a in sources:
            for b in sources:
                if a != b and b.startswith(a + "/"):
                    raise _refuse("plugins: {b} is inside {a}", a=a, b=b)
        return self


class Requires(BaseModel):
    """What a plugin needs from the instance."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kraft: Annotated[StrictStr, Field(pattern=_REQUIRES_KRAFT)]
    harnesses: list[Name] = []
    profiles: list[Name] = []

    @model_validator(mode="before")
    @classmethod
    def _understood(cls, data: object) -> object:
        if isinstance(data, dict):
            for key in data:
                if key not in cls.model_fields:
                    raise _refuse(
                        "requires.{key} is not understood by this Kraft; upgrade Kraft", key=key
                    )
        return data

    @field_validator("harnesses", "profiles")
    @classmethod
    def _unique(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names):
            raise _refuse("lists a name twice")
        return names


class PluginManifest(BaseModel):
    """`.kraft/plugin.json`, in the plugin's directory."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    name: Name
    # Capped: a version is compared as numbers, and one of thousands of digits is not one.
    version: Annotated[StrictStr, Field(pattern=_SEMVER, max_length=64)]
    description: StrictStr | None = None
    author: Contact | None = None
    homepage: StrictStr | None = None
    license: StrictStr | None = None
    requires: Requires

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        if name == RESERVED_NAMESPACE:
            raise _refuse("'kraft' is Kraft's own namespace")
        return name

    @field_validator("homepage")
    @classmethod
    def _homepage(cls, url: str | None) -> str | None:
        if url is not None and not _HTTPS.match(url):
            raise _refuse("must be an absolute https:// URL")
        return url


def _build(model, data: dict, where: str):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(first_error(exc, where)) from exc


def collection(data: dict, where: str) -> CollectionManifest:
    return _build(CollectionManifest, data, where)


def plugin(data: dict, where: str) -> PluginManifest:
    return _build(PluginManifest, data, where)


def unknown_keys(data: Mapping[str, object], model: type[BaseModel]) -> list[str]:
    """The top-level keys of `data` that `model` does not read, sorted."""
    return sorted(k for k in data if k not in model.model_fields and k != "$schema")


def check_plugin(
    found: PluginManifest, entry: str | None, files: Mapping[str, tuple[str, bytes]]
) -> None:
    """What a plugin's manifest and its extracted files must agree on. `entry`
    is its name in `collection.json`; None for a plugin directory on its own."""
    if entry is not None and found.name != entry:
        raise ManifestError(
            f"{PLUGIN_JSON}: name {found.name!r} differs from its collection entry {entry!r}"
        )
    if not any(rel != PLUGIN_JSON for rel in files):
        raise ManifestError(
            f"plugin {found.name!r} holds none of library.yaml, chains/, skills/ or "
            "profiles.yaml; it would add nothing"
        )
    profiles = files.get("profiles.yaml")
    if profiles is not None:
        try:
            data = bounded_yaml(profiles[1].decode(), [20_000])
        except (ValueError, yaml.YAMLError) as exc:
            raise ManifestError(f"profiles.yaml: cannot parse: {exc}") from exc
        if isinstance(data, dict) and "harnesses" in data:
            raise ManifestError(
                "profiles.yaml: 'harnesses' is refused: a plugin never chooses an executable"
            )


def is_source_build(running: str) -> bool:
    """A checkout's version (`0.1.dev50+g…`, `0.0.0+source`) is no release to
    compare against; the caller skips the check and says so."""
    return update._VERSION.fullmatch(running) is None


def kraft_compatible(requires: str, running: str) -> str | None:
    """Why the Kraft at version `running` cannot run a plugin whose
    `requires.kraft` is `requires`, or None. `"2.4"` is at least 2.4.0 and
    below 3.0.0; `"2"` is any 2.x. Only the release part of `running` counts,
    so a release candidate of 2.4.0 satisfies `"2.4"`."""
    found = update._VERSION.fullmatch(running)
    if found is None:
        return None
    want_major, _, want_minor = requires.partition(".")
    if int(found[1]) != int(want_major):
        return f"needs Kraft {want_major}.x"
    if int(found[2]) < int(want_minor or 0):
        return f"needs Kraft {requires} or later"
    return None
