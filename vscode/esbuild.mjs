import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "esbuild";
import { thirdPartyLicenses } from "../dev/third_party_licenses.mjs";

const result = await build({
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
  sourcemap: true,
  metafile: true,
});

// The bundle carries `ws`, whose source has no license comment for esbuild to
// keep. Its notice ships beside it, in dist/, which the .vsix includes.
writeFileSync(
  "dist/THIRD_PARTY_LICENSES.txt",
  thirdPartyLicenses(
    Object.keys(result.metafile.inputs).map((input) => resolve(input)),
    "the Kraft VS Code extension",
  ),
);
