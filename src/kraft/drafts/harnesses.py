"""The `harnesses` draft area (W13 D): `harnesses.yaml` and the access lists of `policy.yaml`."""

from __future__ import annotations

import yaml

from kraft.api import config_check
from kraft.drafts import config, store
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateIssue

NAME = "harnesses"
FILES = ("harnesses.yaml", "policy.yaml")
#: Op name -> `fn(draft, **fields)`; W13 D fills it in.
OPS: dict = {}


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


def _table_issues(path, text, ctx) -> list[TemplateIssue]:
    """`harnesses.yaml` as the profile table loads it. `config_check`'s own check
    also filters out a task that was already broken (`harness_breakage`); a draft
    shows every problem, so launch problems come from the draft table whole."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        return [TemplateIssue(path, None, config_check.NOT_A_MAPPING)]
    try:
        HarnessProfileTable.from_mapping(data, path, harnesses=ctx.providers)
    except TemplateEnvironmentError as exc:
        return [TemplateIssue(path, None, str(exc))]
    return []


def resolve(st, key, raw, files, published) -> dict:
    return config.resolve_files(st, files, published, FILES, {"harnesses.yaml": _table_issues})


async def after_publish(app, written) -> None:
    # The profile table is read from disk per use; only the policy is loaded.
    if "policy.yaml" in written:
        config.reload_policy(app.state)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
