import { describe, expect, it } from "vitest";
import type { Addon } from "../../types";
import { addonLabel, chainOptionText, chainsForRepo, inheritanceText, inheritanceTitle } from "./types";

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

const INHERITANCE = { parent: { id: "company@corp", namespace: "company" }, author: { id: "company@corp", version: "3.2.0" } };
const child = (origin?: "inherited" | "overridden" | "added"): Addon => ({ id: "platform-dept@corp", version: "1.0.0", ...(origin ? { inheritance: { origin, ...INHERITANCE } } : {}) });

describe("inheritanceText", () => {
  it.each([
    ["inherited", child("inherited"), "inherited from company"],
    ["overridden", child("overridden"), "overridden here"],
    ["added", child("added"), "added here"],
    ["none", child(), null],
    ["repo-library", { id: "library@repo:app", version: "", repo: "app" }, null],
  ])("%s", (_name, given, text) => {
    expect(inheritanceText(given)).toBe(text);
  });

  it("titles the mark with its author", () => {
    expect(inheritanceTitle(child("inherited"))).toBe("Written by company@corp 3.2.0");
    expect(inheritanceTitle(child())).toBeNull();
  });
});

describe("chainOptionText", () => {
  const chain = (addon: Addon) => ({ id: "platform-dept:default", nodes: [1, 2, 3, 4, 5], addon });
  it.each([
    ["inherited", chain(child("inherited")), "platform-dept:default · 5 nodes · inherited from company"],
    ["overridden", chain(child("overridden")), "platform-dept:default · 5 nodes · overridden here"],
    ["added", chain(child("added")), "platform-dept:default · 5 nodes"],
    ["root-addon", chain(child()), "platform-dept:default · 5 nodes"],
  ])("%s", (_name, given, text) => {
    expect(chainOptionText(given)).toBe(text);
  });
});
