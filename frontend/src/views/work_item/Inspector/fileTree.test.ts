import { describe, expect, it } from "vitest";
import { buildFileTree } from "./fileTree";

describe("buildFileTree", () => {
  it("groups paths into folders and counts the files under each", () => {
    const tree = buildFileTree([
      { path: "frontend/src/App.tsx", insertions: 2, deletions: 0 },
      { path: "frontend/src/api.ts", insertions: 11, deletions: 0 },
      { path: "frontend/e2e/chain.spec.ts", insertions: 6, deletions: 8 },
    ] as never);
    expect(tree).toHaveLength(1);
    expect(tree[0].name).toBe("frontend");
    expect(tree[0].fileCount).toBe(3);
    const src = tree[0].children.find((c) => c.name === "src")!;
    expect(src.fileCount).toBe(2);
    expect(src.children.map((c) => c.name)).toEqual(["App.tsx", "api.ts"]);
  });

  it("keeps a file at the root when it has no folder", () => {
    const tree = buildFileTree([{ path: "justfile", insertions: 6, deletions: 0 }] as never);
    expect(tree[0].kind).toBe("file");
  });
});
