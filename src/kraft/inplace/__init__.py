"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.

Split by job: `base` (where the work happens, refusals, the checkout's lock
and git reads), `taking` (the take and its switch), `task_check` (the mark
before an agent task and the check after it), `rebasing` (the rebase, in the
checkout or a throwaway worktree, and its autostash), `copy` (the throwaway
shared clone a read-only agent works in instead of the checkout). Every name
callers use is read from here.
"""

from __future__ import annotations

from kraft.inplace.base import AutostashClash as AutostashClash
from kraft.inplace.base import CheckoutRefused as CheckoutRefused
from kraft.inplace.base import NotRunning as NotRunning
from kraft.inplace.base import _lock as _lock
from kraft.inplace.base import _on as _on
from kraft.inplace.base import _settled as _settled
from kraft.inplace.base import folder_for as folder_for
from kraft.inplace.base import head_ref as head_ref
from kraft.inplace.base import is_in_place as is_in_place
from kraft.inplace.base import item_folder as item_folder
from kraft.inplace.copy import CopyFailed as CopyFailed
from kraft.inplace.copy import copy_path as copy_path
from kraft.inplace.copy import drop_dead_copies as drop_dead_copies
from kraft.inplace.copy import make_copy as make_copy
from kraft.inplace.copy import remove_copy as remove_copy
from kraft.inplace.rebasing import _rebase_in_checkout as _rebase_in_checkout
from kraft.inplace.rebasing import drop_stale_scratch as drop_stale_scratch
from kraft.inplace.rebasing import rebase as rebase
from kraft.inplace.rebasing import rebase_in_checkout as rebase_in_checkout
from kraft.inplace.rebasing import refuse_ignored as refuse_ignored
from kraft.inplace.taking import _take as _take
from kraft.inplace.taking import attachments_of as attachments_of
from kraft.inplace.taking import drop_branch as drop_branch
from kraft.inplace.taking import prepare as prepare
from kraft.inplace.taking import take as take
from kraft.inplace.task_check import SAMPLE as SAMPLE
from kraft.inplace.task_check import TaskCheck as TaskCheck
from kraft.inplace.task_check import TaskMark as TaskMark
from kraft.inplace.task_check import _hashes as _hashes
from kraft.inplace.task_check import _put_back as _put_back
from kraft.inplace.task_check import check as check
from kraft.inplace.task_check import check_task as check_task
from kraft.inplace.task_check import leftovers as leftovers
from kraft.inplace.task_check import load_mark as load_mark
from kraft.inplace.task_check import mark as mark
from kraft.inplace.task_check import mark_task as mark_task
from kraft.inplace.task_check import save_leftovers as save_leftovers
from kraft.inplace.task_check import save_mark as save_mark
from kraft.inplace.task_check import still_left as still_left
from kraft.inplace.task_check import stop_refused as stop_refused
from kraft.inplace.task_check import stopped as stopped
