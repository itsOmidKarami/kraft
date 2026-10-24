"""A sandboxed step on a machine. The server sends the sandbox the item was filed with (image,
limits, network lists, credential declarations) and this builds it with the machine's own backend,
in the order `adapters.subprocess` does for a local step. Whatever cannot be built is `Unavailable`,
and the step does not start: nothing here ever runs a command outside the sandbox."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kraft.paths import RunDirs
from kraft.vocab import SandboxEvent
from kraft.worker import backends, ca
from kraft.worker import sandbox as _sandbox
from kraft.worker.egress import PhaseLists
from kraft.worker.inject import InjectRule

if TYPE_CHECKING:
    from kraft.remote.jobs import JobSpec
    from kraft.remote.paths import PathMap
    from kraft.worker.channel import ChannelRegistry


class Unavailable(Exception):
    """str() is what this machine lacks, for the session's log."""


@dataclass
class Host:
    """What a machine gives the sandboxes it builds."""

    machine: str
    run_dirs: RunDirs
    channels: ChannelRegistry
    #: The other sessions of this item live on this machine: their ref stores stay.
    live: Callable[[], list[str]] = lambda: []
    #: Starts the machine's mTLS listener, for a runtime that needs it (a VM's).
    start_tls: Callable[[], Awaitable[None]] = lambda: asyncio.sleep(0)


@dataclass
class Launch:
    """One command wrapped in the sandbox, ready to start; `finish` once it has exited."""

    argv: list[str]
    client_cwd: Path | None
    backend: backends.SandboxBackend
    host: Host
    session_id: str
    work_item_id: str
    refs: tuple
    network: bool
    result_path: Path | None
    cidfile: Path
    branch: str | None

    def launch_failed(self, returncode: int | None) -> bool:
        """The sandbox itself never started, so the command never ran."""
        return self.backend.launch_failed(self.cidfile, returncode)

    async def finish(self, *, publish: bool = True) -> backends.OomKill | None:
        """After the command exited, or never started: bring the result home, ask whether the
        memory limit killed it, remove the sandbox and its route out, publish the commits."""
        from kraft.adapters import subprocess as _subprocess

        sid, wid, backend = self.session_id, self.work_item_id, self.backend
        oom = None
        try:
            try:
                if self.result_path is not None:
                    await backend.collect(sid, self.result_path, work_item_id=wid)
                oom = await backend.oom_killed(sid, work_item_id=wid)
            finally:
                try:
                    await backend.close(sid, work_item_id=wid)
                finally:
                    if self.network:
                        await _subprocess.close_egress(
                            backend, sid, work_item_id=wid, channels=self.host.channels
                        )
        finally:
            if publish and self.refs:
                problems = await asyncio.to_thread(
                    backend.code_out, self.refs, sid, work_item_id=wid
                )
                for problem in problems:
                    await self._unsynced(problem)
        return oom

    async def _unsynced(self, problem: str | None) -> None:
        if problem:
            await self.host.channels.record(
                self.work_item_id,
                SandboxEvent.BRANCH_NOT_SYNCED,
                {"session_id": self.session_id, "branch": self.branch, "reason": problem},
            )


