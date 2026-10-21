"""Kraft Lite's only code: chain state records, and environment detection.

Stdlib only, on purpose. This file ships inside a plugin directory that may be
dropped into a repo which has never installed Kraft, or Python packages at all
beyond the interpreter. Anything needing a dependency belongs in the skills as
prose, or in a dev-time script that is not shipped.

Records are `bd export` shaped, so the fallback file and a bd database hold the
same thing and `bd import` is the whole migration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

#: bd stores a dependency as an object, not an id string. The fallback file uses
#: the same shape on purpose: the promise that `bd import .kraft-lite/chain.jsonl`
#: is the whole migration is only true if the file is genuinely importable.
DEP_TYPE = "blocks"

CHAIN_LABEL = "kraft-chain:"
NODE_LABEL = "kraft-node:"
GATE_LABEL = "kraft-gate:"
ATTEMPT_LABEL = "kraft-attempt:"
#: Attempts this node has ever cost, as against the budget it has left. `rewind`
#: clears the budget on purpose, and the retries it clears are exactly the ones
#: that made the run expensive — a summary reading the budget reports a chain
#: rewound twice as having cost nothing.
SPENT_LABEL = "kraft-spent:"
#: When a node was blocked at its gate, cleared once the wait it opened has been
#: banked. The approve overwrites the node's own `updated_at`, so without a stamp
#: of its own the wait is unrecoverable — and in a gated chain the wait is usually
#: the largest number in the run.
BLOCKED_LABEL = "kraft-blocked-at:"
#: Seconds this node has spent held at a gate, over every round of them.
WAITED_LABEL = "kraft-waited:"
#: When this record was last written, by the wall clock. `updated_at` cannot serve:
#: it is bd's import guard, and `_bump_updated` pushes it forward whenever two
#: writes land inside the same second, so a run timed with it reports seconds that
#: never passed. Every write carries this, so both clocks in a summary are one.
AT_LABEL = "kraft-at:"

#: A feature split into slices is more records in the same store, none of them
#: part of the feature chain. Their ids hold no `.`, which is what keeps them out
#: of `chain_index`: it groups a record under the chain named by the id's prefix
#: before the first `.`, so a slice id with one would be read as a node.
SLICE_OF_LABEL = "kraft-slice-of:"
SLICE_KEY_LABEL = "kraft-slice-key:"
#: The slice's 0-based position in the plan. `bd export` promises no order, so the
#: plan's order has to travel with the record.
SLICE_POS_LABEL = "kraft-slice-pos:"
SLICE_STATUS_LABEL = "kraft-slice-status:"
#: The plan's task numbers this slice implements, joined with commas.
TASKS_LABEL = "kraft-tasks:"
SESSION_LABEL = "kraft-session:"
BRANCH_LABEL = "kraft-branch:"
FORK_LABEL = "kraft-fork:"
#: Marks the run record, valued with the feature chain id it belongs to.
RUN_LABEL = "kraft-slices-run:"
MODE_LABEL = "kraft-slices-mode:"
FEATURE_BRANCH_LABEL = "kraft-feature-branch:"
STATE_BRANCH_LABEL = "kraft-state-branch:"

SLICE_STATUSES = ("waiting", "starting", "running", "in_review", "ready_to_merge", "merged")
MODES = ("feature", "main")

#: What a node of the feature chain means once the run is sliced, by mode. The
#: chain's own node list is untouched; `state` reports the action beside it so
#: the `next` skill knows whether to run the node's hooks or do something else.
#: A node absent from the table runs its hooks in both modes.
SLICED_ACTIONS: dict[str, dict[str, str]] = {
    "implementation": {"feature": "run_slices", "main": "run_slices"},
    "verify": {"feature": "scoped_verify", "main": "skip"},
    "open_mr": {"feature": "hooks", "main": "skip"},
    "mr_checks": {"feature": "hooks", "main": "skip"},
    "human_review": {"feature": "hooks", "main": "await_user_merges"},
    "merge": {"feature": "hooks", "main": "await_user_merges"},
    "post_merge_watch": {"feature": "hooks", "main": "hooks"},
}

#: Who decides what, unless `.kraft-lite/slices.json` says otherwise.
DEFAULT_SETTINGS = {
    "mode": "feature",
    "rights": {
        "plan_question": "controller",
        "slice_review": "controller",
        "merge_slice": "controller",
        "merge_main": "user",
        "push_outside_branch": "user",
    },
}
RIGHT_HOLDERS = ("controller", "user")


def dep_ids(record: dict) -> list[str]:
    """The ids a record waits on. bd accepts `depends_on_id` or `to`."""
    out = []
    for dep in record.get("dependencies", []):
        target = dep.get("depends_on_id") or dep.get("to")
        if target:
            out.append(target)
    return out


def _dep(issue_id: str, depends_on: str) -> dict:
    return {"issue_id": issue_id, "depends_on_id": depends_on, "type": DEP_TYPE}


def new_chain_id() -> str:
    return f"kl-{uuid.uuid4().hex[:8]}"


def materialize(chain: dict, title: str, chain_id: str) -> list[dict]:
    """The epic, then one record per node, linked so each waits on the last."""
    epic = {
        "_type": "issue",
        "id": chain_id,
        "title": title,
        "status": "open",
        "description": f"Kraft Lite chain run of template {chain['id']!r}.",
        "labels": [f"{CHAIN_LABEL}{chain['id']}"],
        "dependencies": [],
    }
    records = [epic]
    previous = chain_id
    for node in chain["nodes"]:
        node_id = f"{chain_id}.{node['id']}"
        records.append(
            {
                "_type": "issue",
                "id": node_id,
                "title": node["id"],
                "status": "open",
                "description": "hooks: " + ", ".join(node["tasks"]),
                "labels": [f"{NODE_LABEL}{node['id']}"],
                "dependencies": [_dep(node_id, previous)],
            }
        )
        previous = node_id
    return records


def label_value(record: dict | None, prefix: str) -> str | None:
    if record is None:
        return None
    for label in record.get("labels", []):
        if label.startswith(prefix):
            return label[len(prefix) :]
    return None


def set_label(record: dict, prefix: str, value: str) -> dict:
    """Return a copy with exactly one label under `prefix`."""
    labels = [x for x in record.get("labels", []) if not x.startswith(prefix)]
    return dict(record, labels=[*labels, f"{prefix}{value}"])


def attempts(record: dict) -> int:
    return int(label_value(record, ATTEMPT_LABEL) or 0)


def spent(record: dict) -> int:
    """Attempts over the node's whole life. Falls back to the budget for chains
    started before this label existed, which is the same number until a rewind."""
    value = label_value(record, SPENT_LABEL)
    return int(value) if value is not None else attempts(record)


def waited(record: dict) -> int:
    return int(label_value(record, WAITED_LABEL) or 0)


def bank_wait(record: dict) -> dict:
    """Close an open gate wait into the node's running total.

    Banked at the moment the wait ends rather than left as two stamps to subtract
    later: the end of the wait is a write, and a write's `updated_at` is pushed
    forward when it follows another inside the same second. Subtracting a wall
    clock start from a pushed end charges the human every second of that push.
    """
    started = label_value(record, BLOCKED_LABEL)
    if started is None:
        return record
    total = waited(record) + max(0, _seconds(started, _now().strftime(BD_TIME)) or 0)
    record = set_label(record, WAITED_LABEL, str(total))
    return dict(record, labels=[x for x in record["labels"] if not x.startswith(BLOCKED_LABEL)])


def open_gate(record: dict, name: str) -> dict:
    """Block a node at a gate, banking any round it is already serving.

    A `gate` run against a node that is already blocked — a re-run, a chain
    steered by hand — would otherwise overwrite the open stamp, and every hour
    waited under it is gone.
    """
    record = bank_wait(set_label(record, GATE_LABEL, name))
    return set_label(record, BLOCKED_LABEL, _now().strftime(BD_TIME))


def spend_attempt(record: dict) -> dict:
    """Charge one attempt to both the node's budget and its lifetime total."""
    # Both read off the record as it came in: `spent` falls back to the budget,
    # so charging the budget first makes the very first attempt count as two.
    charged = spent(record) + 1
    record = set_label(record, ATTEMPT_LABEL, str(attempts(record) + 1))
    return set_label(record, SPENT_LABEL, str(charged))


