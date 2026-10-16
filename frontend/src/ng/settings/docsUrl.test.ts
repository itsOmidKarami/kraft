import { describe, expect, it } from "vitest";
import { docsUrl } from "./docsUrl";

describe("docsUrl", () => {
  it.each([
    ["the latest stable", "2.1.0", "2.1.0", "/"],
    ["a stable behind in its minor", "2.0.0", "2.1.0", "/"],
    ["an older major", "1.4.0", "2.1.0", "/v1/"],
    ["a release candidate", "2.2.0rc1", "2.1.0", "/next/"],
    ["a dev build", "2.2.0.dev3+g1a2b3c", null, "/next/"],
    ["a stable with no feed answer", "1.4.0", null, "/"],
    ["no version yet", null, "2.1.0", "/"],
  ])("%s", (_, version, latest, url) => {
    // The site root is the host, so the expectations are paths under it.
    expect(new URL(docsUrl(version, latest)).pathname).toBe(url);
  });
});
