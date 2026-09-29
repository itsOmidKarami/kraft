"""A grant matches only one plain git invocation of its subcommand
(Kraft-4in7z): anything compound falls to the harness's own classifier."""

import pytest

from kraft.grants import matching
from kraft.policy import GRANTS

ALL = ("git-commit", "git-rebase", "git-push")
#: The work item's own branch: the one ref `git-push` may update.
B = "kraft/x"
PUSH = f"git push origin {B}"


def match(command, grants=ALL, tool="Bash"):
    return matching(grants, tool, {"command": command}, B)


@pytest.mark.parametrize(
    ("command", "grant"),
    [
        (PUSH, "git-push"),
        ("git push origin HEAD:kraft/x", "git-push"),
        ("git push --force-with-lease -u origin kraft/x", "git-push"),
        ("git push --force-with-lease=kraft/x:abc origin HEAD:refs/heads/kraft/x", "git-push"),
        ("git push my.fork_2 refs/heads/kraft/x", "git-push"),
        ("git -c user.name=x commit -m 'a b'", "git-commit"),
        ("git -c User.Email=a@b commit -m x", "git-commit"),
        ("git commit -m 'fix #12: a -x b'", "git-commit"),
        ('git commit -am "two words"', "git-commit"),
        ("git --no-pager rebase origin/main", "git-rebase"),
        ("git -P rebase -i --autosquash origin/main", "git-rebase"),
        ("git rebase -X theirs --strategy-option=ours origin/main", "git-rebase"),
        ("git rebase --no-exec origin/main", "git-rebase"),
        ("git commit -m x -- a.py", "git-commit"),
        ("git push -- origin kraft/x", "git-push"),
    ],
)
def test_a_single_git_invocation_matches_its_grant(command, grant):
    assert match(command) == grant


@pytest.mark.parametrize(
    "command",
    [
        # Operators, substitution, redirection.
        f"{PUSH}; rm -rf x",
        f"{PUSH} ; rm -rf x",
        f"{PUSH} `rm -rf x`",
        "git push origin $X",
        "git p\\ush origin kraft/x",
        f"{PUSH} ( rm -rf x )",
        f"{PUSH} && rm -rf x",
        f"{PUSH} || true",
        f"{PUSH} | tee log",
        f"{PUSH} & rm -rf x",
        f"echo $({PUSH})",
        f"echo `{PUSH}`",
        f"{PUSH} > out",
        f"{PUSH} < in",
        f"{PUSH} <(rm -rf x)",
        'git commit -m "$(rm -rf x)"',
        "git commit -m $'a\\nb'",
        f"{PUSH}\nrm -rf x",
        f"{PUSH}\rrm -rf x",
        f"{PUSH}\\\n; rm -rf x",
        f"{PUSH} \\\nrm",
        "git\tpush origin kraft/x",
        f"({PUSH})",
        f"{{ {PUSH}; }}",
        f"! {PUSH}",
        # Expansion that can turn into words the parser never saw.
        "git push origin {main,kraft/x}",
        "git push origin {main",
        "git push origin [m",
        "git push origin !!",
        "git push --rec* origin kraft/x",
        "git push origin kraft/?",
        "git push origin [k]raft/x",
        "git push origin ${X}",
        # Something other than git runs first.
        f"sh -c '{PUSH}'",
        f'bash -c "{PUSH}"',
        f"GIT_DIR=/x {PUSH}",
        f"FOO=1 {PUSH}",
        f"env {PUSH}",
        f"command {PUSH}",
        f"exec {PUSH}",
        f"sudo {PUSH}",
        "/usr/bin/git push origin kraft/x",
        "./git push origin kraft/x",
        f"#{PUSH}",
        # git options that run a command or point git somewhere else.
        "git -c core.sshCommand='rm -rf x' push origin kraft/x",
        "git -c core.hooksPath=/tmp/h commit -m x",
        "git -c alias.push='!rm -rf x' push origin kraft/x",
        "git -c protocol.ext.allow=always push ext::x kraft/x",
        "git -cuser.name=x commit",
        "git --config-env=core.sshCommand=X push origin kraft/x",
        "git --exec-path=/tmp/evil push origin kraft/x",
        "git --git-dir=/tmp/evil push origin kraft/x",
        "git --work-tree /tmp push origin kraft/x",
        "git -C",
        # -C points git at another repository, its config and hooks.
        "git -C /r push origin kraft/x",
        "git -C /r commit -m x",
        "git -C /r rebase origin/main",
        # A push goes to a remote by name, and only pushes what it names.
        "git push /tmp/evil kraft/x",
        "git push ../evil kraft/x",
        "git push git@host:evil kraft/x",
        "git push https://host/evil kraft/x",
        "git push ext::sh kraft/x",
        "git push -- /tmp/evil kraft/x",
        "git push -- -evil kraft/x",
        "git push -o x /tmp/evil kraft/x",
        "git push --push-option=x origin kraft/x",
        "git push --delete origin kraft/x",
        "git push --del origin kraft/x",
        "git push -d origin kraft/x",
        "git push -ud origin kraft/x",
        "git push origin --delete kraft/x",
        "git push --mirror origin kraft/x",
        "git push --all origin kraft/x",
        "git push --branches origin kraft/x",
        "git push --prune origin kraft/x",
        "git push --tags origin kraft/x",
        "git push --follow-tags origin kraft/x",
        "git push --recurse-submodules=on-demand origin kraft/x",
        "git push --repo=/tmp/evil kraft/x",
        "git push --repo /tmp/evil kraft/x",
        # Kraft-9efnk.15: only the item's own branch, never a plain force.
        "git push",
        "git push origin",
        "git push origin HEAD",
        "git push origin main",
        "git push origin HEAD:main",
        "git push origin kraft/xy",
        "git push origin kraft/x main",
        "git push origin kraft/x:main",
        "git push origin refs/heads/kraft/x:refs/heads/main",
        "git push origin :kraft/x",
        "git push origin +kraft/x",
        "git push origin +HEAD:kraft/x",
        "git push --force origin kraft/x",
        "git push --forc origin kraft/x",
        "git push -f origin kraft/x",
        "git push -uf origin kraft/x",
        "git -c",
        "git -c user.name=x",
        # Subcommand options that run a command of the caller's choosing.
        "git push --receive-pack='rm -rf x' origin kraft/x",
        "git push --receive-pack=x origin kraft/x",
        "git push --rec=x origin kraft/x",
        "git push --exec=x origin kraft/x",
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
        "git pushx origin kraft/x",
        "git psuh origin kraft/x",
        "git 'push;' origin kraft/x",
        "",
        "   ",
        "'unterminated",
    ],
)
def test_anything_else_matches_nothing(command):
    assert match(command) is None


def test_only_the_shell_tool_and_only_held_grants_match():
    assert match(PUSH, tool="Edit") is None
    assert match(PUSH, tool="Shell") is None
    assert match(PUSH, grants=("git-commit",)) is None
    assert match(PUSH, grants=()) is None
    assert match("git status", grants=("git-status",)) is None


def test_a_missing_or_non_string_command_matches_nothing():
    assert matching(ALL, "Bash", {}, B) is None
    assert matching(ALL, "Bash", {"command": ["git", "push", "origin", B]}, B) is None


@pytest.mark.parametrize("grant", GRANTS)
def test_every_policy_grant_has_a_matcher(grant):
    command = PUSH if grant == "git-push" else "git " + grant.removeprefix("git-")
    assert match(command, grants=GRANTS) == grant
