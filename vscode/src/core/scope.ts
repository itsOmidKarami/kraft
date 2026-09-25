import { posix, win32 } from "node:path";

export const FILES: ReadonlySet<string> = new Set([
  "library.yaml", "policy.yaml", "harnesses.yaml", "repos.yaml",
  "intake.yaml", "access.yaml", "theme.yaml", "notify.yaml",
]);
const CHAIN = /^chains\/[a-z][a-z0-9_-]*\.yaml$/;

export function configFile(fsPath: string, templatesDir: string): string | undefined {
  const windows = /^[A-Za-z]:\\/.test(fsPath) || fsPath.includes("\\");
  const rel = (windows ? win32 : posix).relative(templatesDir, fsPath).split(windows ? "\\" : "/").join("/");
  // A path outside templatesDir relativises to "../…", which neither FILES nor CHAIN matches.
  return FILES.has(rel) || CHAIN.test(rel) ? rel : undefined;
}

export const schemaFor = (relative: string) => (relative.startsWith("chains/") ? "chain.schema.json" : relative.replace(/\.yaml$/, ".schema.json"));
export const isLibraryFile = (relative: string) => relative === "library.yaml" || relative.startsWith("chains/");
