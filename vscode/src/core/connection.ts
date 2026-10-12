import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

export interface Locations {
  home: string;
  templatesDir: string;
  runDir: string;
}

// Kraft's own `paths.config_dir()`: the variable, else `config/`, else a 1.x
// `templates/` the first 2.0 start has not renamed yet.
export function configDir(env: NodeJS.ProcessEnv, home: string, exists: (p: string) => boolean = existsSync): string {
  const named = env.KRAFT_CONFIG_DIR || env.KRAFT_TEMPLATES_DIR;
  if (named) return named;
  const current = join(home, "config");
  const legacy = join(home, "templates");
  if (!exists(current) && (exists(join(legacy, "library.yaml")) || exists(join(legacy, "registry.yaml")))) return legacy;
  return current;
}

export function locations(env: NodeJS.ProcessEnv, homedir: string, exists: (p: string) => boolean = existsSync): Locations {
  const home = env.KRAFT_HOME || join(homedir, ".kraft");
  return {
    home,
    templatesDir: configDir(env, home, exists),
    runDir: env.KRAFT_RUN_DIR || join(home, "run"),
  };
}

// ponytail: reads two top-level scalars from access.yaml with a regex rather
// than bundling a YAML parser; a bind/port written as a YAML anchor or on a
// continuation line is not seen (fall back to the default, or set kraft.url).
function scalar(yaml: string | undefined, key: string): string | undefined {
  const m = yaml?.match(new RegExp(`^${key}:\\s*['"]?([^'"#\\s]+)['"]?`, "m"));
  return m?.[1];
}

export function baseUrl(setting: string | undefined, env: NodeJS.ProcessEnv, accessYaml: string | undefined): string {
  if (setting && setting.trim()) return setting.trim().replace(/\/+$/, "");
  let host = env.KRAFT_HOST || scalar(accessYaml, "bind") || "127.0.0.1";
  const port = env.KRAFT_PORT || scalar(accessYaml, "port") || "8765";
  if (host === "0.0.0.0" || host === "::") host = "127.0.0.1";
  if (host.includes(":")) host = `[${host}]`;
  return `http://${host}:${port}`;
}

const LOOPBACK = new Set(["127.0.0.1", "[::1]", "localhost"]);

// Exact hostname match on the parsed URL: `127.0.0.1@evil.example`,
// `localhost.evil.example` and a malformed value are not loopback.
export function isLoopback(base: string): boolean {
  try {
    return LOOPBACK.has(new URL(base).hostname);
  } catch {
    return false;
  }
}

// No `kraft.url` in effect (the URL then comes from the environment and
// $KRAFT_HOME, which no workspace file writes), or the user's own global value.
export function machineChosen(effective: string | undefined, globalValue: string | undefined): boolean {
  const value = effective?.trim();
  return !value || value === globalValue?.trim();
}

export function tokenFor(base: string, machine: boolean, token: string | undefined): string | undefined {
  return machine || isLoopback(base) ? token : undefined;
}

export interface ConnectionInput {
  setting: string | undefined;
  globalSetting: string | undefined;
  env: NodeJS.ProcessEnv;
  accessYaml: string | undefined;
  token: string | undefined;
}

// The daemon URL and the token to send it: a workspace can point the extension
// elsewhere, but the bearer token goes only to loopback or a machine-chosen URL.
export function connectionFor(input: ConnectionInput): { base: string; token: string | undefined } {
  const base = baseUrl(input.setting, input.env, input.accessYaml);
  return { base, token: tokenFor(base, machineChosen(input.setting, input.globalSetting), input.token) };
}

export function readToken(runDir: string): string | undefined {
  try {
    const token = readFileSync(join(runDir, "mcp-token"), "utf8").trim();
    return token || undefined;
  } catch {
    return undefined;
  }
}
