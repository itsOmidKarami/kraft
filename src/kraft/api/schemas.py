"""Response shapes the OpenAPI schema documents for the board's routes.

These only describe: a route lists one under `responses={200: {"model": ...}}`,
never `response_model=`, which would make FastAPI filter and validate every
answer and could silently drop a field a client reads. So each model allows
extra fields and names only the stable ones, and `tests/api/test_openapi.py`
validates a real answer against it so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Shape(BaseModel):
    model_config = ConfigDict(extra="allow")


class OneOffSummary(_Shape):
    """What a one-off chain item's own chain adds to the chain it extends."""

    extends: str | None = None
    custom: int | None = None
    needs_review: bool | None = None
    approval: dict[str, Any] | None = None


class MrRef(_Shape):
    number: int | str | None = None
    url: str | None = None


class Stop(_Shape):
    """Why a `needs_human`, `waiting` or `rate_limited` item stopped."""

    kind: str | None = None
    node: str | None = None
    reason: str | None = None


class WorkItemRow(_Shape):
    """The keys the documented list row and detail object share. A few of the
    board's own keys (`children`, `slices`, `in_place` and others) are left out."""

    id: str
    title: str
    description: str | None = None
    repo: str
    status: str
    display_status: str
    chain_template: str | None = None
    chain_definition: dict[str, Any] | None = None
    current_node_id: str | None = None
    bead_id: str | None = None
    created_at: str
    updated_at: str
    retry_at: str | None = None
    archived_at: str | None = None
    archived_by: str | None = None
    attachments: list[dict[str, Any]] = []
    pending_gate: str | None = None
    one_off: OneOffSummary | None = None
    stop: Stop | None = None
    step: dict[str, Any] | None = None
    mr_ref: MrRef | None = None
    actions: list[str] = []
    worktree_exists: bool | None = None


class WorkItemList(_Shape):
    """`GET /api/work-items`: the board's items, and the event cursor to resume from."""

    items: list[WorkItemRow]
    cursor: int


class WorkItemDetail(WorkItemRow):
    """`GET /api/work-items/{id}`: the stored row and the fields computed on read."""

    branch: str | None = None
    base_ref: str | None = None
    stop_kind: str | None = None
    stop_reason: str | None = None
    effective_chain: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    worktree_path: str | None = None
    head_sha: str | None = None
    review_head: str | None = None
    reject_default: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    dependencies: list[dict[str, Any]] = []
    suggested_action: dict[str, Any] | None = None
    ended_reason: str | None = None
    needs_context_question: str | None = None
    steerable: bool | None = None
    budget_cap: dict[str, Any] | None = None
    running_time: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    worker_sessions: list[dict[str, Any]] = []
