"""Which automated reviewers a merge request waits for (Ruling 171).

Named on a `repos.yaml` entry (`config.RepoEntry`), on the review task
(`ForgeTask.reviewers`) or by an item's node override, and read by the forge
probes that wait on them; its own module so the forge adapters need not import the
config loader to name the type.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictStr,
    Tag,
    model_validator,
)


class AutomatedReview(BaseModel):
    """One reviewer whose verdict `mr.automated_review` waits for, named
    exactly one way.

    * `bot` -- a forge login. The review settles when that login has reviewed
      the merge request's current head.
    * `check` -- a check or commit status on the head. It settles when that
      check completes.

    None named at any layer, no automated reviewer is expected, and the wait settles
    clean at once (`automated_review_not_configured`). How either is read is the
    forge backend's (`automated-review-implementation-is-not-template-
    configuration`): this names *which* reviewer, never how to ask it.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    bot: StrictStr | None = Field(default=None, min_length=1)
    check: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.bot is None) == (self.check is None):
            raise ValueError("automated_review names exactly one of `bot` or `check`")
        return self


#: Every reviewer one `mr.automated_review` wait reads. Empty: none is
#: expected, and the wait settles clean as not configured.
Reviewers = tuple[AutomatedReview, ...]


def as_reviewers(value: AutomatedReview | Sequence[AutomatedReview | dict] | None) -> Reviewers:
    """One layer's reviewers as the tuple the forge reads. `repos.yaml` names
    one or a list; a task names a list; a node override, stored as JSON, a
    list of mappings."""
    if value is None:
        return ()
    if isinstance(value, AutomatedReview):
        return (value,)
    return tuple(r if isinstance(r, AutomatedReview) else AutomatedReview(**r) for r in value)


#: A `repos.yaml` entry's `automated_review:`: one reviewer, as written before
#: lists, or a list. Tagged so a bad entry in a list is reported as itself, not
#: as the union failing to match.
OneOrManyReviewers = Annotated[
    Annotated[AutomatedReview, Tag("one")] | Annotated[list[AutomatedReview], Tag("many")],
    Discriminator(lambda value: "many" if isinstance(value, list) else "one"),
]
