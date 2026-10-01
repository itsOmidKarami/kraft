"""The `policy` draft area (W13 F): `policy.yaml`."""

from __future__ import annotations

from kraft.drafts import config, store

NAME = "policy"
FILES = ("policy.yaml",)
#: Op name -> `fn(draft, **fields)`; W13 F fills it in.
OPS: dict = {}


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


def resolve(st, key, raw, files, published) -> dict:
    return config.resolve_files(st, files, published, FILES)


async def after_publish(app, written) -> None:
    config.reload_policy(app.state)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
