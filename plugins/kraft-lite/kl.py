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
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import kl_bindings
import kl_chains
import kl_migrate
import kl_shape

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
#: How many times this record has been written, counted by `_prepare`. `import`
#: ranks two copies of a record by it before any clock: the machine that wrote
#: last can be the one whose clock runs behind, as when a controller moves from a
#: laptop to the cloud. A record from before this label counts as 0.
SEQ_LABEL = "kraft-seq:"
#: What a node, or the epic for an attachment, produced: `kraft-artifact:<kind>=<path>`,
#: one label per kind. A gate presents the newest record's file of its kind.
ARTIFACT_LABEL = "kraft-artifact:"
#: What an artifact kind may be: it sits in a label before the `=`.
ARTIFACT_KIND = re.compile(r"[A-Za-z0-9_.-]+")
#: Set once the node's `on_failure` ran in this round; a rewind clears it.
ON_FAILURE_RAN_LABEL = "kraft-on-failure-ran"
#: How many times `restart` has sent the walk back from this node because its
#: base moved. Never cleared: the cap is the node's, over the whole run.
RESTARTS_LABEL = "kraft-restarts:"
RESTART_CAP = 3
#: A fix loop's cap when neither the loop nor anything else names one, as Kraft's.
DEFAULT_CAP = 3

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
#: On a run record and its slices once a rewind to a node before the slices were
#: cut has retired them, valued with that node. They stay in the store, and in an
#: export, so an import retires them where they are live; nothing else reads them.
RETIRED_LABEL = "kraft-slices-retired:"
#: The slice a slice was cut early from, by key, while its branch is based on that
#: slice's branch. Cleared when it records a fork after that slice has merged:
#: its base is then the target.
CUT_FROM_LABEL = "kraft-cut-from:"
#: On a slice cut early from one that went back to work since, valued with that
#: slice's key: its base is no longer that branch's tip. Cleared by its next fork.
BASE_MOVED_LABEL = "kraft-base-moved:"

SLICE_STATUSES = ("waiting", "starting", "running", "in_review", "ready_to_merge", "merged")
#: A slice in its final review: the one slice another comes after may be cut
#: from its branch early, before it merges.
EARLY_FROM = ("in_review", "ready_to_merge")
MODES = ("feature", "main")
#: A slice key becomes a label value, a directory, a file name and part of a
#: session title, so it holds nothing any of those would read differently.
SLICE_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
#: What a branch name has to be: a label value and an argument to git, so no
#: whitespace, and nothing git would read as an option (a leading `-`) or refuse
#: as a branch (`git check-ref-format --branch`): no `..`, `//` or `@{`, no part
#: starting with `.`, no ending in `/`, `.` or `.lock`, none of `~^:?*[\`.
BRANCH = re.compile(
    r"(?![-/.])(?!.*(?:\.\.|//|@\{|/\.))(?!.*(?:/|\.|\.lock)\Z)[^\s~^:?*\[\\\x00-\x1f\x7f]+"
)
#: A fork point as git prints a commit: abbreviated to 4 hex digits at the least,
#: and up to a SHA-256 commit's 64.
FORK = re.compile(r"[0-9a-fA-F]{4,64}")
#: A session id, as a harness prints one: one word.
SESSION = re.compile(r"\S+")
#: What a slice plan holds, and what each of its slices holds. Anything else is
#: refused: a misspelt `after` would silently drop the dependency it names.
PLAN_KEYS = ("mode", "feature_branch", "state_branch", "slices")
SLICE_SPEC_KEYS = ("key", "title", "tasks", "after")
#: The ids `materialize_slices` mints for a run record and for a slice.
RUN_ID = re.compile(r"ksr-[0-9a-f]{8}")
SLICE_ID = re.compile(r"ks-[0-9a-f]{8}")

#: On a child's epic: it walks a slice of a controller's chain (`start
#: --slice-of`), so `state` dispatches its tasks with the slice bindings layer,
#: and it is never sliced itself.
SLICE_LAYER_LABEL = "kraft-slice-layer"

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


#: The shape `new_chain_id` mints. A chain id names files under `.kraft-lite/` and
#: in an export, and one from outside this process - an export's epic, a store
#: written by hand or through bd - could hold `..` or a `/`. Every path built
#: from one goes through `_checked`, which refuses anything else.
CHAIN_ID = re.compile(r"kl-[0-9a-f]{8}")


def _checked(chain_id: str) -> str:
    """`chain_id`, or a `SystemExit` before it reaches a path."""
    if not isinstance(chain_id, str) or not CHAIN_ID.fullmatch(chain_id):
        raise SystemExit(
            f"kraft-lite: {chain_id!r} is not a chain id: kl- and 8 of 0-9, a-f, "
            "as `start` mints them"
        )
    return chain_id


def _tasks(container: dict) -> list[dict]:
    """Every task in an exec node's own steps, or a handler's, in order."""
    return [task for step in container["steps"] for task in step["tasks"]]


def _dispatched_tasks(chain: dict) -> list[dict]:
    """Every task the walk dispatches, in chain order: each exec node's own steps,
    its failure handler's, its fix loop's, then the loop's judge."""
    found = []
    for node in (n for n in chain["nodes"] if n["kind"] == "exec"):
        loop = node["fix_loop"]
        for container in (node, node["on_failure"], loop):
            found += _tasks(container) if container else []
        if loop and loop["judge"]:
            found.append(loop["judge"])
    return found


def _describe(node: dict) -> str:
    if node["kind"] == "gate":
        return f"gate: {node['message']}"
    return "tasks: " + ", ".join(task["path"] for task in _tasks(node))


def materialize(chain: dict, title: str, chain_id: str) -> list[dict]:
    """The epic, then one record per node, gate nodes included, linked so each
    waits on the last."""
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
                "description": _describe(node),
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
    return dict(record, labels=[*drop_label(record, prefix)["labels"], f"{prefix}{value}"])


def drop_label(record: dict, prefix: str) -> dict:
    """Return a copy with no label under `prefix`."""
    return dict(record, labels=[x for x in record.get("labels", []) if not x.startswith(prefix)])


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
    return drop_label(set_label(record, WAITED_LABEL, str(total)), BLOCKED_LABEL)


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


