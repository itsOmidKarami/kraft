"""What the config-file draft areas (`harnesses`, `repos`, `policy`, `intake`)
share: the working copy their ops edit, the problem list (the same `check` the
matching `PUT` route runs, so a draft with no problem is one that route would
accept) and the change list against the published files.

An area module (`kraft.drafts.policy`, ...) adds its op table, its extra
problems and its apply hook; `kraft.drafts.areas` registers it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import yaml

from kraft.api import config_check, deps
from kraft.drafts import authored, preserve, resolve
from kraft.drafts.ops import OpError
from kraft.templates.library import TemplateIssue


class ConfigDraft:
    """One request's working copy of an area's files: each file's mapping as
    written, written back over the file's own text when an op touched it, so
    the comments stay (`preserve.rewrite`)."""

    #: So a publish does not warn that its comments will be dropped.
    drops_comments = False

    def __init__(
        self, st, key: str, files: Mapping[str, str | None], *, exists: bool, names: Sequence[str]
    ) -> None:
        self.st = st
        self.key = key
        self.files = dict(files)
        #: Whether the key had a file or a draft before this request.
        self.exists = exists
        self.dirty: set[str] = set()
        self._maps = {n: self._mapping(n, self.files.get(n)) for n in names}

    @staticmethod
    def _mapping(name: str, text: str | None) -> dict:
        data = authored.parse(text)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise OpError(f"{name}: {config_check.NOT_A_MAPPING}")
        return data

    def file(self, name: str) -> dict:
        """The file's mapping, for writing: every op that reaches it marks it dirty."""
        self.dirty.add(name)
        return self._maps[name]

    def read(self, name: str) -> dict:
        """The file's mapping, for reading."""
        return self._maps[name]

    def finish(self) -> dict[str, str | None]:
        """The files, each dirty one rewritten over its own text so the
        comments and layout an op did not touch survive (`preserve.rewrite`)."""
        for name in self.dirty:
            self.files[name] = preserve.rewrite(self.files.get(name), self._maps[name])
        return self.files


def _issue_problems(st, issues: Sequence[TemplateIssue], buffers) -> list[dict]:
    return [
        p
        for issue in issues
        for p in resolve._problems(issue, lambda _f, loc: (None, loc), st, buffers)
    ]


def problems(
    st,
    files: Mapping[str, str | None],
    names: Sequence[str],
    checks: Mapping[str, Callable] | None = None,
) -> list[dict]:
    """`config_check.check` of each of `names` in `files` that parses (a YAML
    error is the result's `yaml_error`); `checks[name](path, text, ctx)`
    replaces it for a file that needs another check."""
    ctx = config_check.context(st)
    buffers = {st.templates_dir / n: t or "" for n, t in files.items()}
    out = []
    for name in names:
        text = files.get(name)
        if text is None:
            continue
        try:
            authored.parse(text)
        except yaml.YAMLError:
            continue
        if checks and name in checks:
            issues = checks[name](st.templates_dir / name, text, ctx)
        else:
            issues = config_check.check(name, text, ctx)
        out += _issue_problems(st, issues, buffers)
    return out


def file_changes(
    files: Mapping[str, str | None], published: Mapping[str, str | None]
) -> list[dict]:
    """One change per file that parses to something other than its published
    text: `fields` are the top-level keys that differ."""

    def load(text):
        try:
            data = authored.parse(text)
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    out = []
    for name in sorted(files):
        before, after = load(published.get(name)), load(files[name])
        if before == after and (files[name] is None) == (published.get(name) is None):
            continue
        kind = (
            "add" if published.get(name) is None else "remove" if files[name] is None else "change"
        )
        fields = sorted(k for k in {*before, *after} if before.get(k) != after.get(k))
        out.append({"path": name, "kind": kind, "summary": ", ".join(fields), "fields": fields})
    return out


def _leaves(value: object, prefix: str = "") -> dict[str, object]:
    """Dotted key → value. A mapping descends; a list of mappings (`triggers:`)
    descends by index; anything else, a list of scalars too, is one leaf."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in value.items():
            out |= _leaves(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        out = {}
        for i, v in enumerate(value):
            out |= _leaves(v, f"{prefix}.{i}")
        return out
    return {prefix: value}


def _shown(path: str, value: object) -> str:
    if value is None:
        return "no bound" if path.startswith("maxima.") else "not set"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return str(value)


def key_changes(files: Mapping[str, str | None], published: Mapping[str, str | None]) -> list[dict]:
    """One change per dotted key that differs, `{path, kind, summary, file}`,
    `summary` reading "was → now" (a missing value is "not set", a missing
    maximum "no bound"). A file that does not parse, or is new or removed, is
    `file_changes`' one file-level row, so a draft in error still says so."""
    out = []
    for name in sorted(files):
        draft, was = files[name], published.get(name)
        try:
            after, before = authored.parse(draft), authored.parse(was)
            whole = draft is None and was is not None
        except yaml.YAMLError:
            whole = True
        if whole:
            out += [{**c, "file": name} for c in file_changes({name: draft}, {name: was})]
            continue
        gone, now = _leaves(before or {}), _leaves(after or {})
        for path in sorted(gone.keys() | now.keys()):
            if path in gone and path in now and gone[path] == now[path]:
                continue
            kind = "add" if path not in gone else "remove" if path not in now else "change"
            out.append(
                {
                    "path": path,
                    "kind": kind,
                    "summary": f"{_shown(path, gone.get(path))} → {_shown(path, now.get(path))}",
                    "file": name,
                }
            )
    return out


def resolve_files(st, files, published, names, checks=None, *, keyed: bool = False) -> dict:
    """The result keys every config-file area answers with: its files'
    problems and their changes. An area adds `resolved`, `impact` and more."""
    return {
        "problems": problems(st, files, names, checks),
        "changes": key_changes(files, published) if keyed else file_changes(files, published),
        "impact": None,
    }


def reload_policy(st) -> None:
    """Apply a written `policy.yaml` as `PUT /policy` does. A file the draft's
    own check passed is not refused; one that is fails the publish."""
    if why := deps.reload_policy(st):
        raise RuntimeError(f"policy.yaml was written but not applied: {why}")
    deps.lint_loaded(st)
