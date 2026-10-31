"""A real HTTPS git remote on 127.0.0.1: every bare repo under `root`, served through CGI to
`git http-backend`, behind a throwaway CA. For a test that proves what git sends over the
wire. Seed and inspect the bare repos through their paths, never over HTTPS."""

from __future__ import annotations

import datetime
import http.server
import ipaddress
import os
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from kraft.adapters.forge import credentials


def cert(subject: str, key, issuer=None, issuer_key=None, *, ca: bool):
    now = datetime.datetime.now(datetime.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer.subject if issuer else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if issuer_key is not None:
        # Python's default context verifies strictly (VERIFY_X509_STRICT).
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


@dataclass
class GitHttps:
    url: str  # https://127.0.0.1:<port>
    ca: Path
    root: Path
    #: Every request's `Authorization` (None when absent), in order.
    authorizations: list[str | None] = field(default_factory=list)
    #: Every request's whole `Authorization` list (`headers.get_all`).
    all_authorizations: list[list[str]] = field(default_factory=list)
    #: When set, a GET of `info/refs` answers 302 to this base plus the same path.
    redirect: str | None = None
    _server: http.server.ThreadingHTTPServer | None = None

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _backend() -> str:
    path = Path(
        subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()
    )
    exe = path / "git-http-backend"
    if not exe.exists():
        raise FileNotFoundError(f"git http-backend is missing ({exe}); the HTTPS tests need it")
    return str(exe)


@contextmanager
def git_https(root: Path, *, token: str | None = None, forge: str = "gitlab") -> Iterator[GitHttps]:
    """Serve every bare repo under `root`. With `token`, a request whose `Authorization` is not
    that token's Basic form gets 401."""
    backend = _backend()
    root.mkdir(parents=True, exist_ok=True)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = cert("Kraft test CA", ca_key, ca=True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = cert("127.0.0.1", leaf_key, ca, ca_key, ca=False)
    pems = root.parent / f"tls-{root.name}"
    pems.mkdir(parents=True, exist_ok=True)
    ca_pem, leaf_pem, key_pem = pems / "ca.pem", pems / "leaf.pem", pems / "leaf.key"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    want = f"Basic {credentials.basic_form(token, 'pat', forge=forge)}" if token else None
    remote = GitHttps("", ca_pem, root)

    class Handler(http.server.BaseHTTPRequestHandler):
        # Sized replies: a close that ends the body reads to GnuTLS as a truncated session.
        protocol_version = "HTTP/1.1"

        def _serve(self):
            remote.authorizations.append(self.headers.get("Authorization"))
            remote.all_authorizations.append(self.headers.get_all("Authorization") or [])
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            path, _, query = self.path.partition("?")
            if remote.redirect and path.endswith("/info/refs"):
                self.send_response(302)
                self.send_header("Location", remote.redirect + self.path)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if want is not None and self.headers.get("Authorization") != want:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            env = {
                "PATH": os.environ["PATH"],
                "GIT_PROJECT_ROOT": str(root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_ADDR": "127.0.0.1",
                # receive-pack is off unless the repo or the caller says on.
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.receivepack",
                "GIT_CONFIG_VALUE_0": "true",
            }
            # git gzips its upload-pack POSTs; http-backend inflates only when told.
            if encoding := self.headers.get("Content-Encoding"):
                env["HTTP_CONTENT_ENCODING"] = encoding
            if protocol := self.headers.get("Git-Protocol"):
                env["GIT_PROTOCOL"] = protocol
            done = subprocess.run([backend], input=body, env=env, capture_output=True)
            head, _, payload = done.stdout.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            status = 200
            headers = []
            for line in lines:
                key, _, value = line.partition(": ")
                if key.lower() == "status":
                    status = int(value.split()[0])
                elif key:
                    headers.append((key, value))
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _serve

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(leaf_pem, key_pem)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    remote._server = server
    remote.url = f"https://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield remote
    finally:
        remote.stop()
        shutil.rmtree(pems, ignore_errors=True)