def read_jsonl(path: Path, check=None) -> list[dict]:
    """Last write per id wins; first-seen order is preserved. A line that is not
    an object with a string id, or that `check` cannot read, is a `SystemExit`
    naming the file and the line, not a traceback."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    merged: dict[str, dict] = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                raise ValueError("not an object with a string id")
            if check is not None:
                check(record)
        except (ValueError, TypeError, AttributeError) as error:
            raise SystemExit(f"kraft-lite: {path} line {number} cannot be read: {error}") from None
        merged[record["id"]] = record
    return list(merged.values())


def _jsonl(records: list[dict]) -> str:
    """One JSON object per line, as `read_jsonl`, `bd import` and `bd export` read and write."""
    return "".join(json.dumps(record) + "\n" for record in records)


def append_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(_jsonl(records))


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
#: Every stamp this file compares, as read: `BD_TIME`, or RFC 3339 with a
#: fraction of a second, an offset in place of `Z`, or both, which is how a bd
#: row's `updated_at` can come back. Spelled out rather than left to
#: `fromisoformat`, which reads neither `Z` nor a short fraction before 3.11.
STAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})"
)


def _parse_stamp(stamp: str | None) -> dt.datetime | None:
    """A stamp as a naive UTC time, or None for anything in no `STAMP` shape, or
    one whose offset carries it outside the years `datetime` holds. Compared as
    text, `10:00:00Z` sorts after `10:00:00.5Z`, and `12:00+02:00` after `11:00Z`."""
    match = STAMP.fullmatch(stamp) if isinstance(stamp, str) else None
    if match is None:
        return None
    *fields, fraction, zone = match.groups()
    try:
        moment = dt.datetime(*map(int, fields), int((fraction or "")[:6].ljust(6, "0")))
    except ValueError:
        return None
    if zone != "Z":
        offset = dt.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
        try:
            moment = moment - offset if zone[0] == "+" else moment + offset
        except OverflowError:
            return None
    return moment


def _when(stamp: str | None) -> dt.datetime:
    """A stamp as a sort key: one that cannot be read sorts oldest."""
    return _parse_stamp(stamp) or dt.datetime.min


def _seq(record: dict) -> int:
    return int(label_value(record, SEQ_LABEL) or 0)


def _prepare(records: list[dict]) -> list[dict]:
    """What every write does to every record, whichever store is behind it: bump
    bd's guard, stamp the wall clock the summary reads back, and count the write."""
    at = _now().strftime(BD_TIME)
    return [
        set_label(set_label(_bump_updated(r), AT_LABEL, at), SEQ_LABEL, str(_seq(r) + 1))
        for r in records
    ]


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
    holds. A `previous` in no `STAMP` shape is no floor at all.
    """
    held = _parse_stamp(previous)
    if held is None:
        return stamp
    floor = held.replace(microsecond=0) + dt.timedelta(seconds=1)
    mine = _parse_stamp(stamp)
    return stamp if mine is not None and mine >= floor else floor.strftime(BD_TIME)


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
        self._bd(["import", "-"], stdin=_jsonl(_prepare(records)))

    def write_raw(self, records: list[dict], local: list[dict]) -> None:
        """Records as exported, for `import`, past bd's guard without `_prepare`.

        `kraft-at:` is left alone, so the summary still times the run that wrote
        them. `updated_at` cannot be: `import` has already decided these records
        win, and bd keeps the local row on a tie or a newer guard. `local` is
        what `import` loaded, so the store is not exported a second time.
        """
        out = _past_local(records, local)
        self._bd(["import", "-"], stdin=_jsonl(out))


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


def _frozen_path(root: Path, chain_id: str) -> Path:
    return root / STATE_DIR / "chains" / f"{_checked(chain_id)}.json"


#: What a frozen chain carries once it is Kraft's V1 shape, resolved at `start`.
FORMAT = "v1"


def _chain(root: Path, chain_id: str | None) -> dict | None:
    """The chain this run walks, as `start` resolved and froze it; None when no
    chain was started here.

    `start` freezes its chain and every later verb reads that copy. Re-reading the
    library each time would mean a `--chain` given at start is forgotten by the
    next verb — and worse, that editing a library retargets a run already in
    flight. One file per chain id is what lets two runs in one directory hold
    different chains. A chain frozen before V1, per chain or in the single
    `chain.json` that came before that, is refused until `migrate` moves it.
    """
    if chain_id is None:
        return None
    path = _frozen_path(root, chain_id)
    if not path.is_file():
        path = root / STATE_DIR / "chain.json"
    if not path.is_file():
        raise SystemExit(
            f"kraft-lite: {_frozen_path(root, chain_id).relative_to(root)} is missing: it is "
            "the chain this run started with — restore it, or start a new chain."
        )
    chain = _read_json(path, "the chain")
    if not isinstance(chain, dict) or chain.get("format") != FORMAT:
        raise SystemExit(
            f"kraft-lite: chain {chain_id} was started on the pre-V1 format. "
            "Run: python3 kl.py migrate"
        )
    return chain


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


def _first_repeat(items: list):
    """The first item met a second time, or None when each is there once."""
    seen = set()
    for item in items:
        if item in seen:
            return item
        seen.add(item)
    return None


#: How a refusal says what a branch name must be.
_NOT_A_BRANCH = (
    "must be a branch name git takes: a string with no whitespace, no leading '-', "
    "no '..', and none of ~^:?*[\\"
)


def _unknown_keys(spec: dict, known: tuple[str, ...]) -> str:
    """The keys of `spec` not in `known`, sorted and joined; empty when none."""
    return ", ".join(sorted(str(k) for k in spec if k not in known))


def _refuse_slice_plan(plan: dict, mode: str) -> list[dict]:
    """The plan's slices if the whole graph is sound, else a `SystemExit` naming
    the first problem. Checked before any record is built: a half-made graph in
    the store is worse than none."""
    if mode not in MODES:
        raise SystemExit(f"kraft-lite: unknown slices mode {mode!r}. Modes: {', '.join(MODES)}")
    unknown = _unknown_keys(plan, PLAN_KEYS)
    if unknown:
        raise SystemExit(f"kraft-lite: the slice plan has unknown keys: {unknown}")
    if not plan.get("state_branch"):
        raise SystemExit("kraft-lite: the slice plan has no state_branch")
    if mode == "feature" and not plan.get("feature_branch"):
        raise SystemExit("kraft-lite: feature mode needs a feature_branch in the slice plan")
    for field in ("state_branch", "feature_branch"):
        # Both become a label value and an argument to git.
        value = plan.get(field)
        if value and not (isinstance(value, str) and BRANCH.fullmatch(value)):
            raise SystemExit(f"kraft-lite: the slice plan's {field} {_NOT_A_BRANCH}, not {value!r}")
    if mode == "main" and plan.get("feature_branch"):
        # Refused, not dropped: a plan that names one expects slices to merge into it.
        raise SystemExit(
            "kraft-lite: main mode has no feature branch: "
            "set feature_branch to null in the slice plan"
        )
    if plan.get("feature_branch") == plan["state_branch"]:
        # The state branch's checkout holds only the export; slices merged there
        # would be overwritten by the next one.
        raise SystemExit(
            f"kraft-lite: feature_branch and state_branch are both {plan['state_branch']!r}: "
            "the state branch is a branch of its own"
        )
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
        unknown = _unknown_keys(spec, SLICE_SPEC_KEYS)
        if unknown:
            named = f" ({spec['key']!r})" if isinstance(spec.get("key"), str) else ""
            raise SystemExit(f"kraft-lite: slice {position}{named} has unknown keys: {unknown}")
        for field in ("key", "title"):
            if not spec.get(field):
                raise SystemExit(f"kraft-lite: slice {position} has no {field}")
        key = spec["key"]
        if not isinstance(key, str):
            raise SystemExit(f"kraft-lite: slice {position} key must be a string")
        if not SLICE_KEY.fullmatch(key):
            raise SystemExit(
                f"kraft-lite: slice {position} key {key!r} must be 1 to 40 of a-z, 0-9, '-' "
                "and '_', starting with a letter or digit: it names a label, a directory, "
                "a file and a session title"
            )
        if not isinstance(spec["title"], str):
            raise SystemExit(f"kraft-lite: slice {position} ({key!r}) title must be a string")
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
        twice = _first_repeat(tasks)
        if twice is not None:
            raise SystemExit(f"kraft-lite: slice {position} ({key!r}) lists task {twice} twice")
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
        # Read back as two links, a slice after one slice would count as after two,
        # and lose its early start.
        twice = _first_repeat(after)
        if twice is not None:
            raise SystemExit(f"kraft-lite: slice {position} ({key!r}) comes after {twice!r} twice")
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


def materialize_slices(plan: dict, chain_id: str) -> list[dict]:
    """The run record, then one record per slice, each waiting on what it comes after.

    Nothing is returned for a plan that is not sound, so a caller writes all of
    it or none. Slice ids are random and carry no `.` (see `SLICE_OF_LABEL`); the
    `after` keys are resolved to those ids here, so the dependency links are the
    same `blocks` objects a node's are.
    """
    mode = plan.get("mode") or DEFAULT_SETTINGS["mode"]
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


def _slicing_of(record: dict, chain_id: str) -> bool:
    """Whether `record` is a run or slice record of `chain_id`, retired or not."""
    return chain_id in (label_value(record, RUN_LABEL), label_value(record, SLICE_OF_LABEL))


def _live(record: dict) -> bool:
    return label_value(record, RETIRED_LABEL) is None


def run_record(records: list[dict], chain_id: str) -> dict | None:
    """The chain's run record; None for a chain never sliced, or whose slices a
    rewind retired."""
    return next((r for r in records if label_value(r, RUN_LABEL) == chain_id and _live(r)), None)


def _run_mode(run: dict | None) -> str | None:
    """The run's mode, which `materialize_slices` writes on every run record;
    None for a chain that was never sliced."""
    return label_value(run, MODE_LABEL)


def slice_records(records: list[dict], chain_id: str) -> list[dict]:
    """The chain's slices in plan order. Sorted on the position label, because
    `bd export` returns records in no order worth relying on."""
    mine = [r for r in records if label_value(r, SLICE_OF_LABEL) == chain_id and _live(r)]
    return sorted(mine, key=lambda r: int(label_value(r, SLICE_POS_LABEL) or 0))


def slice_done(record: dict, mode: str) -> bool:
    """Whether this slice counts as done for closing the first build node. In `main`
    mode a slice ready to merge is enough: the user merges it, and the run does
    not wait on that. What may start after it is `_startable`'s to say, and it
    waits for the merge in both modes."""
    done = ("merged", "ready_to_merge") if mode == "main" else ("merged",)
    return label_value(record, SLICE_STATUS_LABEL) in done


def _merged(record: dict) -> bool:
    return label_value(record, SLICE_STATUS_LABEL) == "merged"


def _startable(slices: list[dict]) -> tuple[list[dict], list[dict]]:
    """`ready` and `early`, of one chain's slices in plan order, both still waiting.

    `ready`: every slice it comes after is merged, in both modes; in main mode
    that is the user's merge. `early`: it comes after exactly one slice, which
    is in review or ready to merge, so that slice's branch holds all it builds on
    and the controller may cut it from there. A slice after two or more has no
    early start: one branch cannot hold two slices' unmerged work."""
    by_id = {r["id"]: r for r in slices}
    ready: list[dict] = []
    early: list[dict] = []
    for record in slices:
        if label_value(record, SLICE_STATUS_LABEL) != "waiting":
            continue
        after = [by_id[d] for d in dep_ids(record) if d in by_id]
        if all(_merged(r) for r in after):
            ready.append(record)
        elif len(after) == 1 and label_value(after[0], SLICE_STATUS_LABEL) in EARLY_FROM:
            early.append(record)
    return ready, early


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
        "cut_from": label_value(record, CUT_FROM_LABEL),
        "done": slice_done(record, mode),
    }


