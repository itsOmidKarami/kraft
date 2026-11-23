"""The forge's vocabulary (`adapters/forge/models.py`)."""

import subprocess

import pytest

from kraft.adapters import forge


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("gh pr create failed: HTTP 401: Bad credentials", "forge_auth"),
        ("glab mr create failed: 401 Unauthorized", "forge_auth"),
        ("gh api failed: HTTP 403: Resource not accessible by personal access token", "forge_auth"),
        ("To get started with GitHub CLI, please run:  gh auth login", "forge_auth"),
        ("fatal: Authentication failed for 'https://github.com/o/r.git/'", "forge_auth"),
        ("fatal: could not read Username for 'https://github.com'", "forge_auth"),
        ("git@github.com: Permission denied (publickey).", "forge_auth"),
        ("fatal: unable to access 'x': Could not resolve host: github.com", "forge_unreachable"),
        ("gh pr list timed out after 60s", "forge_unreachable"),
        ("connect: Connection refused", "forge_unreachable"),
        # A credential the forge refused wins over text that also reads as unreachable.
        ("Authentication failed; Connection timed out", "forge_auth"),
        ("rebase conflict: could not apply abc123", None),
        ("fatal: unable to access 'x': The requested URL returned error: 403", "forge_auth"),
        ("remote: Invalid username or password.", "forge_auth"),
        ("remote: HTTP Basic: Access denied", "forge_auth"),
        # git's own rebase and checkout, not a forge: a timeout is not an unreachable host, a
        # file name is not a refused credential.
        ("git rebase timed out after 600s for /x/wt/abc", None),
        (
            "untracked 403 forbidden.txt would be overwritten by the base branch; move it\n"
            "git rebase could not start in /x/wt/abc, so nothing was rebased",
            None,
        ),
        ("git worktree add timed out after 600s for /x/wt/abc", None),
        ("", None),
    ],
)
def test_failure_cause_reads_a_credential_or_reachability_failure(text, cause):
    assert forge.failure_cause(text) == cause


_NO_REMOTE = (
    "fatal: Could not read from remote repository.\n\n"
    "Please make sure you have the correct access rights"
)


@pytest.mark.parametrize(
    ("output", "dropped"),
    [
        # The incident's three, word for word.
        (f"Connection to github.com closed by remote host.\n{_NO_REMOTE}", True),
        (f"client_loop: send disconnect: Broken pipe\n{_NO_REMOTE}", True),
        (
            'Post "https://api.github.com/graphql": read tcp 192.168.1.126:51619->'
            "140.82.121.6:443: read: connection reset by peer",
            True,
        ),
        ("ssh: connect to host github.com port 22: Operation timed out", True),
        ("ssh: Could not resolve hostname github.com: nodename nor servname", True),
        ("fatal: unable to access 'x': Could not resolve host: github.com", True),
        ("dial tcp: lookup api.github.com: no such host", True),
        ("net/http: TLS handshake timeout", True),
        ("kex_exchange_identification: Connection closed by remote host", True),
        ("send-pack: unexpected disconnect while reading sideband packet", True),
        ("batch response: read tcp 10.0.0.2:5->1.2.3.4:443: i/o timeout", True),
        (
            "fatal: unable to access 'https://github.com/o/r.git/': "
            "Recv failure: Connection reset by peer",
            True,
        ),
        (
            'LFS: Put "https://h/lfs/objects/a.bin": read tcp 1.1.1.1:2->3.3.3.3:443: '
            "write: broken pipe",
            True,
        ),
        # An apostrophe opens no quote, so the cause before a quoted name still counts.
        ("the server's connection reset by peer on 'origin'", True),
        # A file or URL that merely contains a marker is not a cause.
        ('ERROR: LFS: Put "https://h/lfs/objects/connection reset.bin": EOF', False),
        ("error: failed to push 'docs/broken pipe.bin'", False),
        ("error: failed to push 'broken pipe.bin'", False),
        ("LFS: upload failed: https://h/no%20such/no-route/connection-reset/broken/pipe", False),
        ("LFS: missing /tmp/up/connection reset.bin", False),
        # git's ambiguous line, alone: unsure is a real failure.
        (_NO_REMOTE, False),
        (f"git@github.com: Permission denied (publickey).\n{_NO_REMOTE}", False),
        # A refused credential wins over a line that reads as a drop.
        ("Permission denied (publickey).\nConnection closed by 140.82.121.4 port 22", False),
        ("remote: HTTP Basic: Access denied\nconnection reset", False),
        ("HTTP 401: Bad credentials", False),
        ("HTTP 403: Resource not accessible", False),
        ("! [rejected] kraft/x -> kraft/x (stale info)", False),
        ("! [rejected] main -> main (non-fast-forward)", False),
        ("! [remote rejected] x -> x (pre-receive hook declined)", False),
        ("CONFLICT (content): Merge conflict in a.py", False),
        ("ERROR: Repository not found.", False),
        ("HTTP 422: Validation failed: connection reset", False),
        ("", False),
    ],
)
def test_dropped_connection_reads_only_a_call_the_network_cut_off(output, dropped):
    assert forge.dropped_connection(output) is dropped


