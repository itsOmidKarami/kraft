"""What the skills say. They are the executor, so their prose is load-bearing:
each row names a skill, a numbered section of it (0 is the whole skill) and a
sentence an agent acts on, whitespace flattened. A row stays only when dropping
its sentence changes what an agent does; a sentence `kl.py` enforces anyway, or
one that only describes, is not pinned here."""

from __future__ import annotations

import pytest


def section(body: str, number: int) -> str:
    """One numbered `## N.` section of a skill, whitespace flattened; 0 is the whole skill."""
    return " ".join((body.split(f"## {number}.")[1].split("\n## ")[0] if number else body).split())


#: (skill, section) -> row id -> needle. A needle may be a tuple: every part must be there.
SAYS = {
    ("init", 0): {
        "init-migrates-only-without-bindings": (
            "it has `.kraft-lite/registry.yaml` and no `.kraft-lite/bindings.yaml`, or when"
        ),
        "init-migrate-refusal-names-the-ways-on": (
            "finish that run on the previous Kraft Lite version, or `python3 "
            '"$CLAUDE_PLUGIN_ROOT/kl.py" migrate --abandon <chain-id>`'
        ),
        "init-migrate-abandon": (
            "which closes the run's open records, labelled `kraft-abandoned`, and keeps them"
        ),
        "init-migrate-deletes-the-moved-aside-copy": "Delete `bindings.yaml.mine` once merged.",
        "init-migrate-with-both-files": (
            "move `bindings.yaml` aside (`.kraft-lite/bindings.yaml.mine`), run `migrate`",
            "With both files present and no verb refusing",
        ),
        "init-ignores-the-migrated-registry": (
            "`.kraft-lite/registry.yaml.migrated` goes there too, or is deleted"
        ),
    },
    ("start", 0): {
        "start-lists-available-chains": 'kl.py" chains --available',
        "start-chain-file-path": "a chain file path (`.yaml`, or with a `/`)",
    },
    ("next", 0): {
        "next-pre-v1-refusal-migrates": (
            "follow `kraft-lite:init`'s last section, `A pre-V1 directory: migrate`, then go on "
            "with the new id it prints"
        ),
        "next-produced-is-recorded-by-its-absolute-path": (
            "So a file a task writes there is recorded by its absolute path in the worktree.",
            "`<file>` being the absolute path where the task wrote it",
        ),
        "next-says-when-a-bound-skill-is-missing": "(skill missing)",
        "next-replays-a-nodes-tasks": "idempotent",
    },
    ("next", 1): {
        "next-scoped-verify-setup-then-test": (
            "first set it up as the repo says: a fresh one has nothing installed. Then run only "
            "the node's tasks with `task_kind` `builtin` and `ref` "
            "`kraft.verify_changed_test_scopes`"
        ),
        "next-feature-worktree-from-origin": (
            "git worktree add -B <feature branch> <path> origin/<feature branch>"
        ),
        "next-kl-from-the-controller-checkout": "runs from the controller's checkout",
        "next-feature-worktree": "never in the state branch's checkout",
        "next-archives-merged-child": (
            "tell each slice cut from its branch or from the target to rebase onto the target, and"
            " archive its child"
        ),
        "next-records-merges-after-what-they-come-after": (
            "Record a slice merged only once every slice it comes after is"
        ),
        "next-scoped-verify-never-the-whole-suite": (
            "a `prompt` task runs on their test paths, a `subprocess` `command` gets them "
            "appended, and one that takes no paths is a reason to stop and say so, never to run "
            "the whole suite"
        ),
        "next-skip-approves-a-gate-node": (
            "`KL close` with nothing run (`KL approve` on a gate node: `close` refuses one)"
        ),
        "next-scoped-verify-diffs-against-origin": (
            "`git diff --name-only origin/<base branch>...HEAD` lists there"
        ),
    },
    ("next", 2): {
        "next-exports-after-close": "export state after every `close`",
        "next-subprocess-without-a-shell": "run `command` as an argument list, without a shell",
        "next-records-what-a-task-produced": "`KL produced --kind <produces> --path <file>`",
        "next-base-moved-restarts": (
            "If `on_base_changed` is set, stop the node there and `KL restart`"
        ),
        "next-restart-cap-from-state": (
            "It refuses past `on_base_changed.cap` restarts: with `restarts` already at `cap`, "
            "stop and ask the user instead"
        ),
        "next-merge-rejected-is-reported-by-the-merge-task": (
            "The merge task can report that the forge's merge train or merge queue dropped the PR "
            "instead of merging it"
        ),
        "next-merge-rejection-cap-before-the-repair": (
            "No step failed, so spend no `attempt` and skip section 3. First, before any repair "
            "runs or anything is pushed: with `on_merge_rejected` null, or `rejections` already at"
            " its `cap`, stop and tell the user why the PR was dropped, as for `over_cap`."
        ),
        "next-merge-rejected-without-a-reason-stops": (
            "Stop the same way when the forge gave no reason a repair can act on: the PR was taken"
            " off by hand, or for a reason it does not say."
        ),
        "next-merge-rejected-repairs-then-restarts": (
            "Otherwise run `on_merge_rejected.repair.steps` as above, with the drop's reason and "
            "each failed job as their input. If they committed nothing, stop and tell the user: "
            "the forge would drop the same head again. Else push what they committed to the PR's "
            "branch, then `KL restart --merge-rejected`: it reopens the walk from "
            "`on_merge_rejected.restart_from` through this node, gates included, so the PR's "
            "checks and the final gate are answered again before the merge runs."
        ),
    },
    ("next", 3): {
        "next-on-failure-once": (
            "The node failed, `on_failure` is set and `on_failure_ran` is false: run "
            "`on_failure.steps` as in section 2, `KL mark --on-failure-ran`, then the whole node "
            "again from its first step"
        ),
        "next-builtin-or-forge-findings-go-to-the-fix-loop": (
            "Failing tests or failing checks such a task reports are findings, for the fix loop"
        ),
        "next-judge-from-the-second-repair": (
            "When it prints `attempt` 2 or more and `fix_loop.judge` is set, run the judge first"
        ),
        "next-unrepairable-error-spends-no-on-failure": "spend no attempt and no `on_failure`",
    },
    ("next", 4): {
        "next-slice-gate-to-run-slices": (
            "`gate.slice_gate` true and `sliced` null: invoke `kraft-lite:run-slices`"
        ),
        "next-opens-a-gate-node": "Otherwise `KL gate`, then invoke `kraft-lite:gate`.",
    },
    ("gate", 0): {
        "gate-reject-needs-a-note": (
            "`--note` is required: a rejection with no reason strands whoever picks the work up "
            "next."
        ),
    },
    ("gate", 1): {
        "gate-chain-revision-restarts-through-start": (
            "find the chain's source file from the frozen `.kraft-lite/chains/<chain-id>.json`: "
            "its `source` when that is a file path; otherwise the `path` that `python3 "
            '"$CLAUDE_PLUGIN_ROOT/kl.py" chains --available` lists for its `id`'
        ),
        "gate-presents-message-and-artifact": (
            "`gate.message`, and the file at `gate.artifact_path`"
        ),
        "gate-chain-revision-cannot-apply": (
            "Kraft Lite cannot apply it: the chain froze at `start`"
        ),
        "gate-restart-keeps-the-approved-spec-and-plan": (
            "`--chain <that file>` (or `repo:<new id>`), and `--spec`/`--plan` set to the spec and"
            " plan the earlier gates approved, so the new chain starts at the chain revision"
        ),
        "gate-restart-repo-chain-stays-in-the-repo-library": (
            "For a `repo:` chain, put the copy in `.kraft/chains/` with a new `id`, so its "
            "`extends` still resolve, and pass `--chain repo:<new id>`."
        ),
    },
    ("gate", 2): {
        "gate-refused-slices-asks": (
            "If it refuses, tell the user what it said and ask whether to approve unsliced."
        ),
        "gate-rewind-to-the-slice-gate-or-earlier-retires": (
            "The slice gate or earlier: the plan or its slicing is redone, so the rewind retires "
            "every slice. Archive their children as `kraft-lite:run-slices` says."
        ),
        "gate-rewind-to-the-first-build-node-reopens-first": (
            "The first build node, or a node between the slice gate and it, to redo some slices: "
            "reopen each slice to redo first,"
        ),
        "gate-reject-lands-on-reject-to": "A plain `reject` lands on `gate.reject_to`",
    },
    ("status", 0): {
        "status-early": "once the controller's review of that slice found nothing or only small",
        "status-early-template": "| can start early from <key> |",
        "status-starts-nothing": "not even a slice in `ready` or `early`",
        "status-rebase-pending": (
            "- rebase pending: <key> onto <its cut_from> (each key in `base_moved`)"
        ),
        "status-exec-node-steps": "For an exec node, its `steps` in order",
        "status-gate-node": (
            "For a gate node, `gate.message`, the file at `gate.artifact_path`, and that a "
            "rejection goes back to `gate.reject_to`."
        ),
        "status-summary-as-next-shapes-it": (
            "Render it as the table in `kraft-lite:next`'s section 5 (read it, in "
            "`../next/SKILL.md` beside this skill; do not invoke `next` from here)."
        ),
        "status-no-cost-to-estimate": (
            "There is no token count or cost: Lite never sees them, so estimate none."
        ),
        "status-hands-back-to-run-slices": (
            "When `kraft-lite:run-slices` invoked you, return to it after the report."
        ),
    },
    ("run-slices", 0): {
        "controller-no-reference-takes-the-fallbacks": (
            "With no guide for this harness, or an ability its guide marks unconfirmed, follow "
            "[discover.md](references/discover.md) before any fallback."
        ),
        "controller-slices-at-the-slice-gate": (
            "the last gate before the first build node, which `state` marks `gate.slice_gate` true"
            " (`chain_revision_approval` on the default chain, `plan_approval` on one that builds "
            "right after its plan gate). `kraft-lite:next` invokes this skill there while `sliced`"
            " is null."
        ),
        "controller-reads-the-shape-first": (
            "Read `KL slices` first: its `shape` names this chain's `slice_gate`, or says why it "
            "is `unsliceable`."
        ),
        "controller-refuses-a-pre-v1-export": (
            "An import refused as exported by an older Kraft Lite is a run from before V1 chains: "
            "tell the user it must be finished on the previous Kraft Lite version, and write "
            "nothing."
        ),
        "controller-configuration-is-a-refused-binding": (
            "A binding in `.kraft-lite/bindings.yaml` that a child's `start` or `state` refuses is"
            " configuration"
        ),
        "controller-verbs-without-a-chain-id": (
            "except `settings`, `import`, `chains`, `detect`, `migrate` and `start`"
        ),
    },
    ("run-slices", 1): {
        "controller-deletes-unused-proposal": "delete any `.kraft-lite/proposals/<chain-id>.json`",
        "controller-gate-records-the-answer": (
            "`KL gate`, then invoke `kraft-lite:gate`. It presents the gate's own artifact with "
            "the proposal beside it, and records the user's answer"
        ),
        "controller-small-plan-heuristic": "few enough tasks for one session's context",
        "controller-unsliceable-chain-walks-unsliced": (
            "Unsliceable: tell the user its reason and draw nothing. At any other node: tell the "
            "user slicing comes at `shape.slice_gate`, and draw nothing now. In both, go on with "
            "`kraft-lite:next`, so each gate gets `kraft-lite:gate`'s own question."
        ),
        "controller-rewind-retires-the-slices": (
            "A rewind to the slice gate or earlier retires the slices"
        ),
    },
    ("run-slices", 2): {
        "controller-missing-ability": "A missing ability is never a block",
    },
    ("run-slices", 3): {
        "controller-records-fork-at-start": (
            "--status starting --branch <slice-branch> --fork <sha>"
        ),
        "controller-names-the-chain-id": "the controller's chain id, then key",
        "controller-local-name": "locally its name too",
        "controller-main-mode": "### In `main` mode",
        "controller-method-stops-implementation": (
            "dispatches for its first build node under the slice layer"
        ),
        "controller-two-afters-wait-for-all-merges": (
            "A slice that comes after two or more slices starts only from `ready`, once all of "
            "them are merged"
        ),
        "controller-early-start": (
            "`early`: it comes after exactly one slice, and that slice is in review or ready to "
            "merge. It is cut from that slice's branch"
        ),
        "controller-early-start-after-a-clean-review": (
            "Start it now when your review of that slice has no findings or only small ones; "
            "otherwise it waits for `ready`."
        ),
        "controller-target-is-a-word": "where `target` is the word itself, never a branch name",
        "controller-child-wake-up-unconfirmed": (
            "write `none; continue on the controller's check-in` in its place"
        ),
        "controller-gives-the-child-its-tools": (
            'copied from your guide\'s "Be messaged" and "Check in later"'
        ),
        "controller-replacement-reads-cut-from": (
            "from the slice its `cut_from` names, or `target` when that is null"
        ),
    },
    ("run-slices", 4): {
        "controller-resume-matches-ready": (
            "and every `waiting` one in `ready` or `early`, find its session"
        ),
        "controller-resume-runs-a-check-in": (
            "then run one check-in (section 5): it reports and re-arms"
        ),
    },
    ("run-slices", 5): {
        "controller-check-in-while-unmerged": "A check-in is armed while any slice is not merged",
        "controller-check-in-message": (
            "`kraft-lite:run-slices check-in, chain <id>: read state first.`"
        ),
        "controller-records-rebased-fork": "`KL slice-set --slice <key> --fork <sha>`",
        "controller-check-in-reports": "- [ ] Report (section 8). - [ ] Re-arm the check-in",
        "controller-check-in-acts-on-base-moved": (
            "A non-empty `base_moved`: the rebase in section 6."
        ),
    },
    ("run-slices", 6): {
        "controller-rebase-only-cut-from-merged": (
            "Never one cut from another slice that is still unmerged"
        ),
        "controller-failed-review": "--status running`, and review again",
        "controller-feature-worktree": "through `kraft-lite:next`, which says where its tasks run",
        "controller-review-starts-a-review": (
            "Starting a review: `KL slice-set --slice <key> --status in_review`."
        ),
        "controller-review-then-starts-what-slices-lists": (
            "finding. Once your review has no findings or only small ones, start what `KL slices` "
            "lists, as section 3 says."
        ),
        "controller-review-runs-nothing": "run no tests or checks yourself",
        "controller-merges-after-what-it-comes-after": (
            "and only once every slice it comes after is merged"
        ),
        "controller-main-mode-names-the-merge-order": (
            "naming any slice it comes after that the user must merge first"
        ),
        "controller-merge-refused-out-of-order": (
            "It is refused while a slice it comes after is unmerged"
        ),
        "controller-done-is-what-slices-says": (
            "When `KL slices` shows `done: true` on every slice (merged; in `main` mode, ready to "
            "merge or merged):"
        ),
        "controller-base-moved-rebases-onto-the-new-tip": (
            "`KL slices` then lists in `base_moved` each slice cut early from it, and each cut "
            "early from those: once it is back in review, tell each to rebase onto the new tip of "
            "the slice its `cut_from` names, naming that slice's branch, one after another down "
            "the line"
        ),
        "controller-recorded-fork-flags-its-cuts": (
            "A recorded fork flags the slices cut from that one."
        ),
        "controller-cut-from-says-which": (
            "`cut_from` in `KL slices` says which: the parent's key, or null for the target."
        ),
    },
    ("run-slices", 7): {
        "controller-state-commit-skips-a-no-op": (
            "git -C <state-checkout> diff --cached --quiet || git -C <state-checkout> commit -m "
            '"kraft-lite state"'
        ),
    },
    ("run-slices", 8): {
        "controller-status-report-owned-by-status": "Report by invoking `kraft-lite:status`",
    },
    ("run-slices", 10): {
        "controller-no-reference-walks-here": (
            "No guide, or no session tools With no guide for this harness, or an ability its guide"
            " marks unconfirmed, follow [discover.md](references/discover.md) first. With no way "
            "to start a child even then: say so, and walk the slices here"
        ),
    },
    ("build-slice", 0): {
        "child-starts-from-the-exported-chain": (
            "git show FETCH_HEAD:kraft-lite-state/chain.json > <run>/chain.json python3 "
            '"$CLAUDE_PLUGIN_ROOT/kl.py" start --slice-of <run>/chain.json --title "<slice title>"'
        ),
        "child-never-starts-a-chain-of-its-own": "never start with `--chain` instead",
        "child-configuration-is-a-refused-binding": (
            "a binding in `.kraft-lite/bindings.yaml` that `start` or `state` refuses"
        ),
        "child-build-node-under-the-slice-layer": (
            "The first build node's tasks run under the slice layer"
        ),
        "child-done-names-its-last-node": "done | <slice key> | <your child chain's last node>",
        "child-ci-status-names-its-node": "status | <slice key> | <node>",
    },
    ("build-slice", 1): {
        "child-fork-never-head": "never `HEAD`: ask the controller for the line",
        "child-fresh-container": "### A fresh container",
        "child-fresh-container-fork": "never from `HEAD`",
        "child-keeps-pushed-work": "Never redo, reset or force-push away pushed work",
        "child-chain-file": "lives in `<run>/<slice key>/chain`, never in the `chains` list",
        "child-chain-id-from-instructions": (
            "named in the **Slice** item of your starting instructions"
        ),
        "child-target-is-a-word": "where `target` is the word itself, never a branch name",
        "child-base-is-a-word": (
            "`base` holds the word `target` or a slice key, never a branch name"
        ),
        "child-base-as-given": (
            'echo <the word after "Cut from:", as given> > <run>/<slice key>/base'
        ),
        "child-chain-id-to-every-verb-but-start": (
            "Pass your child chain's `--chain-id <id>` to every verb but `start`"
        ),
        "child-fresh-container-takes-the-remote-head": (
            "git checkout -B <your slice branch> origin/<your slice branch>"
        ),
        "child-run-folder-made-first": (
            "Run `mkdir -p <run>/<slice key>` (it makes `<run>` too), then write `<run>/spec.md` "
            "and `plan.md`"
        ),
    },
    ("build-slice", 2): {
        "child-pushes-on-the-users-words": (
            "for a push you asked the controller about, that is the permission: push, then send "
            "the controller a `status`"
        ),
    },
    ("build-slice", 3): {
        "child-over-cap-no-further-repair": (
            "Run no further repair, no further `attempt`, no `close`"
        ),
        "child-messages-name-the-controllers-chain": "never your child chain's",
    },
    ("build-slice", 4): {
        "child-wake-up-from-the-controller-item": (
            "the scheduling tool your **Controller** item names"
        ),
        "child-asks-no-one-to-continue-it": (
            "Ask no one to run a skill or a command for you, the user included"
        ),
    },
    ("build-slice", 5): {
        "child-rebase-onto-target-branch": (
            "| `target` | `kraft.mr_rebase` runs, or the controller says to | `origin/<target "
            "branch>` |"
        ),
        "child-rebase-onto-a-slices-new-tip": (
            "| a slice key | the controller says to rebase onto that slice's new tip | "
            "`origin/<that slice's branch>` |"
        ),
        "child-rebase-onto-a-remote-branch": (
            "with `<onto>` a remote branch, never a sha from a message"
        ),
        "child-new-tip-keeps-base": "Onto a slice's new tip, `base` keeps that slice's key.",
        "child-new-tip-cut-from-line": (
            "onto a slice's new tip `Cut from: <slice key> at <the new fork>`"
        ),
    },
    ("build-slice", 6): {
        "child-no-second-whole-branch-review": (
            "no final whole-branch review and no finishing, merge or PR step"
        ),
    },
}


@pytest.mark.parametrize(
    "name, number, needle",
    [
        pytest.param(name, number, needle, id=row)
        for (name, number), rows in SAYS.items()
        for row, needle in rows.items()
    ],
)
def test_a_skill_says(texts, name, number, needle):
    text = section(texts[name], number)
    for part in (needle,) if isinstance(needle, str) else needle:
        assert part in text, f"{name} section {number} lacks {part!r}"
