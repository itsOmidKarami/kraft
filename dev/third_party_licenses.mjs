// The license notices of the npm packages a bundle is built from.
//
// The web UI (frontend/vite.config.ts) and the VS Code extension
// (vscode/esbuild.mjs) each bundle npm packages into a few files, which keep
// none of their LICENSE files and few of their license comments. MIT, ISC and
// the OFL all ask for the notice to go with every copy, so each build writes
// this text beside its bundle as THIRD_PARTY_LICENSES.txt: in the wheel's
// `web/`, and in the .vsix's `dist/`.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const LICENSE_FILE = /^(licen[cs]e|copying)(\.(md|txt))?$/i;
const RULE = "-".repeat(72);

/**
 * The directory of the npm package a module came from, or null for code that
 * is not in node_modules (the bundle's own source, a bundler's virtual module).
 */
export function packageDir(id) {
  const path = id.replace(/^\0/, "").split("?")[0].replaceAll("\\", "/");
  const at = path.lastIndexOf("/node_modules/");
  if (at < 0) return null;
  const parts = path.slice(at + "/node_modules/".length).split("/");
  const name = parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
  return `${path.slice(0, at)}/node_modules/${name}`;
}

function describe(dir) {
  const meta = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  const file = readdirSync(dir)
    .filter((name) => LICENSE_FILE.test(name))
    .sort()[0];
  if (!file) {
    // A package with no license file has no notice to copy. Stopping the build
    // is better than shipping its code without one.
    throw new Error(`${meta.name} ${meta.version} is bundled but has no LICENSE file to copy`);
  }
  const license = typeof meta.license === "string" ? meta.license : (meta.license?.type ?? "see below");
  return { name: meta.name, version: meta.version, license, text: readFileSync(join(dir, file), "utf8").trim() };
}

/**
 * THIRD_PARTY_LICENSES.txt for a bundle of `product` built from `moduleIds`
 * (absolute paths, as the bundler reports them). Sorted, so the same
 * dependencies always give the same file.
 */
export function thirdPartyLicenses(moduleIds, product) {
  const dirs = new Set();
  for (const id of moduleIds) {
    const dir = packageDir(id);
    if (dir) dirs.add(dir);
  }
  const packages = [...dirs].map(describe);
  const key = (p) => `${p.name}\0${p.version}`;
  packages.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  const head = [
    `Third-party software in ${product}`,
    "",
    "Kraft is licensed under the Apache License 2.0 (see LICENSE). This bundle",
    "also contains the open-source packages below, each under its own license.",
  ];
  const body = packages.flatMap((p) => ["", RULE, `${p.name} ${p.version} (${p.license})`, RULE, "", p.text]);
  return [...head, ...body, ""].join("\n");
}
