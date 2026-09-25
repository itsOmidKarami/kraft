import { readFileSync } from "node:fs";
import { join } from "node:path";

export interface Locations {
  home: string;
  templatesDir: string;
  runDir: string;
}

export function locations(env: NodeJS.ProcessEnv, homedir: string): Locations {
  const home = env.KRAFT_HOME || join(homedir, ".kraft");
  return {
    home,
    templatesDir: env.KRAFT_TEMPLATES_DIR || join(home, "templates"),
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

export function readToken(runDir: string): string | undefined {
  try {
    const token = readFileSync(join(runDir, "mcp-token"), "utf8").trim();
    return token || undefined;
  } catch {
    return undefined;
  }
}
