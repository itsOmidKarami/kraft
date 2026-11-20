"""The gate agent text passes before Kraft posts it: the refusals (what could render,
run a forge command, link out or leak), verbatim quotes, the prose cap and the
secrets Kraft knows; then the note Kraft builds round the text (`render`)."""

from __future__ import annotations

import pytest
from support.harness import commit_all, entry_of

from kraft import redact
from kraft.adapters.forge.replies.body import (
    NOTE_MAX,
    RepoQuotes,
    check,
    known_secrets,
    render,
)
from kraft.adapters.forge.replies.facts import reply_marker

FILES = {
    "calc.py": "\n".join(
        [
            "def add(a, b):",
            "    return a + b",
            "x = '&amp;'",
            "url = 'https://example.com'",
            "key = 'ghp_" + "A" * 36 + "'",
            "def load(file: Path, data: dict):",
            "a[i][j] = handlers[name](event)",
            "errs: Vec<std::io::Error>",
            "if x<max:",
        ]
    )
    + "\n"
}
NOTES = ("Rename `x` to `total`.",)
SECRETS = {"hunter2isnotlong", "production-eu"}


class _Files:
    """`Quotes` over in-memory files, as the spec words them: a substring of one line;
    a contiguous run of whole lines."""

    def __init__(self, files: dict[str, str], notes=()):
        self.files, self.notes = files, tuple(notes)

    async def in_a_line(self, text):
        return any(text in line for body in self.files.values() for line in body.splitlines())

    async def in_whole_lines(self, lines):
        want = list(lines)
        for body in self.files.values():
            have = body.splitlines()
            if any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1)):
                return True
        return False


