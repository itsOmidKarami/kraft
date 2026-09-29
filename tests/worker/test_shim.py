"""The sandbox's `kraft` shim (`kraft.worker.shim`), run by real `sh` and
`curl` against a local HTTP server standing in for the sandbox's proxy: it
reaches http://kraft/w/<verb> the way it does in a container, through
`http_proxy`."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from kraft import permission_hooks
from kraft.worker import shim

SHIM = shim.HOST_DIR / "kraft"


class _Kraft:
    """The proxy's side: every request recorded as (target, form), each
    answered `status` with `body`."""

    def __init__(self):
        self.seen: list[tuple[str, dict]] = []
        self.status, self.body = 200, b'{"ok": true}'
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
                outer.seen.append((self.path, {k: v[0] for k, v in form.items()}))
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def kraft():
    server = _Kraft()
    yield server
    server.server.shutdown()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(tmp_path, port, *argv, stdin=""):
    # A HOME of its own: no .curlrc of the machine running the tests.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "http_proxy": f"http://127.0.0.1:{port}",
    }
    return subprocess.run(
        ["sh", str(SHIM), *argv], input=stdin, capture_output=True, text=True, env=env, timeout=30
    )


_ODD = "a & b=c+d %20\nnext line é"


@pytest.mark.parametrize(
    ("argv", "verb", "form"),
    [
        (["item", "progress", "3"], "progress", {"task": "3", "id": ""}),
        (["item", "progress", "3", "w-x", "--json"], "progress", {"task": "3", "id": "w-x"}),
        (
            ["item", "reply", "t1", "--claim", "fixed", "--body", _ODD],
            "reply",
            {"thread": "t1", "body": _ODD, "claim": "fixed"},
        ),
        (["item", "reply", "t1", "--body="], "reply", {"thread": "t1", "body": "", "claim": ""}),
        (["item", "retry", "--steer", _ODD], "retry", {"id": "", "steer": _ODD}),
        (["view", "show"], "show", {"id": ""}),
        (["view", "threads", "w-x", "--open"], "threads", {"id": "w-x", "open": "1"}),
        (["view", "diff", "--stat"], "diff", {"id": ""}),
        (
            ["view", "compare", "--from", "attempt:1", "--to=latest", "--nodes", "a,b"],
            "compare",
            {"id": "", "from": "attempt:1", "to": "latest", "nodes": "a,b"},
        ),
    ],
    ids=[
        "progress",
        "progress-id",
        "reply",
        "reply-empty",
        "retry",
        "show",
        "threads",
        "diff",
        "compare",
    ],
)
def test_a_verb_posts_its_fields_url_encoded_and_prints_the_answer(
    tmp_path, kraft, argv, verb, form
):
    done = _run(tmp_path, kraft.port, *argv)
    assert (done.returncode, done.stdout) == (0, '{"ok": true}'), done.stderr
    assert kraft.seen == [(f"http://kraft/w/{verb}", form)]


def test_the_hook_posts_its_stdin_as_it_came_and_prints_the_answer(tmp_path, kraft):
    stdin = json.dumps({"tool_name": "Shell", "tool_input": {"command": "a && b +c"}}) + "\n"
    kraft.body = b'{"permission": "allow"}'
    done = _run(tmp_path, kraft.port, "admin", "permission-hook", "cursor", stdin=stdin)
    assert (done.returncode, done.stdout) == (0, '{"permission": "allow"}'), done.stderr
    assert kraft.seen == [("http://kraft/w/permission-hook", {"harness": "cursor", "stdin": stdin})]


@pytest.mark.parametrize("harness", sorted(permission_hooks.TRANSLATORS))
@pytest.mark.parametrize("failure", ["unreachable", "refused"])
def test_the_hook_fails_closed_in_its_harness_own_deny(tmp_path, kraft, harness, failure):
    """Every harness with a hook has its deny here: one added to the
    translators without a literal in the shim fails this, not open. Exit 2,
    the one failure a Claude-shaped hook treats as a block."""
    port = _closed_port() if failure == "unreachable" else kraft.port
    kraft.status, kraft.body = 502, b"kraft: the hook was not answered\n"
    done = _run(tmp_path, port, "admin", "permission-hook", harness, stdin="{}")
    deny, _ = permission_hooks.TRANSLATORS[harness].render("deny", "Kraft could not be reached")
    assert done.returncode == 2, done.stderr
    assert json.loads(done.stdout) == json.loads(deny)
    assert "Kraft could not answer" in done.stderr


@pytest.mark.parametrize("failure", ["unreachable", "refused"])
def test_any_other_verb_fails_loudly(tmp_path, kraft, failure):
    port = _closed_port() if failure == "unreachable" else kraft.port
    kraft.status, kraft.body = 403, b"kraft: not this session's to call\n"
    done = _run(tmp_path, port, "item", "progress", "1")
    assert done.returncode != 0
    assert done.stderr
    if failure == "refused":
        assert done.stdout == "kraft: not this session's to call\n"


def test_a_verb_the_sandbox_does_not_have_posts_nothing(tmp_path, kraft):
    done = _run(tmp_path, kraft.port, "item", "approve", "w-x")
    assert done.returncode == 2
    assert "not available in a sandbox: kraft item approve" in done.stderr
    assert kraft.seen == []


def test_the_hook_reads_no_curlrc_the_worker_could_have_written(tmp_path, kraft):
    """HOME is the worker's to write: a .curlrc there sending the hook's
    call to a server of the worker's own is not read."""
    impostor = _Kraft()
    impostor.body = b'{"permission": "allow"}'
    (tmp_path / ".curlrc").write_text(f'proxy = "http://127.0.0.1:{impostor.port}"\n')
    kraft.body = b'{"permission": "deny"}'
    try:
        done = _run(tmp_path, kraft.port, "admin", "permission-hook", "cursor", stdin="{}")
    finally:
        impostor.server.shutdown()
    assert done.stdout == '{"permission": "deny"}'
    assert impostor.seen == []
