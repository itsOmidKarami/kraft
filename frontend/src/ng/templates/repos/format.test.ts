import { describe, expect, it } from "vitest";
import { patchFor, sourceOf, valueOf, FIELDS } from "./fields";
import { dollars, list, models, scopes, whole } from "./format";
import { repo } from "./fixture";
import { reposOf, runningOf } from "./types";
import { reposView } from "./fixture";

describe("Repos Config row formats", () => {
  it("reads and writes lists, one value per comma", () => {
    expect(list.parse("a, b ,, c")).toEqual({ value: ["a", "b", "c"] });
    expect(list.parse("  ")).toEqual({ value: null });
    expect(list.show(["x", "y"])).toBe("x, y");
  });

  it("reads and writes profile=model pairs and refuses a line without =", () => {
    expect(models.parse("claude=opus; codex = gpt-5")).toEqual({ value: { claude: "opus", codex: "gpt-5" } });
    expect(models.parse("claude")).toEqual({ error: '"claude" is not profile=model.' });
    expect(models.show({ claude: "opus" })).toBe("claude=opus");
  });

  it("reads and writes test scopes as paths => command", () => {
    expect(scopes.parse("web/**, docs/** => npm test; api/** => pytest")).toEqual({ value: [{ paths: ["web/**", "docs/**"], command: "npm test" }, { paths: ["api/**"], command: "pytest" }] });
    expect(scopes.parse("npm test")).toEqual({ error: '"npm test" is not paths => command.' });
    expect(scopes.show([{ paths: ["a"], command: "c" }])).toBe("a => c");
  });

  it("refuses a number that is not whole and positive, and clears on blank", () => {
    expect(whole("Minutes").parse("45")).toEqual({ value: 45 });
    expect(whole("Minutes").parse("0")).toEqual({ error: "Minutes is a whole number above 0." });
    expect(whole("Minutes").parse("")).toEqual({ value: null });
    expect(dollars.parse("$2.5")).toEqual({ value: 2.5 });
    expect(dollars.parse("x")).toEqual({ error: "Dollars is a number above 0." });
  });
});

describe("Repos fields", () => {
  const platform = repo("platform", { policy: { time_cap_minutes: 60, token_budget: 100 } });
  const field = (key: string) => FIELDS.find((f) => f.key === key)!;

  it("reads a policy key from inside the entry's policy block", () => {
    expect(valueOf(platform, field("policy.time_cap_minutes"))).toBe(60);
    expect(valueOf(platform, field("test_command"))).toBe("make test");
  });

  it("patches one policy key without dropping the others, and drops the block when it empties", () => {
    expect(patchFor(platform, field("policy.time_cap_minutes"), 45)).toEqual({ policy: { time_cap_minutes: 45, token_budget: 100 } });
    expect(patchFor(platform, field("policy.token_budget"), null)).toEqual({ policy: { time_cap_minutes: 60 } });
    const one = repo("one", { policy: { time_cap_minutes: 5 } });
    expect(patchFor(one, field("policy.time_cap_minutes"), null)).toEqual({ policy: null });
  });

  it("names a row's source: the server's word when it has one, else whether this repo sets it", () => {
    expect(sourceOf(repo("a", {}, { sources: { steering: "library", deny_tools: "default", models: "default", policy: {} } }), field("steering"))).toBe("library");
    expect(sourceOf(repo("a", { test_command: "x" }), field("test_command"))).toBe("this repo");
    expect(sourceOf(repo("a", { test_command: undefined }), field("test_command"))).toBe("default");
  });
});

describe("Repos draft views", () => {
  it("narrows the resolved answer once and is null while it does not load", () => {
    const r = reposView().result;
    expect(reposOf(r)?.repos.map((x) => x.name)).toEqual(["product_root", "platform", "docs-site"]);
    expect(reposOf({ ...r, resolved: null })).toBeNull();
    expect(runningOf(r)).toEqual({ "/src/platform": 1 });
    expect(runningOf({ ...r, impact: {} as never })).toEqual({});
  });
});
