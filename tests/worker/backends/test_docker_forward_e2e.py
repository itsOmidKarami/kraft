"""The CA bundle against a real container runtime, docker and podman: the one
proof that a client inside the container trusts a CA only the host knows,
through the bundle `docker_forward.prepare` built from the image's own roots,
and still trusts those roots."""

import datetime
import http.server
import ipaddress
import os
import ssl
import subprocess
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from kraft.worker.backends import docker, docker_forward

#: Python's `ssl` reads `SSL_CERT_FILE`, the variable that *replaces* the
#: store: the case a bundle of the extra CA alone would break.
IMAGE = "docker.io/library/python:3.14-slim"

FETCH = """
import ssl, sys, urllib.request
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)
print(opener.open(sys.argv[1], timeout=10).read().decode())
print(len(ssl.create_default_context().get_ca_certs()))
"""


def _cert(subject: str, key, issuer=None, issuer_key=None, *, ca: bool):
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
        )
    return builder.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture
def tls_server(tmp_path):
    """An HTTPS server on the host's 127.0.0.1, its certificate signed by a
    throwaway CA: `(url, ca.pem)`."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("Kraft test CA", ca_key, ca=True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = _cert("127.0.0.1", leaf_key, ca, ca_key, ca=False)
    ca_pem, leaf_pem, key_pem = tmp_path / "ca.pem", tmp_path / "leaf.pem", tmp_path / "leaf.key"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Hello(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"kraft-tls-ok")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hello)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(leaf_pem, key_pem)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_address[1]}/", ca_pem
    server.shutdown()
    server.server_close()


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def runtime_with_ca(request, tmp_path, monkeypatch, tls_server):
    """The runtime under test, made this machine's through `sandbox.yaml`
    with the test CA as its `ca_bundle`, and `IMAGE` present in it. Skips
    where the image cannot be had, unless KRAFT_E2E_REQUIRE names the CLI."""
    cli = request.param
    have = subprocess.run([cli, "image", "inspect", IMAGE], capture_output=True, text=True)
    if have.returncode != 0:
        pulled = subprocess.run([cli, "pull", "-q", IMAGE], capture_output=True, text=True)
        if pulled.returncode != 0:
            why = f"e2e: {cli} could not pull {IMAGE}: {pulled.stderr.strip()}"
            if cli in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
                pytest.fail(why)
            pytest.skip(why)
    templates = tmp_path / "templates"
    templates.mkdir(exist_ok=True)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    (templates / "sandbox.yaml").write_text(f"cli: {cli}\nca_bundle: {tls_server[1]}\n")
    monkeypatch.setattr(docker, "_RUNTIME", docker.detect_runtime())
    return tls_server[0]


async def test_a_container_trusts_the_hosts_extra_ca_and_its_image_roots(runtime_with_ca, tmp_path):
    url = runtime_with_ca
    backend = docker.DockerBackend()
    sandbox = {"kind": "docker", "image": IMAGE}
    assert await backend.probe(sandbox, "python3", None) is None
    bundle = await backend.prepare(sandbox)
    workdir = tmp_path / "work"
    workdir.mkdir()
    argv = backend.wrap(["python3", "-c", FETCH, url], workdir, sandbox, None, ca_bundle=bundle)
    # Reachability only, and only here: the test's server listens on the
    # host's loopback, which a container shares only on the host's network.
    # (A sandboxed launch never gets `--network=host`; the server could be put
    # on a bridge address instead, but which one differs per runtime and mode.)
    argv.insert(argv.index("run") + 1, "--network=host")

    ran = subprocess.run(argv, capture_output=True, text=True, timeout=120)

    assert ran.returncode == 0, ran.stderr
    body, roots = ran.stdout.split()
    assert body == "kraft-tls-ok"
    # The image's own public roots came along, not the test CA alone.
    assert int(roots) > 1
    assert len(docker_forward.certificates(bundle.read_text())) == int(roots)