def ordered(records: list[dict]) -> list[dict]:
    """The chain in walk order, followed through its own dependency links.

    `bd export` returns neither insertion nor sorted order, so reading the store's
    order as the chain's order works on the JSONL file and silently walks the
    wrong node under bd.
    """
    known = {r["id"] for r in records}
    successor: dict[str, dict] = {}
    roots: list[dict] = []
    for record in records:
        waits_on = [d for d in dep_ids(record) if d in known]
        if waits_on:
            successor[waits_on[0]] = record
        else:
            roots.append(record)
    if not roots:
        return list(records)
    # The epic is the head; anything else rootless is not part of this walk.
    head = next((r for r in roots if label_value(r, CHAIN_LABEL)), roots[0])

    out: list[dict] = []
    seen: set[str] = set()
    node: dict | None = head
    while node is not None and node["id"] not in seen:
        seen.add(node["id"])
        out.append(node)
        node = successor.get(node["id"])
    return out


def current(records: list[dict]) -> dict | None:
    """The node to act on: the first not-closed record in walk order. A blocked
    node is returned rather than skipped — a gate stops the walk, and returning
    the node behind it is how the caller learns which gate."""
    for record in ordered(records):
        if record["status"] != "closed":
            return record
    return None


def read_jsonl(path: Path) -> list[dict]:
    """Last write per id wins; first-seen order is preserved."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    merged: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        merged[record["id"]] = record
    return list(merged.values())


def append_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


NOTE_PREFIX = "Rejected: "
STATE_DIR = ".kraft-lite"


def backend(root: Path) -> str:
    """bd only when it is both installed and initialized here. An installed bd
    with no `.beads` in the repo would mean writing this chain into whatever
    database bd resolves from the cwd — not ours to guess."""
    if shutil.which("bd") and (root / ".beads").is_dir():
        return "bd"
    return "jsonl"


class JsonlStore:
    """ponytail: whole-file read and a linear scan, no locking. A chain is a dozen
    records, so there is nothing to index; two sessions racing one chain is the
    real limit, and the fix for that is `bd`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        return read_jsonl(self.path)

    def write(self, records: list[dict]) -> None:
        # Stamped here too, not only in BdStore: `bd import` of this file is the
        # documented migration, and without a timestamp bd keeps the *oldest*
        # row on a tie — importing a chain two gates in rewinds it to `open`.
        append_jsonl(self.path, _prepare(records))

    def write_raw(self, records: list[dict], local: list[dict]) -> None:
        """Records as exported, for `import`, without `_prepare`: `kraft-at:` is
        the truth about when the run that exported them wrote them. Only
        `updated_at` moves, past the line already here (see `_past_local`), or
        `bd import` of this file would keep that older line."""
        append_jsonl(self.path, _past_local(records, local))


#: How bd writes timestamps, and the granularity it compares them at.
BD_TIME = "%Y-%m-%dT%H:%M:%SZ"


def _prepare(records: list[dict]) -> list[dict]:
    """What every write does to every record, whichever store is behind it: bump
    bd's guard, and stamp the wall clock the summary reads back."""
    at = _now().strftime(BD_TIME)
    return [set_label(_bump_updated(r), AT_LABEL, at) for r in records]


def _at(record: dict | None) -> str | None:
    """When this record was last written. Falls back to bd's guard for chains
    started before the stamp existed, which is what it was read as then."""
    if record is None:
        return None
    return label_value(record, AT_LABEL) or record.get("updated_at")


def _now() -> dt.datetime:
    # noqa UP017: `dt.UTC` is 3.11+. This file ships standalone and is tested
    # against the floor in the CI matrix, so the older spelling stays.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # noqa: UP017


def _past(stamp: str | None, previous: str | None) -> str | None:
    """`stamp`, or one second after `previous` when that is later.

    `bd import` is an upsert guarded by `updated_at`, and it is documented to
    keep every local column on a *tie*. Its granularity is one second, so a
    write is silently dropped unless its stamp is strictly past the row bd
    holds. `BD_TIME` sorts as a string; a `previous` in no `BD_TIME` shape is
    no floor at all.
    """
    try:
        floor = dt.datetime.strptime(previous, BD_TIME) + dt.timedelta(seconds=1)
    except (TypeError, ValueError):
        return stamp
    return max(stamp or "", floor.strftime(BD_TIME))


def _bump_updated(record: dict) -> dict:
    """Stamp `updated_at` strictly newer than the record's own, and so than the
    row bd holds for it: two writes inside one second, which is every
    gate-then-approve, would otherwise lose the second."""
    return dict(record, updated_at=_past(_now().strftime(BD_TIME), record.get("updated_at")))


def _past_local(records: list[dict], local: list[dict]) -> list[dict]:
    """Records `import` has already decided win, each moved past the local row
    it replaces, which is the one bd's guard compares it with."""
    held = {r["id"]: r.get("updated_at") for r in local}
    out = []
    for record in records:
        stamp = _past(record.get("updated_at"), held.get(record["id"]))
        out.append(dict(record, updated_at=stamp) if stamp else record)
    return out


