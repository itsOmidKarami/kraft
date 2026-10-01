"""The `repos` draft area (W13 E): `repos.yaml`."""

from __future__ import annotations

from kraft.drafts import config, store

NAME = "repos"
FILES = ("repos.yaml",)
#: Op name -> `fn(draft, **fields)`; W13 E fills it in.
OPS: dict = {}


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


def resolve(st, key, raw, files, published) -> dict:
    return config.resolve_files(st, files, published, FILES)


async def after_publish(app, written) -> None:
    """`repos.yaml` is read from disk per use: nothing to reload."""


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
