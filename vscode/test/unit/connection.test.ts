import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { baseUrl, locations, readToken } from "../../src/core/connection";

describe("locations", () => {
  it("defaults under ~/.kraft", () => {
    expect(locations({}, "/home/u")).toEqual({
      home: "/home/u/.kraft",
      templatesDir: "/home/u/.kraft/templates",
      runDir: "/home/u/.kraft/run",
    });
  });
  it("honours KRAFT_HOME and the per-dir overrides", () => {
    const l = locations({ KRAFT_HOME: "/k", KRAFT_RUN_DIR: "/r" }, "/home/u");
    expect(l).toEqual({ home: "/k", templatesDir: "/k/templates", runDir: "/r" });
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
