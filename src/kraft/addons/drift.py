"""What is wrong with the built-in `kraft-default`, as lines: one check for the
`doctor` row and the server's banner and webhook notice."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

from kraft import config
from kraft.addons import load as addons_load
from kraft.templates.library import CHAINS_DIR

logger = logging.getLogger(__name__)

#: Under `run/addons`: the drift state last sent to the webhook.
NOTIFIED = "drift-notified.json"


def _full_copies(templates: Path) -> list[str]:
    """Local chains that share an id with a `kraft-default` chain and do not
    extend it: they hide the built-in's, so releases never reach them."""
    from kraft.addons import builtin

    ids = {
        Path(name).stem
        for name in builtin.extracted().files
        if name.startswith(f"{CHAINS_DIR}/") and name.endswith(".yaml")
    }
    out = []
    for path in sorted((templates / CHAINS_DIR).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, ValueError, yaml.YAMLError):
            continue
        # A chain with no `id:` is named by its file, as the library reads it.
        chain_id = data.get("id") or path.stem if isinstance(data, dict) else None
        if chain_id in ids and data.get("extends") != f"{builtin.NAMESPACE}:{chain_id}":
            out.append(
                f"chains/{path.name} is a full copy and hides {builtin.NAMESPACE}:{chain_id}; "
                "see Move onto kraft-default"
            )
    return out


def lines(templates: Path, addons_root: Path) -> list[str]:
    """The `kraft-default` row's lines, in its order; empty when there is nothing to say."""
    from kraft import update as update_mod
    from kraft.addons import builtin
    from kraft.policy import PolicyError, PolicyInput

    lines: list[str] = []
    running = update_mod.installed()
    note = builtin.recorded(addons_root)
    if note and not isinstance(note.get("lines"), list):
        lines.append("cannot read kraft-default: its update record is malformed")
        note = None  # the rest of the row still reports
    if note:
        to, lines_ = str(note.get("to")), [str(x) for x in note["lines"]]
        # s3 records before the install runs, so a record can name a release that never installed.
        if note.get("status") == "held" and update_mod.is_older(running, to):
            msg = (
                f"the {to} update was held: {'; '.join(lines_)}. "
                "Run `kraft admin update` to see what it changes and decide"
            )
            lines.append(
                msg
                + (
                    "; after accepting, fix the override it names"
                    if not any(x.startswith("cannot compare") for x in lines_)
                    else ""
                )
            )
        elif note.get("status") == "changed" and to == running:
            lines.append(f"{note.get('from')} -> {to} changed kraft-default:")
            lines += [f"  {x}" for x in lines_]
    try:
        lines += _full_copies(templates)
        found = addons_load.installed(templates, addons_root)
    except Exception as exc:  # a row that cannot read says so; it never crashes doctor
        return [f"cannot read kraft-default: {exc}"]  # the row at base said only this
    lines += [
        f"{a.id}: {a.left_out}" for a in found if (a.left_out or "").startswith("Kraft now ships")
    ]
    try:
        have = config.read_yaml(templates / "harnesses.yaml")
    except config.ConfigError:
        have = {}
    have = have if isinstance(have, dict) else {}
    lacking = [
        name
        for section, names in builtin.required().items()
        for name in names
        if not isinstance(have.get(section), dict) or name not in have[section]
    ]
    if lacking:
        lines.append(f"harnesses.yaml lacks {', '.join(lacking)}; using the packaged ones")
    try:
        if any(r.id == builtin.NAMESPACE for r in config.load_repos(templates / "repos.yaml")):
            lines.append(
                f"repository id {builtin.NAMESPACE} is kraft-default's namespace; rename it"
            )
    except config.ConfigError:
        pass  # repos.yaml's own row says why
    try:
        instance = PolicyInput.from_yaml(templates / "policy.yaml").instance_policy()
        mine = next((a for a in found if builtin.is_builtin(a) and a.left_out is None), None)
        if mine and (why := addons_load.limits_problem(mine, instance)):
            lines.append(f"maxima: clamps kraft-default task(s): {why}")
    except PolicyError:
        pass  # policy.yaml's own check says why
    return lines


def signature(lines: Sequence[str]) -> str | None:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16] if lines else None


def notified(addons_root: Path) -> str | None:
    try:
        sig = json.loads((addons_root / NOTIFIED).read_text())["signature"]
    except Exception:  # noqa: BLE001 -- a file that cannot be read is "nothing sent"
        return None
    return sig if isinstance(sig, str) else None


def mark_notified(addons_root: Path, signature: str | None) -> None:
    path = addons_root / NOTIFIED
    try:
        if signature is None:
            path.unlink(missing_ok=True)
        else:
            record = {"signature": signature, "at": datetime.now(UTC).isoformat()}
            config.write_text(path, json.dumps(record))
    except Exception:  # noqa: BLE001 -- never raises; the next start retries
        logger.warning("cannot record the kraft-default drift notice", exc_info=True)
