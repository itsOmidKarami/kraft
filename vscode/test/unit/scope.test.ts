import { describe, expect, it } from "vitest";
import { configFile, isLibraryFile, schemaFor } from "../../src/core/scope";

const T = "/home/u/.kraft/templates";

describe("configFile", () => {
  it.each([
    [`${T}/policy.yaml`, "policy.yaml"],
    [`${T}/notify.yaml`, "notify.yaml"],
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
});

it("knows which files the library lint covers", () => {
  expect(isLibraryFile("chains/a.yaml")).toBe(true);
  expect(isLibraryFile("library.yaml")).toBe(true);
  expect(isLibraryFile("policy.yaml")).toBe(false);
});
