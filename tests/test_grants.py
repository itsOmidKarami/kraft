"""A grant matches only one plain git invocation of its subcommand
(Kraft-4in7z): anything compound falls to the harness's own classifier."""

import pytest

from kraft.grants import matching
from kraft.policy import GRANTS

ALL = ("git-commit", "git-rebase", "git-push")


@pytest.mark.parametrize(
    ("command", "grant"),
    [
        ("git push", "git-push"),
        ("git push origin HEAD:main", "git-push"),
        ("git push --force-with-lease -u origin kraft/x", "git-push"),
        ("git push --force origin HEAD:main", "git-push"),
        ("git push -f my.fork_2 kraft/x", "git-push"),
        ("git -c user.name=x commit -m 'a b'", "git-commit"),
        ("git -c User.Email=a@b commit -m x", "git-commit"),
        ("git commit -m 'fix #12: a -x b'", "git-commit"),
        ('git commit -am "two words"', "git-commit"),
        ("git --no-pager rebase origin/main", "git-rebase"),
        ("git -P rebase -i --autosquash origin/main", "git-rebase"),
        ("git rebase -X theirs --strategy-option=ours origin/main", "git-rebase"),
        ("git rebase --no-exec origin/main", "git-rebase"),
        ("git commit -m x -- a.py", "git-commit"),
        ("git push -- origin main", "git-push"),
    ],
)
def test_a_single_git_invocation_matches_its_grant(command, grant):
    assert matching(ALL, "Bash", {"command": command}) == grant


@pytest.mark.parametrize(
    "command",
    [
        # Operators, substitution, redirection.
        "git push; rm -rf x",
        "git push ; rm -rf x",
        "git push `rm -rf x`",
        "git push origin $X",
        "git p\\ush",
        "git push ( rm -rf x )",
        "git push && rm -rf x",
        "git push || true",
        "git push | tee log",
        "git push & rm -rf x",
        "echo $(git push)",
        "echo `git push`",
        "git push > out",
        "git push < in",
        "git push <(rm -rf x)",
        'git commit -m "$(rm -rf x)"',
        "git commit -m $'a\\nb'",
        "git push\nrm -rf x",
        "git push\rrm -rf x",
        "git push\\\n; rm -rf x",
        "git push \\\nrm",
        "git\tpush",
        "(git push)",
        "{ git push; }",
        "! git push",
        # Expansion that can turn into words the parser never saw.
        "git push origin {main,x}",
        "git push origin {main",
        "git push origin [m",
        "git push origin !!",
        "git push --rec*",
        "git push origin ma?n",
        "git push origin [m]ain",
        "git push origin ${X}",
        # Something other than git runs first.
        "sh -c 'git push'",
        'bash -c "git push"',
        "GIT_DIR=/x git push",
        "FOO=1 git push",
        "env git push",
        "command git push",
        "exec git push",
        "sudo git push",
        "/usr/bin/git push",
        "./git push",
        "#git push",
        # git options that run a command or point git somewhere else.
        "git -c core.sshCommand='rm -rf x' push",
        "git -c core.hooksPath=/tmp/h commit -m x",
        "git -c alias.push='!rm -rf x' push",
        "git -c protocol.ext.allow=always push ext::x",
        "git -cuser.name=x commit",
        "git --config-env=core.sshCommand=X push",
        "git --exec-path=/tmp/evil push",
        "git --git-dir=/tmp/evil push",
        "git --work-tree /tmp push",
        "git -C",
        # -C points git at another repository, its config and hooks.
        "git -C /r push",
        "git -C /r commit -m x",
        "git -C /r rebase origin/main",
        # A push goes to a remote by name, and only pushes what it names.
        "git push /tmp/evil HEAD",
        "git push ../evil HEAD",
        "git push git@host:evil HEAD",
        "git push https://host/evil HEAD",
        "git push ext::sh HEAD",
        "git push -- /tmp/evil HEAD",
        "git push -- -evil HEAD",
        "git push -o x /tmp/evil HEAD",
        "git push --push-option=x origin",
        "git push --delete origin main",
        "git push --del origin main",
        "git push -d origin main",
        "git push -fd origin main",
        "git push origin --delete main",
        "git push --mirror origin",
        "git push --all origin",
        "git push --branches origin",
        "git push --prune origin",
        "git push --repo=/tmp/evil",
        "git push --repo /tmp/evil",
        "git -c",
        "git -c user.name=x",
        # Subcommand options that run a command of the caller's choosing.
        "git push --receive-pack='rm -rf x' /tmp/r",
        "git push --receive-pack=x /tmp/r",
        "git push --rec=x /tmp/r",
        "git push --exec=x /tmp/r",
        "git rebase --exec 'rm -rf x' origin/main",
        "git rebase --exec='rm -rf x' origin/main",
        "git rebase --ex=x origin/main",
        "git rebase -x 'rm -rf x' origin/main",
        "git rebase -xtrue origin/main",
        "git rebase -ix true origin/main",
        "git rebase -s evil origin/main",
        "git rebase --strategy=evil origin/main",
        "git rebase --strat=evil origin/main",
        "git rebase -- -x true",
        # Not one of the granted subcommands.
        "git status",
        "git",
        "git --no-pager",
        "git pushx",
        "git psuh",
        "git 'push;' x",
        "",
        "   ",
        "'unterminated",
    ],
)
def test_anything_else_matches_nothing(command):
    assert matching(ALL, "Bash", {"command": command}) is None


def test_only_the_shell_tool_and_only_held_grants_match():
    assert matching(ALL, "Edit", {"command": "git push"}) is None
    assert matching(ALL, "Shell", {"command": "git push"}) is None
    assert matching(("git-commit",), "Bash", {"command": "git push"}) is None
    assert matching((), "Bash", {"command": "git push"}) is None
    assert matching(("git-status",), "Bash", {"command": "git status"}) is None


def test_a_missing_or_non_string_command_matches_nothing():
    assert matching(ALL, "Bash", {}) is None
    assert matching(ALL, "Bash", {"command": ["git", "push"]}) is None


@pytest.mark.parametrize("grant", GRANTS)
def test_every_policy_grant_has_a_matcher(grant):
    command = "git " + grant.removeprefix("git-")
    assert matching(GRANTS, "Bash", {"command": command}) == grant
