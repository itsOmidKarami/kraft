import { describe, expect, it } from "vitest";
import type { Addon } from "../../types";
import { addonLabel, chainsForRepo } from "./types";

const local: { id: string; addon?: Addon | null } = { id: "default", addon: null };
const addon = { id: "release:ship", addon: { id: "release@acme", version: "1.0.0", repo: null } };
const app = { id: "app:ship", addon: { id: "library@repo:app", version: "abc", repo: "app" } };
const web = { id: "web:ship", addon: { id: "library@repo:web", version: "def" } };
const ALL = [local, addon, app, web];

describe("chainsForRepo", () => {
  it.each([
    ["own-library-listed", "app", "app:ship", true],
    ["another-repos-hidden", "app", "web:ship", false],
    ["addon-listed", "app", "release:ship", true],
    ["local-listed", "app", "default", true],
    ["repo-without-id-hides-every-repo-library", null, "app:ship", false],
  ])("%s", (_name, repoId, chain, listed) => {
    expect(chainsForRepo(ALL, repoId).some((c) => c.id === chain)).toBe(listed);
  });
});

describe("addonLabel", () => {
  it.each([
    ["addon", addon.addon, "release@acme 1.0.0"],
    ["repo-library", app.addon, "repo library app"],
    ["repo-library-by-id", web.addon, "repo library web"],
  ])("%s", (_name, given, label) => {
    expect(addonLabel(given)).toBe(label);
  });
});
