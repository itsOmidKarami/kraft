"""What of the daemon's network setup crosses into a docker container: its
proxy, and the extra root certificates it trusts.

Nothing did before (Kraft sandbox part 2, P3): `docker_argv` forwarded only
Kraft's own variables and the repository's, so a host behind a proxy or a
TLS-intercepting CA had sandboxed workers that could reach nothing. The
daemon's values cannot cross as they are, either:

- `SSL_CERT_FILE` names a host file the container does not have, which breaks
  TLS rather than extending it. So when there is an extra CA, Kraft builds one
  bundle -- the image's own roots plus the extra CA -- and mounts it at one
  fixed container path (`CONTAINER_CA`), the only path any CA variable names.
  A combined bundle, because `SSL_CERT_FILE` and most of its siblings replace
  the store rather than add to it.
- A proxy on the host's loopback means the container's own loopback inside
  it, where nothing listens. Such a proxy is not forwarded (`forwarded_proxies`)
  and doctor says so (`loopback_proxies`); `network:` (a later part) is the fix.

The proxy variables are forwarded whenever the daemon has them. The CA side
only acts when there is an extra CA, or a launch whose credentials the
egress proxy manages (P6), whose bundle also holds the Kraft CA the proxy
terminates their TLS with: otherwise there is no probe, no mount and no CA
variable, as before. The bundle `prepare` builds is handed to the
launch explicitly (`DockerBackend.wrap(..., ca_bundle=)`), never remembered.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from kraft.config import ConfigError
from kraft.paths import default_run_dir, default_templates_dir

#: Where the combined bundle is mounted in every container. Fixed, never a
#: host path: a backend with no shared filesystem puts it at the same place.
CONTAINER_CA = "/etc/kraft/ca-bundle.pem"

#: Every variable a CLI in the container reads its roots from, each set to
#: `CONTAINER_CA`.
CA_VARS = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "NODE_EXTRA_CA_CERTS",
    "CODEX_CA_CERTIFICATE",
    "PIP_CERT",
    "npm_config_cafile",
)

#: The daemon's proxy variables, forwarded bare (`-e NAME`: the value never
#: lands on an argv, and a proxy URL may carry a password).
PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

#: Where a distribution keeps its root bundle; the first that exists in the
#: image is its roots.
IMAGE_ROOTS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\s.*?-----END CERTIFICATE-----", re.DOTALL)


# -- proxy ----------------------------------------------------------------------------


def _is_loopback(value: str) -> bool:
    """Does a proxy URL point at this machine's loopback, where a container
    cannot follow it? `127.0.0.0/8` (IPv4-mapped too), `::1`, `localhost`
    (and the unspecified address, which a client connects to as loopback)."""
    target = value if "://" in value else f"http://{value}"
    try:
        host = urlsplit(target).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # An IPv4-mapped `::ffff:127.0.0.1` is unwrapped by hand: `is_loopback` and
    # `is_unspecified` read through `ipv4_mapped` themselves only from Python
    # 3.13, and from a late 3.12.x / 3.11.x patch release.
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def loopback_proxies(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The daemon's proxy variables that point at its loopback: `{name: value}`."""
    environ = os.environ if environ is None else environ
    return {
        name: environ[name]
        for name in PROXY_VARS
        if name.lower() != "no_proxy" and environ.get(name) and _is_loopback(environ[name])
    }


def forwarded_proxies(environ: Mapping[str, str] | None = None) -> list[str]:
    """The proxy variables to forward bare into a container: every one the
    daemon sets, except a proxy on its loopback. Forwarded, that one fails
    every request (nothing listens on the container's own loopback);
    left out, the container at least goes direct where the host can."""
    environ = os.environ if environ is None else environ
    skip = loopback_proxies(environ)
    return [name for name in PROXY_VARS if environ.get(name) and name not in skip]


# -- CA -------------------------------------------------------------------------------


def certificates(text: str) -> list[str]:
    """The PEM certificates in `text`, in order; anything else dropped."""
    return _PEM.findall(text)


def _read_pem(path: Path) -> list[str]:
    """`path`'s certificates. Raises `ValueError` saying why there are none
    to use: unreadable (missing, a directory, no permission) or no PEM."""
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        raise ValueError(f"{path} cannot be read ({exc.strerror or exc})") from exc
    found = certificates(text)
    if not found:
        raise ValueError(f"{path} holds no PEM certificate")
    return found


