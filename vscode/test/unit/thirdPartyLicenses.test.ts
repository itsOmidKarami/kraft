import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
// Shared with the web UI's build (frontend/vite.config.ts); tested here because
// this suite runs under Node.
import { packageDir, thirdPartyLicenses } from "../../../dev/third_party_licenses.mjs";

describe("packageDir", () => {
  it("finds the package a module came from", () => {
    expect(packageDir("/r/node_modules/ws/lib/websocket.js")).toBe("/r/node_modules/ws");
    expect(packageDir("/r/node_modules/@fontsource-variable/inter/index.css")).toBe(
      "/r/node_modules/@fontsource-variable/inter",
    );
    // A copy nested under another package is its own package.
    expect(packageDir("/r/node_modules/a/node_modules/b/x.js?commonjs-exports")).toBe("/r/node_modules/a/node_modules/b");
    expect(packageDir("C:\\r\\node_modules\\ws\\index.js")).toBe("C:/r/node_modules/ws");
  });

  it("skips the bundle's own code and a bundler's virtual modules", () => {
    expect(packageDir("/r/src/extension.ts")).toBeNull();
    expect(packageDir("\0vite/preload-helper.js")).toBeNull();
  });
});

function fakePackage(root: string, name: string, version: string, license: string | null): string {
  const dir = join(root, "node_modules", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify({ name, version, license: "MIT" }));
  if (license !== null) writeFileSync(join(dir, "LICENSE"), license);
  return join(dir, "index.js");
}

describe("thirdPartyLicenses", () => {
  it("copies each bundled package's license once, in name order", () => {
    const root = mkdtempSync(join(tmpdir(), "licenses-"));
    const zeta = fakePackage(root, "zeta", "2.0.0", "Copyright (c) Zeta\n\nMIT text\n");
    const alpha = fakePackage(root, "@scope/alpha", "1.0.0", "Copyright (c) Alpha\n");
    const text = thirdPartyLicenses([zeta, `${zeta}?x`, alpha, join(root, "src", "main.ts")], "a test bundle");
    expect(text.startsWith("Third-party software in a test bundle\n")).toBe(true);
    expect(text).toContain("@scope/alpha 1.0.0 (MIT)");
    expect(text).toContain("Copyright (c) Alpha");
    expect(text).toContain("zeta 2.0.0 (MIT)");
    expect(text).toContain("Copyright (c) Zeta\n\nMIT text\n");
    expect(text.split("zeta 2.0.0").length).toBe(2);
    expect(text.indexOf("@scope/alpha")).toBeLessThan(text.indexOf("zeta 2.0.0"));
  });

  it("stops the build when a bundled package has no license file to copy", () => {
    const root = mkdtempSync(join(tmpdir(), "licenses-"));
    const bare = fakePackage(root, "bare", "0.1.0", null);
    expect(() => thirdPartyLicenses([bare], "a test bundle")).toThrow("bare 0.1.0 is bundled but has no LICENSE file");
  });
});
