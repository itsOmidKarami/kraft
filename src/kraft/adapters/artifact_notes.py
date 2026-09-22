"""What a document kind is for, in the chain that runs it: part of the contract
`agent._ARTIFACT` states, never of the method.

A task's `skill:` is the operator's to name -- `kraft:plan` by default, or any
plugin's plan method -- and a method written for an interactive session plans
a full-suite run and ends by asking a human what next. The chain already runs
the suite in verification, and there is no human in this session, so that is
said here, where swapping the method cannot lose it (Kraft-35u4m.3, .4).
"""

NOTES = {
    "plan": (
        "\n\nThis plan is carried out by Kraft, not in this session: a later "
        "node implements it task by task on this work item's branch, and a "
        "verification node after it runs the repository's test suite and a "
        "code review, with a fix loop of its own. Give each task the targeted "
        "test that proves it and the command that runs that test; do not add a "
        "step that runs the whole suite. Whatever method you follow, no human "
        "is here to choose how the plan is executed: write it and report your "
        "status."
    ),
    "spec": (
        "\n\nThis spec is carried out by Kraft, not in this session: later "
        "nodes plan it and implement it on this work item's branch, and before "
        "a merge request opens a verification node runs the repository's test "
        "suite and a code review. Name the tests the change must add; the "
        "chain runs them. Whatever method you follow, no human is here to "
        "answer questions as you go or to hand the spec on to: ask every "
        "question at once with needs_context, or decide, write the spec and "
        "report your status."
    ),
}
