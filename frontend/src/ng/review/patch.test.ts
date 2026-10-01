import { describe, expect, it } from "vitest";
import { parsePatch } from "./patch";

const TWO = `diff --git a/search/embed.py b/search/embed.py
index 1111111..2222222 100644
--- a/search/embed.py
+++ b/search/embed.py
@@ -55,4 +55,5 @@ def embed_all(docs, model, cache):
     out = []
-    text = normalise(doc.text)
+    key = content_hash(doc.text)
+    text = normalise(doc.text)
     return out
@@ -80,2 +81,2 @@
-a
+b
diff --git a/docs/search.md b/docs/search.md
index 3..4 100644
--- a/docs/search.md
+++ b/docs/search.md
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
`;

describe("parsePatch", () => {
  it("splits files and numbers lines across hunks", () => {
    const [embed, docs] = parsePatch(TWO);
    expect(embed.path).toBe("search/embed.py");
    expect(embed.status).toBe("modified");
    expect(embed.hunks.map((h) => [h.oldStart, h.newStart])).toEqual([[55, 55], [80, 81]]);
    expect(embed.hunks[0].lines).toEqual([
      { kind: " ", old: 55, new: 55, text: "    out = []" },
      { kind: "-", old: 56, new: null, text: "    text = normalise(doc.text)" },
      { kind: "+", old: null, new: 56, text: "    key = content_hash(doc.text)" },
      { kind: "+", old: null, new: 57, text: "    text = normalise(doc.text)" },
      { kind: " ", old: 57, new: 58, text: "    return out" },
    ]);
    expect(embed.hunks[1].lines).toEqual([{ kind: "-", old: 80, new: null, text: "a" }, { kind: "+", old: null, new: 81, text: "b" }]);
    expect(embed.text.startsWith("diff --git a/search/embed.py") && embed.text.endsWith("+b\n")).toBe(true);
    // The marker is not a line; it is a flag.
    expect(docs.hunks[0].lines.map((l) => l.text)).toEqual(["old", "new"]);
    expect(docs.noNewline).toBe(true);
  });

  it("reads renames, with and without a content change, under the new path", () => {
    const [pure, edited] = parsePatch(`diff --git a/a.py b/b.py
similarity index 100%
rename from a.py
rename to b.py
diff --git a/old/x.ts b/new/x.ts
similarity index 90%
rename from old/x.ts
rename to new/x.ts
index 1..2 100644
--- a/old/x.ts
+++ b/new/x.ts
@@ -1 +1 @@
-const a = 1;
+const a = 2;
`);
    expect(pure).toMatchObject({ path: "b.py", oldPath: "a.py", status: "renamed", hunks: [] });
    expect(edited).toMatchObject({ path: "new/x.ts", oldPath: "old/x.ts", status: "renamed" });
    expect(edited.hunks).toHaveLength(1);
  });

  it("reads new, deleted and binary files", () => {
    const [added, gone, bin] = parsePatch(`diff --git a/n.py b/n.py
new file mode 100644
index 0000000..1
--- /dev/null
+++ b/n.py
@@ -0,0 +1,2 @@
+one
+two
diff --git a/pyproject.toml b/pyproject.toml
deleted file mode 100644
index 1..0000000
--- a/pyproject.toml
+++ /dev/null
@@ -1 +0,0 @@
-[tool.poetry]
diff --git a/logo.png b/logo.png
index 1..2 100644
Binary files a/logo.png and b/logo.png differ
`);
    expect(added).toMatchObject({ path: "n.py", status: "added" });
    expect(added.hunks[0].lines.map((l) => l.new)).toEqual([1, 2]);
    expect(gone).toMatchObject({ path: "pyproject.toml", status: "deleted" });
    expect(bin).toMatchObject({ path: "logo.png", binary: true, hunks: [] });
  });

  it("is empty for an empty diff", () => {
    expect(parsePatch("")).toEqual([]);
  });
});
