"""`kraft doctor`'s CA row for a `kind: os` repository."""

from types import SimpleNamespace

from kraft import doctor


def test_doctor_os_row(monkeypatch):
    # Whatever the daemon's SSL_CERT_FILE says, os trusts the host's store.
    monkeypatch.setenv("SSL_CERT_FILE", "/does/not/exist.pem")
    repo = SimpleNamespace(name="r", path="/r", effective_sandbox=SimpleNamespace(kind="os"))
    row = doctor._ignored_ca_check(repo)
    assert row["ok"] and not row["warn"]
    assert "trust store" in row["detail"] and "ca_bundle is not used" in row["detail"]
