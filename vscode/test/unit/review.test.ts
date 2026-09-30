import { describe, expect, it } from "vitest";
import { commandItemId, leftUri, parseReviewUri, reviewFiles, rightUri } from "../../src/core/review";

describe("reviewFiles", () => {
  it("unions landed, in-flight and untracked, sorted and unique", () => {
    const diff = {
      files: [{ path: "b.py", insertions: 1, deletions: 0 }],
      untracked: ["c.py"],
      landed: { commits: [], files: [{ path: "a.md", insertions: 3, deletions: 0 }, { path: "b.py", insertions: 1, deletions: 1 }], diff: "", truncated: false },
    } as any;
    expect(reviewFiles(diff)).toEqual(["a.md", "b.py", "c.py"]);
  });

  it("copes with an older server that sends no landed range", () => {
    expect(reviewFiles({ files: [], untracked: ["x"] } as any)).toEqual(["x"]);
  });
});

describe("review URIs", () => {
  it("round-trip id and path, including nested paths and odd refs", () => {
    expect(leftUri("K-1", "src/a b#.py", "abc123")).toBe("kraft-git:/K-1/src/a%20b%23.py?abc123");
    expect(rightUri("K-1", "src/a.py")).toBe("kraft-wt:/K-1/src/a.py");
    expect(parseReviewUri("/K-1/src/a b.py")).toEqual({ id: "K-1", file: "src/a b.py" });
  });
});

describe("commandItemId", () => {
  it("reads the id from the Uri an editor title bar passes, not only from an id string", () => {
    const tab = { scheme: "kraft-artifact", path: "/K-1/Spec.md" };
    expect(commandItemId(tab, undefined, ["kraft-artifact"])).toBe("K-1");
    expect(commandItemId("K-2", tab, ["kraft-artifact"])).toBe("K-2");
    expect(commandItemId({ item: { id: "K-3" } }, tab, ["kraft-artifact"])).toBe("K-3");
    expect(commandItemId(undefined, tab, ["kraft-artifact"])).toBe("K-1");
    expect(commandItemId({ scheme: "file", path: "/Users/x/a.md" }, undefined, ["kraft-artifact"])).toBeUndefined();
  });
});