class BdStore:
    def __init__(self, run=subprocess.run) -> None:
        self.run = run

    def _bd(self, args: list[str], stdin: str | None = None):
        result = self.run(["bd", *args], capture_output=True, text=True, input=stdin)
        if result.returncode != 0:
            # A store that fails quietly hands the walk a chain with no history,
            # and the next node runs as if nothing had happened.
            raise SystemExit(f"kraft-lite: bd {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def load(self) -> list[dict]:
        # No `-o`: with one, bd writes a file, and `-o -` writes a file named `-`.
        out = self._bd(["export"]).stdout
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def write(self, records: list[dict]) -> None:
        payload = "".join(json.dumps(r) + "\n" for r in _prepare(records))
        self._bd(["import", "-"], stdin=payload)

    def write_raw(self, records: list[dict], local: list[dict]) -> None:
        """Records as exported, for `import`, past bd's guard without `_prepare`.

        `kraft-at:` is left alone, so the summary still times the run that wrote
        them. `updated_at` cannot be: `import` has already decided these records
        win, and bd keeps the local row on a tie or a newer guard. `local` is
        what `import` loaded, so the store is not exported a second time.
        """
        out = _past_local(records, local)
        self._bd(["import", "-"], stdin="".join(json.dumps(r) + "\n" for r in out))


def repo_root(start: Path | None = None) -> Path:
    """The directory the chain lives in, found by walking up.

    `Path.cwd()` alone means a verb run from a subdirectory finds no `.kraft-lite`
    and no `.beads`, silently drops to the jsonl backend, and reports `unstarted`
    in the middle of a live chain.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / STATE_DIR).is_dir() or (candidate / ".beads").is_dir():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return here


def store_for(root: Path):
    if backend(root) == "bd":
        return BdStore()
    return JsonlStore(root / STATE_DIR / "chain.jsonl")


def settings(root: Path) -> dict:
    """The standing rules: `.kraft-lite/slices.json` over `DEFAULT_SETTINGS`.

    Top-level keys replace; `rights` merges key by key, so a file that changes
    one right leaves the others at their defaults. Anything the file says that
    is not understood is refused, naming it: a mistyped right that silently
    changed nothing would leave a decision with the wrong party."""
    path = root / STATE_DIR / "slices.json"
    merged = {**DEFAULT_SETTINGS, "rights": dict(DEFAULT_SETTINGS["rights"])}
    if not path.exists():
        return merged
    try:
        given = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SystemExit(f"kraft-lite: cannot read {path}: {error}") from error
    if not isinstance(given, dict):
        raise SystemExit(f"kraft-lite: {path} is not a JSON object")
    for key, value in given.items():
        if key not in DEFAULT_SETTINGS:
            raise SystemExit(
                f"kraft-lite: unknown setting {key!r} in {path}. "
                f"Settings: {', '.join(DEFAULT_SETTINGS)}"
            )
        if key == "mode":
            if value not in MODES:
                raise SystemExit(
                    f"kraft-lite: unknown mode {value!r} in {path}. Modes: {', '.join(MODES)}"
                )
            merged["mode"] = value
            continue
        if not isinstance(value, dict):
            raise SystemExit(f"kraft-lite: {path}: rights is not a JSON object")
        for right, holder in value.items():
            if right not in DEFAULT_SETTINGS["rights"]:
                raise SystemExit(
                    f"kraft-lite: unknown right {right!r} in {path}. "
                    f"Rights: {', '.join(DEFAULT_SETTINGS['rights'])}"
                )
            if holder not in RIGHT_HOLDERS:
                raise SystemExit(
                    f"kraft-lite: right {right!r} in {path} must be one of "
                    f"{', '.join(RIGHT_HOLDERS)}, not {holder!r}"
                )
            merged["rights"][right] = holder
    return merged


DEFAULT_CHAIN = Path(__file__).parent / "chains" / "default.json"


def _frozen_path(root: Path, chain_id: str) -> Path:
    return root / STATE_DIR / "chains" / f"{chain_id}.json"


def _chain(root: Path, path: Path | None, chain_id: str | None = None) -> dict:
    """The chain this run walks.

    `start` freezes its chain and every later verb reads that copy. Re-reading the
    template each time would mean a `--chain` given at start is forgotten by the
    next verb — and worse, that editing a template retargets a run already in
    flight. One file per chain id is what lets two runs in one directory hold
    different templates; a single file meant the second `start` silently
    retargeted the first run.
    """
    if path is not None:
        return json.loads(path.read_text())
    if chain_id is not None:
        per_chain = _frozen_path(root, chain_id)
        if per_chain.is_file():
            return json.loads(per_chain.read_text())
    # Chains frozen before templates were per-chain. Remove at the next breaking
    # release; until then it is what keeps a run in flight walking across upgrade.
    legacy = root / STATE_DIR / "chain.json"
    if legacy.is_file():
        return json.loads(legacy.read_text())
    return json.loads(DEFAULT_CHAIN.read_text())


def _freeze_chain(root: Path, chain: dict, chain_id: str) -> None:
    frozen = _frozen_path(root, chain_id)
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(json.dumps(chain, indent=2) + "\n")


def chain_index(records: list[dict]) -> dict[str, list[dict]]:
    """Every chain in this store, keyed by its epic id, oldest first. Node ids are
    `<chain-id>.<node-id>`, so the prefix names the owner."""
    epics = [r["id"] for r in records if label_value(r, CHAIN_LABEL)]
    by_chain: dict[str, list[dict]] = {e: [] for e in epics}
    for record in records:
        owner = record["id"].split(".")[0]
        if owner in by_chain:
            by_chain[owner].append(record)
    return by_chain


def _refuse_slice_plan(plan: dict, mode: str) -> list[dict]:
    """The plan's slices if the whole graph is sound, else a `SystemExit` naming
    the first problem. Checked before any record is built: a half-made graph in
    the store is worse than none."""
    if mode not in MODES:
        raise SystemExit(f"kraft-lite: unknown slices mode {mode!r}. Modes: {', '.join(MODES)}")
    if not plan.get("state_branch"):
        raise SystemExit("kraft-lite: the slice plan has no state_branch")
    if mode == "feature" and not plan.get("feature_branch"):
        raise SystemExit("kraft-lite: feature mode needs a feature_branch in the slice plan")
    slices = plan.get("slices") or []
    if not slices:
        raise SystemExit("kraft-lite: the slice plan has no slices")
    keys: set[str] = set()
    # A task in two slices is built twice, by two children that never see each other.
    task_owner: dict[int, str] = {}
    for position, spec in enumerate(slices, 1):
        # `approve --slices` feeds this a person's JSON, so a slice missing a
        # field is refused by position rather than left to a KeyError traceback.
        if not isinstance(spec, dict):
            raise SystemExit(f"kraft-lite: slice {position} is not an object")
        for field in ("key", "title"):
            if not spec.get(field):
                raise SystemExit(f"kraft-lite: slice {position} has no {field}")
        key = spec["key"]
        if not isinstance(key, str):
            raise SystemExit(f"kraft-lite: slice {position} key must be a string")
        if key in keys:
            raise SystemExit(f"kraft-lite: duplicate slice key {key!r}")
        keys.add(key)
        if not spec.get("tasks"):
            raise SystemExit(f"kraft-lite: slice {position} ({key!r}) has no tasks")
        # What is written is read back as numbers and keys by `slices`, so what
        # cannot be read back is refused here, while nothing has been written.
        tasks = spec["tasks"]
        # `bool` is an `int`, and `True` would be stored as the task "True".
        if not isinstance(tasks, list) or not all(
            isinstance(t, int) and not isinstance(t, bool) for t in tasks
        ):
            raise SystemExit(
                f"kraft-lite: slice {position} ({key!r}) tasks must be a list of task numbers"
            )
        for task in tasks:
            if task_owner.get(task, key) != key:
                raise SystemExit(
                    f"kraft-lite: task {task} is in two slices, {task_owner[task]!r} and {key!r}"
                )
            task_owner[task] = key
        after = spec.get("after", [])
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
            raise SystemExit(
                f"kraft-lite: slice {position} ({key!r}) after must be a list of slice keys"
            )
    for spec in slices:
        for after in spec.get("after", []):
            if after not in keys:
                raise SystemExit(
                    f"kraft-lite: slice {spec['key']!r} comes after {after!r}, which is not a slice"
                )
    after_of = {spec["key"]: spec.get("after", []) for spec in slices}
    done: set[str] = set()

    def visit(key: str, path: list[str]) -> None:
        if key in path:
            loop = " -> ".join([*path[path.index(key) :], key])
            raise SystemExit(f"kraft-lite: slices form a cycle: {loop}")
        if key in done:
            return
        for after in after_of[key]:
            visit(after, [*path, key])
        done.add(key)

    for key in after_of:
        visit(key, [])
    return slices


def materialize_slices(plan: dict, chain_id: str, mode_default: str = "feature") -> list[dict]:
    """The run record, then one record per slice, each waiting on what it comes after.

    Nothing is returned for a plan that is not sound, so a caller writes all of
    it or none. Slice ids are random and carry no `.` (see `SLICE_OF_LABEL`); the
    `after` keys are resolved to those ids here, so the dependency links are the
    same `blocks` objects a node's are.
    """
    mode = plan.get("mode") or mode_default
    slices = _refuse_slice_plan(plan, mode)
    run_labels = [
        f"{RUN_LABEL}{chain_id}",
        f"{MODE_LABEL}{mode}",
        f"{STATE_BRANCH_LABEL}{plan['state_branch']}",
    ]
    # Null in main mode, where slices merge into the base branch directly.
    if plan.get("feature_branch"):
        run_labels.append(f"{FEATURE_BRANCH_LABEL}{plan['feature_branch']}")
    records = [
        {
            "_type": "issue",
            "id": f"ksr-{uuid.uuid4().hex[:8]}",
            "title": f"Slices of {chain_id}",
            "status": "open",
            "labels": run_labels,
            "dependencies": [],
        }
    ]
    ids = {spec["key"]: f"ks-{uuid.uuid4().hex[:8]}" for spec in slices}
    for position, spec in enumerate(slices):
        slice_id = ids[spec["key"]]
        records.append(
            {
                "_type": "issue",
                "id": slice_id,
                "title": spec["title"],
                "status": "open",
                "labels": [
                    f"{SLICE_OF_LABEL}{chain_id}",
                    f"{SLICE_KEY_LABEL}{spec['key']}",
                    f"{SLICE_POS_LABEL}{position}",
                    f"{SLICE_STATUS_LABEL}waiting",
                    f"{TASKS_LABEL}{','.join(str(t) for t in spec['tasks'])}",
                ],
                "dependencies": [_dep(slice_id, ids[a]) for a in spec.get("after", [])],
            }
        )
    return records


def run_record(records: list[dict], chain_id: str) -> dict | None:
    return next((r for r in records if label_value(r, RUN_LABEL) == chain_id), None)


def slice_records(records: list[dict], chain_id: str) -> list[dict]:
    """The chain's slices in plan order. Sorted on the position label, because
    `bd export` returns records in no order worth relying on."""
    mine = [r for r in records if label_value(r, SLICE_OF_LABEL) == chain_id]
    return sorted(mine, key=lambda r: int(label_value(r, SLICE_POS_LABEL) or 0))


def slice_done(record: dict, mode: str) -> bool:
    """Whether what comes after this slice may start. In `main` mode a slice
    that is ready to merge is enough, because nothing merges into a feature
    branch for the next one to build on."""
    done = ("merged", "ready_to_merge") if mode == "main" else ("merged",)
    return label_value(record, SLICE_STATUS_LABEL) in done


def ready_slices(records: list[dict], chain_id: str) -> list[dict]:
    """Slices still waiting whose every dependency is done."""
    run = run_record(records, chain_id)
    mode = label_value(run, MODE_LABEL) or "feature"
    slices = slice_records(records, chain_id)
    by_id = {r["id"]: r for r in slices}
    return [
        r
        for r in slices
        if label_value(r, SLICE_STATUS_LABEL) == "waiting"
        and all(slice_done(by_id[d], mode) for d in dep_ids(r) if d in by_id)
    ]


def _slice_entry(record: dict, by_id: dict[str, dict], mode: str) -> dict:
    """One slice as the `slices` and `slice-set` verbs print it. `after` is
    keys, not ids: ids are an implementation detail the controller never types."""
    tasks = label_value(record, TASKS_LABEL) or ""
    return {
        "id": record["id"],
        "key": label_value(record, SLICE_KEY_LABEL),
        "title": record["title"],
        "tasks": [int(t) for t in tasks.split(",") if t],
        "after": [label_value(by_id[d], SLICE_KEY_LABEL) for d in dep_ids(record) if d in by_id],
        "status": label_value(record, SLICE_STATUS_LABEL),
        "session": label_value(record, SESSION_LABEL),
        "branch": label_value(record, BRANCH_LABEL),
        "fork": label_value(record, FORK_LABEL),
        "done": slice_done(record, mode),
    }


def slices_view(records: list[dict], chain_id: str | None) -> dict:
    """The run and its slices, for the whole store: a chain that was never
    sliced reports an empty run rather than an error."""
    run = run_record(records, chain_id) if chain_id else None
    mode = label_value(run, MODE_LABEL)
    slices = slice_records(records, chain_id) if chain_id else []
    by_id = {r["id"]: r for r in slices}
    return {
        "mode": mode,
        "feature_branch": label_value(run, FEATURE_BRANCH_LABEL),
        "state_branch": label_value(run, STATE_BRANCH_LABEL),
        "slices": [_slice_entry(r, by_id, mode or "feature") for r in slices],
        "ready": [label_value(r, SLICE_KEY_LABEL) for r in ready_slices(records, chain_id)]
        if run
        else [],
    }


def slices_for_gate(
    records: list[dict], node_record: dict, chain_id: str, path: Path, root: Path
) -> list[dict]:
    """The records `approve --slices` writes with the approval, or a `SystemExit`.
    Slicing happens once, at the gate that finalizes the chain: anywhere else a
    plan has nothing settled to be split from, and a second time it would
    replace work already handed to children."""
    if run_record(records, chain_id) is not None:
        raise SystemExit("kraft-lite: this chain is already sliced")
    if (
        node_record["status"] != "blocked"
        or label_value(node_record, GATE_LABEL) != "chain_finalized"
    ):
        raise SystemExit("kraft-lite: --slices is only accepted at chain_finalized")
    try:
        plan = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SystemExit(f"kraft-lite: cannot read the slice plan {path}: {error}") from error
    if not isinstance(plan, dict):
        raise SystemExit(f"kraft-lite: the slice plan {path} is not a JSON object")
    # Read only when the plan names no mode: a slices.json that cannot be read
    # has nothing to say to a plan that already decided.
    return materialize_slices(
        plan, chain_id, mode_default=plan.get("mode") or settings(root)["mode"]
    )


def _by_age(by_chain: dict[str, list[dict]]) -> list[str]:
    """Chain ids oldest first, by the epic's timestamp rather than by store order.

    `bd export` promises neither insertion nor sorted order — `ordered()` says so
    a few functions up — so reading the last record as the newest is a coin flip.
    The epic is closed at `start` and never written again, so its `updated_at` is
    the run's start time. Ties fall back to store order, which is what a sort this
    stable does anyway.
    """

    def stamp(chain_id: str) -> str:
        epic = next((r for r in by_chain[chain_id] if label_value(r, CHAIN_LABEL)), None)
        return (epic or {}).get("updated_at", "")

    return sorted(by_chain, key=stamp)


def rewind(records: list[dict], from_node: str, note: str) -> list[dict]:
    """Reopen `from_node` and every node after it, carrying the reason.

    Reopening only the named node is not enough: `current` returns the first open
    node whose dependencies are closed, so with the later nodes still closed the
    walk would run the reopened one and jump straight back to the gate that
    rejected it — the redone work would never be verified. The chain's links are
    strictly linear and cannot express a backward edge, so the rewind is done by
    reopening the tail.
    """
    walk = [r for r in ordered(records) if label_value(r, NODE_LABEL)]
    ids = [label_value(r, NODE_LABEL) for r in walk]
    if from_node not in ids:
        raise SystemExit(
            f"kraft-lite: no node {from_node!r} in this chain. Its nodes are:\n  " + ", ".join(ids)
        )
    out = []
    for offset, record in enumerate(walk[ids.index(from_node) :]):
        # Banked before the stamp that opens it is stripped below. `--from-node` is
        # how the last gate sends work back, so the wait it interrupts is usually
        # the longest one in the run - dropping it here loses exactly that.
        record = bank_wait(record)
        labels = [
            x
            for x in record.get("labels", [])
            # `WAITED_LABEL` is not in this list on purpose: those hours were
            # spent, whatever the redo goes on to cost.
            if not x.startswith(GATE_LABEL)
            and not x.startswith(ATTEMPT_LABEL)
            and not x.startswith(BLOCKED_LABEL)
        ]
        reopened = dict(record, status="open", labels=labels)
        if offset == 0:
            description = record.get("description", "")
            reopened["description"] = f"{description}\n{NOTE_PREFIX}{note}"
        out.append(reopened)
    return out


def chain_summaries(records: list[dict]) -> list[dict]:
    """Every chain in the directory: what it is called, and where it got to.

    Reads the node record's own label rather than a template, so listing chains
    costs no template reads and works even where a frozen template has gone
    missing.
    """
    by_chain = chain_index(records)
    rows = []
    for chain_id in _by_age(by_chain):
        epic = next((r for r in by_chain[chain_id] if label_value(r, CHAIN_LABEL)), None)
        node_record = current(by_chain[chain_id])
        rows.append(
            {
                "chain_id": chain_id,
                "title": (epic or {}).get("title", ""),
                "status": "done" if node_record is None else node_record["status"],
                "node": label_value(node_record, NODE_LABEL) if node_record else None,
            }
        )
    return rows


def _seconds(earlier: str | None, later: str | None) -> int | None:
    try:
        return int(
            (
                dt.datetime.strptime(later, BD_TIME) - dt.datetime.strptime(earlier, BD_TIME)
            ).total_seconds()
        )
    except (TypeError, ValueError):
        return None


#: Every key `summary` reports a total under, so an unstarted chain and a walked
#: one hand a caller the same shape.
TOTALS = ("nodes", "closed", "attempts", "blocked_seconds", "gates", "rejections")


def summary(records: list[dict], chain: dict) -> dict:
    """What the run cost, read back out of the records it already wrote.

    The shape of the run is read back out of records the walk wrote anyway: the
    epic is closed at `start` and never written again, so its stamp is the run's
    start, and each node's is when it was last closed. What the walk does not
    otherwise keep, and this reads, is three labels written by writes that were
    already happening - `kraft-at:`, `kraft-waited:` and `kraft-spent:`. Two
    ceilings are worth knowing about:

    ponytail: one second is the granularity of every stamp here, so a chain walked
    faster than that reports zeros, including two nodes that closed inside the same
    second. Nothing sub-second is recoverable.

    ponytail: a rewind rewrites the tail's stamps, so a node redone after a
    rejection is timed from the redo, not from the attempt that was rejected. Time
    already spent waiting at a gate is the exception — it is banked, not restamped,
    so it survives.
    """
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    walk = [r for r in ordered(records) if label_value(r, NODE_LABEL)]
    if epic is None:
        # Same distinction `_state` draws, for the same reason: a directory with
        # no chain in it has not finished one.
        return {
            "chain_id": None,
            "title": "",
            "status": "unstarted",
            "started": None,
            "finished": None,
            "duration_seconds": None,
            "nodes": [],
            "totals": dict.fromkeys(TOTALS, 0),
        }
    node_record = current(records)
    definitions = {n["id"]: n for n in chain["nodes"]}

    #: Every stamp the chain carries, so a node can be timed from whatever happened
    #: last before it closed rather than from its predecessor's close. After a
    #: rewind those differ: the predecessor still holds its stamp from the first
    #: pass, and timing from it charges the redo for the whole walk in between.
    #: Each stamp is paired with its record's position in the walk, because
    #: `BD_TIME` resolves to the second and two nodes closed inside one second
    #: are otherwise indistinguishable — the later of the two then finds nothing
    #: earlier and falls all the way back to the epic (Kraft-ao6). Position
    #: breaks ties only; it never picks the predecessor.
    order = {r["id"]: i for i, r in enumerate([epic, *walk])}
    events = sorted((x, order.get(r["id"], -1)) for r in records if (x := _at(r)))

    def since(record: dict) -> str | None:
        closed_at = _at(record)
        if not closed_at:
            return _at(epic)
        key = (closed_at, order.get(record["id"], -1))
        # `< key` excludes the record's own entry without a separate id test.
        earlier = [e for e in events if e < key]
        return earlier[-1][0] if earlier else _at(epic)

    rows = []
    for record in walk:
        node_id = label_value(record, NODE_LABEL)
        definition = definitions.get(node_id, {})
        loop = definition.get("fix_loop")
        # Lines, not occurrences: `--note "Rejected: by CI"` is one rejection whose
        # reason happens to quote another.
        rejections = [
            line[len(NOTE_PREFIX) :].strip()
            for line in record.get("description", "").splitlines()
            if line.startswith(NOTE_PREFIX)
        ]
        # Banked rounds, plus the one being served right now if the node is sitting
        # at a gate — mid-run, that open wait is the whole question.
        holding = _seconds(label_value(record, BLOCKED_LABEL), _now().strftime(BD_TIME))
        blocked_seconds = waited(record) + max(0, holding or 0)
        closed = record["status"] == "closed"
        rows.append(
            {
                "node": node_id,
                "status": record["status"],
                # An unreached node carries the stamp `materialize` gave it, which
                # would otherwise read as work that took no time.
                # Clamped, and the carry below is a running maximum: `_bump_updated`
                # moves each record's stamp forward on its own, so a node written
                # three times (gate, reject, close) is stamped later than the node
                # that ran after it. Timed straight, the successor reports negative
                # seconds. Sub-second is the truth being floored here — the whole
                # overlap is the artificial second-per-write bump.
                "seconds": max(0, _seconds(since(record), _at(record)) or 0)
                if closed and _at(record)
                else None,
                "attempts": spent(record),
                "cap": chain["loops"].get(loop, {}).get("attempts") if loop else None,
                "gate": definition.get("gate_after"),
                # Of that time, the part the human held. A node that never reached
                # a gate reports null rather than zero: it did not wait, as against
                # waiting for no time.
                # Presence, not size: a gate answered inside a second banks zero,
                # and zero is a different answer from a node that never had a gate.
                "blocked_seconds": blocked_seconds
                if label_value(record, WAITED_LABEL) is not None
                or label_value(record, BLOCKED_LABEL)
                else None,
                # The latest reason leads, and every one of them is counted: a node
                # rejected twice cost two rounds, not one.
                "rejected": rejections[-1] if rejections else None,
                "rejections": len(rejections),
            }
        )
    # The last stamp any node carries, not the last row's: a chain written before
    # the wall clock stamp existed falls back to `updated_at`, which a node written
    # several times pushes past the node that ran after it.
    closed_stamps = [_at(r) for r in walk if r["status"] == "closed" and _at(r)]
    finished = max(closed_stamps, default=None) if node_record is None else None
    return {
        "chain_id": (epic or {}).get("id"),
        "title": (epic or {}).get("title", ""),
        "status": "done" if node_record is None else node_record["status"],
        "started": _at(epic),
        "finished": finished,
        "duration_seconds": _seconds(_at(epic), finished),
        "nodes": rows,
        "totals": {
            "nodes": len(rows),
            "closed": sum(1 for r in rows if r["status"] == "closed"),
            "attempts": sum(r["attempts"] for r in rows),
            "blocked_seconds": sum(r["blocked_seconds"] or 0 for r in rows),
            # Gates *answered*. The template's gate count is knowable before the
            # run starts, so reporting it as what the run cost says nothing.
            "gates": sum(1 for r in rows if r["gate"] and r["status"] == "closed"),
            "rejections": sum(r["rejections"] for r in rows),
        },
    }


def _titled(by_chain: dict[str, list[dict]], ids: list[str]) -> str:
    lines = []
    for chain_id in ids:
        epic = next((r for r in by_chain[chain_id] if label_value(r, CHAIN_LABEL)), None)
        lines.append(f"  {chain_id}  {epic.get('title', '') if epic else ''}")
    return "\n".join(lines)


def _ours(records: list[dict], chain_id: str | None = None) -> list[dict]:
    """The chain a verb acts on.

    A directory accumulates one epic per `start`. Picking the newest unfinished one
    silently is how the wrong run gets advanced — a gate answered against a chain
    nobody named — so two or more unfinished chains and no id is an error rather
    than a guess. With one unfinished chain the id stays optional, which is the
    case nearly every run is in.
    """
    by_chain = chain_index(records)
    if chain_id is not None:
        # Checked before the empty case: an id that names nothing must say so.
        # Reporting `unstarted` instead sends the human to `start` over a typo.
        if chain_id not in by_chain:
            raise SystemExit(
                f"kraft-lite: no chain {chain_id!r} in this directory. Chains here:\n"
                + (_titled(by_chain, list(by_chain)) if by_chain else "  (none)")
            )
        return by_chain[chain_id]
    if not by_chain:
        return []

    def unfinished(candidate: str) -> bool:
        return any(r["status"] != "closed" for r in by_chain[candidate])

    live = [e for e in by_chain if unfinished(e)]
    if len(live) > 1:
        raise SystemExit(
            "kraft-lite: this directory has more than one unfinished chain. "
            "Pass --chain-id to say which:\n" + _titled(by_chain, live)
        )
    # No unfinished chain means the newest overall, so a completed run still
    # reports `done` rather than `unstarted`.
    return by_chain[(live or _by_age(by_chain))[-1]]


def _sliced(everything: list[dict], epic: dict | None, node_id: str | None) -> dict | None:
    """`null` for a chain that was never sliced, else the run's mode and what
    the current node means in it. Read from the whole store: `_ours` returns only
    the epic and its nodes, and the run record is neither."""
    run = run_record(everything, epic["id"]) if epic else None
    if run is None:
        return None
    mode = label_value(run, MODE_LABEL) or "feature"
    return {"mode": mode, "action": SLICED_ACTIONS.get(node_id, {}).get(mode, "hooks")}


def _state(root: Path, chain: dict, chain_id: str | None = None) -> dict:
    everything = store_for(root).load()
    records = _ours(everything, chain_id)
    node_record = current(records)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    if node_record is None:
        # "done" and "never started" are different answers. Reporting both as
        # done tells the `next` skill a chain that does not exist has finished.
        return {
            "chain_id": epic["id"] if epic else None,
            "node": None,
            "hooks": [],
            "gate": None,
            "attempt": 0,
            "cap": None,
            "status": "done" if epic else "unstarted",
            "note": "",
            "backend": backend(root),
            "sliced": _sliced(everything, epic, None),
        }
    node_id = label_value(node_record, NODE_LABEL)
    definition = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if definition is None:
        # Reporting `hooks: []` here is worse than failing: the `next` skill's
        # contract is "no hooks, so close and move on", and the chain walks to
        # `done` having run nothing at all.
        raise SystemExit(
            f"kraft-lite: node {node_id!r} is not in this chain. "
            f"{_frozen_path(root, epic['id']).relative_to(root) if epic else 'the frozen chain'}"
            " is the chain this run started with — restore it, or start a new chain."
        )
    loop = definition.get("fix_loop")
    note = node_record.get("description", "")
    return {
        "chain_id": epic["id"] if epic else None,
        "node": node_id,
        "hooks": definition.get("tasks", []),
        "gate": definition.get("gate_after"),
        "attempt": attempts(node_record),
        "cap": chain["loops"].get(loop, {}).get("attempts") if loop else None,
        "status": node_record["status"],
        "note": note[note.find(NOTE_PREFIX) :] if NOTE_PREFIX in note else "",
        "backend": backend(root),
        "sliced": _sliced(everything, epic, node_id),
    }


#: A registry hook is an unquoted key at column zero. Everything under it — `kind`,
#: `skill`, `prompt` — is indented, and comments start with `#`. That is enough
#: structure to find the hooks with a scan, which is what keeps this file free of
#: the YAML dependency it would otherwise need for one check.
HOOK_KEY = re.compile(r"^([a-z][a-z0-9_.]*):", re.MULTILINE)


def _bound_hooks(text: str) -> set[str]:
    """Keys that are actually bound, not merely present.

    A regex cannot express "followed by an indented line, but not by another
    column-zero key" without a lookahead nobody wants to read at 3am, so this
    walks the lines instead. Structural only: `kind: banana` is bound as far as
    this is concerned, and fails at dispatch with a message that says so.
    """
    bound: set[str] = set()
    pending: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = HOOK_KEY.match(line)
        if match:
            # A key immediately following another leaves the first unbound.
            pending = match.group(1)
        elif pending and line[:1] in " \t":
            bound.add(pending)
            pending = None
    return bound


def registry_hooks(root: Path) -> set[str] | None:
    """The hooks bound in this repo's registry, or None when there is no registry."""
    path = root / STATE_DIR / "registry.yaml"
    if not path.is_file():
        return None
    return _bound_hooks(path.read_text())


def validate_hooks(root: Path, chain: dict) -> None:
    """A hook with no binding is otherwise found mid-walk, after the earlier nodes
    have already run and spent the human's attention.

    A missing registry only warns: the `start` skill already refuses to run without
    one, so erroring here would duplicate that guard at the price of a fixture in
    every test that starts a chain.
    """
    bound = registry_hooks(root)
    if bound is None:
        print(
            f"kraft-lite: no {STATE_DIR}/registry.yaml — starting without checking "
            "the chain's hooks. Run the init skill to write one.",
            file=sys.stderr,
        )
        return
    wanted = {task for node in chain["nodes"] for task in node["tasks"]}
    missing = sorted(wanted - bound)
    if missing:
        raise SystemExit(
            "kraft-lite: this chain names hooks with no registry binding: "
            + ", ".join(missing)
            + f"\nAdd them to {STATE_DIR}/registry.yaml, or re-run the init skill."
        )


def _slice_set(store, everything: list[dict], chain_id: str | None, args) -> dict:
    """Move one slice: its status, the session running it, its branch, its fork
    point. `merged` closes the record too, so a store that shows only open work
    stops showing it, and the run record closes with the last slice to merge.
    Main mode's slices reach `merged` too, as the `next` skill records the
    user's merges, so this is the one place a finished run is seen in both."""
    slices = slice_records(everything, chain_id) if chain_id else []
    by_key = {label_value(r, SLICE_KEY_LABEL): r for r in slices}
    if args.slice not in by_key:
        raise SystemExit(
            f"kraft-lite: no slice {args.slice!r} in this chain. Slices: "
            + (", ".join(by_key) or "(none)")
        )
    if args.status is not None and args.status not in SLICE_STATUSES:
        raise SystemExit(
            f"kraft-lite: unknown slice status {args.status!r}. "
            f"Statuses: {', '.join(SLICE_STATUSES)}"
        )
    record = by_key[args.slice]
    if args.status is not None:
        record = set_label(record, SLICE_STATUS_LABEL, args.status)
        record = dict(record, status="closed" if args.status == "merged" else "open")
    for prefix, value in (
        (SESSION_LABEL, args.session),
        (BRANCH_LABEL, args.branch),
        (FORK_LABEL, args.fork),
    ):
        if value is not None:
            record = set_label(record, prefix, value)
    writes = [record]
    run = run_record(everything, chain_id)
    others = [r for r in slices if r["id"] != record["id"]]
    finished = all(label_value(r, SLICE_STATUS_LABEL) == "merged" for r in [*others, record])
    # Reopened too, when a call overturned at the wrap-up puts a slice back to work.
    if run is not None and run["status"] != ("closed" if finished else "open"):
        writes.append(dict(run, status="closed" if finished else "open"))
    store.write(writes)
    mode = label_value(run, MODE_LABEL) or "feature"
    by_id = {r["id"]: r for r in slices}
    return _slice_entry(record, by_id, mode)


def _decisions_path(root: Path, chain_id: str) -> Path:
    """The decisions log. Not the issue store, even with bd: what the controller
    decided for the human is a log for the human to read, not work to track."""
    return root / STATE_DIR / "decisions" / f"{chain_id}.jsonl"


def _decide(root: Path, chain_id: str, args) -> dict:
    if args.held and args.answer is not None:
        raise SystemExit("kraft-lite: a --held question has no answer yet; drop --answer")
    if not args.held and args.answer is None:
        raise SystemExit("kraft-lite: a decision needs --answer, or --held to hold it for the user")
    at = _now().strftime(BD_TIME)
    entry = {
        "id": f"kd-{uuid.uuid4().hex[:8]}",
        "at": at,
        "slice": args.slice,
        "question": args.question,
        "answer": args.answer,
        "why": args.why,
        "held": args.held,
        "answered_by": None if args.held else "controller",
        "answered_at": None if args.held else at,
        # Kept on the entry, not as a position in the log: `read_jsonl` keeps an
        # entry where it was first written, so an answer, or an import, that
        # rewrites one already reported would land behind a position.
        "reported": False,
    }
    append_jsonl(_decisions_path(root, chain_id), [entry])
    return entry


def _answer(root: Path, chain_id: str, args) -> dict:
    path = _decisions_path(root, chain_id)
    entries = {e["id"]: e for e in read_jsonl(path)}
    if args.decision not in entries:
        raise SystemExit(
            f"kraft-lite: no decision {args.decision!r} in this chain. Decisions: "
            + (", ".join(entries) or "(none)")
        )
    if not entries[args.decision]["held"]:
        # Answering it again would put the user's name on the controller's call,
        # or overwrite what the user already said.
        raise SystemExit(
            f"kraft-lite: decision {args.decision} is not held: "
            f"{entries[args.decision]['answered_by']} answered it. `answer` is for a held question"
        )
    entry = dict(
        entries[args.decision],
        answer=args.answer,
        held=False,
        answered_by="user",
        answered_at=_now().strftime(BD_TIME),
        # A new answer is news, whether or not the question was reported held.
        reported=False,
    )
    append_jsonl(path, [entry])
    return entry


def _decisions(root: Path, everything: list[dict], chain_id: str, args) -> list[dict]:
    """The log in write order. `--unreported` is every entry whose latest write
    has not been reported, so a status report never repeats itself; `--mark`
    marks what it returned reported, in the log."""
    if args.mark and not args.unreported:
        raise SystemExit(
            "kraft-lite: --mark marks what --unreported returns; pass --unreported too"
        )
    if args.mark and args.held:
        # A report shows the whole unreported window, held questions and
        # decisions alike; marking a held-only list would mark a report never made.
        raise SystemExit(
            "kraft-lite: --mark marks the whole unreported window, so run it without --held"
        )
    run = run_record(everything, chain_id)
    if args.mark and run is None:
        raise SystemExit("kraft-lite: this chain is not sliced, so it has no status report to mark")
    path = _decisions_path(root, chain_id)
    entries = read_jsonl(path)
    if args.unreported:
        entries = [e for e in entries if not e.get("reported")]
    if args.held:
        entries = [e for e in entries if e["held"]]
    if args.mark and entries:
        append_jsonl(path, [dict(e, reported=True) for e in entries])
    return entries


EXPORT_RECORDS = "records.jsonl"
EXPORT_DECISIONS = "decisions.jsonl"


def _export(root: Path, everything: list[dict], chain_id: str, to: Path) -> dict:
    """The run's state as three files for the state branch: its records as the
    store holds them, its frozen template, and its decisions log. Nothing else
    is written, and nothing else in `to` is touched, because `to` is usually a
    checkout of that branch. Specs and plans travel by message, never here."""
    run = run_record(everything, chain_id)
    records = [
        *chain_index(everything)[chain_id],
        *([run] if run else []),
        *slice_records(everything, chain_id),
    ]
    template = to / "chains" / f"{chain_id}.json"
    template.parent.mkdir(parents=True, exist_ok=True)
    frozen = _frozen_path(root, chain_id)
    if frozen.is_file():
        shutil.copyfile(frozen, template)
    else:
        template.write_text(json.dumps(_chain(root, None, chain_id), indent=2) + "\n")
    (to / EXPORT_RECORDS).write_text("".join(json.dumps(r) + "\n" for r in records))
    decisions = read_jsonl(_decisions_path(root, chain_id))
    (to / EXPORT_DECISIONS).write_text("".join(json.dumps(e) + "\n" for e in decisions))
    return {
        "chain_id": chain_id,
        "to": str(to),
        "records": len(records),
        "decisions": len(decisions),
    }


def _import_records(local: list[dict], exported: list[dict]) -> tuple[list[dict], int]:
    """Which exported records to write, and how many local ones win instead.

    A record is written only where the local copy is missing or was last written
    earlier, by `kraft-at:`: `BD_TIME` sorts as a string, and no stamp is oldest.
    A tie keeps the local copy. The epic is written only where it is missing:
    it is never written after `start`, and its stamp is the run's start time.
    """
    held = {r["id"]: r for r in local}
    write: list[dict] = []
    kept = 0
    for record in exported:
        mine = held.get(record["id"])
        if mine is None or (
            label_value(record, CHAIN_LABEL) is None
            and (label_value(mine, AT_LABEL) or "") < (label_value(record, AT_LABEL) or "")
        ):
            write.append(record)
        else:
            kept += 1
    return write, kept


def _merge_decisions(local: list[dict], exported: list[dict]) -> tuple[list[dict], int]:
    """Which exported decisions to append, and how many local ones win instead.
    The later of `answered_at or at` wins; on a tie, an answer beats a held
    question, so a held copy never takes back the user's answer, and then a
    reported copy beats an unreported one. That is the order an entry's own
    writes come in, so the copy further along always wins."""

    def rank(entry: dict) -> tuple[str, bool, bool]:
        return (
            entry.get("answered_at") or entry.get("at") or "",
            not entry.get("held"),
            bool(entry.get("reported")),
        )

    held = {e["id"]: e for e in local}
    write = [e for e in exported if e["id"] not in held or rank(e) > rank(held[e["id"]])]
    return write, len(exported) - len(write)


def _import(root: Path, store, source: Path) -> dict:
    """Resume, or catch up, from an export. Dispatched before `_ours`: the
    export names its own chain, and the directory may hold others.

    Records are written through `write_raw`, never `store.write`: `_prepare`
    would restamp every one with the time of the import, and the summary would
    report a run that started, and finished every node, just now."""
    exported = read_jsonl(source / EXPORT_RECORDS)
    epics = [r["id"] for r in exported if label_value(r, CHAIN_LABEL)]
    if len(epics) != 1:
        raise SystemExit(
            f"kraft-lite: {source / EXPORT_RECORDS} is not an export of one chain: "
            f"it holds {len(epics)} chain epics ({', '.join(epics) or 'none'})"
        )
    chain_id = epics[0]
    template = source / "chains" / f"{chain_id}.json"
    if not template.is_file():
        raise SystemExit(f"kraft-lite: the export has no frozen template {template}")
    frozen = _frozen_path(root, chain_id)
    if not frozen.is_file():
        frozen.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, frozen)
    local = store.load()
    write, kept = _import_records(local, exported)
    if write:
        store.write_raw(write, local)
    path = _decisions_path(root, chain_id)
    decided, kept_decisions = _merge_decisions(
        read_jsonl(path), read_jsonl(source / EXPORT_DECISIONS)
    )
    if decided:
        append_jsonl(path, decided)
    return {
        "chain_id": chain_id,
        "imported": len(write) + len(decided),
        "kept_local": kept + kept_decisions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kl")
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--chain", type=Path, default=None)
    detect_parser = sub.add_parser("detect")
    detect_parser.add_argument("--chain", type=Path, default=None)
    sub.add_parser("chains")
    sub.add_parser("settings")
    gate = sub.add_parser("gate")
    gate.add_argument("--name", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--note", required=True)
    # The last gate rejects work that an earlier node produced: its own hook only
    # presents it. Without this the gate is approve-or-stall.
    reject.add_argument("--from-node", default=None)
    for name in ("state", "summary", "close", "approve", "attempt"):
        sub.add_parser(name)
    sub.choices["approve"].add_argument("--slices", type=Path, default=None)
    sub.add_parser("slices")
    slice_set = sub.add_parser("slice-set")
    slice_set.add_argument("--slice", required=True)
    slice_set.add_argument("--status", default=None)
    slice_set.add_argument("--session", default=None)
    slice_set.add_argument("--branch", default=None)
    slice_set.add_argument("--fork", default=None)
    decide = sub.add_parser("decide")
    decide.add_argument("--question", required=True)
    decide.add_argument("--why", required=True)
    decide.add_argument("--answer", default=None)
    decide.add_argument("--slice", default=None)
    decide.add_argument("--held", action="store_true")
    answer = sub.add_parser("answer")
    answer.add_argument("--decision", required=True)
    answer.add_argument("--answer", required=True)
    decisions = sub.add_parser("decisions")
    decisions.add_argument("--held", action="store_true")
    decisions.add_argument("--unreported", action="store_true")
    decisions.add_argument("--mark", action="store_true")
    sub.add_parser("export").add_argument("--to", type=Path, required=True)
    # No `--chain-id`: the export names its chain.
    sub.add_parser("import").add_argument("--from", dest="source", type=Path, required=True)
    # Every verb that reads or writes chain state can be told which chain. `start`
    # mints its own id and `detect` touches no state, so neither takes one.
    for name in (
        "state",
        "summary",
        "close",
        "approve",
        "attempt",
        "gate",
        "reject",
        "slices",
        "slice-set",
        "decide",
        "answer",
        "decisions",
        "export",
    ):
        sub.choices[name].add_argument("--chain-id", default=None)

    args = parser.parse_args(argv)
    root = repo_root()
    chain_id = getattr(args, "chain_id", None)

    if args.verb == "detect":
        print(json.dumps(detect(root, args.chain), indent=2))
        return 0

    if args.verb == "chains":
        print(json.dumps(chain_summaries(store_for(root).load()), indent=2))
        return 0

    if args.verb == "settings":
        print(json.dumps(settings(root), indent=2))
        return 0

    store = store_for(root)

    if args.verb == "import":
        print(json.dumps(_import(root, store, args.source), indent=2))
        return 0

    if args.verb == "start":
        new_id = new_chain_id()
        # Not `_chain`: its legacy rung exists to keep runs already in flight
        # walking, and nothing deletes `chain.json`. Reading it here would make
        # every future `start` in an upgraded directory run that old template
        # instead of the packaged default.
        chain = json.loads((args.chain or DEFAULT_CHAIN).read_text())
        validate_hooks(root, chain)
        _freeze_chain(root, chain, new_id)
        records = materialize(chain, args.title, new_id)
        # The epic is a container, not a step: close it now so the walk starts at
        # the first real node.
        records[0]["status"] = "closed"
        store.write(records)
        print(json.dumps({"chain_id": new_id, "backend": backend(root)}))
        return 0

    everything = store.load()
    records = _ours(everything, chain_id)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    # A verb run without the flag still has to read the template of the chain it
    # actually resolved, not the directory's last one.
    resolved_id = epic["id"] if epic else None
    chain = _chain(root, None, resolved_id)
    node_record = current(records)
    # Wrap-up and status read after the walk is over, so these are not refused
    # for want of an open node.
    if node_record is None and args.verb not in (
        "state",
        "summary",
        "slices",
        "slice-set",
        "decisions",
        "decide",
        "answer",
        "export",
    ):
        raise SystemExit("kraft-lite: no open node — the chain is finished or was never started")

    if args.verb in ("decide", "answer", "decisions", "export"):
        if resolved_id is None:
            raise SystemExit("kraft-lite: no chain in this directory — start one first")
        if args.verb == "decide":
            out = _decide(root, resolved_id, args)
        elif args.verb == "answer":
            out = _answer(root, resolved_id, args)
        elif args.verb == "decisions":
            out = _decisions(root, everything, resolved_id, args)
        else:
            out = _export(root, everything, resolved_id, args.to)
        print(json.dumps(out, indent=2))
        return 0

    if args.verb == "state":
        print(json.dumps(_state(root, chain, resolved_id), indent=2))
        return 0

    if args.verb == "summary":
        print(json.dumps(summary(records, chain), indent=2))
        return 0

    if args.verb == "slices":
        print(json.dumps(slices_view(everything, resolved_id), indent=2))
        return 0

    if args.verb == "slice-set":
        print(json.dumps(_slice_set(store, everything, resolved_id, args), indent=2))
        return 0

    if args.verb == "approve" and args.slices is not None:
        made = slices_for_gate(everything, node_record, resolved_id, args.slices, root)
        store.write([bank_wait(dict(node_record, status="closed")), *made])
    elif args.verb in ("close", "approve"):
        store.write([bank_wait(dict(node_record, status="closed"))])
    elif args.verb == "gate":
        store.write([open_gate(dict(node_record, status="blocked"), args.name)])
    elif args.verb == "reject" and args.from_node:
        store.write(rewind(records, args.from_node, args.note))
    elif args.verb == "reject":
        description = node_record.get("description", "")
        # Banked on the way out too: a gate that ends in a rejection held the human
        # exactly as long as one that ends in an approval.
        store.write(
            [
                bank_wait(
                    dict(
                        node_record,
                        status="open",
                        description=f"{description}\n{NOTE_PREFIX}{args.note}",
                    )
                )
            ]
        )
    elif args.verb == "attempt":
        charged = spend_attempt(node_record)
        count = attempts(charged)
        store.write([charged])
        state = _state(root, chain, resolved_id)
        state["over_cap"] = state["cap"] is not None and count > state["cap"]
        print(json.dumps(state, indent=2))
        return 0

    print(json.dumps(_state(root, chain, resolved_id), indent=2))
    return 0


#: Which installed skill names plausibly serve which hook. Deliberately keyword
#: matching and deliberately generous: init reports every candidate and asks the
#: human only when there is more than one. A wrong guess offered as a choice is
#: cheap; a missing candidate is a hook the human has to fill in by hand.
HOOK_KEYWORDS = {
    "on.spec.requested": ("brainstorm", "spec", "requirement"),
    "on.plan.requested": ("plan",),
    "on.chain.review_ready": ("chain-review",),
    "on.env.prepare": ("worktree", "env"),
    "on.implementation.start": ("test-driven", "tdd", "implement"),
    # No candidates: scanning repos for undeclared submodule changes is the
    # forge adapter's own job, not something an installed skill serves.
    "on.repos.scan": (),
    "on.test.run": (),
    # A slice's implementation runs as subagents, one per task.
    "on.slice.implementation.start": ("subagent-driven", "subagent"),
    # No candidates: which tests to run comes from the slice's own instructions.
    "on.slice.test.run": (),
    "on.review.local.run": ("review",),
    # No candidates: rebasing onto the repo's current HEAD is the forge
    # adapter's own job, not something an installed skill serves.
    "on.mr.rebase": (),
    "on.mr.describe": ("mr-metadata",),
    "on.mr.open": ("finishing", "branch", "pull-request", "merge-request"),
    # No candidates: rewriting a merge request description from the branch is
    # the forge adapter's own job, not something an installed skill serves.
    "on.mr.sync": (),
    "on.ci.poll": (),
    "on.review.mr.run": ("review",),
    "on.human_review.requested": (),
    "on.merge": ("finishing", "merge"),
    # No candidates: watching the pipeline the merge itself just triggered on
    # the target branch is the forge adapter's own job, not something an
    # installed skill serves.
    "on.merge.watch": (),
}

#: Words in a hook name that say when it fires, not what it does. A custom hook
#: derives its candidates from the words that are left, so these must not match
#: a skill: `on.deploy.staging` should look for deploy skills, not for every
#: skill with "start" in its name.
HOOK_STOPWORDS = frozenset({"on", "requested", "run", "start", "prepare", "poll", "open", "ready"})


def _hook_keywords(hook: str) -> tuple[str, ...]:
    """Keywords to match installed skill names against.

    `HOOK_KEYWORDS` is the curated answer for the hooks that ship with the default
    chain — it encodes judgements the name alone cannot, like `on.mr.open` wanting
    `finishing`. It is not the list of hooks that exist: a custom chain names
    hooks nobody curated, and those derive from their own words.
    """
    if hook in HOOK_KEYWORDS:
        return HOOK_KEYWORDS[hook]
    return tuple(w for w in re.split(r"[._]", hook) if w and w not in HOOK_STOPWORDS)


SKILL_ROOTS = (
    Path.home() / ".claude" / "plugins",
    Path.home() / ".claude" / "skills",
    Path(".claude") / "skills",
)


def _test_command(root: Path) -> list[str] | None:
    justfile = root / "justfile"
    # `^test\b` would also match `test-ui:` and `test-e2e:`.
    if justfile.is_file() and re.search(r"^test(?:\s|:)", justfile.read_text(), re.M):
        return ["just", "test"]
    makefile = root / "Makefile"
    if makefile.is_file() and re.search(r"^test\s*:", makefile.read_text(), re.M):
        return ["make", "test"]
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text()).get("scripts") or {}
        except ValueError:
            scripts = {}
        if "test" in scripts:
            return ["npm", "test"]
    if (root / "pyproject.toml").is_file():
        return ["pytest", "-q"]
    return None


#: The client each forge's CI is read with, and the command that reads it once.
#: Both are read-only status calls: `next` is forbidden to sit in a wait loop.
FORGE_CLI = {
    "gitlab": ("glab", ["glab", "ci", "status"]),
    "github": ("gh", ["gh", "pr", "checks"]),
}


def _origin_url(root: Path) -> str:
    """`origin`'s URL, or empty when there is no remote, no git, or no repo."""
    try:
        done = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _origin_host(root: Path) -> str:
    """Just the host from origin's URL.

    The repo name is not evidence of a forge: `github.com/me/gitlab-mirror` is a
    GitHub repo, and matching the whole URL binds it the wrong client. Handles
    both git's scp-like `git@host:path` and ordinary `scheme://host/path`.
    """
    url = _origin_url(root)
    if "://" in url:
        url = url.split("://", 1)[1]
    # Strip any userinfo, then take everything up to the path or port separator.
    host = url.rsplit("@", 1)[-1]
    return re.split(r"[/:]", host, maxsplit=1)[0].lower()


def _forge(root: Path) -> str | None:
    """Which forge's CI this repo's merge requests run on.

    `origin` decides, because that is where a merge request lands. The CI files
    are only the fallback for a checkout with no remote configured yet — and a
    repo can carry a `.github/workflows` it has stopped using.
    """
    host = _origin_host(root)
    if "gitlab" in host:
        return "gitlab"
    if "github" in host:
        return "github"
    if (root / ".gitlab-ci.yml").is_file():
        return "gitlab"
    if (root / ".github" / "workflows").is_dir():
        return "github"
    return None


def _ci_command(root: Path) -> list[str] | None:
    """None when the forge is unknown or its client is not installed. init then
    writes `kind: prompt`, which is honest: a bound command that cannot run reads
    as finished and fails at the node instead."""
    forge = _forge(root)
    if forge is None:
        return None
    cli, command = FORGE_CLI[forge]
    return command if shutil.which(cli) else None


def _installed_skills(root: Path) -> list[str]:
    """Every SKILL.md reachable, named as the agent would invoke it: prefixed
    with the plugin whose manifest encloses it, bare when there is none."""
    names: set[str] = set()
    for entry in SKILL_ROOTS:
        base = entry if entry.is_absolute() else root / entry
        if not base.is_dir():
            continue
        for skill_md in base.rglob("SKILL.md"):
            name = skill_md.parent.name
            prefix = None
            for parent in skill_md.parents:
                manifest = parent / ".claude-plugin" / "plugin.json"
                if manifest.is_file():
                    try:
                        prefix = json.loads(manifest.read_text()).get("name")
                    except ValueError:
                        prefix = None
                    break
                if parent == base:
                    break
            names.add(f"{prefix}:{name}" if prefix else name)
    return sorted(names)


def detect(root: Path, chain_path: Path | None = None) -> dict:
    chain = json.loads((chain_path or DEFAULT_CHAIN).read_text())
    # dict.fromkeys, not set: init writes the registry in this order, and a
    # registry whose keys follow the chain reads like the run it configures.
    hooks = dict.fromkeys(task for node in chain["nodes"] for task in node["tasks"])
    installed = _installed_skills(root)
    skills = {}
    for hook in hooks:
        keywords = _hook_keywords(hook)
        skills[hook] = [n for n in installed if any(k in n.lower() for k in keywords)]
    return {
        "test_command": _test_command(root),
        "ci_command": _ci_command(root),
        "skills": skills,
    }


if __name__ == "__main__":
    sys.exit(main())
