import io
import json
import logging
import secrets

import pytest
from rich.console import Console
from rich.logging import RichHandler
from support.store_fixtures import mk_item
from uvicorn.logging import AccessFormatter

from kraft import builtins, events, redact
from kraft.adapters.forge.git import run_git
from kraft.adapters.forge.models import ForgeError
from kraft.api import deps


@pytest.fixture(autouse=True)
def _no_secrets(monkeypatch):
    monkeypatch.setattr(redact, "_secrets", ())


async def _forge_error(tok, **_):
    return str(ForgeError(f"x {tok}"))


async def _git_stderr(tok, tmp_path, **_):
    with pytest.raises(ForgeError) as caught:
        # Through env, so the token reaches the message only by git's stderr.
        await run_git(tmp_path, ["sh", "-c", 'echo "$T" >&2; exit 1'], env={"T": tok})
    return str(caught.value)


async def _node_log(tok, database, run_dirs, **_):
    await mk_item(database)
    _, log, result = await builtins.start_session(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="n",
        hook_point="on",
        round=1,
    )
    await builtins.finish_session(
        database,
        log,
        result,
        session_id="s1",
        work_item_id="w1",
        status="done",
        log=f"before {tok} after\n",
    )
    return log.read_text()


async def _event_payload(tok, database, **_):
    await mk_item(database)
    await database.write(lambda c: events.append(c, "w1", "node_started", {"detail": tok}))
    return database.read(
        lambda c: c.execute("SELECT payload FROM events WHERE type = 'node_started'").fetchone()[0]
    )


async def _crash_stop_reason(tok, database, **_):
    # A direct git run's RuntimeError carries raw stderr into guard's stop reason.
    async def crash():
        raise RuntimeError(f"could not restore: {tok}")

    await mk_item(database)
    await deps.guard(database, "w1", crash())
    return database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'work_item_needs_human'"
        ).fetchone()[0]
    )


async def _kraft_log(tok, caplog, **_):
    # Rich as the MCP SDK sets it up: it draws a traceback from exc_info itself.
    rich = io.StringIO()
    handler = RichHandler(console=Console(file=rich, width=500), rich_tracebacks=True)
    logger = logging.getLogger("kraft.test")
    logger.addHandler(handler)
    try:
        try:
            raise RuntimeError(f"git said {tok}")
        except RuntimeError:
            logger.exception("could not stage: %s", tok)
        logger.warning("could not commit: %s", RuntimeError(f"stderr {tok}"))
    finally:
        logger.removeHandler(handler)
    return caplog.text + rich.getvalue()


@pytest.mark.parametrize(
    "sink",
    [
        pytest.param(_forge_error, id="forge-error"),
        pytest.param(_git_stderr, id="git-stderr"),
        pytest.param(_node_log, id="node-log"),
        pytest.param(_event_payload, id="event-payload"),
        pytest.param(_crash_stop_reason, id="crash-stop-reason"),
        pytest.param(_kraft_log, id="kraft-log"),
    ],
)
@pytest.mark.parametrize(
    "tok",
    [
        pytest.param(secrets.token_hex(16), id="plain"),
        # Stored JSON-escaped in an event payload, so registered in that form too.
        pytest.param(f'ab"cd-{secrets.token_hex(4)}-\u00e9', id="json-escaped"),
    ],
)
async def test_a_registered_token_never_reaches_a_sink(
    sink, tok, database, run_dirs, tmp_path, caplog
):
    redact.register(tok)
    text = await sink(tok, database=database, run_dirs=run_dirs, tmp_path=tmp_path, caplog=caplog)
    assert tok not in text and "<redacted>" in text
    escaped = json.dumps(tok)[1:-1]
    assert escaped not in text


def test_a_short_secret_is_never_registered():
    with pytest.raises(ValueError):
        redact.register("abc")


def test_a_secret_inside_another_never_leaves_the_longer_ones_tail():
    redact.register("abcdefgh")
    redact.register("abcdefgh-12345678")
    assert redact.redact("x abcdefgh-12345678 y") == "x <redacted> y"


def test_nothing_registered_leaves_text_untouched():
    text = "a token-like abcdefgh12345678"
    assert redact.redact(text) is text


def test_a_log_record_keeps_the_args_a_formatter_unpacks(caplog):
    redact.register(secrets.token_hex(16))
    logger = logging.getLogger("uvicorn.access")
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/health", "1.1", 200)
    record = caplog.records[-1]
    assert AccessFormatter("%(request_line)s %(status_code)s").format(record) == (
        "GET /health HTTP/1.1 200 OK"
    )
