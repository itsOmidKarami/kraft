import { describe, expect, it } from "vitest";
import { remoteConfig, remoteReview, remoteStart, remoteWorktree } from "../../src/core/remote";

describe("remote sentences", () => {
  it.each([
    ["remoteStart", remoteStart],
    ["remoteWorktree", remoteWorktree],
    ["remoteReview", remoteReview],
    ["remoteConfig", remoteConfig],
  ])("%s names the server and not a token", (_id, sentence) => {
    const text = sentence("https://box.example:443");
    expect(text).toContain("box.example:443");
    expect(text).not.toMatch(/token/i);
  });
});
