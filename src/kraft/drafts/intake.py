"""The `intake` draft area (W13 G): `intake.yaml`, and the schedules of `policy.yaml`."""

from __future__ import annotations

from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft.drafts import config, store

NAME = "intake"
FILES = ("intake.yaml", "policy.yaml")
#: Op name -> `fn(draft, **fields)`; W13 G fills it in.
OPS: dict = {}


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


def resolve(st, key, raw, files, published) -> dict:
    return config.resolve_files(st, files, published, FILES)


async def after_publish(app, written) -> None:
    """As `PUT /intake` does: the poller is replaced, not restarted."""
    if "policy.yaml" in written:
        config.reload_policy(app.state)
    if "intake.yaml" in written:
        st = app.state
        st.intake = config_mod.Intake.load(st.templates_dir / "intake.yaml").model_dump()
        await intake_mod.restart(app)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