@pytest.mark.parametrize(
    "text, why",
    [
        ("Declining: `add` already returns the sum.", None),
        ("zero​width", "an invisible or control character"),
        ("bidi ‮", "an invisible or control character"),
        ("bell \x07", "an invisible or control character"),
        ("tab\tand\r\nbreaks are fine", None),
        ("see ![x](y)", "an image"),
        ("<img src=x>", "an image"),
        ("<!-- hidden -->", "a marker or HTML comment"),
        ("kraft-reply:w1-0123456789ab", "a marker or HTML comment"),
        ("go to https://example.com", "a link"),
        ("go to www.example.com", "a link"),
        ("go to //evil.example/x", "a link"),
        ("[here](x)", "a link"),
        ("[here][1]", "a link"),
        ("[1]: x", "a link"),
        ("<mailto:a>", "a link"),
        ("`url = 'https://example.com'`", "a link"),
        ("see file: calc.py", None),
        ("`def load(file: Path, data: dict)`", None),
        ("`a[i][j] = handlers[name](event)`", None),
        ("`errs: Vec<std::io::Error>`", None),
        ("```\nif x<max:\n```", None),
        ("x\r[1]: y", "a link"),
        ("a <b>bold</b> word", "raw HTML"),
        ("\\`<b onclick=x>\\`", "an encoded character"),
        ("```py`x\n<b>\n```", "raw HTML"),
        ("```\nx = '&amp;'\n```", None),
        ("`x = '&amp;'`", None),
        ("an &amp; in prose", "an encoded character"),
        ("an &#64; in prose", "an encoded character"),
        ("an \\* escape", "an encoded character"),
        ("```\n" + "key = 'ghp_" + "A" * 36 + "'\n```", "a token"),
        ("sk-" + "a" * 24, "a token"),
        ("AKIA" + "B" * 16, "a token"),
        ("-----BEGIN RSA PRIVATE KEY-----", "a token"),
        ("the secret is hunter2isnotlong", "a secret Kraft knows"),
        ("the secret is gnolton si2retnuh", "a secret Kraft knows"),
        ("the secret is h-u-n-t-e-r-2-i-s-n-o-t-l-o-n-g", "a secret Kraft knows"),
        ("the production eu stack fails", None),
        ("set it to production-eu", "a secret Kraft knows"),
        ("set it to ue-noitcudorp", "a secret Kraft knows"),
        ("`return a + b`", None),
        ("`return a - b`", "a quote not found verbatim"),
        ("```\ndef add(a, b):\n    return a + b\n```", None),
        ("```\ndef add(a, b):\n    return a - b\n```", "a quote not found verbatim"),
        ("> Rename `x` to `total`.", None),
        ("> Rename it all.", "a quote not found verbatim"),
        ("    def add(a, b):", None),
        ("x" * 1_501, "more than 1,500 bytes of prose"),
        # 1,489 bytes of prose and markup, plus a 31-byte verbatim quote.
        ("x" * 1_480 + "\n```\ndef add(a, b):\n    return a + b\n```", None),
        # 500 one-character spans: each is in the file, none is a quote, 1,999 bytes of prose.
        (" ".join(["`a`"] * 500), "more than 1,500 bytes of prose"),
        # A 31-byte span of real code is a quote: 1,480 bytes of prose and it still passes.
        ("x" * 1_480 + " `def load(file: Path, data: dict):`", None),
        # 100 block quotes of a 19-byte slice of a note: 2,300 bytes, 400 of them markup.
        ("\n\n".join(["> Rename `x` to `tota"] * 100), "more than 1,500 bytes of prose"),
        # 100 one-line fences of a real 14-byte line: 2,299 bytes, 800 of them markup.
        ("\n".join(["```\ndef add(a, b):\n```"] * 100), "more than 1,500 bytes of prose"),
        # 200 indented one-liners of a real 14-byte line: 4,000 bytes, 1,200 of them markup.
        ("\n\n".join(["    def add(a, b):"] * 200), "more than 1,500 bytes of prose"),
        # The floor is 20 bytes: one of 20 is a quote (1,494 bytes of prose), one of 19 is not.
        ("x" * 1_490 + "\n\n> Rename `x` to `total", None),
        ("x" * 1_490 + "\n\n> Rename `x` to `tota", "more than 1,500 bytes of prose"),
    ],
    ids=[
        "plain-prose",
        "zero-width",
        "bidi",
        "bell",
        "tab-and-line-breaks",
        "markdown-image",
        "img-tag",
        "html-comment",
        "forged-marker",
        "url",
        "www",
        "protocol-relative",
        "markdown-link",
        "link-reference",
        "link-definition",
        "autolink",
        "url-in-a-span",
        "a-bare-word-and-colon",
        "a-type-annotation-in-a-span",
        "index-chain-in-a-span",
        "generic-in-a-span",
        "comparison-in-a-fence",
        "cr-only-link-definition",
        "raw-html",
        "escaped-backtick",
        "fence-with-a-backtick-info",
        "entity-in-a-fence",
        "entity-in-a-span",
        "entity-in-prose",
        "numeric-reference",
        "backslash-escape",
        "token-in-a-fence",
        "sk-token",
        "aws-key",
        "pem-header",
        "secret-as-written",
        "secret-reversed",
        "secret-squeezed",
        "hyphenated-config-value-in-prose",
        "config-value-as-written",
        "config-value-reversed",
        "span-verbatim",
        "span-not-verbatim",
        "fence-verbatim",
        "fence-not-verbatim",
        "block-quote-of-a-note",
        "block-quote-not-verbatim",
        "indented-block-verbatim",
        "prose-over-the-cap",
        "quotes-are-not-prose",
        "many-short-spans-are-prose",
        "a-long-span-is-a-quote",
        "many-short-block-quotes-are-prose",
        "many-one-line-fences-are-prose",
        "many-short-indented-blocks-are-prose",
        "a-20-byte-quote-counts",
        "a-19-byte-quote-is-prose",
    ],
)
async def test_the_gate_refuses_what_could_render_run_or_leak(text, why):
    got = await check(text, quotes=_Files(FILES, NOTES), secrets=SECRETS)
    assert (got.why if got else None) == why


