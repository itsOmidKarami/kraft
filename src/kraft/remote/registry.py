"""`workers.yaml`: the machines this server may run steps on. One entry per
machine, exactly one connection mode each (`ssh` or `token_sha256`). Read by
the hub, edited by `kraft admin worker`, never written with a secret in it:
a dial-in token is shown once and kept as its SHA-256."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kraft.config import ConfigError, RepoEntry, first_error, read_yaml

FILE = "workers.yaml"
TOKEN_PREFIX = "kwt_"
DEFAULT_GIVE_UP = "2h"
#: `local` and `any` are placement values (`runner: any`); a machine named so
#: could never be addressed.
RESERVED = frozenset({"local", "any"})
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,38}")
#: user@host, a host, an ssh config alias, a bracketed IPv6 literal. No leading
#: dash (it would be read as an ssh option), no whitespace or shell characters.
_SSH = re.compile(r"(?!-)[A-Za-z0-9_.@:\[\]%-]+")
_DURATION = re.compile(r"([1-9][0-9]*)([smhd])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SHA = re.compile(r"[0-9a-f]{64}")


def parse_duration(text: str) -> float | None:
    """Seconds in `2h`/`30m`/`90s`/`1d`; None for `never`. ValueError otherwise."""
    if text == "never":
        return None
    found = _DURATION.fullmatch(text)
    if found is None:
        raise ValueError(
            f"give_up_after {text!r} is not a duration like 2h, 30m, 90s, 1d, or never"
        )
    return int(found[1]) * _UNITS[found[2]]


def check_name(name: str) -> str:
    if name in RESERVED:
        raise ValueError(f"worker name {name!r} is reserved (it is a placement value)")
    if _NAME.fullmatch(name) is None:
        raise ValueError(
            f"worker name {name!r} must be lowercase letters, digits, - or _, 1-39 characters"
        )
    return name


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Worker(_Strict):
    ssh: str | None = None
    token_sha256: str | None = None
    capacity: int = Field(default=1, ge=1)
    repos: list[str] | None = None
    give_up_after: str = DEFAULT_GIVE_UP

    @field_validator("ssh")
    @classmethod
    def _ssh_host(cls, value: str | None) -> str | None:
        if value is not None and _SSH.fullmatch(value) is None:
            raise ValueError(f"ssh host {value!r} is not a host or ssh config alias")
        return value

    @field_validator("token_sha256")
    @classmethod
    def _sha(cls, value: str | None) -> str | None:
        if value is not None and _SHA.fullmatch(value) is None:
            raise ValueError("token_sha256 must be 64 lowercase hex characters")
        return value

    @field_validator("give_up_after")
    @classmethod
    def _duration(cls, value: str) -> str:
        parse_duration(value)
        return value

    @model_validator(mode="after")
    def _one_mode(self) -> Worker:
        if (self.ssh is None) == (self.token_sha256 is None):
            raise ValueError("set exactly one of ssh or token_sha256")
        return self

    @property
    def mode(self) -> str:
        return "ssh" if self.ssh is not None else "dial-in"

    @property
    def give_up_s(self) -> float | None:
        return parse_duration(self.give_up_after)


class WorkersFile(_Strict):
    workers: dict[str, Worker] = Field(default_factory=dict)

    @field_validator("workers", mode="before")
    @classmethod
    def _none_is_empty(cls, value):
        return {} if value is None else value

    @field_validator("workers")
    @classmethod
    def _names(cls, value: dict[str, Worker]) -> dict[str, Worker]:
        for name in value:
            check_name(name)
        return value

    @classmethod
    def load(cls, path: str | Path) -> WorkersFile:
        """The file at `path`; a missing one is no machines. Raises `ConfigError`."""
        try:
            return cls.model_validate(read_yaml(path))
        except ValidationError as exc:
            raise ConfigError(first_error(exc, FILE)) from exc

    def to_yaml(self) -> dict:
        return self.model_dump(exclude_none=True)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_by_token(file: WorkersFile, token: str) -> str | None:
    """The dial-in machine `token` belongs to. Compares every entry in constant
    time, so how long a refusal takes says nothing about which hash was close."""
    digest = hash_token(token) if token else ""
    found = None
    for name, worker in file.workers.items():
        if worker.token_sha256 and hmac.compare_digest(worker.token_sha256, digest):
            found = name
    return found


def repo_matches(entry: RepoEntry, name: str) -> bool:
    """A `repos:` entry names a connected repo by its id, its name, or the last
    component of its path **(call)**."""
    return name in {entry.id, entry.name, Path(entry.path).name}
