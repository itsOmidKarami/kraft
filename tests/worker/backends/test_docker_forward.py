"""What of the daemon's proxy and CA crosses into a sandboxed container
(`worker.backends.docker_forward`), against a stub `docker`: the bundle's
contents and caching, the argv that mounts it, and the proxy variables."""

import os

import pytest
from support.harness import entry_of

from kraft import builtins as kraft_builtins
from kraft.worker.backends import docker, docker_forward
from kraft.worker.sandbox import SandboxNotReady

IMAGE = "kraft-worker:py"
SANDBOX = {"kind": "docker", "image": IMAGE}


def _pem(name: str) -> str:
    return f"-----BEGIN CERTIFICATE-----\n{name}\n-----END CERTIFICATE-----"


ROOT_A, ROOT_B, CORP = _pem("ROOTA"), _pem("ROOTB"), _pem("CORP")


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A stub `docker` first on PATH, logging each call to `host.calls`: it
    answers `image inspect` with id `abc123`, the roots read (`--entrypoint=sh`)
    with `host.roots` (or fails, after `host.fail_roots()`), and anything
    else with yes. `host.yaml(text)`
    writes this machine's `sandbox.yaml`; `host.ca(text)` writes a CA file
    and returns its path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log, roots = tmp_path / "docker-calls", tmp_path / "image-roots"
    fail = tmp_path / "fail-roots"
    roots.write_text(f"{ROOT_A}\n{ROOT_B}\n")
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        'case "$1" in image) echo sha256:abc123; exit 0;; esac\n'
        f'case "$*" in *--entrypoint=sh*) [ -e {fail} ] && exit 125; cat {roots}; exit 0;; esac\n'
        "echo kraft-probe-yes\n"
    )
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))

    class Host:
        def calls(self) -> list[str]:
            return log.read_text().splitlines() if log.exists() else []

        def roots_read(self) -> int:
            return sum("--entrypoint=sh" in c for c in self.calls())

        def fail_roots(self) -> None:
            """From now on the roots read exits 125, as a failed pull does."""
            fail.touch()

        def yaml(self, text: str) -> None:
            (templates / "sandbox.yaml").write_text(text)

        def ca(self, text: str = CORP, name: str = "corp.pem"):
            path = tmp_path / name
            path.write_text(text)
            return path

    Host.roots = roots
    return Host()