async def test_a_short_or_empty_secret_refuses_nothing():
    """The floor is at the match, so a caller's "" or "ab" cannot refuse every reply."""
    assert (
        await check(
            "a plain answer about the kraft tracker", quotes=_Files(FILES), secrets={"", "ab", "k"}
        )
        is None
    )


@pytest.mark.parametrize(
    "env, passthrough, literal, counted",
    [
        ({"ANTHROPIC_API_KEY": "dummy"}, [], {}, set()),
        ({"ANTHROPIC_API_KEY": "placeholder"}, [], {}, set()),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "abcdefghijklmnop"}, [], {}, {"abcdefghijklmnop"}),
        ({"ANTHROPIC_API_KEY": "sk-ant-Abc12345"}, [], {}, {"sk-ant-Abc12345"}),
        ({"MY_TOKEN": "s3cr3t-Value"}, ["MY_TOKEN"], {}, {"s3cr3t-Value"}),
        ({}, [], {"DB_URL": "postgres-pw-77"}, {"postgres-pw-77"}),
        ({}, [], {"CI": "1", "NODE_ENV": "production", "FLAG": "TRUE", "SHORT": "aB3$"}, set()),
        ({"HOME": "/Users/someone-long"}, [], {}, set()),
        ({"KRAFT_WORK_ITEM_ID": "w1abcdef"}, [], {}, set()),
    ],
    ids=[
        "named-short-placeholder",
        "named-plain-word-8-to-15",
        "named-16-plus-plain",
        "named-real-key",
        "passthrough",
        "literal-env",
        "plain-words-numbers-booleans-and-short",
        "baseline",
        "kraft-session-names",
    ],
)
def test_the_secrets_kraft_knows(monkeypatch, env, passthrough, literal, counted):
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)  # the developer's own key must not leak in
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    entry = entry_of({"setup_command": "", "env_passthrough": passthrough, "env": literal})
    assert known_secrets(entry) & {*env.values(), *literal.values()} == counted


@pytest.mark.parametrize(
    "registered, extra, counted",
    [
        ("forge-token-abc1", [], {"forge-token-abc1"}),
        (None, ["ab"], set()),
        (None, ["abcdefghijklmnop"], {"abcdefghijklmnop"}),
    ],
    ids=["a-registered-token", "a-short-extra", "a-long-extra"],
)
def test_registered_tokens_and_extras(registered, extra, counted):
    if registered:
        redact.register(registered)
    assert known_secrets(None, extra=extra) & {registered, *extra} == counted


_MANY = {f"a{n:03}.py": "def f():\n    return None\n" for n in range(60)}
_REAL = {"z.py": "def g(x):\n    y = x\n    return None\n"}


@pytest.mark.parametrize(
    "files, quote, block, found",
    [
        ({"calc.py": "def add(a, b):\n    return a + b\n"}, "return", False, True),
        ({"calc.py": "def add(a, b):\n"}, "no such text", False, False),
        (
            {"calc.py": "def add(a, b):\n    return a + b\n"},
            ["def add(a, b):", "    return a + b"],
            True,
            True,
        ),
        (
            {"calc.py": "def add(a, b):\n    return a + b\n"},
            ["    return a + b", "def add(a, b):"],
            True,
            False,
        ),
        ({**_MANY, **_REAL}, ["    y = x", "    return None"], True, True),
        ({"calc.py": "x\n"}, ["", ""], True, True),
        (
            {"win.py": "def add(a, b):\r\n    return a + b\r\n"},
            ["def add(a, b):", "    return a + b"],
            True,
            True,
        ),
    ],
    ids=[
        "in-a-line",
        "absent",
        "whole-lines-in-order",
        "out-of-order",
        "common-anchor-with-many-hits",
        "all-blank-block",
        "crlf-file",
    ],
)
async def test_repo_quotes_search_the_tracked_files_at_the_head(repo, files, quote, block, found):
    for name, body in files.items():
        (repo / name).write_bytes(body.encode())  # bytes: keep a CRLF file's CRLF
    head = commit_all(repo, "files")
    for name in files:
        (repo / name).write_text("")  # the worktree no longer holds it: the head still does
    quotes = RepoQuotes(repo, head)

    ask = quotes.in_whole_lines(quote) if block else quotes.in_a_line(quote)
    assert await ask is found


