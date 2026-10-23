"""`runs.asgi_call`: a machine's API request made in-process, as the session its connection holds."""

from support.api import _post_default

from kraft.remote import runs as runs_mod


def test_a_forwarded_request_is_answered_by_the_app_as_the_session_it_names(client, repo):
    wid = _post_default(client, repo)
    call = runs_mod.asgi_call(client.app)

    status, headers, body = client.portal.call(call, "GET", f"/api/work-items/{wid}", f"{wid}/s1")

    assert status == 200 and headers["content-type"] == "application/json"
    assert f'"id":"{wid}"' in body.decode().replace(" ", "")


def test_the_app_wires_runs_and_the_attach_listener_at_startup(client):
    runs = runs_mod.current()
    assert runs is client.app.state.workers_hub.runs and runs is not None
    assert client.app.state.workers_listener.attached == set()
