import pytest

from kraft.index import db as index_db


@pytest.fixture
def conn(tmp_path):
    """An open search index at `tmp_path/index.db`, closed after the test;
    the `Indexer`'s first argument, alongside the `database` fixture."""
    connection = index_db.open_index(tmp_path / "index.db")
    yield connection
    connection.close()