WJ = "\u2060"
LEAD, ITEM, MARK = "Declined:", "w1abcdef", reply_marker("w1abcdef-0123456789ab")
FOOT = f"\n\n---\n_Posted by [Kraft](https://kraft-sdlc.dev/) for work item `{ITEM}`._\n\n{MARK}"


@pytest.mark.parametrize(
    "text, body",
    [
        ("plain", "plain"),
        ("one\r\ntwo\rthree", "one\ntwo\nthree"),
        ("/approve", f"{WJ}/approve"),
        ("  /merge now", f"  {WJ}/merge now"),
        ("```\n/close\n```", f"```\n{WJ}/close\n```"),
        ("ask @alice", f"ask @{WJ}alice"),
        ("ask @_team and @.ops", f"ask @{WJ}_team and @{WJ}.ops"),
        ("see #12 and !34", f"see #{WJ}12 and !{WJ}34"),
        ('~bug %"v1" $77 &epic', f'~{WJ}bug %{WJ}"v1" ${WJ}77 &{WJ}epic'),
        ("owner/repo@abc1234", f"owner/repo@{WJ}abc1234"),
        ("*iteration:3", f"*{WJ}iteration:3"),
        ("[vulnerability:1]", f"[{WJ}vulnerability:1]"),
        ("PROJ-123 is open", f"PROJ-{WJ}123 is open"),
        ("a # heading and 50% off", "a # heading and 50% off"),
        ("`@Override`", f"`@{WJ}Override`"),
        ("tok aB3dE5fG7hJ9kL1mN3pQ5rS7 end", "tok [redacted] end"),
        ("tok aB3dE5fG7hJ9-QX-1kL1mN3pQ5r end", "tok [redacted] end"),
        ("sha " + "a1b2c3d4" * 5, "sha " + "a1b2c3d4" * 5),
        ("test_a_long_snake_case_name_here", "test_a_long_snake_case_name_here"),
        (
            "see src/kraft/adapters/forge/V2Replies/Body3",
            "see src/kraft/adapters/forge/V2Replies/Body3",
        ),
    ],
    ids=[
        "plain",
        "line-breaks",
        "slash-command",
        "indented-slash",
        "slash-in-code",
        "mention",
        "mention-underscore-and-dot",
        "issue-and-mr-refs",
        "label-milestone-snippet-epic",
        "user-at-sha",
        "iteration",
        "vulnerability",
        "jira-key",
        "no-sigil-target",
        "inside-code-too",
        "high-entropy",
        "a-token-with-a-key-shape-inside",
        "a-full-sha-is-kept",
        "an-identifier-is-kept",
        "mixed-case-path",
    ],
)
def test_the_note_is_neutralised_led_signed_and_marked(text, body):
    assert render(text, lead=LEAD, work_item_id=ITEM, marker=MARK) == f"{LEAD}\n\n{body}{FOOT}"


def test_a_note_too_long_is_cut_closing_its_fence_before_the_footer():
    note = render("```\n" + "x = 1\n" * 20_000 + "```", lead=LEAD, work_item_id=ITEM, marker=MARK)

    assert len(note.encode()) <= NOTE_MAX
    assert note.endswith(FOOT)
    before_footer = note[: -len(FOOT)]
    assert before_footer.count("```") % 2 == 0


def test_a_cut_never_splits_a_character():
    note = render("é" * 40_000, lead=LEAD, work_item_id=ITEM, marker=MARK)
    assert len(note.encode()) <= NOTE_MAX and "\ufffd" not in note
