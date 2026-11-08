import { describe, expect, it } from "vitest";
import { needsPlainHttpWarning, normalizeServerUrl } from "../../src/core/serverUrl";

describe("normalizeServerUrl", () => {
  it.each([
    ["lower-cases the host", "HTTP://Kraft.Example:9000", "http://kraft.example:9000"],
    ["defaults http to 80", "http://h", "http://h:80"],
    ["defaults https to 443", "https://h/", "https://h:443"],
    ["drops a trailing slash", "http://h:8765/", "http://h:8765"],
    ["brackets IPv6", "http://[::1]:8765", "http://[::1]:8765"],
    ["trims whitespace", "  http://h:1  ", "http://h:1"],
  ])("accepts: %s", (_id, input, url) => expect(normalizeServerUrl(input)).toEqual({ ok: true, url }));

  it.each([
    ["no scheme", "h:8765", /http/],
    ["ftp", "ftp://h", /http/],
    ["empty", "", /address/],
    ["a path", "https://proxy/kraft/", /path/],
    ["a query", "http://h:1/?a=b", /path|query/],
    ["a fragment", "http://h:1#x", /fragment|path/],
    ["credentials", "http://u:p@h:1", /user/],
    ["a zone id", "http://[fe80::1%25eth0]:1", /zone/],
  ])("refuses: %s", (_id, input, why) => {
    const r = normalizeServerUrl(input);
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.reason).toMatch(why);
  });

  it("gives the same key to equivalent spellings", () => {
    const a = normalizeServerUrl("HTTP://H:80/");
    const b = normalizeServerUrl("http://h");
    expect(a).toEqual(b);
  });
});

describe("needsPlainHttpWarning", () => {
  it.each([
    ["http to a LAN host", "http://192.168.1.5:8765", true],
    ["http to a tailnet name", "http://box.tailnet.ts.net:8765", true],
    ["https", "https://box:443", false],
    ["http to localhost", "http://localhost:9000", false],
    ["http to 127.0.0.1 (an ssh -L forward)", "http://127.0.0.1:9000", false],
    ["http to [::1]", "http://[::1]:9000", false],
  ])("%s", (_id, url, want) => expect(needsPlainHttpWarning(url)).toBe(want));
});
