"""`config/plugins.yaml`, the collections this instance has added and the
plugins it has installed from them, and `config/plugins.lock`, what each
installed plugin resolved to. An operator may edit `plugins.yaml` by hand or
through `kraft admin plugin`; only `kraft admin plugin` writes the lock."""

from __future__ import annotations

import posixpath
import re
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    StrictStr,
    Tag,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kraft.config import _Model
from kraft.templates.environment import Identifier, branch_name_problem

#: A collection name, plugin name or alias: an `Identifier` short enough to be
#: a directory name under `run/plugins/`.
Name = Annotated[Identifier, Field(max_length=64)]
#: `<plugin>@<collection>`.
PluginId = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}@[a-z][a-z0-9_-]{0,63}$")]
#: A URL git clones: https without credentials, ssh, file, or scp-like
#: `user@host:path`. Never the GitHub shorthand: `collection add owner/repo`
#: writes https://github.com/owner/repo.git. No form may start with `-`.
_HOST = r"[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?"
_USER = r"[A-Za-z0-9_][A-Za-z0-9._-]*@"
_GIT_URL = re.compile(
    rf"^(https://{_HOST}(/[^\s\x00-\x1f]*)?"
    rf"|ssh://({_USER})?{_HOST}/[^\s\x00-\x1f]*"
    r"|file:///[^\s\x00-\x1f]+"
    rf"|{_USER}[A-Za-z0-9][A-Za-z0-9.-]*:[^\s\x00-\x1f-][^\s\x00-\x1f]*)$"
)
#: A plugin's directory in its collection, as `collection.json` writes it.
_SOURCE = re.compile(r"^\./[^/\x00-\x1f\\]+(/[^/\x00-\x1f\\]+)*$")
_SEMVER = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
_OID = r"^[0-9a-f]{40}([0-9a-f]{24})?$"  # a SHA-1 or SHA-256 object name
#: Reserved for Kraft's own components: no plugin name or alias may be it.
RESERVED_NAMESPACE = "kraft"


def _refuse(message: str) -> PydanticCustomError:
    return PydanticCustomError("plugins", message)


def source_problem(source: str) -> str | None:
    """Why `source` is not a plugin directory inside its collection, or None.
    Segment checks in Python, not a negative regex, so `.KRAFT` is caught
    however a filesystem folds case."""
    if not _SOURCE.match(source) or source.endswith("\n"):
        return "must be ./ followed by a relative path, with no empty segment or trailing /"
    parts = source[2:].split("/")
    if any(p in (".", "..") for p in parts):
        return "may not have a . or .. segment"
    if parts[0].casefold() == ".kraft":
        return "may not be inside .kraft/"
    return None