def slices_view(records: list[dict], chain_id: str | None) -> dict:
    """The run and its slices, for the whole store: a chain that was never
    sliced reports an empty run rather than an error."""
    run = run_record(records, chain_id) if chain_id else None
    slices = slice_records(records, chain_id) if chain_id else []
    by_id = {r["id"]: r for r in slices}
    ready, early = _startable(slices)
    return {
        "mode": _run_mode(run),
        "feature_branch": label_value(run, FEATURE_BRANCH_LABEL),
        "state_branch": label_value(run, STATE_BRANCH_LABEL),
        "slices": [_slice_entry(r, by_id, _run_mode(run)) for r in slices],
        "ready": [label_value(r, SLICE_KEY_LABEL) for r in ready],
        "early": [label_value(r, SLICE_KEY_LABEL) for r in early],
        # Cut early from a slice whose branch moved since (it went back to work,
        # or recorded a new fork), directly or through another such slice: each
        # rebases onto that slice's new tip once it is in review again.
        "base_moved": [
            label_value(r, SLICE_KEY_LABEL)
            for r in slices
            if label_value(r, BASE_MOVED_LABEL)
            and label_value(r, SLICE_STATUS_LABEL) not in ("waiting", "merged")
        ],
    }


def _shape(chain: dict | None) -> kl_shape.SliceShape | None:
    """Where `chain` slices, or None for a chain without the shape: walked
    unsliced, with no slice gate."""
    if chain is None:
        return None
    try:
        return kl_shape.slice_shape(chain)
    except kl_chains.ChainError:
        return None


def slices_for_gate(
    records: list[dict], node_record: dict, chain_id: str, path: Path, root: Path, chain: dict
) -> list[dict]:
    """The records `approve --slices` writes with the approval, or a `SystemExit`.
    Slicing happens once, at the chain's slice gate (`kl_shape.slice_shape`),
    once the plan is settled: anywhere else a plan has nothing settled to be
    split from, and a second time it would replace work already handed to
    children. A child walks a slice already, and a chain without the shape is
    refused naming what it lacks."""
    epic = next(r for r in records if r["id"] == chain_id)
    if label_value(epic, SLICE_LAYER_LABEL) is not None:
        raise SystemExit(
            "kraft-lite: this run walks a slice: a slice's child chain is never sliced"
        )
    if run_record(records, chain_id) is not None:
        raise SystemExit("kraft-lite: this chain is already sliced")
    gate = kl_shape.slice_shape(chain).slice_gate
    if node_record["status"] != "blocked" or label_value(node_record, GATE_LABEL) != gate:
        raise SystemExit(f"kraft-lite: --slices is only accepted at {gate}")
    try:
        plan = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SystemExit(f"kraft-lite: cannot read the slice plan {path}: {error}") from error
    if not isinstance(plan, dict):
        raise SystemExit(f"kraft-lite: the slice plan {path} is not a JSON object")
    if not plan.get("mode"):
        # Read only when the plan names no mode: a slices.json that cannot be read
        # has nothing to say to a plan that already decided.
        plan = dict(plan, mode=settings(root)["mode"])
    return materialize_slices(plan, chain_id)


def _by_age(by_chain: dict[str, list[dict]]) -> list[str]:
    """Chain ids oldest first, by the epic's timestamp rather than by store order.

    `bd export` promises neither insertion nor sorted order — `ordered()` says so
    a few functions up — so reading the last record as the newest is a coin flip.
    The epic is closed at `start` and never written again, so its `updated_at` is
    the run's start time. Ties fall back to store order, which is what a sort this
    stable does anyway.
    """

    def stamp(chain_id: str) -> dt.datetime:
        epic = next((r for r in by_chain[chain_id] if label_value(r, CHAIN_LABEL)), None)
        return _when((epic or {}).get("updated_at"))

    return sorted(by_chain, key=stamp)


def rewind(
    records: list[dict],
    from_node: str,
    note: str,
    *,
    through: str | None = None,
    prefix: str = NOTE_PREFIX,
) -> list[dict]:
    """Reopen `from_node` and every node after it (up to `through`, when given),
    carrying the reason as a `prefix` line on the first.

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
    end = ids.index(through) + 1 if through in ids else len(walk)
    out = []
    for offset, record in enumerate(walk[ids.index(from_node) : end]):
        # Banked before the stamp that opens it is stripped below. `--from-node` is
        # how the last gate sends work back, so the wait it interrupts is usually
        # the longest one in the run - dropping it here loses exactly that.
        record = bank_wait(record)
        # `WAITED_LABEL` is not dropped on purpose: those hours were spent,
        # whatever the redo goes on to cost.
        for label in (GATE_LABEL, ATTEMPT_LABEL, BLOCKED_LABEL, ON_FAILURE_RAN_LABEL):
            record = drop_label(record, label)
        reopened = dict(record, status="open")
        if offset == 0:
            description = record.get("description", "")
            reopened["description"] = f"{description}\n{prefix}{note}"
        out.append(reopened)
    return out


def rewind_slices(
    everything: list[dict], chain_id: str, tail: list[dict], chain: dict
) -> list[dict]:
    """What a rewind that reopens `tail` writes to the run's slices, or a
    `SystemExit`. Rewound to the slice gate or before it, the plan or its
    slicing is redone, so the run and its slices are retired: closed, and
    labelled so that nothing reads them as the chain's. The chain is then sliced
    again at the slice gate. Rewound to the first build node, or to a node
    between the slice gate and it, every slice done would close the build at
    once with nothing redone, so that is refused until a slice is reopened.
    Rewound past it, the slices stand."""
    run = run_record(everything, chain_id)
    shape = _shape(chain)
    if run is None or shape is None:
        return []
    mode = _run_mode(run)
    nodes = [label_value(r, NODE_LABEL) for r in tail]
    if shape.slice_gate in nodes:
        slices = slice_records(everything, chain_id)
        retired = [dict(r, status="closed") for r in [run, *slices]]
        return [set_label(r, RETIRED_LABEL, nodes[0]) for r in retired]
    if shape.first_build in nodes and all(
        slice_done(r, mode) for r in slice_records(everything, chain_id)
    ):
        raise SystemExit(
            f"kraft-lite: every slice is done, so {shape.first_build} would close at once with "
            "nothing redone. Reopen the slices to redo first (slice-set --slice KEY --status "
            "running), or rewind further back to slice the chain again"
        )
    return []


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
    start, end = _parse_stamp(earlier), _parse_stamp(later)
    if start is None or end is None:
        return None
    return int((end - start).total_seconds())


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
    # Sorted as times, each kept beside the stamp as written: a chain timed by
    # bd's `updated_at` can hold stamps in more than one `STAMP` form.
    order = {r["id"]: i for i, r in enumerate([epic, *walk])}
    events = sorted((_when(x), order.get(r["id"], -1), x) for r in records if (x := _at(r)))

    def since(record: dict) -> str | None:
        closed_at = _at(record)
        if not closed_at:
            return _at(epic)
        key = (_when(closed_at), order.get(record["id"], -1))
        # `< key` excludes the record's own entry without a separate id test.
        earlier = [e for e in events if e[:2] < key]
        return earlier[-1][2] if earlier else _at(epic)

    rows = []
    for record in walk:
        node_id = label_value(record, NODE_LABEL)
        definition = definitions.get(node_id, {})
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
                "cap": _cap(definition),
                # The gate node's own id: a gate is a node of its own in V1.
                "gate": node_id if definition.get("kind") == "gate" else None,
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
    finished = max(closed_stamps, key=_when, default=None) if node_record is None else None
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


def _action(chain: dict | None, node_id: str | None, mode: str) -> str:
    """What `node_id` means in a run sliced in `mode` (`kl_shape.sliced_action`);
    `run` once the walk is done, or for a chain that lost its shape."""
    shape = _shape(chain)
    if shape is None or node_id is None:
        return "run"
    return kl_shape.sliced_action(chain, shape, node_id, mode)


def _sliced(
    everything: list[dict], epic: dict | None, node_id: str | None, chain: dict | None
) -> dict | None:
    """`null` for a chain that was never sliced, else the run's mode and what
    the current node means in it. Read from the whole store: `_ours` returns only
    the epic and its nodes, and the run record is neither."""
    run = run_record(everything, epic["id"]) if epic else None
    if run is None:
        return None
    mode = _run_mode(run)
    return {"mode": mode, "action": _action(chain, node_id, mode)}


def _cap(definition: dict) -> int | None:
    """A node's fix-loop cap: the loop's `max_attempts`, else Kraft's default;
    None for a node with no fix loop."""
    loop = definition.get("fix_loop")
    return (loop.get("max_attempts") or DEFAULT_CAP) if loop else None


def _definition(root: Path, chain: dict, chain_id: str | None, node_id: str | None) -> dict:
    """The frozen chain's node `node_id`, or a `SystemExit` naming the file."""
    definition = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if definition is None:
        # Reporting no tasks here is worse than failing: the `next` skill's
        # contract is "no tasks, so close and move on", and the chain walks to
        # `done` having run nothing at all.
        raise SystemExit(
            f"kraft-lite: node {node_id!r} is not in this chain. "
            f"{_frozen_path(root, chain_id).relative_to(root) if chain_id else 'the frozen chain'}"
            " is the chain this run started with — restore it, or start a new chain."
        )
    return definition


