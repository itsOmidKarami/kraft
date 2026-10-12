import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { baseUrl, connectionFor, isLoopback, locations, machineChosen, readToken, tokenFor } from "../../src/core/connection";

describe("locations", () => {
  it("defaults under ~/.kraft", () => {
    expect(locations({}, "/home/u")).toEqual({
      home: "/home/u/.kraft",
      templatesDir: "/home/u/.kraft/config",
      runDir: "/home/u/.kraft/run",
    });
  });
  it("honours KRAFT_HOME and the per-dir overrides", () => {
    const l = locations({ KRAFT_HOME: "/k", KRAFT_RUN_DIR: "/r" }, "/home/u");
    expect(l).toEqual({ home: "/k", templatesDir: "/k/config", runDir: "/r" });
  });
  it.each([
    ["a 1.x home not yet renamed", ["/k/templates/library.yaml"], "/k/templates"],
    ["a 0.x home not yet renamed", ["/k/templates/registry.yaml"], "/k/templates"],
    ["a renamed home", ["/k/config", "/k/templates/library.yaml"], "/k/config"],
    ["a templates/ that holds no home", ["/k/templates"], "/k/config"],
  ])("reads %s where Kraft does", (_, present, want) => {
    const l = locations({ KRAFT_HOME: "/k" }, "/home/u", (p) => present.includes(p));
    expect(l.templatesDir).toBe(want);
  });
});

describe("baseUrl", () => {
  it("prefers the setting", () => {
    expect(baseUrl("http://h:1/", {}, "port: 2\n")).toBe("http://h:1");
  });
  it("reads bind and port from access.yaml", () => {
    expect(baseUrl("", {}, "bind: 127.0.0.1\nport: 9001\n")).toBe("http://127.0.0.1:9001");
  });
  it("lets KRAFT_HOST/KRAFT_PORT win over access.yaml", () => {
    expect(baseUrl(undefined, { KRAFT_PORT: "7000" }, "port: 9001\n")).toBe("http://127.0.0.1:7000");
  });
  it("rewrites a wildcard bind to loopback", () => {
    expect(baseUrl("", {}, "bind: 0.0.0.0\nport: 9001\n")).toBe("http://127.0.0.1:9001");
    expect(baseUrl("", {}, "bind: '::'\nport: 9001\n")).toBe("http://127.0.0.1:9001");
  });
  it("brackets an IPv6 literal", () => {
    expect(baseUrl("", {}, "bind: '::1'\nport: 9001\n")).toBe("http://[::1]:9001");
  });
  it("defaults when access.yaml is missing", () => {
    expect(baseUrl("", {}, undefined)).toBe("http://127.0.0.1:8765");
  });
});

describe("readToken", () => {
  it("reads and trims the token, or undefined", () => {
    const dir = mkdtempSync(join(tmpdir(), "kraft-token-"));
    expect(readToken(dir)).toBeUndefined();
    writeFileSync(join(dir, "mcp-token"), "  abc\n");
    expect(readToken(dir)).toBe("abc");
    writeFileSync(join(dir, "mcp-token"), "   \n");
    expect(readToken(dir)).toBeUndefined();
  });
});

describe("isLoopback", () => {
  it.each([
    ["http://127.0.0.1:8765", true], ["http://[::1]:8765", true], ["http://localhost:8765", true],
    ["http://LOCALHOST:8765", true], ["http://127.0.0.1@evil.example", false],
    ["http://localhost.evil.example", false], ["http://127.0.0.1.nip.io", false],
    ["http://localhost.:8765", false], ["http://10.0.0.5:8765", false],
    ["kraft", false], ["127.0.0.1:8765", false],
  ])("%s → %s", (base, want) => expect(isLoopback(base)).toBe(want));
});

describe("machineChosen", () => {
  it.each([
    { id: "no setting", effective: "", global: undefined, want: true },
    { id: "undefined setting", effective: undefined, global: undefined, want: true },
    { id: "the user's own value", effective: "http://h:1", global: " http://h:1 ", want: true },
    { id: "a value the user did not set", effective: "http://evil:1", global: undefined, want: false },
    { id: "a value other than the user's", effective: "http://evil:1", global: "http://h:1", want: false },
  ])("$id", ({ effective, global, want }) => expect(machineChosen(effective, global)).toBe(want));
});

describe("tokenFor", () => {
  it.each([
    { id: "loopback, not machine", base: "http://127.0.0.1:8765", machine: false, want: "tok" },
    { id: "remote, machine", base: "http://10.0.0.5:8765", machine: true, want: "tok" },
    { id: "remote, not machine", base: "http://evil.example", machine: false, want: undefined },
  ])("$id", ({ base, machine, want }) => expect(tokenFor(base, machine, "tok")).toBe(want));
});

describe("connectionFor", () => {
  it.each([
    { id: "a workspace's non-loopback url", setting: "http://evil.example:8765", global: undefined, env: {}, yaml: undefined,
      want: { base: "http://evil.example:8765", token: undefined } },
    { id: "a workspace url other than the user's", setting: "http://evil.example:8765", global: "http://10.0.0.5:8765", env: {}, yaml: undefined,
      want: { base: "http://evil.example:8765", token: undefined } },
    { id: "a workspace's loopback url", setting: "http://127.0.0.1:9000", global: undefined, env: {}, yaml: undefined,
      want: { base: "http://127.0.0.1:9000", token: "tok" } },
    { id: "the user's own non-loopback url", setting: "http://10.0.0.5:8765", global: "http://10.0.0.5:8765", env: {}, yaml: undefined,
      want: { base: "http://10.0.0.5:8765", token: "tok" } },
    { id: "no setting, a LAN bind in access.yaml", setting: "", global: undefined, env: {}, yaml: "bind: 10.0.0.5\nport: 9001\n",
      want: { base: "http://10.0.0.5:9001", token: "tok" } },
    { id: "no setting, KRAFT_HOST", setting: undefined, global: undefined, env: { KRAFT_HOST: "10.0.0.5" }, yaml: undefined,
      want: { base: "http://10.0.0.5:8765", token: "tok" } },
    { id: "a malformed workspace url: bare word", setting: "kraft", global: undefined, env: {}, yaml: undefined,
      want: { base: "kraft", token: undefined } },
    { id: "a malformed workspace url: no scheme", setting: "127.0.0.1:8765", global: undefined, env: {}, yaml: undefined,
      want: { base: "127.0.0.1:8765", token: undefined } },
    { id: "a malformed workspace url: another scheme", setting: "ftp://x", global: undefined, env: {}, yaml: undefined,
      want: { base: "ftp://x", token: undefined } },
  ])("$id", ({ setting, global, env, yaml, want }) =>
    expect(connectionFor({ setting, globalSetting: global, env, accessYaml: yaml, token: "tok" })).toEqual(want));
});
