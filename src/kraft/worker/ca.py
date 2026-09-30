"""The Kraft CA (sandbox part 2, P4b; spec §4 "VM-backed runtimes" and §6):
one EC P-256 root under `run/ca/`, generated once, whose key never leaves
there (mode 0600, directory 0700).

It signs, for now, the daemon's loopback TLS listener (`server_cert`) and
one client certificate per egress session on a VM-backed runtime
(`mint_session_cert`), whose subject CN is the session id -- the only
identity the listener trusts, since every relay-B connection arrives from
127.0.0.1. And one server certificate per injected host (`mint_host_leaf`),
which the egress proxy shows a worker's client when it terminates TLS to
inject a credential (P6).

Every certificate carries what Python 3.13+'s `VERIFY_X509_STRICT` (on in
`ssl.create_default_context`) demands: critical basicConstraints and
keyUsage, SKI, and AKI on a leaf. An ad-hoc `openssl req -x509` CA is
refused under it (spike 4.5a); the flag is never relaxed instead.

Call it from the daemon only: generation is not locked against a second
process doing the same at once.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import re
import shutil
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def strict(context: ssl.SSLContext) -> ssl.SSLContext:
    """`context` with VERIFY_X509_STRICT on: the default of `ssl.create_default_context`
    from Python 3.13, set by hand so 3.12 verifies no more loosely."""
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    return context


_CA_DAYS = 3650
#: The listener's certificate lives as long as the root it is cached beside.
_SERVER_DAYS = _CA_DAYS
# ponytail: no revocation; the registry forgetting the session is the whole
# mechanism -- add a CRL if a leaked leaf outliving its session ever matters.
# A session that outlives this fails closed: relay B's handshake is refused.
_SESSION_DAYS = 7
#: Backdating, for a runtime VM's clock a little behind the host's.
_SKEW = datetime.timedelta(minutes=5)
#: The names relay B dials the daemon by from a VM-backed runtime (Docker
#: Desktop's, podman machine's); socat verifies the listener's certificate
#: against the one it dialled.
GATEWAY_HOSTS = ("host.docker.internal", "host.containers.internal")


def _dir(run_dirs) -> Path:
    directory = run_dirs.ca
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _write(path: Path, data: bytes, mode: int) -> None:
    """`data` at `path` whole or not at all: written to a file of its own,
    created `mode` (a key's 0600 never readable by anyone else even for an
    instant), then renamed over `path`. A reader never sees half a file,
    and a crash never leaves one where the next mint would reuse it."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.unlink(missing_ok=True)  # one a crash left, perhaps not `mode`
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    _write(path, pem, 0o600)


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    _write(path, cert.public_bytes(serialization.Encoding.PEM), 0o666)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def ensure_ca(run_dirs) -> tuple[Path, Path]:
    """`run/ca/ca.pem` and `ca.key`: generated when either is missing,
    reused forever after (no rotation: delete both to rotate by hand)."""
    directory = _dir(run_dirs)
    cert_path, key_path = directory / "ca.pem", directory / "ca.key"
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Kraft CA"))
        .issuer_name(_name("Kraft CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _SKEW)
        .not_valid_after(now + datetime.timedelta(days=_CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    _write_cert(cert_path, cert)
    return cert_path, key_path


def _leaf(
    run_dirs,
    cert_path: Path,
    key_path: Path,
    cn: str,
    *,
    usage: x509.ObjectIdentifier,
    days: int,
    san: list[x509.GeneralName] | None = None,
) -> tuple[Path, Path]:
    """One end-entity certificate for `cn`, signed by the CA, written to
    `cert_path`/`key_path`."""
    ca_cert_path, ca_key_path = ensure_ca(run_dirs)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _SKEW)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            ),
            critical=False,
        )
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    _write_key(key_path, key)
    _write_cert(cert_path, builder.sign(ca_key, hashes.SHA256()))
    return cert_path, key_path


def server_cert(run_dirs) -> tuple[Path, Path]:
    """`run/ca/server.pem` and `server.key`, the daemon listener's: CN
    `kraft-daemon`, SAN `IP:127.0.0.1` and every `GATEWAY_HOSTS` name.
    Minted once, again only when missing or older than the CA (rotated by
    hand)."""
    ca_cert_path, _ = ensure_ca(run_dirs)
    directory = _dir(run_dirs)
    cert_path, key_path = directory / "server.pem", directory / "server.key"
    if (
        cert_path.is_file()
        and key_path.is_file()
        and cert_path.stat().st_mtime >= ca_cert_path.stat().st_mtime
    ):
        return cert_path, key_path
    return _leaf(
        run_dirs,
        cert_path,
        key_path,
        "kraft-daemon",
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
        days=_SERVER_DAYS,
        san=[
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            *(x509.DNSName(name) for name in GATEWAY_HOSTS),
        ],
    )


#: One exact DNS name, lowercase: no wildcard, no port, nothing a path
#: could make more of.
_EXACT_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")


def mint_host_leaf(run_dirs, host: str) -> tuple[Path, Path]:
    """`run/ca/hosts/<host>/leaf.pem` and `leaf.key`: a server certificate
    for exactly `host` (SAN `DNSName(host)`), for the egress proxy to show a
    worker's client when it terminates TLS to inject a credential (spec §6).
    Keyed by the host as DNS compares it (case, a trailing dot), cached
    like `server_cert`: minted again only when missing or older than the
    CA. `ValueError` for anything but an exact host -- the name is a
    directory. One at a time: the proxy mints on each CONNECT's thread, and
    a CLI opens several connections at once; hold `HOST_LEAF_LOCK` around
    this and the load too, or another re-mint can land between the files."""
    name = host.lower().rstrip(".")
    if not _EXACT_HOST.fullmatch(name):
        raise ValueError(f"{host!r} is not an exact host name to mint a certificate for")
    # ponytail: one lock for every host; per-host locks if minting ever shows up.
    with HOST_LEAF_LOCK:
        return _host_leaf(run_dirs, name)


#: Held while a host leaf is checked and minted (`mint_host_leaf`), and by
#: the proxy while it loads the pair: a re-mint rewrites both files.
HOST_LEAF_LOCK = threading.RLock()


def _host_leaf(run_dirs, name: str) -> tuple[Path, Path]:
    ca_cert_path, _ = ensure_ca(run_dirs)
    directory = _dir(run_dirs) / "hosts" / name
    for d in (directory.parent, directory):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)
    cert_path, key_path = directory / "leaf.pem", directory / "leaf.key"
    if (
        cert_path.is_file()
        and key_path.is_file()
        and cert_path.stat().st_mtime >= ca_cert_path.stat().st_mtime
    ):
        return cert_path, key_path
    return _leaf(
        run_dirs,
        cert_path,
        key_path,
        name,
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
        days=_SERVER_DAYS,
        san=[x509.DNSName(name)],
    )


def session_dir(run_dirs, session_id: str) -> Path:
    """`run/ca/sessions/<session_id>/`: everything relay B mounts, and only
    that -- `client.pem`, `key.pem` and the root's `ca.pem`."""
    return run_dirs.ca / "sessions" / session_id


def mint_session_cert(run_dirs, session_id: str) -> tuple[Path, Path]:
    """The session's client certificate and key, CN = the whole session id,
    beside a copy of the CA's certificate (never its key). Idempotent: an
    existing pair is returned as it is."""
    ca_cert_path, _ = ensure_ca(run_dirs)
    directory = session_dir(run_dirs, session_id)
    for d in (directory.parent, directory):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)
    cert_path, key_path = directory / "client.pem", directory / "key.pem"
    shutil.copyfile(ca_cert_path, directory / "ca.pem")
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path
    return _leaf(
        run_dirs,
        cert_path,
        key_path,
        session_id,
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
        days=_SESSION_DAYS,
    )


def discard_session_cert(run_dirs, session_id: str) -> None:
    """Remove the session's directory, key and all. Best-effort; a no-op for
    a session that never minted one."""
    shutil.rmtree(session_dir(run_dirs, session_id), ignore_errors=True)
