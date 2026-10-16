#!/usr/bin/env python
"""Fill a running dev instance with work items in every interesting state.

Runs against the HTTP API rather than writing rows: the data is then whatever
the real executor produces, so it cannot drift from the schema or from the
states the code can actually reach. Expects `just dev` to be running, which
puts the fake agent (fixtures/fake-claude.sh) ahead of the real one on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

HOME = Path(os.environ.get("KRAFT_HOME") or ".dev").resolve()
REPO = HOME / "repo"
RICH = "--rich" in sys.argv
BASE = f"http://127.0.0.1:{os.environ.get('KRAFT_PORT', '8765')}"

CALC = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
MCP_JSON = '{"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}}\n'

#: title, template, expected end state. The instruction the agent receives is the
#: title, so KRAFT_FAIL / KRAFT_SLOW in a title steer that item's fake agent
#: without touching the ones running beside it.
ITEMS = [
    ("Fix add() so the tests pass", "quick-task", "completed"),
    ("Rewrite the parser KRAFT_FAIL", "quick-task", "failed"),
    ("Design the caching layer", "default", "gate"),
    ("Investigate the flaky import test KRAFT_SLOW", "quick-task", "paused"),
]


def git(*args: str, repo: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=repo or REPO, check=True, capture_output=True)


def build_repo(repo: Path | None = None) -> None:
    """A throwaway repo with the bug fake-claude.sh knows how to fix."""
    repo = repo or REPO
    if repo.exists():
        # A dev repo built before the seed committed its own registration.
        if not (repo / ".mcp.json").exists():
            (repo / ".mcp.json").write_text(MCP_JSON)
            git("add", ".mcp.json", repo=repo)
            git("commit", "-qm", "register kraft for the fake agent", repo=repo)
        say(f"reusing {repo}")
        return
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", repo=repo)
    git("config", "user.email", "dev@kraft.local", repo=repo)
    git("config", "user.name", "Kraft Dev", repo=repo)
    (repo / "calc.py").write_text(CALC)
    (repo / "test_calc.py").write_text(TEST)
    # A Claude launch is refused unless something registers Kraft's MCP server
    # (`registration.permission_tool`). Committed here, the seed repo carries its
    # own registration, so a dev instance works on a machine that never ran
    # `kraft admin init` or installed the plugin. The fake agent never calls it.
    (repo / ".mcp.json").write_text(MCP_JSON)
    git("add", "-A", repo=repo)
    git("commit", "-qm", "initial", repo=repo)
    # Intake calls `bd create` in KRAFT_BD_CWD; without a workspace here it would
    # fall through to whichever beads DB the launch directory belongs to. Without
    # bd at all, intake files no bead and the item runs anyway (executor.entry),
    # so a seed that needs no beads -- the VS Code integration tests in CI, which
    # keeps bd out of every job but e2e-cli -- skips it.
    if shutil.which("bd"):
        subprocess.run(
            ["bd", "init", "--non-interactive", "--prefix", "devseed"],
            cwd=repo,
            # Pinned: REPO may sit inside a checkout that tracks its own .beads/,
            # whose config bd init would otherwise inherit.
            env={**os.environ, "BEADS_DIR": str(repo / ".beads")},
            check=True,
            capture_output=True,
        )
    say(f"built {repo}")


def say(msg: str) -> None:
    """Flushed, so progress shows up even when stdout is a pipe (`| tee`)."""
    print(f"seed: {msg}", flush=True)


def describe(item: dict) -> tuple[str, str]:
    """(state, where) of a work-item detail: the end state in the vocabulary
    ITEMS uses, and where the item is right now -- its current node and whether
    an agent is live.

    A pending gate and a dead agent are both `needs_human` on the row;
    `pending_gate` tells them apart, and only while the gate is still waiting.
    """
    node = item.get("current_node_id") or "-"
    live = any(s["status"] == "running" for s in item.get("worker_sessions") or [])
    where = f"node {node}" + (", agent running" if live else "")
    status = item["status"]
    if item.get("archived_at"):
        return "archived", where
    if status == "abandoned":
        return "cancelled", where
    if status == "active" and live:
        return "running", where
    if status != "needs_human":
        return status, where
    return ("gate" if item.get("pending_gate") else "failed"), where


def snapshot(client: httpx.Client, wid: str) -> tuple[str, str]:
    return describe(client.get(f"/work-items/{wid}").raise_for_status().json())


#: How often `settle` says it is still waiting when nothing has changed.
HEARTBEAT = 10.0


def settle(client: httpx.Client, wid: str, want: str, timeout: float = 90.0) -> str:
    """Poll until the item reaches `want`, saying so on every change of state
    or node, and every HEARTBEAT seconds while nothing moves -- a seed waiting
    on a slow item must not look the same as a hung one."""
    start = time.monotonic()
    deadline = start + timeout
    seen, where, last, beat = "active", "-", None, start
    while time.monotonic() < deadline:
        seen, where = snapshot(client, wid)
        now = time.monotonic()
        if (seen, where) != last:
            say(f"{wid[:8]} {seen} ({where})")
            last, beat = (seen, where), now
        if seen == want:
            return seen
        if now - beat >= HEARTBEAT:
            say(
                f"{wid[:8]} still {seen} ({where}), want {want}"
                f" -- {now - start:.0f}s of {timeout:.0f}s"
            )
            beat = now
        time.sleep(0.5)
    say(f"{wid[:8]} gave up after {timeout:.0f}s: still {seen} ({where}), wanted {want}")
    return seen


def pause_mid_flight(client: httpx.Client, wid: str, timeout: float = 30.0) -> str:
    """Pause only applies to a running item, so wait for a live agent first.

    The item's title carries KRAFT_SLOW, which keeps its fake agent asleep long
    enough for this to be a wait rather than a race.
    """
    deadline = time.monotonic() + timeout
    item: dict = {"worker_sessions": []}
    while time.monotonic() < deadline:
        item = client.get(f"/work-items/{wid}").raise_for_status().json()
        statuses = {s["status"] for s in item["worker_sessions"]}
        if "running" in statuses:
            say(f"{wid[:8]} agent running, pausing it")
            client.post(f"/work-items/{wid}/pause").raise_for_status()
            return settle(client, wid, "paused", timeout=15)
        if statuses and not statuses & {"pending", "running"}:
            # Its agent came and went between two polls: nothing left to pause.
            say(f"{wid[:8]} its agent exited before it could be paused")
            return describe(item)[0]
        time.sleep(0.3)
    # Filed paused at capacity, it never ran: its row says "paused" too, but it
    # is a not-started item, not the mid-flight pause this was asked for.
    if not item["worker_sessions"]:
        say(f"{wid[:8]} no agent started within {timeout:.0f}s")
        return "never started"
    say(f"{wid[:8]} its agent never got past pending within {timeout:.0f}s")
    return describe(item)[0]


def settle_order(created: list) -> list:
    """Paused items first: one can only be paused while its KRAFT_SLOW agent is
    still asleep, and `pause_mid_flight` waits on that running session itself.
    Settled after the others, it waited behind their settle timeouts instead.
    `main` files them in this order too: filed last, a paused item lost the
    capacity race (`max_concurrent`) and was filed paused without ever running."""
    return sorted(created, key=lambda item: item[2] != "paused")


def connect(client: httpx.Client, path: Path) -> None:
    say(f"connecting {path} to {BASE}")
    # The default chain's verification refuses to guess a test command, and its
    # merge-request half needs a forge: `fake` is the dev-only in-process one
    # (Ruling 147). `uv run` finds this checkout's venv above `.dev/`.
    resp = client.post(
        "/repos",
        json={
            "path": str(path),
            "setup_command": "",
            "test_command": "uv run pytest -q",
            "forge": "fake",
        },
    )
    if resp.status_code not in (201, 409):
        resp.raise_for_status()
    if resp.status_code == 409:
        # An older `.dev` connected the repo before it declared a forge and a test
        # command; keeping that entry leaves the default chain unable to verify or
        # open a merge request, and the rows below read BAD with no hint why.
        entry = next(
            (r for r in client.get("/repos").json()["repos"] if r["path"] == str(path)), {}
        )
        if entry.get("forge") != "fake" or not entry.get("test_command"):
            say("the dev repo predates forge: fake -- run `just dev-reset` first")


def report(rows: list, ok: bool) -> int:
    shown = [
        (wid, title if len(title) <= 80 else title[:79] + "…", w, g) for wid, title, w, g in rows
    ]
    width = max(len(r[1]) for r in shown)
    for wid, title, want, got in shown:
        mark = "ok " if want == got else "BAD"
        print(f"{mark} {wid}  {title:<{width}}  want={want:<9} got={got}")
    say(BASE)
    # A seed that quietly produces four identical rows is worse than no seed.
    return 0 if ok else 1


# --- rich -------------------------------------------------------------------
# `--rich` fills the instance for judging the dashboard under load: about forty
# items over every board state, the extremes of text and length, a chain thirty
# nodes long, three repos (one on a long path), and a review page with a wide
# diff and long threads. The plain seed above is unchanged.

#: Analytics' windows count from each row's own clock, which the API never lets a
#: caller set; the rich seed's usage therefore all lands in the current week.
RICH_CONCURRENCY = 16

LONG_NODES = [
    "read_the_brief",
    "survey_the_code",
    "sketch_the_approach",
    ("approach_approval", "Approve the approach before any code is written."),
    "write_the_schema",
    "build_the_models",
    "wire_the_api",
    "add_validation",
    "handle_errors",
    "write_the_migrations",
    ("migrations_approval", "Review the migrations before they touch a real database."),
    "build_the_client",
    "add_caching",
    "add_retries",
    "add_metrics",
    "add_tracing",
    "write_unit_tests",
    "write_integration_tests",
    "fix_flaky_tests",
    "profile_hot_paths",
    ("performance_approval", "Approve the performance numbers."),
    "update_the_docs",
    "write_the_changelog",
    "update_examples",
    "harden_security",
    "audit_dependencies",
    "final_cleanup",
    ("final_approval", "Sign off on the whole change."),
    "prepare_release_notes",
    "tag_the_release",
]
LONG_GATES = [n[0] for n in LONG_NODES if isinstance(n, tuple)]


def chain_yaml(chain_id: str, nodes: list) -> str:
    """A chain of agent steps and gates; a gate sends a rejection back to the step before it."""
    out = [f"id: {chain_id}", "", "nodes:"]
    previous = None
    for node in nodes:
        if isinstance(node, tuple):
            out += [
                f"  - id: {node[0]}",
                "    kind: gate",
                f"    message: {node[1]}",
                f"    reject_to: {previous}",
            ]
        else:
            out += [
                f"  - id: {node}",
                "    kind: exec",
                "    tasks:",
                "      - id: work",
                "        extends: implementer",
            ]
            previous = node
    return "\n".join(out) + "\n"


def write_rich_config(client: httpx.Client) -> None:
    """The long chains and a higher concurrency, in the dev instance's own config
    and reloaded into the running server; the packaged config/ is never touched."""
    chains = HOME / "config" / "chains"
    chains.mkdir(parents=True, exist_ok=True)
    (chains / "long-haul.yaml").write_text(chain_yaml("long-haul", LONG_NODES))
    (chains / "review-change.yaml").write_text(
        chain_yaml(
            "review-change",
            ["implementation", ("change_approval", "Review the change before it ships.")],
        )
    )
    policy = HOME / "config" / "policy.yaml"
    text = policy.read_text()
    policy.write_text(
        re.sub(r"(?m)^max_concurrent:\s*\d+", f"max_concurrent: {RICH_CONCURRENCY}", text)
    )
    client.post("/apply/reload").raise_for_status()


def add_rich_base_files(repo: Path) -> None:
    """The files the wide change deletes, renames and overwrites."""
    if (repo / "legacy.txt").exists():
        return
    (repo / "legacy.txt").write_text("retired in favour of the new handlers\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "old-notes.md").write_text(
        "# Notes\n\n" + "".join(f"- note {n}: keep this wording stable\n" for n in range(1, 21))
    )
    (repo / "assets").mkdir(exist_ok=True)
    (repo / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRold\x00\x01")
    git("add", "-A", repo=repo)
    git("commit", "-qm", "add notes, a logo and a legacy file", repo=repo)


def rich_repos() -> list[Path]:
    """The dev repo, a second one, and one under a long directory path."""
    store = HOME / "repos" / "web-storefront"
    deep = HOME.joinpath(
        "clients", "acme-industries-international", "platform-engineering", "services",
        "billing-reconciliation", "monorepo-checkout-main",
    )  # fmt: skip
    repos = [REPO, store, deep]
    for repo in repos:
        build_repo(repo)
        add_rich_base_files(repo)
    return repos


def paragraphs(n: int, topic: str) -> str:
    base = (
        f"The {topic} has grown past what one person can hold in their head. Today it "
        "is split across three modules that each re-implement the same lookup, and "
        "the copies have started to disagree about what an empty value means.\n\n"
        "The change should leave one lookup, one definition of empty, and a test that "
        "pins both. Please keep the public names stable: other teams import them, and "
        "a rename would turn a small cleanup into a migration.\n\n"
        "Out of scope: the legacy exporter, which is scheduled for removal next "
        "quarter, and anything under vendor/. If something in there looks wrong, note "
        "it in the summary and leave it alone.\n\n"
        "Context for whoever reviews this: naïve café résumé handling, 日本語のファイル名, "
        "and emoji such as 🚀 or 🎉 in paths all appear in real data, so none of them "
        "may be mangled on the way through.\n\n"
        "Done means the full suite passes, the new test fails without the change, and "
        "the diff reads in one sitting."
    )
    return "\n\n".join(base.split("\n\n")[: max(n, 1)])


LONG_TITLE = (
    "Make the nightly reconciliation job tolerate partially delivered upstream files without "
    "restarting from scratch, and report which ledger rows it had to skip and why"
)
TOKEN = "src/kraft_polish/internationalization/translations/right_to_left/plural_rules.py"
TOKEN_TITLE = f"Split {TOKEN} before it grows again"
assert len(LONG_TITLE) > 150 and len(TOKEN) >= 80  # noqa: PT018 -- guards the fixtures themselves


KEYWORD = re.compile(r"\s*KRAFT_[A-Z]+(?:_[A-Z]+)*(?:_[\d.]+)?")


def rich_items(repos: list[Path]) -> list[dict]:
    """The work to file. `want` is the end state; `key` lets others come after it."""
    a, b, c = (str(r) for r in repos)
    q = "quick-task"
    items: list[dict] = []

    def add(title, want, repo=a, chain=q, desc="", **extra):
        items.append(
            {"title": title, "want": want, "repo": repo, "chain": chain, "desc": desc, **extra}
        )

    # Paused first: it can only be paused while its agent hangs, and a hung agent
    # holds a slot until then.
    for title, repo in [
        ("Investigate the flaky import test KRAFT_HANG", a),
        ("Try the new queue backend behind a flag KRAFT_HANG", b),
        ("Spike: can the exporter stream instead of buffering? KRAFT_HANG", c),
        (
            "Waiting on a decision about the data model that keeps moving KRAFT_HANG",
            a,
        ),
    ]:
        add(title, "paused", repo, desc=paragraphs(2, "importer"))

    add(
        "Rotate the API signing keys KRAFT_HANG",
        "running",
        a,
        key="rotate",
        desc=paragraphs(3, "key store"),
    )
    add(
        f"{LONG_TITLE} KRAFT_HANG",
        "running",
        c,
        key="long",
        desc=paragraphs(5, "reconciliation job"),
    )
    add("Move the avatar upload to the CDN KRAFT_HANG", "running", b, key="avatar")

    for title, cost, repo in [
        ("Fix add() so the tests pass", 0.42, a),
        ("Add retry with backoff to the sync client", 1.85, b),
        ("Tidy the changelog generator", 0.07, a),
        ("Replace the hand-rolled date parsing with the stdlib", 3.10, c),
        ("Document the export flags", 0.19, b),
        ("Bump the linter and fix what it finds", 2.40, a),
        (LONG_TITLE, 0.95, c),
        (TOKEN_TITLE, 0.31, a),
        ("修复支付页面在日语环境下的字符截断问题,并补充回归测试", 0.63, b),
        ("Résumé export: keep “smart quotes” and emoji 🚀🎉 in filenames", 1.20, a),
    ]:
        add(f"{title} KRAFT_COST_{cost}", "completed", repo, desc=paragraphs(4, "exporter"))
    # Four more finish and are then archived.
    for title, repo in [
        ("Drop the unused feature flags", a),
        ("Rename the config loader's private helpers", b),
        ("Pin the CI image by digest", c),
        ("Remove the deprecated v1 webhooks", a),
    ]:
        add(f"{title} KRAFT_COST_0.5", "archived", repo)

    for title, repo in [
        ("Rewrite the parser KRAFT_FAIL", a),
        ("Make the search index rebuild incremental KRAFT_FAIL", b),
        (f"{LONG_TITLE} KRAFT_FAIL", c),
        ("Handle the 503s from the payments sandbox KRAFT_FAIL", a),
        ("Port the settings page to the new form library KRAFT_FAIL", b),
    ]:
        add(title, "failed", repo, desc=paragraphs(3, "parser"))
    for title, repo in [
        ("Add SAML single sign-on KRAFT_FAIL", a),
        ("Backfill the audit log KRAFT_FAIL", b),
        ("Switch the mailer to the new provider KRAFT_FAIL", a),
        ("Cache the permissions lookup KRAFT_FAIL", c),
    ]:
        add(title, "cancelled", repo, reason="Superseded by the platform team's rewrite.")

    for key, title in [
        ("rotate", "Re-encrypt stored tokens with the new key"),
        ("long", "Delete the old reconciliation tables"),
        ("avatar", "Purge the old avatar bucket"),
        ("rotate", "Announce the key rotation to customers"),
    ]:
        add(title, "blocked", b if key == "avatar" else a, after=key)

    for title, repo in [
        ("Design the caching layer", a),
        ("Design the notification preferences screen", b),
        (f"Design the audit trail for {TOKEN}", c),
    ]:
        add(title, "gate", repo, chain="default", desc=paragraphs(3, "design"))
    add(
        "Ship the reconciliation overhaul across every service",
        "gate",
        c,
        chain="long-haul",
        gate="performance_approval",
        desc=paragraphs(5, "overhaul"),
    )
    add(
        "Start the reporting rework",
        "gate",
        a,
        chain="long-haul",
        gate="approach_approval",
        desc=paragraphs(2, "reporting"),
    )
    add(
        "Rework the pluralization tables KRAFT_MANY_FILES KRAFT_COST_2.2",
        "gate",
        a,
        chain="review-change",
        gate="change_approval",
        review=True,
        desc=paragraphs(4, "pluralization tables"),
    )
    return items


def pending_gate(item: dict) -> str | None:
    gate = item.get("pending_gate")
    return gate.get("id") if isinstance(gate, dict) else gate


def reach_gate(client: httpx.Client, wid: str, gate: str, timeout: float = 300.0) -> str:
    """Approve every gate on the way to `gate` and wait there."""
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        item = client.get(f"/work-items/{wid}").raise_for_status().json()
        here = pending_gate(item)
        if here != seen and here:
            say(f"{wid[:8]} at gate {here}")
            seen = here
        if here == gate:
            return "gate"
        if here:
            client.post(f"/work-items/{wid}/gates/{here}/approve").raise_for_status()
        elif describe(item)[0] in {"failed", "completed", "cancelled"}:
            return describe(item)[0]
        time.sleep(0.5)
    return "timeout"


SUGGESTION = "\n".join(
    [
        "def handle(value, *, strict=False):",
        '    """Return the value normalised for comparison, or raise when strict."""',
        "    if value is None:",
        "        if strict:",
        '            raise ValueError("value is required")',
        '        return ""',
        "    text = str(value).strip()",
        "    if not text:",
        "        if strict:",
        '            raise ValueError("value is blank")',
        '        return ""',
        '    return unicodedata.normalize("NFKC", text).casefold()',
    ]
    * 3
)
LONG_PATH = (
    "src/kraft_polish/internationalization/translations/right_to_left_languages/"
    "pluralization_rules/very_long_module_name_for_checking_truncation_in_the_file_tree_sidebar.py"
)


def review_threads(client: httpx.Client, wid: str) -> None:
    """Threads for the wide change: every label, one with a dozen replies, a long
    suggestion, an old-side and a rename comment, resolved and open ones, drafts left over."""

    def thread(path, line, body, label=None, side="new", end=None, **extra):
        resp = client.post(
            f"/work-items/{wid}/threads",
            json={
                "file_path": path,
                "side": side,
                "start_line": line,
                "end_line": end or line,
                "body": body,
                "label": label,
                **extra,
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]

    noisy = thread(
        "src/pkg/module_02.py",
        2,
        "Why does this return `value + 2`? It reads like a magic number.",
        "question",
    )
    must = thread(
        "src/pkg/module_01.py",
        1,
        "Normalise before comparing. The suggestion is long on purpose: a tall one, narrow column.",
        "must_fix",
        end=3,
        suggestion={"start_line": 1, "end_line": 3, "replacement": SUGGESTION},
    )
    nit = thread(
        "src/pkg/module_03.py", 3, "Nit: a trailing comma would keep the next diff smaller.", "nit"
    )
    long_path = thread(
        LONG_PATH,
        12,
        "RULE_012 and RULE_013 say the same thing in different words.",
        "question",
        end=14,
    )
    gone = thread(
        "legacy.txt",
        1,
        "Is anything still importing this? A search found nothing; a second look would help.",
        "question",
        side="old",
    )
    renamed = thread(
        "docs/guide/notes.md",
        4,
        "Moving this also changed the heading level in the old copy; was that deliberate?",
    )
    done = thread(
        "src/pkg/module_10.py",
        1,
        "Docstring says `case 10` but the function is `handler_10`. Fine, just noting.",
        "nit",
    )
    done_too = thread("src/pkg/module_11.py", 3, "Should this clamp at zero?", "question")
    for n in range(12, 20):
        thread(
            f"src/pkg/module_{n}.py",
            3,
            f"Comment {n}: same pattern here, same answer as above.",
            ["nit", "question", None][n % 3],
        )

    for n in range(1, 13):
        who = "Replying to myself" if n % 2 else "Following up"
        client.post(
            f"/threads/{noisy}/comments",
            json={
                "body": f"{who} ({n}/12): the constant comes from the pluralization table "
                "(RULE_002), and a longer note wraps: " + "it keeps going " * (n % 4 + 1)
            },
        ).raise_for_status()
    client.post(
        f"/work-items/{wid}/review",
        json={
            "outcome": "comment",
            "summary": "A first pass; the must-fix and the questions are the ones that matter.",
        },
    ).raise_for_status()
    time.sleep(1)
    for tid in (done, done_too):
        client.post(f"/threads/{tid}/resolve").raise_for_status()
    for n in range(1, 4):
        client.post(
            f"/threads/{noisy}/comments", json={"body": f"A later reply, still a draft ({n})."}
        ).raise_for_status()
    # Left as drafts: a new thread and a reply the review has not published.
    thread("src/pkg/module_20.py", 2, "A draft thread: not submitted yet.", "nit")
    client.post(f"/threads/{nit}/comments", json={"body": "A draft reply."}).raise_for_status()
    say(f"{wid[:8]} review threads: {len(client.get(f'/work-items/{wid}/threads').json())}")
    _ = (long_path, gone, renamed, must)


def rich(client: httpx.Client) -> int:
    repos = rich_repos()
    for repo in repos[1:]:
        connect(client, repo)
    write_rich_config(client)

    ids: dict[str, str] = {}
    filed = []
    for n, spec in enumerate(rich_items(repos), 1):
        # The fake agent reads the description as well as the title, so the keywords
        # that steer it ride there and the board shows titles as a person wrote them.
        keywords = [k.strip() for k in KEYWORD.findall(spec["title"])]
        spec["title"] = KEYWORD.sub("", spec["title"]).strip()
        body = {
            "title": spec["title"],
            "description": "\n\n".join(filter(None, [spec["desc"], *keywords])),
            "repo": spec["repo"],
            "chain_template": spec["chain"],
            "autostart": True,
        }
        if spec.get("after"):
            body["depends_on"] = [ids[spec["after"]]]
        wid = client.post("/work-items", json=body).raise_for_status().json()["id"]
        if spec.get("key"):
            ids[spec["key"]] = wid
        say(f"{wid[:8]} filed {spec['title'][:70]!r} ({spec['chain']}), want {spec['want']}")
        filed.append((n, wid, spec))

    rows, ok = [], True
    total = len(filed)
    for n, wid, spec in filed:
        want, title = spec["want"], spec["title"]
        say(f"[{n}/{total}] waiting for {wid[:8]} to reach {want}")
        if want == "paused":
            got = pause_mid_flight(client, wid, timeout=60)
        elif want == "gate":
            got = (
                reach_gate(client, wid, spec["gate"])
                if spec.get("gate")
                else settle(client, wid, want)
            )
            if spec.get("review") and got == want:
                review_threads(client, wid)
        elif want == "cancelled":
            settle(client, wid, "failed")
            client.post(
                f"/work-items/{wid}/cancel", json={"reason": spec["reason"]}
            ).raise_for_status()
            got = settle(client, wid, want, timeout=20)
        elif want == "archived":
            settle(client, wid, "completed")
            client.post(f"/work-items/{wid}/archive").raise_for_status()
            got = settle(client, wid, want, timeout=20)
        else:
            got = settle(client, wid, want)
        ok &= got == want
        rows.append((wid[:8], title, want, got))
    return report(rows, ok)


def main() -> int:
    build_repo()
    # `just dev` builds the repo before the server starts: KRAFT_BD_CWD points at
    # it, and intake shells out to `bd` there on the first work item.
    if "--repo-only" in sys.argv:
        return 0
    # Every JSON route lives under /api/ (Kraft-psuq); baked into base_url so
    # every call below stays a bare "/work-items"-style path.
    client = httpx.Client(base_url=f"{BASE}/api", timeout=30.0)
    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError as exc:
        sys.exit(f"seed: no dev server on {BASE} ({exc}). Start one with `just dev`.")

    connect(client, REPO)
    if RICH:
        return rich(client)

    created = []
    rows, ok = [], True
    # A paused item is filed and paused before the rest are filed, so its slot
    # is free again by the time they take theirs.
    for n, (title, template, want) in enumerate(settle_order(ITEMS), 1):
        wid = (
            client.post(
                "/work-items",
                json={
                    "title": title,
                    "repo": str(REPO),
                    "chain_template": template,
                    "autostart": True,
                },
            )
            .raise_for_status()
            .json()["id"]
        )
        say(f"{wid[:8]} filed {title!r} ({template}), want {want}")
        if want == "paused":
            say(f"[{n}/{len(ITEMS)}] waiting for {wid[:8]} to start, to pause it")
            got = pause_mid_flight(client, wid)
            ok &= got == want
            rows.append((wid[:8], title, want, got))
        else:
            created.append((n, wid, title, want))

    for n, wid, title, want in created:
        say(f"[{n}/{len(ITEMS)}] waiting for {wid[:8]} to reach {want}")
        got = settle(client, wid, want)
        ok &= got == want
        rows.append((wid[:8], title, want, got))

    return report(rows, ok)


if __name__ == "__main__":
    sys.exit(main())