@pytest.mark.parametrize(
    ("args", "stderr", "no_answer"),
    [
        (["git", "push", "origin", "b"], "client_loop: send disconnect: Broken pipe", True),
        (["git", "fetch", "origin"], "fatal: connection reset by peer", True),
        # A title on the command line is not what the CLI said.
        (["git", "push", "origin", "broken pipe"], "! [rejected] (stale info)", False),
        # Only a command that calls the forge can be cut off by the network.
        (["git", "rebase", "main"], "write error: Broken pipe", False),
    ],
)
async def test_run_git_marks_a_call_the_network_cut_off(
    tmp_path, monkeypatch, args, stderr, no_answer
):
    done = subprocess.CompletedProcess(args, 1, "", stderr)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: done)
    with pytest.raises(forge.ForgeError) as raised:
        await forge.git.run_git(tmp_path, args)
    assert raised.value.no_answer is no_answer


@pytest.mark.parametrize(
    ("args", "no_answer"),
    [
        (["git", "push", "origin", "b"], True),
        (["git", "lfs", "pre-push", "origin", "b"], True),
        (["git", "lfs", "version"], False),
        (["git", "rebase", "main"], False),
    ],
)
async def test_only_a_forge_call_running_out_of_time_is_the_network(
    tmp_path, monkeypatch, args, no_answer
):
    def expired(*a, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(subprocess, "run", expired)
    with pytest.raises(forge.ForgeError, match="timed out") as raised:
        await forge.git.run_git(tmp_path, args, timeout=1)
    assert raised.value.no_answer is no_answer


@pytest.mark.parametrize(
    ("text", "record", "cause"),
    [
        ("boom", {"reason": "forbidden"}, "forge_auth"),
        ("boom", {"reason": "auth"}, "forge_auth"),
        ("boom", {"reason": "unreachable"}, "forge_unreachable"),
        ("boom", {"reason": "tls"}, "forge_unreachable"),
        # A rate limit is no login failure, whatever its text says.
        ("HTTP 403: API rate limit exceeded", {"reason": "rate_limited"}, None),
        ("HTTP 401 Unauthorized", {"reason": "not_found"}, None),
        # No reason (the CLI sent no request) or no record: the text decides.
        ("gh auth login", {"reason": None, "status": None}, "forge_auth"),
        ("gh auth login", None, "forge_auth"),
    ],
    ids=[
        "forbidden",
        "auth",
        "unreachable",
        "tls",
        "rate-limited",
        "not-found",
        "no-reason",
        "none",
    ],
)
def test_failure_cause_reads_the_forge_error_record_before_the_text(text, record, cause):
    assert forge.failure_cause(text, record) == cause


@pytest.mark.parametrize(
    ("status", "headers", "reason", "message"),
    [
        (401, {}, "auth", ""),
        (403, {}, "forbidden", ""),
        (403, {"x-ratelimit-remaining": "0"}, "rate_limited", ""),
        (403, {"retry-after": "30"}, "rate_limited", ""),
        (403, {}, "rate_limited", "You have exceeded a secondary rate limit."),
        (429, {}, "rate_limited", ""),
        (404, {}, "not_found", ""),
        (409, {}, "conflict", ""),
        (502, {}, "unreachable", ""),
        (200, {}, None, ""),
    ],
    ids=[
        "401",
        "403",
        "403-ratelimit",
        "403-retry-after",
        "403-secondary-limit-message",
        "429",
        "404",
        "409",
        "5xx",
        "ok",
    ],
)
def test_reason_for_maps_status_and_headers(status, headers, reason, message):
    assert forge.reason_for(status, headers, message) == reason


def test_head_moved_carries_its_reason():
    assert forge.HeadMoved("x", "abc").reason == "head_moved"


@pytest.mark.parametrize(
    ("kw", "want"),
    [({}, ("", "")), ({"account": "corp", "source": "env X"}, ("corp", "env X"))],
    ids=["default-empty", "carries-account-and-source"],
)
def test_forge_error_carries_its_account_and_source(kw, want):
    err = forge.ForgeError("x", **kw)
    assert (err.account, err.source) == want
