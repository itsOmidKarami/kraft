"""Which automated reviewers a merge request waits for (Ruling 171).

Named on a `repos.yaml` entry (`config.RepoEntry`), in the review task's
`config.reviewers` (read as `ForgeTask.reviewers`) or by an item's node
override, and read by the forge probes that wait on them; its own module so the
forge adapters need not import the config loader to name the type.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    StrictStr,
    Tag,
    TypeAdapter,
    model_serializer,
    model_validator,
)


class _Reviewer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _null_other_name(cls, data: object) -> object:
        # Every list stored before the union holds the other name as null.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (k in ("bot", "check") and v is None)}
        return data


class BotReviewer(_Reviewer):
    """A forge login. The review settles when that login has reviewed the head."""

    bot: StrictStr = Field(min_length=1)
    #: Kraft replies in this bot's threads and posts its summary comment
    #: (`executor.replies`).
    replies: StrictBool = False
    #: Kraft resolves a thread only when it is a confirmed fix, at node completion
    #: (`executor.replies`). Needs `replies`.
    resolve_fixed: StrictBool = False
    check: ClassVar[None] = None

    @model_validator(mode="after")
    def _resolve_needs_replies(self) -> BotReviewer:
        if self.resolve_fixed and not self.replies:
            raise ValueError("`resolve_fixed` needs `replies: true` on the same entry")
        return self

    @model_serializer(mode="wrap")
    def _quiet_default(self, handler):
        # Stored snapshots and saved templates keep writing `{bot: x}`. No return
        # annotation: one would replace the serialization schema with a bare object.
        data = handler(self)
        if isinstance(data, dict):
            for key in ("replies", "resolve_fixed"):
                if not data.get(key):
                    data.pop(key, None)
        return data


class CheckReviewer(_Reviewer):
    """A check or commit status on the head. It settles when it completes."""

    check: StrictStr = Field(min_length=1)
    bot: ClassVar[None] = None
    replies: ClassVar[bool] = False
    resolve_fixed: ClassVar[bool] = False


def _which(value: object) -> str | None:
    if isinstance(value, _Reviewer):
        return "bot" if isinstance(value, BotReviewer) else "check"
    if not isinstance(value, dict):
        return None
    named = [k for k in ("bot", "check") if value.get(k) is not None]
    return named[0] if len(named) == 1 else None


# One reviewer whose verdict `mr.automated_review` waits for, named exactly
# one way, and its kind is its shape: a key is checked against that kind alone.
#
# * `bot` -- a forge login. The review settles when that login has reviewed
#   the merge request's current head.
# * `check` -- a check or commit status on the head. It settles when that
#   check completes.
#
# None named at any layer, no automated reviewer is expected, and the wait
# settles clean at once (`automated_review_not_configured`). How either is
# read is the forge backend's (`automated-review-implementation-is-not-
# template-configuration`): this names *which* reviewer, never how to ask it.
Reviewer = Annotated[
    Annotated[BotReviewer, Tag("bot")] | Annotated[CheckReviewer, Tag("check")],
    Discriminator(
        _which,
        custom_error_type="reviewer",
        custom_error_message="automated_review names exactly one of `bot` or `check`",
    ),
]

#: Every reviewer one `mr.automated_review` wait reads. Empty: none is
#: expected, and the wait settles clean as not configured.
Reviewers = tuple[BotReviewer | CheckReviewer, ...]
_ADAPTER = TypeAdapter(Reviewer)


def as_reviewers(
    value: BotReviewer | CheckReviewer | Sequence[BotReviewer | CheckReviewer | dict] | None,
) -> Reviewers:
    """One layer's reviewers as the tuple the forge reads. `repos.yaml` names
    one or a list; a task names a list; a node override, stored as JSON, a
    list of mappings."""
    if value is None:
        return ()
    if isinstance(value, _Reviewer):
        return (value,)
    return tuple(r if isinstance(r, _Reviewer) else _ADAPTER.validate_python(r) for r in value)


#: A `repos.yaml` entry's `automated_review:`: one reviewer, as written before
#: lists, or a list. Tagged so a bad entry in a list is reported as itself, not
#: as the union failing to match.
OneOrManyReviewers = Annotated[
    Annotated[Reviewer, Tag("one")] | Annotated[list[Reviewer], Tag("many")],
    Discriminator(lambda value: "many" if isinstance(value, list) else "one"),
]
