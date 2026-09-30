"""A Kit on a registry on loopback, for the `kind: kit` e2e (spec "Testing"):
a digest-pinned `registry:2` serving TLS on 127.0.0.1, a small image pushed
to it, and a copy of that image's manifest PUT with the descriptor
annotation. Neither BuildKit nor the Kit frontend is needed.

The copy is an OCI image manifest, a single manifest (no index): docker's
`manifest inspect` re-prints a docker schema2 manifest without its
annotations, and a single manifest is the form the spec left unproven
under podman. Its config and layers are the pushed blobs, as they are."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import socket
import ssl
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from kraft.worker import kit

#: registry:2, by its multi-arch index digest.
IMAGE = (
    "docker.io/library/registry@sha256:"
    "a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
)
_INDEX = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
_OCI = "application/vnd.oci.image.manifest.v1+json"
_ACCEPT = ", ".join((*_INDEX, _OCI, "application/vnd.docker.distribution.manifest.v2+json"))


@dataclass
class Registry:
    cli: str
    container: str
    #: `127.0.0.1:<port>`, as the runtime and this process both reach it.
    host: str
    #: Its self-signed certificate, which is also the CA to trust.
    cert: Path

    def _open(self, request: urllib.request.Request):
        context = ssl.create_default_context(cafile=str(self.cert))
        return urllib.request.urlopen(request, context=context, timeout=30)

    def _manifest(self, repository: str, reference: str) -> tuple[str, dict]:
        request = urllib.request.Request(
            f"https://{self.host}/v2/{repository}/manifests/{reference}",
            headers={"Accept": _ACCEPT},
        )
        with self._open(request) as answer:
            return answer.headers["Content-Type"], json.loads(answer.read())

    def kit(self, image: str, descriptor: str, repository: str = "kraft-kit") -> str:
        """`image` pushed as `repository`, then a copy of its manifest
        carrying `descriptor` as the Kit annotation: that copy's
        `<host>/<repository>@sha256:<digest>`."""
        pushed = f"{self.host}/{repository}:image"
        _run(self.cli, "tag", image, pushed)
        _run(self.cli, "push", *_insecure(self.cli), pushed)
        media, manifest = self._manifest(repository, "image")
        if media in _INDEX:
            entry = next(
                m for m in manifest["manifests"] if m.get("platform", {}).get("os") != "unknown"
            )
            media, manifest = self._manifest(repository, entry["digest"])
        if media != _OCI:
            manifest = {
                **manifest,
                "mediaType": _OCI,
                "config": {
                    **manifest["config"],
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                },
                "layers": [
                    {**layer, "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}
                    for layer in manifest["layers"]
                ],
            }
        manifest["annotations"] = {**manifest.get("annotations", {}), kit.ANNOTATION: descriptor}
        body = json.dumps(manifest).encode()
        request = urllib.request.Request(
            f"https://{self.host}/v2/{repository}/manifests/kit",
            data=body,
            method="PUT",
            headers={"Content-Type": _OCI},
        )
        self._open(request).close()
        return f"{self.host}/{repository}@sha256:{hashlib.sha256(body).hexdigest()}"

    def stop(self) -> None:
        subprocess.run([self.cli, "rm", "-f", self.container], capture_output=True)


def _run(cli: str, *args: str) -> str:
    done = subprocess.run([cli, *args], capture_output=True, text=True)
    assert done.returncode == 0, f"{cli} {' '.join(args)}: {done.stderr.strip()}"
    return done.stdout.strip()


def _insecure(cli: str) -> tuple[str, ...]:
    """What podman needs to push to or pull from it, however it is reached;
    docker's daemon trusts 127.0.0.0/8 already."""
    return ("--tls-verify=false",) if cli == "podman" else ()


def pull(cli: str, ref: str) -> None:
    _run(cli, "pull", "-q", *_insecure(cli), ref)


def _self_signed(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kraft e2e registry")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "registry.pem", directory / "registry.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(cli: str, directory: Path) -> Registry:
    """A registry on a free 127.0.0.1 port, its certificate copied in rather
    than mounted (podman machine shares no arbitrary host path). The port is
    chosen here, not by the runtime: Docker Desktop's VM listens on a
    different one from the host's for an ephemeral publish."""
    cert, key = _self_signed(directory)
    port = _free_port()
    container = _run(
        cli,
        "create",
        "-p",
        f"127.0.0.1:{port}:5000",
        "-e",
        "REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.pem",
        "-e",
        "REGISTRY_HTTP_TLS_KEY=/certs/registry.key",
        IMAGE,
    )
    registry = Registry(cli, container, f"127.0.0.1:{port}", cert)
    try:
        _run(cli, "cp", str(directory) + "/.", f"{container}:/certs")
        _run(cli, "start", container)
        deadline = time.monotonic() + 30
        while True:
            try:
                registry._open(urllib.request.Request(f"https://{registry.host}/v2/")).close()
                return registry
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
    except BaseException:
        registry.stop()
        raise
