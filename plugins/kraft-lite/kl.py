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
        append_jsonl(self.path, [_bump_updated(r) for r in records])


#: How bd writes timestamps, and the granularity it compares them at.
BD_TIME = "%Y-%m-%dT%H:%M:%SZ"


def _bump_updated(record: dict) -> dict:
    """Stamp `updated_at` strictly newer than the row bd already holds.

    `bd import` is an upsert guarded by `updated_at`, and it is documented to
    keep every local column on a *tie*. Its granularity is one second, so two
    writes inside the same second — which is every gate-then-approve — are
    silently dropped unless the timestamp moves.
    """
    # noqa UP017: `dt.UTC` is 3.11+. This file ships standalone and is tested
    # against the floor in the CI matrix, so the older spelling stays.
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # noqa: UP017
    previous = record.get("updated_at")
    if previous:
        try:
            was = dt.datetime.strptime(previous, BD_TIME)
            now = max(now, was + dt.timedelta(seconds=1))
        except ValueError:
            pass
    return dict(record, updated_at=now.strftime(BD_TIME))


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
        payload = "".join(json.dumps(_bump_updated(r)) + "\n" for r in records)
        self._bd(["import", "-"], stdin=payload)


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


DEFAULT_CHAIN = Path(__file__).parent / "chains" / "default.json"


def _chain(root: Path, path: Path | None) -> dict:
    """The chain this run walks.

    `start` freezes its chain into the state directory and every later verb reads
    that copy. Re-reading the template each time would mean a `--chain` given at
    start is forgotten by the next verb — and worse, that editing a template
    retargets a run already in flight.
    """
    frozen = root / STATE_DIR / "chain.json"
    if path is not None:
        return json.loads(path.read_text())
    if frozen.is_file():
        return json.loads(frozen.read_text())
    return json.loads(DEFAULT_CHAIN.read_text())


def _freeze_chain(root: Path, chain: dict) -> None:
    frozen = root / STATE_DIR / "chain.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(json.dumps(chain, indent=2) + "\n")


def _ours(records: list[dict]) -> list[dict]:
    """The live chain's records. bd holds a whole project's issues, and a repo
    accumulates one epic per `start`, so both have to be filtered out.

    The live chain is the newest one with unfinished work; with none unfinished,
    the newest overall, so a completed run still reports `done` rather than
    `unstarted`. Node ids are `<chain-id>.<node-id>`, so the prefix identifies the
    owner.

    ponytail: newest-wins, no way to name an older run. Upgrade path is a
    `--chain-id` argument on every verb, worth adding the first time somebody
    wants to walk two at once.
    """
    epics = [r["id"] for r in records if label_value(r, CHAIN_LABEL)]
    if not epics:
        return []
    by_chain: dict[str, list[dict]] = {e: [] for e in epics}
    for record in records:
        owner = record["id"].split(".")[0]
        if owner in by_chain:
            by_chain[owner].append(record)

    def unfinished(chain_id: str) -> bool:
        return any(r["status"] != "closed" for r in by_chain[chain_id])

    live = [e for e in epics if unfinished(e)] or epics
    return by_chain[live[-1]]


def _state(root: Path, chain: dict) -> dict:
    records = _ours(store_for(root).load())
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
        }
    node_id = label_value(node_record, NODE_LABEL)
    definition = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if definition is None:
        # Reporting `hooks: []` here is worse than failing: the `next` skill's
        # contract is "no hooks, so close and move on", and the chain walks to
        # `done` having run nothing at all.
        raise SystemExit(
            f"kraft-lite: node {node_id!r} is not in this chain. "
            f"{STATE_DIR}/chain.json is the chain this run started with — "
            "restore it, or start a new chain."
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
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kl")
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--chain", type=Path, default=None)
    sub.add_parser("state")
    sub.add_parser("close")
    sub.add_parser("approve")
    sub.add_parser("attempt")
    sub.add_parser("detect")
    gate = sub.add_parser("gate")
    gate.add_argument("--name", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--note", required=True)

    args = parser.parse_args(argv)
    root = repo_root()

    if args.verb == "detect":
        print(json.dumps(detect(root), indent=2))
        return 0

    chain = _chain(root, getattr(args, "chain", None))
    store = store_for(root)

    if args.verb == "start":
        chain_id = new_chain_id()
        _freeze_chain(root, chain)
        records = materialize(chain, args.title, chain_id)
        # The epic is a container, not a step: close it now so the walk starts at
        # the first real node.
        records[0]["status"] = "closed"
        store.write(records)
        print(json.dumps({"chain_id": chain_id, "backend": backend(root)}))
        return 0

    node_record = current(_ours(store.load()))
    if node_record is None and args.verb != "state":
        raise SystemExit("kraft-lite: no open node — the chain is finished or was never started")

    if args.verb == "state":
        print(json.dumps(_state(root, chain), indent=2))
        return 0

    if args.verb in ("close", "approve"):
        store.write([dict(node_record, status="closed")])
    elif args.verb == "gate":
        store.write([set_label(dict(node_record, status="blocked"), GATE_LABEL, args.name)])
    elif args.verb == "reject":
        description = node_record.get("description", "")
        store.write(
            [
                dict(
                    node_record,
                    status="open",
                    description=f"{description}\n{NOTE_PREFIX}{args.note}",
                )
            ]
        )
    elif args.verb == "attempt":
        count = attempts(node_record) + 1
        store.write([set_label(node_record, ATTEMPT_LABEL, str(count))])
        state = _state(root, chain)
        state["over_cap"] = state["cap"] is not None and count > state["cap"]
        print(json.dumps(state, indent=2))
        return 0

    print(json.dumps(_state(root, chain), indent=2))
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
    "on.test.run": (),
    "on.review.local.run": ("review",),
    "on.mr.open": ("finishing", "branch", "pull-request", "merge-request"),
    "on.ci.poll": (),
    "on.review.mr.run": ("review",),
    "on.human_review.requested": (),
    "on.merge": ("finishing", "merge"),
}

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


def detect(root: Path) -> dict:
    installed = _installed_skills(root)
    skills = {
        hook: [n for n in installed if any(k in n.lower() for k in keywords)] if keywords else []
        for hook, keywords in HOOK_KEYWORDS.items()
    }
    return {"test_command": _test_command(root), "skills": skills}


if __name__ == "__main__":
    sys.exit(main())