def _dispatcher(root: Path, slice_layer: bool = False):
    """How `state` turns a frozen task into the one instruction the walk
    follows: this repo's bindings over the defaults, with the skills installed
    in this harness, and for a child walking a slice the slice layer over both.
    Read per call, so a binding edited mid-run counts from the next `state`."""
    bindings = kl_bindings.read_bindings(root)
    installed = set(_installed_skills(root))

    def dispatch(task: dict) -> dict:
        return kl_bindings.dispatch(task, bindings, installed.__contains__, slice_layer=slice_layer)

    return dispatch


def _dispatched(container: dict | None, dispatch) -> list[dict] | None:
    """A node's, or a handler's, steps with every task dispatched."""
    if container is None:
        return None
    return [
        {"id": step["id"], "tasks": [dispatch(task) for task in step["tasks"]]}
        for step in container["steps"]
    ]


def _artifact(records: list[dict], kind: str | None) -> str | None:
    """The newest file of `kind` any record of the run holds: the one a gate on
    that kind presents. A rewind leaves older ones in place, so newest wins,
    with walk order breaking a tie inside one second."""
    if kind is None:
        return None
    prefix = f"{ARTIFACT_LABEL}{kind}="
    holding = [
        (_when(_at(record)), position, label_value(record, prefix))
        for position, record in enumerate(ordered(records))
        if label_value(record, prefix) is not None
    ]
    return max(holding)[2] if holding else None


#: Every key `state` reports, so an unstarted chain, a finished one and a gate
#: node hand a caller the same shape.
STATE_KEYS = (
    "chain_id",
    "node",
    "kind",
    "status",
    "attempt",
    "cap",
    "note",
    "backend",
    "sliced",
    "steps",
    "on_failure",
    "on_failure_ran",
    "fix_loop",
    "on_base_changed",
    "gate",
)
#: The lines `state` reports as the node's note: why it was sent back.
RESTART_PREFIX = "Base changed: "


def _note(description: str) -> str:
    """Every rejection or restart line, from the first one on."""
    lines = description.splitlines()
    first = next(
        (i for i, line in enumerate(lines) if line.startswith((NOTE_PREFIX, RESTART_PREFIX))),
        None,
    )
    return "\n".join(lines[first:]) if first is not None else ""


def _state(
    root: Path, chain: dict | None, everything: list[dict], chain_id: str | None = None
) -> dict:
    records = _ours(everything, chain_id)
    node_record = current(records)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    state = dict.fromkeys(STATE_KEYS)
    state.update(
        chain_id=epic["id"] if epic else None,
        backend=backend(root),
        steps=[],
        on_failure_ran=False,
        attempt=0,
        note="",
    )
    if node_record is None:
        # "done" and "never started" are different answers. Reporting both as
        # done tells the `next` skill a chain that does not exist has finished.
        state.update(
            status="done" if epic else "unstarted", sliced=_sliced(everything, epic, None, chain)
        )
        return state
    node_id = label_value(node_record, NODE_LABEL)
    definition = _definition(root, chain, state["chain_id"], node_id)
    state.update(
        node=node_id,
        kind=definition["kind"],
        status=node_record["status"],
        attempt=attempts(node_record),
        note=_note(node_record.get("description", "")),
        sliced=_sliced(everything, epic, node_id, chain),
    )
    if definition["kind"] == "gate":
        state["gate"] = {
            "message": definition["message"],
            "artifact": definition["artifact"],
            "artifact_path": _artifact(records, definition["artifact"]),
            "reject_to": definition["reject_to"],
            "chain_finalized": definition["chain_finalized"],
            "slice_gate": node_id == getattr(_shape(chain), "slice_gate", None),
        }
        return state
    dispatch = _dispatcher(root, label_value(epic, SLICE_LAYER_LABEL) is not None)
    loop, base = definition["fix_loop"], definition["on_base_changed"]
    state.update(
        cap=_cap(definition),
        steps=_dispatched(definition, dispatch),
        on_failure={"steps": _dispatched(definition["on_failure"], dispatch)}
        if definition["on_failure"]
        else None,
        on_failure_ran=label_value(node_record, ON_FAILURE_RAN_LABEL) is not None,
        fix_loop={
            "steps": _dispatched(loop, dispatch),
            "judge": dispatch(loop["judge"]) if loop["judge"] else None,
        }
        if loop
        else None,
        on_base_changed={
            "restart_from": base["restart_from"],
            "restarts": int(label_value(node_record, RESTARTS_LABEL) or 0),
            "cap": RESTART_CAP,
        }
        if base
        else None,
    )
    return state


def _slice_by_key(everything: list[dict], chain_id: str | None, key: str) -> dict:
    """The slice `key` names, or a `SystemExit` listing the ones there are."""
    slices = slice_records(everything, chain_id) if chain_id else []
    by_key = {label_value(r, SLICE_KEY_LABEL): r for r in slices}
    if key not in by_key:
        raise SystemExit(
            f"kraft-lite: no slice {key!r} in this chain. Slices: "
            + (", ".join(by_key) or "(none)")
        )
    return by_key[key]


def _named(records: list[dict]) -> str:
    """Slices as a refusal names them: `a (waiting), b (in_review)`."""
    return ", ".join(
        f"{label_value(r, SLICE_KEY_LABEL)} ({label_value(r, SLICE_STATUS_LABEL)})" for r in records
    )


def _refuse_slice_move(slices: list[dict], record: dict, status: str) -> None:
    """A `SystemExit` for a move a slice cannot make. A waiting slice starts only
    from `ready` or `early`, and what was never started cannot be reviewed or
    merged. A slice merges only after every slice it comes after: one cut early
    from another's branch holds that slice's work, unmerged. Every other move is
    allowed, the same status again included: a started slice has left `ready`, and
    a call overturned at the wrap-up reopens a merged one."""
    current = label_value(record, SLICE_STATUS_LABEL)
    key = label_value(record, SLICE_KEY_LABEL)
    by_id = {r["id"]: r for r in slices}
    if status == "merged" and current not in ("merged", "waiting"):
        left = [by_id[d] for d in dep_ids(record) if d in by_id and not _merged(by_id[d])]
        if left:
            raise SystemExit(
                f"kraft-lite: slice {key!r} merges after every slice it comes after; "
                f"not merged: {_named(left)}"
            )
    if current != "waiting" or status == "waiting":
        return
    if status not in ("starting", "running"):
        raise SystemExit(
            f"kraft-lite: slice {key!r} was never started: it is waiting, so it cannot be "
            f"{status}. Start it with --status starting first"
        )
    ready, early = _startable(slices)
    if record["id"] in {r["id"] for r in [*ready, *early]}:
        return
    after = _named([by_id[d] for d in dep_ids(record) if d in by_id])
    raise SystemExit(
        f"kraft-lite: slice {key!r} is not in `ready` or `early`: it comes after {after}. "
        "It starts once all of those are merged, or early from the one slice it comes "
        "after once that is in review or ready to merge"
    )


def _refuse_close_before_slices(
    everything: list[dict], chain_id: str, node_record: dict, chain: dict
) -> None:
    """A `SystemExit` for closing a sliced run's node before the slices it waits
    on, by `close` or by `approve`. `run_slices` waits for every slice done for the
    run's mode: closing it moves the walk on to what checks seams between
    slices that are not all there yet. `await_user_merges` waits for every slice
    merged: the user's merges are all it waits on, and closing it first finishes
    the run before any of them."""
    run = run_record(everything, chain_id)
    if run is None:
        return
    mode = _run_mode(run)
    node = label_value(node_record, NODE_LABEL)
    action = _action(chain, node, mode)
    slices = slice_records(everything, chain_id)
    if action == "run_slices":
        left = [r for r in slices if not slice_done(r, mode)]
        done, short = ("merged or ready to merge" if mode == "main" else "merged"), "not done"
    elif action == "await_user_merges":
        left = [r for r in slices if not _merged(r)]
        done, short = "merged", "not merged"
    else:
        return
    if left:
        raise SystemExit(
            f"kraft-lite: {node} closes once every slice is {done}; {short}: {_named(left)}"
        )


