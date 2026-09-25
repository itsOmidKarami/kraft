import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { join, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "../../..");
// Outside /tmp: macOS tmp paths trip the sandbox profile Kraft workers use.
const home = mkdtempSync(join(repoRoot, ".kraft-vscode-it-"));
const port = await new Promise((ok) => {
  const s = createServer().listen(0, () => {
    const { port } = s.address();
    s.close(() => ok(port));
  });
});
const env = {
  ...process.env,
  KRAFT_HOME: home,
  KRAFT_BD_CWD: join(home, "repo"),
  KRAFT_PORT: String(port),
  PATH: `${join(repoRoot, "fixtures", "bin")}:${process.env.PATH}`,
};
const uv = (args, opts = {}) => spawnSync("uv", ["run", "python", ...args], { cwd: repoRoot, env, stdio: "inherit", ...opts });

let daemon;
let code = 1;
try {
  // Same dev home `just dev` builds: the tracked templates (minus the machine's
  // access.yaml) and the throwaway repo the seed items work on.
  cpSync(join(repoRoot, "templates"), join(home, "templates"), { recursive: true });
  rmSync(join(home, "templates", "access.yaml"), { force: true });
  if (uv(["dev/seed.py", "--repo-only"]).status !== 0) throw new Error("building the seed repo failed");
  daemon = spawn("uv", ["run", "python", "-m", "kraft"], { cwd: repoRoot, env, stdio: "inherit" });
  let up = false;
  for (let i = 0; i < 120 && !up; i++) {
    try {
      up = (await fetch(`http://127.0.0.1:${port}/api/health`)).ok;
    } catch {}
    if (!up) await new Promise((r) => setTimeout(r, 500));
  }
  if (!up) throw new Error("the daemon did not come up");
  if (uv(["dev/seed.py"]).status !== 0) throw new Error("seeding the daemon failed");
  const run = spawnSync("npx", ["vscode-test"], {
    cwd: resolve(import.meta.dirname, "../.."),
    env: { ...env, KRAFT_TEST_WORKSPACE: join(home, "repo") },
    stdio: "inherit",
  });
  code = run.status ?? 1;
} catch (e) {
  console.error(String(e));
} finally {
  if (daemon) {
    // Stop it before its home goes away, or it logs a stack trace per open file.
    const exited = new Promise((r) => daemon.once("exit", r));
    daemon.kill("SIGTERM");
    await exited;
  }
  rmSync(home, { recursive: true, force: true });
}
process.exit(code);