class Sandboxed:
    """The sandbox one job runs in: its setup command first, then the step."""

    def __init__(
        self, host: Host, spec: JobSpec, pathmap: PathMap, root: Path, job_dir: Path
    ) -> None:
        self.host, self.spec, self.pathmap, self.root, self.dir = host, spec, pathmap, root, job_dir
        try:
            self.backend = backends.for_sandbox(spec.sandbox)
        except KeyError:
            raise Unavailable(
                f"{host.machine}: this machine has no {spec.sandbox.get('kind')!r} sandbox backend"
            ) from None

    def _mapped(self, paths) -> list[Path]:
        return [Path(self.pathmap.text(p)) for p in paths]

    def _rules(self, declared: list[dict], env: dict[str, str]) -> tuple[InjectRule, ...]:
        """Each declared credential with the value this machine's environment gives it. A
        declaration the machine cannot fill is a stop, never a launch with an empty header."""
        rules = tuple(InjectRule.from_json(d, env) for d in declared)
        for rule in rules:
            if rule.value is None:
                name = rule.source or rule.env
                raise Unavailable(
                    f"{self.host.machine}: the credential {rule.env} has no value: {name} is not "
                    f"set in this machine's environment"
                    + (" (list it under env_passthrough)" if not rule.source else "")
                )
        return rules

    async def step(self, argv: list[str], env: dict[str, str], step_env: dict[str, str]) -> Launch:
        """The step's command, wrapped. `env` is the client's own, `step_env` what the job set."""
        s = self.spec
        result_path = Path(self.pathmap.text(s.env["KRAFT_RESULT_PATH"]))
        rw_dirs = self._mapped(s.dirs)
        rw_files = [p for p in self._mapped(s.rw) if p not in rw_dirs]
        return await self._wrap(
            argv,
            env,
            step_env,
            session_id=s.session_id,
            branch=s.branch,
            lists=s.egress,
            declared=s.credentials,
            sentinels=s.sentinels,
            result_path=result_path,
            rw_files=rw_files,
            rw_dirs=rw_dirs,
            results=[p for p in self._mapped(s.ro) if p.is_file()],
            workdir=Path(self.pathmap.text(s.cwd)),
            step=True,
        )

    async def setup(self, command: str, env: dict[str, str]) -> Launch:
        """The setup command in the same sandbox, under the install phase's lists."""
        s = self.spec
        install = s.setup_sandbox or {}
        return await self._wrap(
            ["sh", "-c", command],
            env,
            {},
            session_id=f"setup-{uuid.uuid4().hex[:12]}",
            branch=None,
            lists=install.get("egress"),
            declared=install.get("credentials", []),
            sentinels=install.get("sentinels", {}),
            result_path=None,
            rw_files=[],
            rw_dirs=[],
            results=None,
            workdir=None,
            step=False,
        )

    async def _wrap(
        self,
        cmd: list[str],
        env: dict[str, str],
        step_env: dict[str, str],
        *,
        session_id: str,
        branch: str | None,
        lists: dict | None,
        declared: list[dict],
        sentinels: dict[str, str],
        result_path: Path | None,
        rw_files: list[Path],
        rw_dirs: list[Path],
        results: list[Path] | None,
        workdir: Path | None,
        step: bool,
    ) -> Launch:
        from kraft.adapters import subprocess as _subprocess

        s, host, backend, sandbox = self.spec, self.host, self.backend, self.spec.sandbox
        wid, run_dirs = s.work_item_id, host.run_dirs
        withheld = set(s.withheld)
        repo_env = {k: self.pathmap.text(v) for k, v in s.repo_env.items()}
        cidfile = self.dir / f"{session_id}.cid"
        try:
            rules = self._rules(declared, env)
            for name in withheld:  # the worker holds a sentinel; the value stays here
                env.pop(name, None)
            if result_path is not None and (
                problem := await backend.owner_refusal(
                    run_dirs,
                    self.root,
                    wid,
                    result_path,
                    members={},
                    rw_files=rw_files,
                    rw_dirs=rw_dirs,
                )
            ):
                raise Unavailable(f"{host.machine}: {problem}")
            for d in rw_dirs:
                d.mkdir(parents=True, exist_ok=True)
            cidfile.unlink(missing_ok=True)  # docker refuses a cidfile that already exists
            try:
                refs = tuple(
                    await asyncio.to_thread(
                        backend.code_in,
                        run_dirs.base,
                        self.root,
                        branch,
                        members={},
                        session_id=session_id,
                        live=host.live(),
                        work_item_id=wid,
                    )
                )
            except (OSError, RuntimeError) as exc:
                raise Unavailable(
                    f"{host.machine}: could not prepare the sandbox's ref store: {exc}"
                ) from exc
            if step and (
                problem := await backend.probe(
                    sandbox, cmd[0], {k: v for k, v in repo_env.items() if k not in withheld}
                )
            ):
                raise Unavailable(f"{host.machine}: {problem}")
            ca_bundle = await backend.prepare(
                sandbox, kraft_ca=ca.ensure_ca(run_dirs)[0] if rules else None
            )
            network = bool(sandbox.get("network"))
            proxy_env: dict[str, str] = {}
            if network and lists is not None:
                if await backend.egress_transport() == "tls":
                    await host.start_tls()
                try:
                    proxy_env = await _subprocess.open_egress(
                        None,
                        backend,
                        session_id,
                        wid,
                        sandbox,
                        PhaseLists.from_json(lists),
                        rules,
                        None,
                        channels=host.channels,
                    )
                except BaseException:
                    # whatever part of the route did open, closed
                    await _subprocess.close_egress(
                        backend, session_id, work_item_id=wid, channels=host.channels
                    )
                    raise
        except _sandbox.SandboxNotReady as exc:
            raise Unavailable(f"{host.machine}: {exc}") from exc
        except OSError as exc:
            raise Unavailable(f"{host.machine}: {exc}") from exc
        launch = Launch(
            argv=cmd,
            client_cwd=None,
            backend=backend,
            host=host,
            session_id=session_id,
            work_item_id=wid,
            refs=refs,
            network=network and lists is not None,
            result_path=result_path,
            cidfile=cidfile,
            branch=branch,
        )
        try:
            for store in refs:
                await launch._unsynced(store.carried)
            home = backend.home(run_dirs, wid)
            home.mkdir(parents=True, exist_ok=True)
            identity = {k: v for k, v in s.identity.items() if k not in repo_env} if step else {}
            launch.argv = await asyncio.to_thread(
                backend.wrap,
                cmd,
                self.root,
                sandbox,
                results,
                env={**identity, **step_env, **proxy_env},
                session_id=session_id,
                work_item_id=wid,
                result_path=result_path,
                rw_files=rw_files,
                rw_dirs=rw_dirs,
                cidfile=cidfile,
                refs=refs,
                workdir=workdir,
                home=home,
                passthrough=(*s.passthrough, *repo_env),
                ca_bundle=ca_bundle,
                sentinels=sentinels,
            )
            launch.client_cwd = backend.client_cwd(session_id, work_item_id=wid)
        except BaseException as exc:
            await launch.finish(publish=False)
            if isinstance(exc, _sandbox.SandboxRefused | _sandbox.SandboxNotReady):
                raise Unavailable(f"{host.machine}: {exc}") from exc
            raise
        return launch
