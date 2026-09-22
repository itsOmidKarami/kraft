"""Which automated reviewer a repository expects (Ruling 171).

Read on a `repos.yaml` entry (`config.RepoEntry`) and by the forge probes
that wait on it; its own module so the forge adapters need not import the
config loader to name the type.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class AutomatedReview(BaseModel):
    """`automated_review:` on a repository: the one reviewer whose verdict
    `mr.automated_review` waits for, named exactly one way.

    * `bot` -- a forge login. The review settles when that login has reviewed
      the merge request's current head.
    * `check` -- a check or commit status on the head. It settles when that
      check completes.

    Absent, the repository expects no automated reviewer, and the wait settles
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
