"""Every agent launch a template directory's chains resolve to, resolved the
way `executor.dispatch` resolves one."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from kraft import harness as _harness
from kraft.adapters import agent
from kraft.adapters.agent import Invocation
from kraft.executor import fallback
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import AgentTask


def agent_launches(
    templates: Path, harnesses: _harness.HarnessSet | None = None
) -> Iterator[tuple[str, str, Invocation]]:
    """(chain, task path, invocation) for every agent task of every chain in
    `templates` -- each fallback candidate too -- through
    `agent.resolve_agent_task`. The profile table is read from
    `KRAFT_TEMPLATES_DIR`, as at a launch: point it at `templates` first."""
    harnesses = harnesses if harnesses is not None else _harness.load(None)
    table = agent.harness_table(harnesses)[0]
    library = TemplateLibrary.from_yaml_dir(templates)
    for cid in library.chain_ids:
        chain = library.resolve_chain(cid)
        for node in chain.nodes:
            for t in node.tasks():
                if not isinstance(t.task, AgentTask):
                    continue
                for cand in fallback.candidates(t.task, table):
                    inv = agent.resolve_agent_task(
                        cand, None, None, harnesses=harnesses, steering=chain.steering
                    )
                    yield cid, t.path, inv


def distinct_launches(
    templates: Path, harnesses: _harness.HarnessSet | None = None
) -> dict[tuple[str, str, str | None, str | None], str]:
    """Each distinct (harness, command, model, effort) the chains in
    `templates` launch, mapped to the first `chain:task path` launching it --
    so a real-CLI check pays once per combination, not once per task."""
    seen: dict = {}
    for cid, path, inv in agent_launches(templates, harnesses):
        seen.setdefault((inv.harness, inv.command, inv.model, inv.effort), f"{cid}:{path}")
    return seen
