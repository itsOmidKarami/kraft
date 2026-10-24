import { posix, win32 } from "node:path";

export const FILES: ReadonlySet<string> = new Set([
  "library.yaml", "policy.yaml", "harnesses.yaml", "repos.yaml",
  "intake.yaml", "access.yaml", "sandbox.yaml", "theme.yaml", "notify.yaml", "update.yaml", "detectors.yaml",
  "addons.yaml",
]);
const CHAIN = /^chains\/[a-z][a-z0-9_-]*\.yaml$/;

export function configFile(fsPath: string, templatesDir: string): string | undefined {
  const windows = /^[A-Za-z]:\\/.test(fsPath) || fsPath.includes("\\");
  const rel = (windows ? win32 : posix).relative(templatesDir, fsPath).split(windows ? "\\" : "/").join("/");
  // A path outside templatesDir relativises to "../…", which neither FILES nor CHAIN matches.
  return FILES.has(rel) || CHAIN.test(rel) ? rel : undefined;
}

/** What each configuration file holds, in the order Open Config lists them: the often-edited first. */
export const ABOUT: Readonly<Record<string, string>> = {
  "policy.yaml": "Caps, budgets, retries and concurrency",
  "harnesses.yaml": "Agent profiles, and which one runs each task",
  "repos.yaml": "Connected repos: commands, env and sandbox",
  "intake.yaml": "Auto-intake from beads, and scheduled work",
  "notify.yaml": "Webhook notifications",
  "update.yaml": "Update channel and when to install",
  "access.yaml": "Web UI address, port and password",
  "sandbox.yaml": "Sandbox runtime, image and credentials",
  "theme.yaml": "The web UI's look",
  "detectors.yaml": "How a connect finds a repo's commands",
  "addons.yaml": "Add-on collections and installed add-ons",
  "library.yaml": "Nodes, tasks and steps the chains extend",
};

/** The config files present in `names` (relative paths, as `configFile` returns them), split as
 *  Kraft splits them: library.yaml and chains/ are templates, the rest is configuration. */
export function configListing(names: readonly string[]): { configuration: string[]; templates: string[] } {
  const present = new Set(names.filter((n) => FILES.has(n) || CHAIN.test(n)));
  const known = Object.keys(ABOUT).filter((n) => present.has(n));
  const chains = [...present].filter((n) => CHAIN.test(n)).sort();
  return { configuration: known.filter((n) => !isLibraryFile(n)), templates: [...known.filter(isLibraryFile), ...chains] };
}

/** A chain file's own top-level `description:`, when it is on that line; a block scalar (`>`, `|`) is not. */
export const chainDescriptionOf = (text: string) => text.match(/^description:[ \t]*(?![ \t|>])["']?(.+?)["']?[ \t]*$/m)?.[1];

export const schemaFor = (relative: string) => (relative.startsWith("chains/") ? "chain.schema.json" : relative.replace(/\.yaml$/, ".schema.json"));
export const isLibraryFile = (relative: string) => relative === "library.yaml" || relative.startsWith("chains/");