def _sandbox_host(environ: Mapping[str, str]):
    from kraft import config

    templates = Path(environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    return config.SandboxHost.load(templates / config.SandboxHost.FILE)


def extra_ca(environ: Mapping[str, str] | None = None) -> tuple[str, list[str]] | None:
    """`(where it came from, its certificates)`: `sandbox.yaml`'s
    `ca_bundle`, else the daemon's `SSL_CERT_FILE`, else None.

    A `ca_bundle` that cannot be used raises `ConfigError`: someone named it
    for sandboxes, and skipping it would launch a container that fails TLS
    later with nothing saying why. An unusable `SSL_CERT_FILE` is only
    ignored (`ignored_ssl_cert_file` says why, for doctor): it was never set
    for sandboxes, and refusing on it would stop launches that ran before."""
    environ = os.environ if environ is None else environ
    host = _sandbox_host(environ)
    if host.ca_bundle is not None:
        try:
            return f"sandbox.yaml `ca_bundle` ({host.ca_bundle})", _read_pem(host.ca_bundle)
        except ValueError as exc:
            raise ConfigError(
                f"sandbox.yaml `ca_bundle`: {exc}, so a sandboxed task could not trust its "
                "CA; fix the path or remove it (see the sandbox.yaml reference)"
            ) from exc
    if not environ.get("SSL_CERT_FILE"):
        return None
    path = Path(environ["SSL_CERT_FILE"])
    try:
        return f"the daemon's SSL_CERT_FILE ({path})", _read_pem(path)
    except ValueError:
        return None


def ignored_ssl_cert_file(environ: Mapping[str, str] | None = None) -> str | None:
    """Why the daemon's `SSL_CERT_FILE` is not used as the extra CA although
    it is set, or None when it is used or does not apply. Raises
    `ConfigError` for a `sandbox.yaml` that does not parse."""
    environ = os.environ if environ is None else environ
    if not environ.get("SSL_CERT_FILE") or _sandbox_host(environ).ca_bundle is not None:
        return None
    try:
        _read_pem(Path(environ["SSL_CERT_FILE"]))
    except ValueError as exc:
        return str(exc)
    return None


def _write(path: Path, text: str) -> None:
    """`text` at `path`, atomically, and only when it changed: a running
    container keeps the file it mounted, the next launch gets the new one."""
    try:
        if path.read_text() == text:
            return
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


async def _image_id(image: str) -> str | None:
    from kraft.worker.backends.docker import docker_call

    inspected = await docker_call("image", "inspect", "--format", "{{.Id}}", image)
    if inspected is None or inspected[0] != 0 or not inspected[1].strip():
        return None
    return inspected[1].strip().removeprefix("sha256:")


async def _read_image_roots(image: str) -> list[str] | None:
    """The image's own roots, read by a container with no network and its
    entrypoint bypassed (whatever one prints is not a certificate). `[]`
    when the image holds none of `IMAGE_ROOTS`; None when the run itself
    failed (a pull that timed out, no `sh`, the daemon)."""
    from kraft.worker.backends.docker import docker_call, home_label

    ran = await docker_call(
        "run",
        "--rm",
        "--label",
        home_label(),
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--entrypoint=sh",
        image,
        "-c",
        'for f in "$@"; do if [ -f "$f" ]; then cat "$f"; exit 0; fi; done',
        "sh",
        *IMAGE_ROOTS,
        timeout=300,
    )
    if ran is None or ran[0] != 0:
        return None
    return certificates(ran[1])


def ca_dir(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("KRAFT_RUN_DIR") or default_run_dir()) / "sandbox-ca"


async def prepare(image: str, *, kraft_ca: Path | None = None) -> Path | None:
    """`image`'s combined CA bundle on the host, built now, or None when there
    is neither an extra CA nor a `kraft_ca`: the Kraft CA's certificate,
    given when the launch has a proxy-managed credential (spec §7), whose
    bundle is a file of its own -- a launch without one never trusts it,
    and never has its bundle rewritten under it. Raises `ConfigError` for a
    `ca_bundle` that cannot be used (`extra_ca`) and for an image whose own
    roots could not be read: a bundle of the extra CA alone would replace
    the store and fail TLS to every public host.

    One bundle per image id under `run/sandbox-ca/`: `<id>.roots.pem`, the
    image's own roots, read once per image id (an id is immutable); `<id>.pem`,
    those plus the extra CA, rewritten when the extra CA changes;
    `<id>.kraft.pem`, those plus the Kraft CA."""
    extra = extra_ca()
    if extra is None and kraft_ca is None:
        return None
    extra_certs = extra[1] if extra is not None else []
    if kraft_ca is not None:
        extra_certs = [*extra_certs, *certificates(kraft_ca.read_text())]
    base = ca_dir()
    base.mkdir(parents=True, exist_ok=True)
    image_id = await _image_id(image)
    roots_file = base / f"{image_id}.roots.pem" if image_id else None
    if roots_file is not None and roots_file.is_file():
        roots = certificates(roots_file.read_text())
    else:
        roots = await _read_image_roots(image)
        if roots is None:
            raise ConfigError(
                f"could not read image {image!r}'s own root certificates (the image did not "
                "run: not pulled, no `sh`, or the runtime did not answer), and trusting the "
                "extra CA alone would fail TLS to every other host; pull the image, or check "
                "that it runs `sh`"
            )
        # Run first, then ask its id: the run is what pulled an image that
        # was not here yet.
        image_id = image_id or await _image_id(image)
        if image_id:
            _write(base / f"{image_id}.roots.pem", "".join(f"{c}\n" for c in roots))
    name = image_id or "image-" + hashlib.sha256(image.encode()).hexdigest()[:32]
    bundle = base / f"{name}{'.kraft' if kraft_ca is not None else ''}.pem"
    _write(bundle, "".join(f"{c}\n" for c in dict.fromkeys([*roots, *extra_certs])))
    return bundle


def ca_args(bundle: str | Path | None) -> tuple[list[str], dict[str, str]]:
    """`docker run` mount arguments and variables for a prepared `bundle`;
    nothing at all without one."""
    if bundle is None:
        return [], {}
    return ["-v", f"{bundle}:{CONTAINER_CA}:ro"], dict.fromkeys(CA_VARS, CONTAINER_CA)