def _env_values(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-e" and "=" in argv[i + 1]]
    return dict(p.split("=", 1) for p in pairs)


# -- the bundle ------------------------------------------------------------------------


async def test_no_extra_ca_means_no_probe_no_mount_and_no_variable(host):
    """A host with no extra CA launches exactly as before."""
    bundle = await docker.DockerBackend().prepare(SANDBOX)
    argv = docker.docker_argv(["true"], "/w", SANDBOX, None, ca_bundle=bundle)
    assert bundle is None and host.calls() == []
    assert docker_forward.CONTAINER_CA not in " ".join(argv)
    assert not set(docker_forward.CA_VARS) & set(_env_values(argv))


async def test_the_bundle_is_the_image_roots_then_the_extra_ca(host, tmp_path):
    host.yaml(f"ca_bundle: {host.ca()}\n")
    bundle = await docker_forward.prepare(IMAGE)
    assert bundle == tmp_path / "run" / "sandbox-ca" / "abc123.pem"
    assert docker_forward.certificates(bundle.read_text()) == [ROOT_A, ROOT_B, CORP]


@pytest.mark.parametrize("extra", [False, True], ids=["no-extra-ca", "with-extra-ca"])
async def test_a_managed_credential_puts_the_kraft_ca_in_a_bundle_of_its_own(host, extra):
    """Spec §7 (P6): the Kraft CA joins the bundle of a launch with a
    managed credential, extra CA or none; a launch without one never
    trusts it, and building its bundle leaves the other's alone."""
    if extra:
        host.yaml(f"ca_bundle: {host.ca()}\n")
    kraft = host.ca(_pem("KRAFT"), "kraft.pem")
    managed = await docker.DockerBackend().prepare(SANDBOX, kraft_ca=kraft)
    plain = await docker.DockerBackend().prepare(SANDBOX)
    corp = [CORP] if extra else []
    assert docker_forward.certificates(managed.read_text()) == [
        ROOT_A,
        ROOT_B,
        *corp,
        _pem("KRAFT"),
    ]
    assert (plain and docker_forward.certificates(plain.read_text())) == (
        [ROOT_A, ROOT_B, CORP] if extra else None
    )


async def test_the_daemons_ssl_cert_file_is_the_extra_ca_when_sandbox_yaml_names_none(
    host, monkeypatch
):
    monkeypatch.setenv("SSL_CERT_FILE", str(host.ca(f"{CORP}\n{ROOT_A}\n")))
    bundle = await docker_forward.prepare(IMAGE)
    # A root the image already has is not carried twice.
    assert docker_forward.certificates(bundle.read_text()) == [ROOT_A, ROOT_B, CORP]


async def test_the_image_roots_are_read_once_per_image_id(host):
    """An image id never changes what it holds; the extra CA may, and the
    bundle follows it without another container."""
    ca = host.ca()
    host.yaml(f"ca_bundle: {ca}\n")
    await docker_forward.prepare(IMAGE)
    ca.write_text(_pem("CORP2"))
    host.roots.write_text("")
    bundle = await docker_forward.prepare(IMAGE)
    assert host.roots_read() == 1
    assert docker_forward.certificates(bundle.read_text()) == [ROOT_A, ROOT_B, _pem("CORP2")]


async def test_an_image_without_roots_gets_the_extra_ca_alone_and_it_is_remembered(host):
    host.roots.write_text("")
    host.yaml(f"ca_bundle: {host.ca()}\n")
    await docker_forward.prepare(IMAGE)
    bundle = await docker_forward.prepare(IMAGE)
    assert docker_forward.certificates(bundle.read_text()) == [CORP]
    assert host.roots_read() == 1


async def test_roots_that_could_not_be_read_stop_the_launch_and_are_not_remembered(host, tmp_path):
    """A bundle of the extra CA alone would replace the store and fail TLS to
    every public host: a pull that timed out must not cost that."""
    host.yaml(f"ca_bundle: {host.ca()}\n")
    host.fail_roots()
    for _ in range(2):
        with pytest.raises(SandboxNotReady, match=f"image '{IMAGE}'.*root certificates"):
            await docker.DockerBackend().prepare(SANDBOX)
    assert host.roots_read() == 2
    assert list((tmp_path / "run" / "sandbox-ca").iterdir()) == []


async def test_the_roots_are_read_with_no_network_and_past_the_entrypoint(host):
    """The entrypoint may print anything, and the image's roots are all that
    is read: nothing is mounted, nothing reached."""
    host.yaml(f"ca_bundle: {host.ca()}\n")
    await docker_forward.prepare(IMAGE)
    [read] = [c for c in host.calls() if "--entrypoint=sh" in c]
    assert "--network=none" in read.split() and "--cap-drop=ALL" in read.split()
    assert " -v " not in read
    assert all(path in read for path in docker_forward.IMAGE_ROOTS)


@pytest.mark.parametrize(
    "yaml",
    [
        "ca_bundle: /nonexistent/corp.pem\n",
        "ca_bundle: {not_pem}\n",
        "ca_bundle: {directory}\n",
    ],
    ids=["missing", "no-certificate", "directory"],
)
async def test_a_ca_bundle_that_cannot_be_used_stops_the_launch_and_doctor(host, tmp_path, yaml):
    """Named for sandboxes and skipped, it would launch a container that
    fails TLS somewhere later with nothing saying why."""
    host.yaml(yaml.format(not_pem=host.ca("not a certificate"), directory=tmp_path))
    backend = docker.DockerBackend()
    with pytest.raises(SandboxNotReady, match="ca_bundle.*could not trust its CA"):
        await backend.prepare(SANDBOX)
    ok, detail = await backend.health(SANDBOX)
    assert not ok and "could not trust its CA" in detail


@pytest.mark.parametrize(
    "value",
    ["/nonexistent/daemon.pem", "{directory}", "{not_pem}"],
    ids=["missing", "directory", "no-certificate"],
)
async def test_an_unusable_ssl_cert_file_is_ignored_and_said_why(
    host, tmp_path, monkeypatch, value
):
    """Never set for sandboxes, so it never stops one: the launch goes ahead
    as it did before, and doctor says why it was not used."""
    path = value.format(directory=tmp_path, not_pem=host.ca("not a certificate"))
    monkeypatch.setenv("SSL_CERT_FILE", path)
    assert await docker.DockerBackend().prepare(SANDBOX) is None
    assert host.calls() == []
    assert path in docker_forward.ignored_ssl_cert_file()


async def test_a_relative_ca_bundle_is_refused_by_name(host):
    """Relative to what: the daemon's cwd is nobody's choice."""
    host.yaml("ca_bundle: corp.pem\n")
    with pytest.raises(SandboxNotReady, match="ca_bundle.*absolute"):
        await docker.DockerBackend().prepare(SANDBOX)


# -- the launch ------------------------------------------------------------------------


async def test_a_session_mounts_the_bundle_read_only_and_every_ca_variable_names_it(host):
    """Never the host path: the container does not have it."""
    ca = host.ca()
    host.yaml(f"ca_bundle: {ca}\n")
    backend = docker.DockerBackend()
    bundle = await backend.prepare(SANDBOX)
    argv = backend.wrap(["claude"], "/w", SANDBOX, None, ca_bundle=bundle)
    assert argv[argv.index(f"{bundle}:{docker_forward.CONTAINER_CA}:ro") - 1] == "-v"
    env = _env_values(argv)
    assert {name: env.get(name) for name in docker_forward.CA_VARS} == dict.fromkeys(
        docker_forward.CA_VARS, docker_forward.CONTAINER_CA
    )
    assert str(ca) not in " ".join(argv)


async def test_a_ca_removed_since_the_last_launch_leaves_the_next_one_without_it(host):
    """Nothing is remembered between launches: the one after the CA went
    gets no mount and no CA variable."""
    host.yaml(f"ca_bundle: {host.ca()}\n")
    backend = docker.DockerBackend()
    assert await backend.prepare(SANDBOX) is not None
    host.yaml("")
    argv = backend.wrap(["claude"], "/w", SANDBOX, None, ca_bundle=await backend.prepare(SANDBOX))
    assert docker_forward.CONTAINER_CA not in " ".join(argv)
    assert not set(docker_forward.CA_VARS) & set(_env_values(argv))


async def test_a_repositorys_own_env_still_wins_over_a_ca_variable(host):
    host.yaml(f"ca_bundle: {host.ca()}\n")
    bundle = await docker_forward.prepare(IMAGE)
    argv = docker.docker_argv(
        ["true"], "/w", SANDBOX, None, env={"PIP_CERT": "/opt/pip.pem"}, ca_bundle=bundle
    )
    assert _env_values(argv)["PIP_CERT"] == "/opt/pip.pem"


def _setup_runs(host) -> list[str]:
    """The setup command's own `docker run`s, as the stub logged them."""
    return [c for c in host.calls() if c.startswith("run ") and c.endswith(f"{IMAGE} sh -c true")]


async def test_a_setup_command_gets_the_bundle_too(host, tmp_path):
    host.yaml(f"ca_bundle: {host.ca()}\n")
    await kraft_builtins.run_setup_command(
        tmp_path, tmp_path, entry_of({"setup_command": "true"}), sandbox=SANDBOX
    )
    [run] = _setup_runs(host)
    bundle = tmp_path / "run" / "sandbox-ca" / "abc123.pem"
    assert f" {bundle}:{docker_forward.CONTAINER_CA}:ro " in run
    assert f" SSL_CERT_FILE={docker_forward.CONTAINER_CA} " in run


async def test_a_setup_command_with_an_unusable_ca_does_not_run(host, tmp_path):
    host.yaml("ca_bundle: /nonexistent/corp.pem\n")
    with pytest.raises(RuntimeError, match="could not trust its CA"):
        await kraft_builtins.run_setup_command(
            tmp_path, tmp_path, entry_of({"setup_command": "true"}), sandbox=SANDBOX
        )
    assert _setup_runs(host) == []


# -- the proxy -------------------------------------------------------------------------


@pytest.fixture
def no_proxy(monkeypatch):
    """A daemon with no proxy variable set, whatever this machine has."""
    for name in docker_forward.PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


def test_the_daemons_proxy_is_forwarded_bare(monkeypatch, no_proxy):
    """Bare, so a proxy password never lands on an argv."""
    monkeypatch.setenv("HTTPS_PROXY", "http://kraft:secret@proxy.corp:3128")
    monkeypatch.setenv("all_proxy", "socks5://10.0.0.8:1080")
    monkeypatch.setenv("NO_PROXY", "localhost,.corp")
    argv = docker.docker_argv(["true"], "/w", SANDBOX, None)
    for name in ("HTTPS_PROXY", "all_proxy", "NO_PROXY"):
        assert argv[argv.index(name) - 1] == "-e"
    assert "HTTP_PROXY" not in argv
    assert "secret" not in " ".join(argv)


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:3128",
        "127.0.0.1:3128",
        "http://127.8.0.1:3128",
        "http://localhost:8080",
        "http://[::1]:3128",
        "http://0.0.0.0:3128",
        "http://[::ffff:127.0.0.1]:3128",
    ],
)
def test_a_proxy_on_the_daemons_loopback_is_not_forwarded(monkeypatch, no_proxy, value):
    """The container's loopback is its own: forwarded, every request fails."""
    monkeypatch.setenv("HTTPS_PROXY", value)
    monkeypatch.setenv("NO_PROXY", "localhost")
    assert docker_forward.loopback_proxies() == {"HTTPS_PROXY": value}
    argv = docker.docker_argv(["true"], "/w", SANDBOX, None)
    assert "HTTPS_PROXY" not in argv
    assert argv[argv.index("NO_PROXY") - 1] == "-e"
