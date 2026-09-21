import asyncio

import pytest

from kraft import client


@pytest.fixture
def make_item(app):
    """`make_item(repo, title=...)`: file one paused work item on `repo`
    through the API the `app` fixture wires up, and return its id."""

    def make(repo, title="a thing"):
        async def go():
            async with client.transport.http() as http:
                response = await http.post(
                    "/api/work-items", json={"title": title, "repo": str(repo), "autostart": False}
                )
            assert response.status_code == 201, response.text
            return response.json()["id"]

        return asyncio.run(go())

    return make
