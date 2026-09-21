// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("PWA manifest", () => {
  it("declares a standalone, installable app with an icon", () => {
    const manifest = JSON.parse(
      readFileSync(join(here, "..", "public", "manifest.json"), "utf-8"),
    );
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(manifest.name).toBe("Kraft");
    expect(manifest.icons.length).toBeGreaterThan(0);
    expect(manifest.icons[0].src).toBe("/icon.svg");
  });

  it("index.html links the manifest, the icon, and a theme-color", () => {
    const html = readFileSync(join(here, "..", "index.html"), "utf-8");
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
    expect(html).toContain('<link rel="icon" href="/icon.svg" type="image/svg+xml" />');
    expect(html).toMatch(/<meta name="theme-color" content="#161826" \/>/);
  });
});
