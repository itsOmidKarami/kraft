import pytest

from kraft.addons import load


@pytest.fixture(autouse=True)
def _contain_unrestored_stores():
    """`load._unrestored` remembers why a store could not be put back, by its
    digest, for the life of the process. The fixtures build the same add-on,
    so the same digest, in every test: one that fails a restore on purpose
    would otherwise make a later test in that worker see "could not be
    restored" where the store is only missing."""
    yield
    load._unrestored.clear()