def _cut(slices: list[dict], record: dict) -> dict:
    """A waiting slice as it starts: cut from the target from `ready`, or from
    the one slice it comes after from `early`, which `CUT_FROM_LABEL` records.
    `_refuse_slice_move` has already said it is in one of them."""
    record = drop_label(record, BASE_MOVED_LABEL)
    ready, _ = _startable(slices)
    if record["id"] in {r["id"] for r in ready}:
        return drop_label(record, CUT_FROM_LABEL)
    by_id = {r["id"]: r for r in slices}
    [after] = [by_id[d] for d in dep_ids(record) if d in by_id]
    return set_label(record, CUT_FROM_LABEL, label_value(after, SLICE_KEY_LABEL))


def _rebased(slices: list[dict], record: dict) -> dict:
    """A slice that records a new fork has rebased: onto the new tip of the slice
    it was cut from, or once that slice is merged, onto the target."""
    record = drop_label(record, BASE_MOVED_LABEL)
    by_key = {label_value(r, SLICE_KEY_LABEL): r for r in slices}
    parent = by_key.get(label_value(record, CUT_FROM_LABEL))
    return drop_label(record, CUT_FROM_LABEL) if parent is None or _merged(parent) else record


def _moved_base(others: list[dict], key: str) -> list[dict]:
    """The slices cut early from `key`, flagged, as its branch moves: back to
    work from its review, or rebased. Each was cut from a tip its branch moves
    past, and so, in turn, is each slice cut early from one of those: it rebases
    too. Each is flagged with the slice it was cut from. `slices` lists only
    those started and unmerged."""
    flagged: list[dict] = []
    moved = [key]
    while moved:
        cut = [r for r in others if label_value(r, CUT_FROM_LABEL) == moved[0]]
        cut = [r for r in cut if r["id"] not in {f["id"] for f in flagged}]
        flagged += [set_label(r, BASE_MOVED_LABEL, moved[0]) for r in cut]
        moved = [*moved[1:], *(label_value(r, SLICE_KEY_LABEL) for r in cut)]
    return flagged


def _refuse_slice_fields(args) -> None:
    """A `SystemExit` for a session, branch or fork `slice-set` cannot record.
    Each becomes a label value, and the branch and fork arguments to git. A new
    fork is read as a rebase, so one that names no commit would clear the flags
    a real rebase clears."""
    if args.session is not None and not SESSION.fullmatch(args.session):
        raise SystemExit(
            f"kraft-lite: --session must be a session id, with no whitespace, not {args.session!r}"
        )
    if args.branch is not None and not BRANCH.fullmatch(args.branch):
        raise SystemExit(f"kraft-lite: --branch {_NOT_A_BRANCH}, not {args.branch!r}")
    if args.fork is not None and not FORK.fullmatch(args.fork):
        raise SystemExit(
            f"kraft-lite: --fork must be a commit id, 4 to 64 hex digits, not {args.fork!r}"
        )


def _slice_set(store, everything: list[dict], chain_id: str | None, args) -> dict:
    """Move one slice: its status, the session running it, its branch, its fork
    point. `merged` closes the record too, so a store that shows only open work
    stops showing it, and the run record closes with the last slice to merge.
    Main mode's slices reach `merged` too, as the `next` skill records the
    user's merges, so this is the one place a finished run is seen in both."""
    slices = slice_records(everything, chain_id) if chain_id else []
    record = _slice_by_key(everything, chain_id, args.slice)
    before = record
    if args.status is not None and args.status not in SLICE_STATUSES:
        raise SystemExit(
            f"kraft-lite: unknown slice status {args.status!r}. "
            f"Statuses: {', '.join(SLICE_STATUSES)}"
        )
    _refuse_slice_fields(args)
    was = label_value(record, SLICE_STATUS_LABEL)
    if args.status is not None:
        _refuse_slice_move(slices, record, args.status)
        if was == "waiting" and args.status != "waiting":
            record = _cut(slices, record)
        record = set_label(record, SLICE_STATUS_LABEL, args.status)
        record = dict(record, status="closed" if args.status == "merged" else "open")
    for prefix, value in (
        (SESSION_LABEL, args.session),
        (BRANCH_LABEL, args.branch),
        (FORK_LABEL, args.fork),
    ):
        if value is not None:
            record = set_label(record, prefix, value)
    if args.fork is not None:
        record = _rebased(slices, record)
    # A move that changes nothing is not written: every write counts toward
    # `kraft-seq:`, and `import` would rank a no-op over a real write elsewhere.
    # `set_label` moves a label to the end, so labels compare as a set.
    same = dict(record, labels=sorted(record["labels"])) == dict(
        before, labels=sorted(before["labels"])
    )
    writes = [] if same else [record]
    run = run_record(everything, chain_id)
    others = [r for r in slices if r["id"] != record["id"]]
    # Its branch moves: back to work from its review, or onto a new fork.
    if (was in EARLY_FROM and args.status in ("waiting", "starting", "running")) or (
        args.fork is not None and args.fork != label_value(before, FORK_LABEL)
    ):
        writes += _moved_base(others, args.slice)
    finished = all(_merged(r) for r in [*others, record])
    # Reopened too, when a call overturned at the wrap-up puts a slice back to work.
    if run is not None and run["status"] != ("closed" if finished else "open"):
        writes.append(dict(run, status="closed" if finished else "open"))
    if writes:
        store.write(writes)
    by_id = {r["id"]: r for r in slices}
    return _slice_entry(record, by_id, _run_mode(run))


def _decisions_path(root: Path, chain_id: str) -> Path:
    """The decisions log. Not the issue store, even with bd: what the controller
    decided for the human is a log for the human to read, not work to track."""
    return root / STATE_DIR / "decisions" / f"{_checked(chain_id)}.jsonl"


