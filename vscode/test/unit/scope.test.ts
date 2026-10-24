import { describe, expect, it } from "vitest";
import { ABOUT, chainDescriptionOf, configFile, configListing, FILES, isLibraryFile, schemaFor } from "../../src/core/scope";

const T = "/home/u/.kraft/config";

describe("configFile", () => {
  it.each([
    [`${T}/policy.yaml`, "policy.yaml"],
    [`${T}/notify.yaml`, "notify.yaml"],
    [`${T}/update.yaml`, "update.yaml"],
    [`${T}/detectors.yaml`, "detectors.yaml"],
    [`${T}/sandbox.yaml`, "sandbox.yaml"],
    [`${T}/addons.yaml`, "addons.yaml"],
    [`${T}/chains/default.yaml`, "chains/default.yaml"],
    [`${T}/chains/Bad Name.yaml`, undefined],
    [`${T}/chains/nested/x.yaml`, undefined],
    [`${T}/other.yaml`, undefined],
    [`/elsewhere/policy.yaml`, undefined],
    [`${T}/../policy.yaml`, undefined],
  ])("%s → %s", (path, want) => {
    expect(configFile(path, T)).toBe(want);
  });

  it("normalises Windows separators", () => {
    expect(configFile("C:\\k\\templates\\chains\\a.yaml", "C:\\k\\templates")).toBe("chains/a.yaml");
  });
});

it("names the schema for each file", () => {
  expect(schemaFor("chains/default.yaml")).toBe("chain.schema.json");
  expect(schemaFor("harnesses.yaml")).toBe("harnesses.schema.json");
  expect(schemaFor("addons.yaml")).toBe("addons.schema.json");
});

it("knows which files the library lint covers", () => {
  expect(isLibraryFile("chains/a.yaml")).toBe(true);
  expect(isLibraryFile("library.yaml")).toBe(true);
  expect(isLibraryFile("policy.yaml")).toBe(false);
});

describe("configListing", () => {
  it("lists configuration in ABOUT's order and templates as library.yaml then chains by name, dropping what is not config", () => {
    expect(configListing(["repos.yaml", "chains/quick-task.yaml", "other.yaml", "library.yaml", "policy.yaml", "chains/default.yaml", "chains/Bad.yaml"]))
      .toEqual({ configuration: ["policy.yaml", "repos.yaml"], templates: ["library.yaml", "chains/default.yaml", "chains/quick-task.yaml"] });
  });

  it("describes every file configFile accepts", () => {
    expect([...FILES].filter((f) => !ABOUT[f])).toEqual([]);
  });
});

describe("chainDescriptionOf", () => {
  it.each([
    ["plain", "id: x\ndescription: Spec, plan, build\n", "Spec, plan, build"],
    ["quoted", "description: \"Quick: no gates\"\n", "Quick: no gates"],
    ["indented is a node's, not the chain's", "nodes:\n  - id: a\n    description: inner\n", undefined],
    ["block scalar", "description: >\n  folded text\n", undefined],
    ["none", "id: x\n", undefined],
  ])("%s", (_, text, want) => expect(chainDescriptionOf(text)).toBe(want));
});