class CollectionConfig(_Model):
    """One `collections:` entry, keyed by the name its `collection.json`
    declares. A git collection is fetched at `ref`; a directory collection is
    read in place, so it has no ref and nothing to auto-update."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        # The validator below, for an editor.
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["git"],
                    "not": {"required": ["path"], "properties": {"path": {"type": "string"}}},
                },
                {
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "pattern": "^/."},
                        "auto_update": {"const": False},
                    },
                    "not": {"required": ["ref"], "properties": {"ref": {"type": "string"}}},
                },
            ]
        },
    )

    git: StrictStr | None = Field(
        default=None,
        description="A URL git clones: https://, ssh://, file:/// or user@host:path.",
        json_schema_extra={"pattern": _GIT_URL.pattern},
    )
    path: StrictStr | None = Field(
        default=None, description="An absolute directory, in normal form."
    )
    ref: StrictStr | None = Field(
        default=None,
        description="A branch, tag or commit of a git collection. "
        "None: the remote's default branch.",
    )
    auto_update: StrictBool = Field(
        default=False,
        description="Update every installed plugin of this collection on server start. "
        "A plugin cannot opt out.",
    )

    @field_validator("git")
    @classmethod
    def _git(cls, git: str | None) -> str | None:
        if git is not None and not _GIT_URL.match(git):
            raise _refuse(
                "git must be a full URL: https://host/path (no credentials), ssh://..., "
                "file:///... or user@host:path; for GitHub's owner/repo write "
                "https://github.com/owner/repo.git"
            )
        return git

    @field_validator("ref", mode="before")
    @classmethod
    def _ref_is_text(cls, ref: object) -> object:
        """YAML reads `ref: 1.10` as the number 1.1."""
        if ref is not None and not isinstance(ref, str):
            raise _refuse(f"ref must be a string; quote it: ref: '{ref}'")
        return ref

    @field_validator("ref")
    @classmethod
    def _ref(cls, ref: str | None) -> str | None:
        if ref is not None and (problem := branch_name_problem(ref)):
            raise _refuse(f"ref {problem}")
        return ref

    @field_validator("path")
    @classmethod
    def _path(cls, path: str | None) -> str | None:
        if path is None:
            return None
        if path.startswith("~"):
            raise _refuse("path must be absolute; ~ is not expanded")
        if not path.startswith("/") or path == "/" or posixpath.normpath(path) != path:
            raise _refuse(
                "path must be an absolute directory in normal form (no trailing /, . or ..)"
            )
        return path

    @model_validator(mode="after")
    def _source(self) -> CollectionConfig:
        if (self.git is None) == (self.path is None):
            raise _refuse("give exactly one of git or path")
        if self.path is not None and self.ref is not None:
            raise _refuse("ref is for a git collection; a directory collection is read as it is")
        if self.path is not None and self.auto_update:
            raise _refuse("a directory collection never auto-updates")
        return self


class PluginEntry(_Model):
    """One installed plugin, in its long form; `true` and `false` are short
    for `{enabled: true}` and `{enabled: false}`."""

    # By alias only: `as_:` in the file is a typo, refused like any other.
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        validate_by_name=False,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    as_: Name | None = Field(
        default=None,
        alias="as",
        description="The namespace its components are referenced under, in place of its name.",
    )
    enabled: StrictBool = True
    auto_update: StrictBool | None = Field(
        default=None,
        description="Only when its collection does not auto-update, and only for a git collection.",
    )


#: `true`/`false` or a map, told apart before validation so an error names the
#: form the operator wrote ("plugins.x@y.map.enabeld") rather than both.
Entry = Annotated[
    Annotated[StrictBool, Tag("true/false")] | Annotated[PluginEntry, Tag("map")],
    Discriminator(lambda v: "true/false" if isinstance(v, bool) else "map"),
]


class PluginsConfig(_Model):
    """`config/plugins.yaml`."""

    FILE: ClassVar[str] = "plugins.yaml"
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # pydantic exports a key pattern as patternProperties, which lets any
    # other key through; closing the object makes the schema refuse it too.
    collections: dict[Name, CollectionConfig] = Field(
        default={}, json_schema_extra={"additionalProperties": False, "type": ["object", "null"]}
    )
    plugins: dict[PluginId, Entry] = Field(
        default={}, json_schema_extra={"additionalProperties": False, "type": ["object", "null"]}
    )

    @field_validator("collections", "plugins", mode="before")
    @classmethod
    def _empty(cls, value: object) -> object:
        """`plugins:` with every entry deleted reads as YAML null."""
        return {} if value is None else value

    def entry(self, plugin_id: str) -> PluginEntry:
        value = self.plugins[plugin_id]
        return PluginEntry(enabled=value) if isinstance(value, bool) else value

    def namespace(self, plugin_id: str) -> str:
        return self.entry(plugin_id).as_ or plugin_id.split("@", 1)[0]

    @model_validator(mode="after")
    def _plugins(self) -> PluginsConfig:
        seen: dict[str, str] = {}
        for plugin_id in self.plugins:
            name, collection_name = plugin_id.split("@", 1)
            collection = self.collections.get(collection_name)
            if collection is None:
                raise _refuse(f"{plugin_id}: no collection {collection_name!r} under collections")
            entry = self.entry(plugin_id)
            if entry.auto_update is not None:
                if collection.path is not None:
                    raise _refuse(f"{plugin_id}: a directory collection never auto-updates")
                if collection.auto_update:
                    raise _refuse(
                        f"{plugin_id}: {collection_name} auto-updates every plugin; "
                        "drop auto_update here"
                    )
            if RESERVED_NAMESPACE in (name, entry.as_):
                raise _refuse(f"{plugin_id}: {RESERVED_NAMESPACE!r} is Kraft's own namespace")
            namespace = self.namespace(plugin_id)
            if namespace in seen:
                raise _refuse(
                    f"{plugin_id}: namespace {namespace!r} is taken by {seen[namespace]}; "
                    "install one under another name with --as"
                )
            seen[namespace] = plugin_id
        return self


class LockEntry(_Model):
    """What one installed plugin resolved to at its last install or update."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    #: The namespace and the collection `ref` it was reviewed under. A
    #: `plugins.yaml` that says otherwise waits for `update` to apply it.
    namespace: Name
    ref: StrictStr | None  # None: a directory collection
    #: The collection's `git:` URL it was fetched from: another repository
    #: under the same name is a change a person reviews. None for a directory.
    git: StrictStr | None = None
    #: The collection's commit, and the tree at `source` in it. Both None for
    #: a directory collection.
    commit: Annotated[StrictStr, Field(pattern=_OID)] | None
    tree: Annotated[StrictStr, Field(pattern=_OID)] | None
    #: The entry's `source` in `collection.json`, as written there.
    source: StrictStr
    digest: Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    version: Annotated[StrictStr, Field(pattern=_SEMVER)]
    # Not strict: written back by `model_dump(mode="json")`, it reads as a string.
    updated_at: Annotated[AwareDatetime, Field(strict=False)]

    @field_validator("source")
    @classmethod
    def _source(cls, source: str) -> str:
        if problem := source_problem(source):
            raise _refuse(f"source {problem}")
        return source

    @model_validator(mode="after")
    def _pinned(self) -> LockEntry:
        if (self.commit is None) != (self.tree is None):
            raise _refuse("commit and tree are both set (git) or both null (directory)")
        return self


class PluginsLock(_Model):
    """`config/plugins.lock`. `lock_version` lets a later Kraft change the
    format and refuse a lock it cannot read with a message, not a field error."""

    FILE: ClassVar[str] = "plugins.lock"
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lock_version: Literal[1] = 1
    plugins: dict[PluginId, LockEntry] = {}
