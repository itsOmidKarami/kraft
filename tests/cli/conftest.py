import asyncio

import pytest

from kraft import client


@pytest.fixture
def make_item(app):
    """`make_item(repo, title=...)`: file one paused work item on `repo`
    through the API the `app` fixture wires up, and return its id. Connects
    `repo` first: intake files only against a connected repo (Kraft-ta8nv)."""

    def make(repo, title="a thing"):
        async def go():
            async with client.transport.http() as http:
                connected = await http.post(
                    "/api/repos",
                    json={"path": str(repo), "setup_command": "", "test_command": "true"},
                )
                assert connected.status_code in (201, 409), connected.text
                response = await http.post(
                    "/api/work-items", json={"title": title, "repo": str(repo), "autostart": False}
                )
            assert response.status_code == 201, response.text
            return response.json()["id"]

        return asyncio.run(go())

    return make