def _decide(root: Path, everything: list[dict], chain_id: str, args) -> dict:
    if args.slice is not None:
        _slice_by_key(everything, chain_id, args.slice)
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
        # The count of this entry's writes, which `import` ranks copies by.
        "seq": 1,
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
    # `.get`: an entry is a person's JSON once it travels, and an import takes it
    # as it comes. One without `held` was never held.
    if not entries[args.decision].get("held", False):
        # Answering it again would put the user's name on the controller's call,
        # or overwrite what the user already said.
        raise SystemExit(
            f"kraft-lite: decision {args.decision} is not held: "
            f"{entries[args.decision].get('answered_by') or 'someone'} answered it. "
            "`answer` is for a held question"
        )
    entry = dict(
        entries[args.decision],
        answer=args.answer,
        held=False,
        answered_by="user",
        answered_at=_now().strftime(BD_TIME),
        # A new answer is news, whether or not the question was reported held.
        reported=False,
        seq=_decision_seq(entries[args.decision]) + 1,
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
        entries = [e for e in entries if e.get("held", False)]
    if args.mark and entries:
        append_jsonl(path, [dict(e, reported=True, seq=_decision_seq(e) + 1) for e in entries])
    return entries


EXPORT_RECORDS = "records.jsonl"
EXPORT_DECISIONS = "decisions.jsonl"
#: The run's frozen chain, beside its records: what an import resumes it on,
#: and what a child starts from (`start --slice-of`).
EXPORT_CHAIN = "chain.json"


def _export(root: Path, everything: list[dict], chain_id: str, to: Path) -> dict:
    """The run's state as three files for the state branch: its records as the
    store holds them, its frozen chain, and its decisions log. Nothing else
    is written, and nothing else in `to` is touched, because `to` is usually a
    checkout of that branch. Specs and plans travel by message, never here."""
    # Retired ones too: an import is what retires them in a checkout where they are live.
    records = [
        *chain_index(everything)[chain_id],
        *(r for r in everything if _slicing_of(r, chain_id)),
    ]
    to.mkdir(parents=True, exist_ok=True)
    (to / EXPORT_CHAIN).write_text(json.dumps(_chain(root, chain_id), indent=2) + "\n")
    (to / EXPORT_RECORDS).write_text(_jsonl(records))
    decisions = read_jsonl(_decisions_path(root, chain_id))
    (to / EXPORT_DECISIONS).write_text(_jsonl(decisions))
    return {
        "chain_id": chain_id,
        "to": str(to),
        "records": len(records),
        "decisions": len(decisions),
    }


def _record_rank(record: dict) -> tuple:
    """How `import` ranks two copies of one record: the higher wins. First the
    count of the copy's writes, which no clock can skew; then `kraft-at:`, then
    `updated_at`, each read as a time (`_when`), because as text `10:00:00Z`
    sorts after `10:00:00.5Z`."""
    return (_seq(record), _when(label_value(record, AT_LABEL)), _when(record.get("updated_at")))


def _decision_rank(entry: dict) -> tuple:
    """How `import` ranks two copies of one decision: the higher wins. First the
    count of the copy's writes; on a tie, an answer over a held question, so a
    held copy never takes back the user's answer; then `answered_at or at`, read
    as a time, which is all an entry from before the count has; then a reported
    copy over an unreported one."""
    return (
        _decision_seq(entry),
        not entry.get("held"),
        _when(entry.get("answered_at") or entry.get("at")),
        bool(entry.get("reported")),
    )


def _import_records(local: list[dict], exported: list[dict]) -> tuple[list[dict], int]:
    """Which exported records to write, and how many local ones win instead.

    A record is written only where the local copy is missing or ranks lower
    (`_record_rank`). A record without `kraft-seq:` counts as 0. Two machines
    that each wrote once since the last import tie on it, and then `kraft-at:`
    decides, where no stamp is oldest. Two writes inside one second tie on that too, so
    `updated_at` breaks the tie: every write moves it strictly forward
    (`_bump_updated`), and `kraft-at:` is not moved, because the summary times the
    run by it. A tie on all three keeps the local copy. The epic is written only
    where it is missing: it is never written after `start`, and its stamp is the
    run's start time.
    """
    held = {r["id"]: r for r in local}
    write: list[dict] = []
    kept = 0
    for record in exported:
        mine = held.get(record["id"])
        if mine is None or (
            label_value(record, CHAIN_LABEL) is None and _record_rank(mine) < _record_rank(record)
        ):
            write.append(record)
        else:
            kept += 1
    return write, kept


def _decision_seq(entry: dict) -> int:
    """How many times this decision was written; an entry from before the count, 0."""
    return int(entry.get("seq") or 0)


def _merge_decisions(local: list[dict], exported: list[dict]) -> tuple[list[dict], int]:
    """Which exported decisions to append, and how many local ones win instead:
    those missing locally, or ranking higher (`_decision_rank`) than the local
    copy. A tie on everything keeps the local copy."""
    held = {e["id"]: e for e in local}
    write = [
        e
        for e in exported
        if e["id"] not in held or _decision_rank(e) > _decision_rank(held[e["id"]])
    ]
    return write, len(exported) - len(write)


def _of_chain(record: dict, chain_id: str) -> bool:
    """Whether `record` is one an export of `chain_id` carries: its epic, a
    node, its run record or a slice, each under the id it is minted with. A
    label alone is not enough: under another id, the record would land on one
    of the directory's own."""
    record_id = record["id"]
    if record_id == chain_id or (
        record_id.startswith(f"{chain_id}.") and len(record_id) > len(chain_id) + 1
    ):
        return True
    return bool(
        (label_value(record, RUN_LABEL) == chain_id and RUN_ID.fullmatch(record_id))
        or (label_value(record, SLICE_OF_LABEL) == chain_id and SLICE_ID.fullmatch(record_id))
    )


def _exported_chain(path: Path, written: str, missing: str) -> dict:
    """The V1 frozen chain at `path`, or a `SystemExit`: `missing` when there is
    no file, and the older-Kraft-Lite refusal, which `written` opens, for one
    that is not V1."""
    if not path.is_file():
        raise SystemExit(f"kraft-lite: {missing}")
    try:
        chain = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SystemExit(f"kraft-lite: cannot read the chain {path}: {error}") from error
    if not isinstance(chain, dict) or chain.get("format") != FORMAT:
        raise SystemExit(f"kraft-lite: {written} by an older Kraft Lite: not a V1 chain")
    flaw = _chain_flaw(chain)
    if flaw:
        raise SystemExit(f"kraft-lite: {path} is not a usable V1 chain: {flaw}")
    return chain


def _chain_flaw(chain: dict) -> str | None:
    """What a chain that says it is V1 lacks of what the walk reads from every
    node (an id, a kind, and for an exec node its steps), or None. A file from
    outside this process - an export, a hand-edited copy - is checked here so a
    missing key is a refusal, not a `KeyError` in the middle of a verb."""
    nodes = chain.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return "it has no list of nodes"
    for number, node in enumerate(nodes, 1):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            return f"node {number} has no id"
        if node.get("kind") not in ("exec", "gate"):
            return f"node {node['id']!r} has no kind (exec or gate)"
        if node["kind"] == "exec" and not (
            isinstance(node.get("steps"), list)
            and all(isinstance(s, dict) and isinstance(s.get("tasks"), list) for s in node["steps"])
        ):
            return f"exec node {node['id']!r} has no steps"
    return None


def _read_json(path: Path, what: str) -> object:
    """The JSON in `path`, or a `SystemExit` saying it cannot be read."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SystemExit(f"kraft-lite: cannot read {what} {path}: {error}") from error


def _import(root: Path, store, source: Path) -> dict:
    """Resume, or catch up, from an export. Dispatched before `_ours`: the
    export names its own chain, and the directory may hold others.

    Records are written through `write_raw`, never `store.write`: `_prepare`
    would restamp every one with the time of the import, and the summary would
    report a run that started, and finished every node, just now."""
    # Refused here, not read as empty: a mistyped path is not an export of nothing.
    if not (source / EXPORT_RECORDS).is_file():
        raise SystemExit(f"kraft-lite: no export at {source}: {source / EXPORT_RECORDS} is missing")
    # Read whole, and checked as `import` will rank them, before anything is written.
    exported = read_jsonl(source / EXPORT_RECORDS, _record_rank)
    exported_decisions = read_jsonl(source / EXPORT_DECISIONS, _decision_rank)
    epics = [r["id"] for r in exported if label_value(r, CHAIN_LABEL)]
    if len(epics) != 1:
        raise SystemExit(
            f"kraft-lite: {source / EXPORT_RECORDS} is not an export of one chain: "
            f"it holds {len(epics)} chain epics ({', '.join(epics) or 'none'})"
        )
    chain_id = epics[0]
    # Built first: it is what refuses an id `start` never minted, before any read.
    frozen = _frozen_path(root, chain_id)
    # Upserted as they come, so anything not this chain's would land on the
    # directory's own records: another run's, or bd's other issues.
    strays = [r["id"] for r in exported if not _of_chain(r, chain_id)]
    if strays:
        raise SystemExit(
            f"kraft-lite: {source / EXPORT_RECORDS} holds records that are not chain "
            f"{chain_id}'s: {', '.join(strays)}"
        )
    chain = _exported_chain(
        source / EXPORT_CHAIN,
        f"{source} was exported",
        f"{source} was exported by an older Kraft Lite: no {EXPORT_CHAIN}",
    )
    # A run's chain never changes after `start`: an export carrying another is
    # not this run's to resume, and the frozen copy here is never replaced.
    if frozen.is_file() and _read_json(frozen, "the chain") != chain:
        raise SystemExit(
            f"kraft-lite: {source / EXPORT_CHAIN} is not the chain {chain_id} started with, "
            f"which is {frozen}. A run's chain never changes after start"
        )
    if not frozen.is_file():
        _freeze_chain(root, chain, chain_id)
    local = store.load()
    write, kept = _import_records(local, exported)
    if write:
        store.write_raw(write, local)
    path = _decisions_path(root, chain_id)
    decided, kept_decisions = _merge_decisions(read_jsonl(path), exported_decisions)
    if decided:
        append_jsonl(path, decided)
    return {
        "chain_id": chain_id,
        "imported": len(write) + len(decided),
        "kept_local": kept + kept_decisions,
    }


#: What `start --spec P --plan P` takes, as Kraft's intake does: each drops the
#: nodes that would produce or approve its kind (`kl_chains.trim`).
ATTACHMENT_KINDS = ("spec", "plan")


def _artifact_path(root: Path, path: Path) -> str:
    """`path` as a label records it: relative to the repo root when inside it,
    so a verb run from a subdirectory, or an import elsewhere, reads the same
    file; absolute otherwise."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def _resolve_chain(root: Path, chain: str | Path | None) -> dict:
    """`chain` resolved as a library id or, ending in `.yaml`/`.yml` or holding a
    `/`, as a chain file; the default chain when it is None. The rule `start` and
    `detect` share for their `--chain`."""
    ref = None if chain is None else str(chain)
    if ref is not None and (ref.endswith((".yaml", ".yml")) or "/" in ref):
        return kl_chains.resolve(root, os.environ, path=Path(ref))
    return kl_chains.resolve(root, os.environ, ref=ref)


def _start_chain(root: Path, chain: str | None, attached: dict[str, Path]) -> dict:
    """The chain `start` freezes: `chain` resolved by `_resolve_chain`; then
    trimmed for the attached kinds. Every task is dispatched once before anything is written, so
    a bindings file Lite cannot use, or a task it has no instruction for, stops
    `start` rather than the walk at that node."""
    for kind, path in attached.items():
        if not path.is_file():
            raise SystemExit(f"kraft-lite: --{kind} {path}: no such file")
    resolved = kl_chains.trim(_resolve_chain(root, chain), set(attached))
    dispatch = _dispatcher(root)
    for task in _dispatched_tasks(resolved):
        dispatch(task)
    return resolved


def _start_records(chain: dict, title: str, chain_id: str) -> list[dict]:
    """A new run's records: `materialize`, with the epic closed. The epic is a
    container, not a step, so the walk starts at the first real node."""
    records = materialize(chain, title, chain_id)
    records[0]["status"] = "closed"
    return records


def _start_slice(root: Path, path: Path, chain: str | None, attached: dict) -> dict:
    """The chain a child freezes for its slice: the child chain of the
    controller's frozen chain at `path` (its export's `chain.json`), never
    resolved from templates, so the child walks exactly what the controller
    froze. Every task is dispatched with the slice layer before anything is
    written, as `_start_chain` does."""
    if chain is not None or attached:
        raise SystemExit("kraft-lite: --slice-of takes no --chain, --spec or --plan")
    parent = _exported_chain(
        path, f"{path} was written", f"cannot read the chain {path}: no such file"
    )
    child = kl_shape.child_chain(parent)
    dispatch = _dispatcher(root, slice_layer=True)
    for task in _dispatched_tasks(child):
        dispatch(task)
    return child


def _exec_before(chain: dict, node_id: str) -> str:
    """The nearest exec node before `node_id`: where a gate with no `reject_to`
    sends its rejection."""
    ids = [n["id"] for n in chain["nodes"]]
    earlier = [n for n in chain["nodes"][: ids.index(node_id)] if n["kind"] == "exec"]
    if not earlier:
        raise SystemExit(
            f"kraft-lite: {node_id} has no exec node before it to send work back to. "
            "Pass --from-node, or approve it"
        )
    return earlier[-1]["id"]


def _restart(
    everything: list[dict],
    records: list[dict],
    chain_id: str,
    node_record: dict,
    definition: dict,
    chain: dict,
) -> list[dict]:
    """What `restart` writes: the walk sent back to `on_base_changed.restart_from`
    through this node, their attempts reset, this node's restarts counted. The
    node did not fail - a step moved its base - so the note is no rejection."""
    node_id = definition["id"]
    base = definition.get("on_base_changed")
    if not base:
        raise SystemExit(f"kraft-lite: {node_id} has no on_base_changed to restart from")
    restarts = int(label_value(node_record, RESTARTS_LABEL) or 0)
    if restarts >= RESTART_CAP:
        raise SystemExit(
            f"kraft-lite: {node_id} is over its restart cap: it restarted {restarts} times "
            f"(cap {RESTART_CAP}). Stop and ask the human, as over_cap"
        )
    target = base["restart_from"]
    tail = rewind(
        records, target, f"restarted from {target}", through=node_id, prefix=RESTART_PREFIX
    )
    tail = [
        set_label(r, RESTARTS_LABEL, str(restarts + 1))
        if label_value(r, NODE_LABEL) == node_id
        else r
        for r in tail
    ]
    return [*tail, *rewind_slices(everything, chain_id, tail, chain)]


#: The verbs that answer beside a pre-V1 registry no bindings file replaced yet:
#: listing chains, what `init` reads to write the bindings, and `migrate` itself.
BEFORE_MIGRATE = frozenset({"chains", "detect", "migrate"})


def _legacy_registry(root: Path) -> bool:
    """A `registry.yaml` from before V1 with no `bindings.yaml` written from it."""
    state = root / STATE_DIR
    return (state / "registry.yaml").is_file() and not (root / kl_bindings.BINDINGS).is_file()


#: What each verb needs before it runs, declared once for `main`:
#: - `chain`: it reads or writes one chain's state, so it takes `--chain-id` and
#:   resolves that chain through `_ours`, refusing a run frozen before V1.
#:   `start` mints its own id, and `detect`, `chains`, `settings` and `import`
#:   (whose export names its chain) take none.
#: - `open_node`: it acts on the chain's open node, so a finished chain refuses
#:   it. The rest answer after the walk, because wrap-up and status need them.
#: - `log`: it reads or writes the decisions log or the export, so it needs a
#:   chain that was started.
VERBS: dict[str, frozenset[str]] = {
    "start": frozenset(),
    "detect": frozenset(),
    "chains": frozenset(),
    "settings": frozenset(),
    "import": frozenset(),
    "migrate": frozenset(),
    "state": frozenset({"chain"}),
    "summary": frozenset({"chain"}),
    "slices": frozenset({"chain"}),
    "slice-set": frozenset({"chain"}),
    "decide": frozenset({"chain", "log"}),
    "answer": frozenset({"chain", "log"}),
    "decisions": frozenset({"chain", "log"}),
    "export": frozenset({"chain", "log"}),
    "close": frozenset({"chain", "open_node"}),
    "approve": frozenset({"chain", "open_node"}),
    "attempt": frozenset({"chain", "open_node"}),
    "gate": frozenset({"chain", "open_node"}),
    "reject": frozenset({"chain", "open_node"}),
    "produced": frozenset({"chain", "open_node"}),
    "mark": frozenset({"chain", "open_node"}),
    "restart": frozenset({"chain", "open_node"}),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kl")
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    # A chain id from the libraries (`default`, `repo:ship`), or a chain file.
    start.add_argument("--chain", default=None)
    for kind in ATTACHMENT_KINDS:
        start.add_argument(f"--{kind}", type=Path, default=None)
    # A child's start: the controller's exported chain, which it slices from.
    start.add_argument("--slice-of", type=Path, default=None)
    detect_parser = sub.add_parser("detect")
    # A chain id from the libraries or a chain file, as for `start`.
    detect_parser.add_argument("--chain", default=None)
    sub.add_parser("chains").add_argument("--available", action="store_true")
    sub.add_parser("settings")
    produced = sub.add_parser("produced")
    produced.add_argument("--kind", required=True)
    produced.add_argument("--path", required=True)
    sub.add_parser("mark").add_argument("--on-failure-ran", action="store_true", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--note", required=True)
    # The last gate rejects work that an earlier node produced: its own hook only
    # presents it. Without this the gate is approve-or-stall.
    reject.add_argument("--from-node", default=None)
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
    sub.add_parser("import").add_argument("--from", dest="source", type=Path, required=True)
    # The verbs with no arguments of their own.
    for name in VERBS:
        if name not in sub.choices:
            sub.add_parser(name)
    sub.choices["approve"].add_argument("--slices", type=Path, default=None)
    for name, needs in VERBS.items():
        if "chain" in needs:
            sub.choices[name].add_argument("--chain-id", default=None)

    args = parser.parse_args(argv)
    root = repo_root()
    chain_id = getattr(args, "chain_id", None)

    if args.verb not in BEFORE_MIGRATE and _legacy_registry(root):
        raise SystemExit(
            f"kraft-lite: {STATE_DIR}/registry.yaml is the pre-V1 hook registry. "
            "Run: python3 kl.py migrate"
        )

    if args.verb == "detect":
        print(json.dumps(detect(root, args.chain), indent=2))
        return 0

    if args.verb == "chains":
        if args.available:
            print(json.dumps(kl_chains.available(root, os.environ), indent=2))
        else:
            print(json.dumps(chain_summaries(store_for(root).load()), indent=2))
        return 0

    if args.verb == "settings":
        print(json.dumps(settings(root), indent=2))
        return 0

    store = store_for(root)

    if args.verb == "import":
        print(json.dumps(_import(root, store, args.source), indent=2))
        return 0

    # Before `_ours`: a legacy run is what it moves, and `_chain` refuses one.
    if args.verb == "migrate":
        print(json.dumps(kl_migrate.migrate(sys.modules[__name__], root, store), indent=2))
        return 0

    if args.verb == "start":
        new_id = new_chain_id()
        attached = {k: getattr(args, k) for k in ATTACHMENT_KINDS if getattr(args, k)}
        if args.slice_of is None:
            chain = _start_chain(root, args.chain, attached)
        else:
            chain = _start_slice(root, args.slice_of, args.chain, attached)
        _freeze_chain(root, chain, new_id)
        records = _start_records(chain, args.title, new_id)
        if args.slice_of is not None:
            records[0] = set_label(records[0], SLICE_LAYER_LABEL, "")
        for kind, path in attached.items():
            records[0] = set_label(
                records[0], f"{ARTIFACT_LABEL}{kind}=", _artifact_path(root, path)
            )
        store.write(records)
        print(json.dumps({"chain_id": new_id, "backend": backend(root)}))
        return 0

    everything = store.load()
    records = _ours(everything, chain_id)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    # A verb run without the flag still has to read the template of the chain it
    # actually resolved, not the directory's last one.
    resolved_id = epic["id"] if epic else None
    chain = _chain(root, resolved_id)
    node_record = current(records)
    if node_record is None and "open_node" in VERBS[args.verb]:
        raise SystemExit("kraft-lite: no open node — the chain is finished or was never started")
    node_id = label_value(node_record, NODE_LABEL)
    # Only for a verb that acts on the node: `summary` or `export` of a run whose
    # frozen chain lost a node still answers.
    if "open_node" in VERBS[args.verb]:
        definition = _definition(root, chain, resolved_id, node_id)

    if "log" in VERBS[args.verb]:
        if resolved_id is None:
            raise SystemExit("kraft-lite: no chain in this directory — start one first")
        if args.verb == "decide":
            out = _decide(root, everything, resolved_id, args)
        elif args.verb == "answer":
            out = _answer(root, resolved_id, args)
        elif args.verb == "decisions":
            out = _decisions(root, everything, resolved_id, args)
        else:
            out = _export(root, everything, resolved_id, args.to)
        print(json.dumps(out, indent=2))
        return 0

    if args.verb == "state":
        print(json.dumps(_state(root, chain, everything, resolved_id), indent=2))
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
        made = slices_for_gate(everything, node_record, resolved_id, args.slices, root, chain)
        store.write([bank_wait(dict(node_record, status="closed")), *made])
    elif args.verb in ("close", "approve"):
        if args.verb == "close" and definition["kind"] == "gate":
            raise SystemExit(f"kraft-lite: {node_id} is a gate: approve or reject it")
        _refuse_close_before_slices(everything, resolved_id, node_record, chain)
        store.write([bank_wait(dict(node_record, status="closed"))])
    elif args.verb == "gate":
        if definition["kind"] != "gate":
            raise SystemExit(
                f"kraft-lite: {node_id} is an exec node, not a gate node: "
                "run its tasks and close it"
            )
        store.write([open_gate(dict(node_record, status="blocked"), node_id)])
    elif args.verb == "reject" and (args.from_node or definition["kind"] == "gate"):
        target = args.from_node or definition["reject_to"] or _exec_before(chain, node_id)
        tail = rewind(records, target, args.note)
        store.write([*tail, *rewind_slices(everything, resolved_id, tail, chain)])
    elif args.verb == "restart":
        store.write(_restart(everything, records, resolved_id, node_record, definition, chain))
    elif args.verb == "produced":
        if not ARTIFACT_KIND.fullmatch(args.kind):
            raise SystemExit(
                f"kraft-lite: --kind must be an artifact kind (letters, digits, _ . -), "
                f"not {args.kind!r}"
            )
        prefix = f"{ARTIFACT_LABEL}{args.kind}="
        store.write([set_label(node_record, prefix, _artifact_path(root, Path(args.path)))])
    elif args.verb == "mark":
        if not definition.get("on_failure"):
            raise SystemExit(f"kraft-lite: {node_id} has no on_failure to have run")
        store.write([set_label(node_record, ON_FAILURE_RAN_LABEL, "")])
    elif args.verb == "reject":
        description = node_record.get("description", "")
        # Banked on the way out too: a gate that ends in a rejection held the human
        # exactly as long as one that ends in an approval. A rejection starts a new
        # round, so the node's `on_failure` may run again.
        reopened = dict(
            node_record, status="open", description=f"{description}\n{NOTE_PREFIX}{args.note}"
        )
        store.write([bank_wait(drop_label(reopened, ON_FAILURE_RAN_LABEL))])
    elif args.verb == "attempt":
        charged = spend_attempt(node_record)
        count = attempts(charged)
        store.write([charged])
        # Read again: the write above is what this reports.
        state = _state(root, chain, store.load(), resolved_id)
        state["over_cap"] = state["cap"] is not None and count > state["cap"]
        print(json.dumps(state, indent=2))
        return 0

    # Read again: the write above is what this reports.
    print(json.dumps(_state(root, chain, store.load(), resolved_id), indent=2))
    return 0


#: Which installed skill names plausibly serve which task. Keyed by the library
#: component an agent task extends, and by the ref or target of a builtin or
#: forge step. Deliberately keyword matching and deliberately generous: init
#: reports every candidate and asks the human only when there is more than one.
#: A wrong guess offered as a choice is cheap; a missing candidate is a skill
#: the human has to name by hand. An empty tuple says no installed skill serves
#: the task; a name missing from the table derives its keywords from its own
#: words (see `_task_keywords`).
TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "spec_author": ("brainstorm", "spec", "design"),
    "plan_author": ("plan",),
    "revise_chain": ("chain-review",),
    "implementer": ("test-driven", "tdd", "implement"),
    "code_review": ("review",),
    # No candidates: the repair tasks and the judge act on what their node's
    # verification reported, not on anything an installed skill is for.
    "repair_verification": (),
    "repair_mr_feedback": (),
    "repair_mr_checks": (),
    "strict_judge": (),
    "write_work_brief": ("brief",),
    "write_summary": ("summary",),
    "describe_mr": ("mr", "pr", "metadata", "describe"),
    # No candidates: which tests to run and how to rebase are the repo's own
    # commands and the walk's own builtins, not something a skill serves.
    "kraft.verify_changed_test_scopes": (),
    "kraft.mr_rebase": (),
    "mr.open_draft": ("finishing", "branch", "pull-request", "merge-request"),
    # No candidates: pushing the branch, watching CI or review, and marking the
    # draft ready are the forge adapter's own job.
    "mr.sync": (),
    "mr.ci": (),
    "mr.automated_review": (),
    "mr.mark_ready": (),
    "mr.external_approval": (),
    "mr.merge": ("finishing", "merge"),
    "mr.post_merge_ci": (),
}

#: Words in a task name that say when it runs, not what it does. A repo's own
#: task derives its candidates from the words that are left, so these must not
#: match a skill: `deploy_staging` should look for deploy skills, not for every
#: skill with "start" in its name.
TASK_STOPWORDS = frozenset(
    {"task", "main", "step", "run", "start", "prepare", "poll", "open", "ready", "await"}
)


def _task_keywords(name: str, words_of: str) -> tuple[str, ...]:
    """Keywords to match installed skill names against.

    `TASK_KEYWORDS` is the curated answer for the tasks the bundled library
    ships; it encodes judgements the name alone cannot, like `mr.open_draft`
    wanting `finishing`. It is not the list of tasks that exist: a repo's own
    task names nothing curated, and those derive from `words_of`.
    """
    if name in TASK_KEYWORDS:
        return TASK_KEYWORDS[name]
    return tuple(w for w in re.split(r"[._-]", words_of) if w and w not in TASK_STOPWORDS)


def _keyword_in(keyword: str, name: str) -> bool:
    """Whether `keyword`'s words begin consecutive words of the skill `name`.

    Whole words, because `pr` inside `compress` or `project-artifact` is not a
    pull request. A word of three letters or fewer must match exactly (or take
    an `s`); a longer one matches the start of a word, so `plan` finds `plans`
    and `brainstorm` finds `brainstorming`."""
    parts, words = re.findall(r"[a-z0-9]+", keyword.lower()), re.findall(r"[a-z0-9]+", name.lower())

    def same(part: str, word: str) -> bool:
        return word in (part, part + "s") if len(part) <= 3 else word.startswith(part)

    return any(
        all(same(part, words[i + j]) for j, part in enumerate(parts))
        for i in range(len(words) - len(parts) + 1)
    )


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


def detect(root: Path, chain_ref: str | Path | None = None) -> dict:
    """What `init` needs to write the bindings: the repo's test and CI commands,
    the installed skills that could serve each task of the chain (a library id
    or a chain file, as `start` takes it), and `proposed`, the bindings that
    differ from the defaults and need no question."""
    chain = _resolve_chain(root, chain_ref)
    installed = _installed_skills(root)
    candidates: dict[str, dict[str, list[str]]] = {"tasks": {}, "builtins": {}, "forge": {}}
    agents: dict[str, dict] = {}
    for task in _dispatched_tasks(chain):
        kind = task["kind"]
        if kind == "agent":
            section, name = "tasks", (task["components"] or [task["path"]])[0]
            words_of = name if task["components"] else task["id"]
        elif kind in ("builtin", "forge"):
            section = "builtins" if kind == "builtin" else "forge"
            name = task["ref"] if kind == "builtin" else task["target"]
            words_of = name
        else:
            continue
        keywords = _task_keywords(name, words_of)
        found = {n for n in installed if any(_keyword_in(k, n) for k in keywords)}
        if task.get("skill") in installed:
            found.add(task["skill"])
        candidates[section][name] = sorted(found | set(candidates[section].get(name, ())))
        if section == "tasks":
            agents.setdefault(name, task)

    test, ci = _test_command(root), _ci_command(root)
    proposed: dict[str, dict] = {}
    # A task with exactly one candidate that is not already its own skill is
    # bound to it; two or more are the human's to choose between.
    bound = {
        name: {"kind": "skill", "skill": found[0], "prompt": agents[name]["prompt"]}
        for name, found in candidates["tasks"].items()
        if len(found) == 1 and found[0] != agents[name].get("skill")
    }
    if bound:
        proposed["tasks"] = bound
    if test:
        proposed["builtins"] = {
            "kraft.verify_changed_test_scopes": {"kind": "subprocess", "command": list(test)}
        }
    if ci:
        proposed["forge"] = {"mr.ci": {"kind": "subprocess", "command": list(ci)}}
    return {
        "test_command": test,
        "ci_command": ci,
        "chain": chain["id"],
        "candidates": candidates,
        "proposed": proposed,
    }


if __name__ == "__main__":
    sys.exit(main())
