import base64
import secrets

import pytest
from support.store_fixtures import mk_item

from kraft import builtins, events, redact
from kraft.adapters.forge.git import run_git
from kraft.adapters.forge.models import ForgeError


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


async def _basic_form(tok, **_):
    basic = base64.b64encode(f"oauth2:{tok}".encode()).decode()
    redact.register(basic)
    return str(ForgeError(f"Authorization: Basic {basic}"))


@pytest.mark.parametrize(
    "sink",
    [
        pytest.param(_forge_error, id="forge-error"),
        pytest.param(_git_stderr, id="git-stderr"),
        pytest.param(_node_log, id="node-log"),
        pytest.param(_event_payload, id="event-payload"),
        pytest.param(_basic_form, id="basic-form"),
    ],
)
async def test_a_registered_token_never_reaches_a_sink(sink, database, run_dirs, tmp_path):
    tok = secrets.token_hex(16)
    redact.register(tok)
    text = await sink(tok, database=database, run_dirs=run_dirs, tmp_path=tmp_path)
    assert tok not in text and "<redacted>" in text
    if sink is _basic_form:
        assert base64.b64encode(f"oauth2:{tok}".encode()).decode() not in text


def test_a_short_secret_is_never_registered():
    with pytest.raises(ValueError):
        redact.register("abc")
