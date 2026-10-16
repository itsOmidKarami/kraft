import { describe, expect, it } from "vitest";
import { docsUrl } from "./docsUrl";

describe("docsUrl", () => {
  it.each([
    ["the latest stable", "2.1.0", "2.1.0", "https://kraft-sdlc.dev/"],
    ["a stable behind in its minor", "2.0.0", "2.1.0", "https://kraft-sdlc.dev/"],
    ["an older major", "1.4.0", "2.1.0", "https://kraft-sdlc.dev/v1/"],
    ["a release candidate", "2.2.0rc1", "2.1.0", "https://kraft-sdlc.dev/next/"],
    ["a dev build", "2.2.0.dev3+g1a2b3c", null, "https://kraft-sdlc.dev/next/"],
    ["a stable with no feed answer", "1.4.0", null, "https://kraft-sdlc.dev/"],
    ["no version yet", null, "2.1.0", "https://kraft-sdlc.dev/"],
  ])("%s", (_, version, latest, url) => {
    expect(docsUrl(version, latest)).toBe(url);
  });
});
