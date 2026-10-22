"""`kl.py migrate`: a directory's pre-V1 state moved onto Kraft's V1 chains.

Stdlib only, like `kl.py`. Two things move: the hook registry
(`.kraft-lite/registry.yaml`) becomes `.kraft-lite/bindings.yaml`, and each
unfinished run frozen on the legacy `default` chain moves onto the bundled V1
`default`. A run that is sliced, or frozen on any other legacy chain, is
refused: finish it on the previous Kraft Lite version, or abandon it with
`migrate --abandon <chain-id>`, which closes its records and keeps them.

`migrate(host, root, store)` takes `host`, the `kl` module that is running, for
the record helpers and label names; `kl` imports this module, so this one
cannot import it back. What it relies on in `kl` (rename one and this breaks):
- labels and settings: `ATTEMPT_LABEL`, `BLOCKED_LABEL`, `FORMAT`, `GATE_LABEL`,
  `NODE_LABEL`, `SPENT_LABEL`, `STATE_DIR`, `WAITED_LABEL`, `CHAIN_ID`;
- record helpers: `bank_wait`, `chain_index`, `label_value`, `run_record`,
  `set_label`, `slice_records`, `new_chain_id`;
- private helpers: `_checked`, `_freeze_chain`, `_frozen_file`, `_note`,
  `_of_chain`, `_read_json`, `_start_records`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

import kl_bindings
import kl_chains
import kl_yaml

REGISTRY = "registry.yaml"
MIGRATED_REGISTRY = "registry.yaml.migrated"
#: Written on every legacy record `migrate` closes, naming the run that replaces it.
MIGRATED_LABEL = "kraft-migrated-to:"
#: The gate each legacy node held its human at (`gate_after` in the legacy chain).
#: A node blocked at any other name was blocked ad hoc, before its work was done.
LEGACY_GATES = {
    "spec": "spec_approval",
    "plan": "plan_approval",
    "chain_review": "chain_finalized",
    "human_review": "human_review_approval",
}
#: Written on every record `migrate --abandon` closes: the run was given up, not finished.
ABANDONED_LABEL = "kraft-abandoned"
#: What a refused legacy run is told.
FINISH_OR_ABANDON = (
    "finish it on the previous Kraft Lite version, or abandon it: "
    "python3 kl.py migrate --abandon <chain-id>"
)

#: Each legacy hook -> the (section, key) of `bindings.yaml` that serves the same
#: task now; `slice.tasks` and `slice.builtins` are the `slice:` layer's sections.
#: None: no V1 task does its job, so its binding is dropped.
LEGACY_HOOKS: dict[str, tuple[str, str] | None] = {
    "on.spec.requested": ("tasks", "spec_author"),
    "on.plan.requested": ("tasks", "plan_author"),
    "on.chain.review_ready": ("tasks", "revise_chain"),
    "on.implementation.start": ("tasks", "implementer"),
    "on.test.run": ("builtins", "kraft.verify_changed_test_scopes"),
    "on.review.local.run": ("tasks", "code_review"),
    "on.mr.rebase": ("builtins", "kraft.mr_rebase"),
    "on.mr.describe": ("tasks", "describe_mr"),
    "on.mr.open": ("forge", "mr.open_draft"),
    "on.ci.poll": ("forge", "mr.ci"),
    "on.review.mr.run": ("forge", "mr.automated_review"),
    # Its job, presenting the work for sign-off, is the `final_review` gate's:
    # `write_summary` only writes the brief that gate presents.
    "on.human_review.requested": None,
    "on.merge": ("forge", "mr.merge"),
    # It pulled the merge request into the branch; V1's `mr.sync` pushes the
    # branch after a repair, so the binding would undo the job it is bound to.
    "on.mr.sync": None,
    "on.merge.watch": ("forge", "mr.post_merge_ci"),
    "on.slice.implementation.start": ("slice.tasks", "implementer"),
    "on.slice.test.run": ("slice.builtins", "kraft.verify_changed_test_scopes"),
    "on.env.prepare": None,
    "on.repos.scan": None,
}

#: Each legacy default-chain node -> the V1 `default` nodes it became, in order.
LEGACY_NODES: dict[str, tuple[str, ...]] = {
    "spec": ("spec", "spec_approval"),
    "plan": ("plan", "plan_approval"),
    "chain_review": ("chain_revision", "chain_revision_approval"),
    "implementation": ("implementation",),
    "verify": ("verification",),
    "open_mr": ("work_brief", "local_review", "describe_merge_request", "draft_merge_request"),
    "mr_checks": ("merge_request_feedback",),
    "human_review": ("work_item_summary", "final_review"),
    "merge": ("mark_ready", "external_approval", "merge"),
    "post_merge_watch": ("post_merge_ci",),
}


def _refuse(detail: str) -> SystemExit:
    return SystemExit(f"kraft-lite: {detail}")


# -- the registry -------------------------------------------------------------


def _norm(text: object) -> str:
    return " ".join(str(text).split())


def _bundled_default(library: kl_chains.Library, section: str, key: str) -> dict | None:
    """The entry the walk uses when `bindings.yaml` has none for `key`, as a
    binding; None when the bundled `library` has none."""
    if section == "tasks":
        prompt = library.components["tasks"].get(key, {}).get("prompt")
        return {"kind": "prompt", "prompt": prompt} if prompt else None
    if section.startswith("slice."):
        return kl_bindings.SLICE_DEFAULTS.get(section[len("slice.") :], {}).get(key)
    return {"builtins": kl_bindings.BUILTIN_DEFAULTS, "forge": kl_bindings.FORGE_DEFAULTS}[
        section
    ].get(key)


#: Defaults a #1005 registry may hold word for word that the bundled ones have
#: since reworded: such a binding is a default too, and dropped as one.
LEGACY_DEFAULTS: dict[tuple[str, str], dict] = {
    ("slice.tasks", "implementer"): {
        "kind": "skill",
        "skill": "superpowers:subagent-driven-development",
        "prompt": (
            "Implement the slice's tasks with one subagent per task, each working test "
            "first. After each task run a spec-compliance review and then a code-quality "
            "review, with a capped fix round. Run independent tasks in parallel, each in "
            "its own isolated worktree. Set a model explicitly on every subagent. Stop "
            "after the last task's review: no final whole-branch review and no finishing "
            "or merge step, since on.review.local.run and open_mr are those."
        ),
    },
}


def _is_default(entry: dict, default: dict | None) -> bool:
    """Whether a binding says what the bundled default for its key already says:
    the same kind and fields, prompts compared with their whitespace folded."""
    if default is None or set(entry) != set(default):
        return False
    return all(_norm(entry[key]) == _norm(default[key]) for key in default)


def convert_registry(
    registry: object, library: kl_chains.Library | None = None
) -> tuple[dict, list[str]]:
    """The bindings a legacy registry becomes, and the hooks left out of them: those
    V1 has no task for, and those whose binding is the bundled default's own, of any kind.
    `library` is the bundled library, when the caller loaded it already. A hook
    that is not in `LEGACY_HOOKS` is a `SystemExit` naming it."""
    library = library or kl_chains.bundled_library()
    if registry is None:
        return {}, []
    if not isinstance(registry, Mapping):
        raise _refuse(f".kraft-lite/{REGISTRY}: expected a mapping of hooks")
    custom = [str(hook) for hook in registry if hook not in LEGACY_HOOKS]
    if custom:
        raise _refuse(
            f".kraft-lite/{REGISTRY} binds hooks that no V1 task replaces: {', '.join(custom)}. "
            "Nothing was migrated. Take each out of the registry, run migrate, then add its "
            "binding to .kraft-lite/bindings.yaml by hand under the task it should serve "
            "(tasks, builtins or forge, keyed by task name)"
        )
    bindings: dict = {}
    dropped: list[str] = []
    for hook, entry in registry.items():
        target = LEGACY_HOOKS[hook]
        if target is None:
            dropped.append(hook)
            continue
        if not isinstance(entry, Mapping):
            raise _refuse(
                f".kraft-lite/{REGISTRY}: {hook}: expected a mapping with a kind. Nothing was "
                "migrated. Fix that entry in the registry by hand (a mapping with a `kind`, as "
                "the other hooks have), or take it out, then run migrate again"
            )
        section, key = target
        defaults = (_bundled_default(library, section, key), LEGACY_DEFAULTS.get((section, key)))
        if any(_is_default(dict(entry), default) for default in defaults):
            dropped.append(hook)
            continue
        layer = bindings
        for part in section.split("."):
            layer = layer.setdefault(part, {})
        layer[key] = dict(entry)
    return bindings, dropped


_PLAIN = re.compile(r"[A-Za-z_](?:[A-Za-z0-9_.:/-]*[A-Za-z0-9_./-])?")


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    # Plain only where the reader gives the same string back: not `on`, `null`, `1.5`.
    if _PLAIN.fullmatch(text) and kl_yaml.load(f"k: {text}\n") == {"k": text}:
        return text
    return json.dumps(text, ensure_ascii=False)


def emit_yaml(data: Mapping, indent: int = 0) -> str:
    """`data` as block-style YAML that `kl_yaml.load` reads back to the same
    dict: mappings in block style, a list of scalars in flow style, every
    string quoted unless it is safe plain. Handles what a bindings file holds."""
    if not data:
        return "{}\n" if indent == 0 else ""
    pad = " " * indent
    lines = []
    for key, value in data.items():
        if isinstance(value, Mapping) and value:
            lines.append(f"{pad}{_scalar(key)}:\n{emit_yaml(value, indent + 2)}")
        elif isinstance(value, Mapping):
            lines.append(f"{pad}{_scalar(key)}: {{}}\n")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{pad}{_scalar(key)}: [{', '.join(_scalar(v) for v in value)}]\n")
        else:
            lines.append(f"{pad}{_scalar(key)}: {_scalar(value)}\n")
    return "".join(lines)


HEADER = (
    "# Kraft Lite bindings, written by `kl.py migrate` from registry.yaml (kept beside it as\n"
    "# registry.yaml.migrated). Comments were not carried. Keys are Kraft's task names.\n"
)


def _checked(bindings: dict) -> str:
    """The YAML for `bindings`, after Lite's own reader has accepted it: written
    to a scratch directory and read back, so nothing in the repo changes if it
    would refuse."""
    text = HEADER + emit_yaml(bindings)
    scratch = Path(tempfile.mkdtemp())
    try:
        target = scratch / kl_bindings.BINDINGS
        target.parent.mkdir(parents=True)
        target.write_text(text)
        try:
            read = kl_bindings.read_bindings(scratch)
        except kl_bindings.BindingError as error:
            raise _refuse(
                f"the registry would become a bindings file Lite refuses ({error.detail}). "
                "Nothing was migrated. Correct that entry in the registry"
            ) from None
        if read != bindings:
            raise _refuse("the bindings did not read back as written. Nothing was migrated")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return text


# -- the runs -----------------------------------------------------------------


def _same_file(path: Path, text: str) -> bool:
    return path.is_file() and path.read_text() == text


def _label(host, record: dict, prefix: str, value: str) -> None:
    record["labels"] = host.set_label(record, prefix, value)["labels"]


def _frozen(host, root: Path, chain_id: str) -> dict | None:
    """The chain a run was frozen on: its own file, else the single `chain.json`
    that came before; None when there is neither."""
    path = host._frozen_file(root, chain_id)
    if path is None:
        return None
    try:
        chain = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise _refuse(
            f"cannot read the chain {path.relative_to(root)}: {error}. Nothing was migrated. "
            "Restore it (from version control or a backup), or remove the file, then run "
            "migrate again"
        ) from error
    return chain if isinstance(chain, dict) else {}


def _legacy_runs(host, root: Path, everything: list[dict]) -> tuple[list[dict], list[str]]:
    """The unfinished runs frozen before V1, each with its records, and a line
    for each that `migrate` cannot move."""
    found, refused = [], []
    for chain_id, records in host.chain_index(everything).items():
        if all(r["status"] == "closed" for r in records):
            continue
        frozen = _frozen(host, root, chain_id)
        if frozen is None or frozen.get("format") == host.FORMAT:
            continue
        template = frozen.get("id")
        nodes = {host.label_value(r, host.NODE_LABEL) for r in records} - {None}
        if host.run_record(everything, chain_id) or host.slice_records(everything, chain_id):
            why = "it is sliced"
        elif template == "slice":
            why = "it is a slice's child chain"
        elif template != "default":
            why = f"it runs the chain {template!r}, not the default"
        elif nodes - set(LEGACY_NODES):
            unknown = ", ".join(sorted(nodes - set(LEGACY_NODES)))
            why = f"it has nodes the default chain does not: {unknown}"
        else:
            found.append({"id": chain_id, "records": records})
            continue
        refused.append(f"{chain_id} ({why})")
    # Node records whose epic is gone: no chain lists them, so they would be left unfinished.
    known = host.chain_index(everything)
    orphans = {
        r["id"].split(".")[0]
        for r in everything
        if host.label_value(r, host.NODE_LABEL) and r["status"] != "closed" and "." in r["id"]
    } - set(known)
    for owner in sorted(orphans):
        frozen = _frozen(host, root, owner) if host.CHAIN_ID.fullmatch(owner) else None
        if frozen is not None and frozen.get("format") != host.FORMAT:
            refused.append(f"{owner} (its chain record is missing)")
    return found, refused


def _carry(host, legacy: dict, title: str, chain: dict) -> tuple[str, list[dict]]:
    """The new run for a legacy `default` run: its id, and its records with
    every node's status set from the legacy node it came from, attempts, waits
    and notes carried."""
    new_id = host.new_chain_id()
    records = host._start_records(chain, title, new_id)
    new = {host.label_value(r, host.NODE_LABEL): r for r in records[1:]}
    kinds = {n["id"]: n["kind"] for n in chain["nodes"]}
    old = {host.label_value(r, host.NODE_LABEL): r for r in legacy["records"]}
    for legacy_id, mapped in LEGACY_NODES.items():
        before = old.get(legacy_id)
        status = before["status"] if before else "open"
        gate = next((i for i, n in enumerate(mapped) if kinds[n] == "gate"), None)
        if status == "blocked" and (
            gate is None or host.label_value(before, host.GATE_LABEL) != LEGACY_GATES.get(legacy_id)
        ):
            # Nothing in V1 to hold it, or held ad hoc before its work was done:
            # the node runs, its wait banked.
            status = "open"
        states = ["open"] * len(mapped)
        if status == "closed":
            states = ["closed"] * len(mapped)
        elif status == "blocked":
            states = ["closed"] * gate + ["blocked"] + ["open"] * (len(mapped) - gate - 1)
        for node, state in zip(mapped, states, strict=True):
            new[node]["status"] = state
        if before is None:
            continue
        if status != "blocked":
            before = host.bank_wait(before)
        # The wait sits on the gate that was held, or on the node that runs again.
        first, held = (
            new[mapped[0]],
            new[mapped[gate if status != "open" and gate is not None else 0]],
        )
        for prefix in (host.ATTEMPT_LABEL, host.SPENT_LABEL):
            value = host.label_value(before, prefix)
            if value is not None:
                _label(host, first, prefix, value)
        note = host._note(before.get("description", ""))
        if note:
            first["description"] = f"{first['description']}\n{note}"
        for prefix in (host.WAITED_LABEL, host.BLOCKED_LABEL):
            value = host.label_value(before, prefix)
            if value is not None:
                _label(host, held, prefix, value)
        if status == "blocked":
            _label(host, held, host.GATE_LABEL, mapped[gate])
    return new_id, records


def migrate(host, root: Path, store) -> dict:
    """Move this directory onto V1: the registry to bindings, each unfinished
    legacy `default` run to a new run on the bundled `default`. Everything that
    can refuse does so before anything is written; the registry is renamed last,
    so a run interrupted halfway is finished by running it again."""
    state = root / host.STATE_DIR
    registry = state / REGISTRY
    # Read once: the registry's defaults and the chain each legacy run moves onto.
    bundled = kl_chains.bundled_library()
    bindings_path = root / kl_bindings.BINDINGS
    bindings: dict = {}
    dropped: list[str] = []
    text = None
    written_already = False
    if registry.is_file():
        try:
            bindings, dropped = convert_registry(kl_yaml.read(registry), bundled)
            text = _checked(bindings)
        except SystemExit:
            if not bindings_path.is_file():
                raise
        # What an interrupted migrate leaves: the bindings written, the registry
        # not yet renamed. Exactly what this would write, so finish it.
        written_already = text is not None and _same_file(bindings_path, text)
        if bindings_path.is_file() and not written_already:
            raise _refuse(
                f"{kl_bindings.BINDINGS} and {host.STATE_DIR}/{REGISTRY} both exist: migrate "
                f"would overwrite the bindings. Move {kl_bindings.BINDINGS} aside, run migrate, "
                "and merge the two by hand"
            )

    everything = store.load()
    legacy, refused = _legacy_runs(host, root, everything)
    if refused:
        raise _refuse(
            "migrate moves only unsliced runs of the legacy default chain, and cannot move "
            + "; ".join(refused)
            + f". Nothing was migrated. For each, {FINISH_OR_ABANDON}"
        )

    written: list[dict] = []
    moved = []
    if legacy:
        chain = kl_chains.resolve_bundled(library=bundled)
        for run in legacy:
            epic = next(r for r in run["records"] if r["id"] == run["id"])
            new_id, records = _carry(host, run, epic.get("title", ""), chain)
            moved.append({"from": run["id"], "to": new_id, "chain": chain, "records": records})
            closed = [
                host.set_label(host.bank_wait(dict(r, status="closed")), MIGRATED_LABEL, new_id)
                for r in run["records"]
                if r["status"] != "closed"
            ]
            written += [*records, *closed]
    for run in moved:
        host._freeze_chain(root, run["chain"], run["to"])
    if written:
        store.write(written)
    if text is not None:
        if not written_already:
            bindings_path.parent.mkdir(parents=True, exist_ok=True)
            # Whole or not at all: a half-written file is not what a rerun can finish.
            partial = bindings_path.with_name(bindings_path.name + ".tmp")
            partial.write_text(text)
            os.replace(partial, bindings_path)
        registry.rename(state / MIGRATED_REGISTRY)

    result: dict = {"bindings": {}, "chains": [{"from": m["from"], "to": m["to"]} for m in moved]}
    if text is not None:
        result["bindings"] = {**bindings, "dropped": dropped}
    if moved:
        result["note"] = (
            "Each run now walks Kraft's bundled V1 default chain, not the default of a user or "
            "repo library; the legacy run is closed, labelled " + MIGRATED_LABEL + "<new id>."
        )
    return result


def abandon(host, root: Path, store, chain_id: str) -> dict:
    """`migrate --abandon <chain-id>`: close every open record of an unfinished
    run frozen before V1, each labelled `ABANDONED_LABEL`, so a run `migrate`
    cannot move stops blocking it. Nothing is deleted: the records and the
    frozen chain stay, and a later `migrate` passes the run as finished."""
    chain_id = host._checked(chain_id)
    still_open = [
        r for r in store.load() if host._of_chain(r, chain_id) and r["status"] != "closed"
    ]
    frozen = _frozen(host, root, chain_id)
    if not still_open or frozen is None or frozen.get("format") == host.FORMAT:
        raise _refuse(
            f"{chain_id} is not an unfinished run frozen before V1: "
            "--abandon closes only such a run"
        )
    closed = [
        host.set_label(host.bank_wait(dict(r, status="closed")), ABANDONED_LABEL, "")
        for r in still_open
    ]
    store.write(closed)
    return {"abandoned": chain_id, "closed": sorted(r["id"] for r in closed)}
