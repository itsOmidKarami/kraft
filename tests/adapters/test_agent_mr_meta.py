"""The front matter an `mr_meta` artifact is asked to carry (`agent._MR_META_KEYS`):
Kraft reads it straight into the merge request it opens."""


def test_reviewers_are_not_taken_from_codeowners(run):
    """#1294. The forge handles code owners itself and Kraft opens the merge
    request as an account CODEOWNERS may name: an agent told to copy CODEOWNERS
    into `reviewers` asked the author to review its own change."""
    cmd = run(artifact="mr_meta")["cmd"]
    prompt = cmd[cmd.index("--append-system-prompt") + 1]

    (reviewers,) = [line for line in prompt.splitlines() if line.startswith("reviewers:")]
    assert "CODEOWNERS" not in reviewers
