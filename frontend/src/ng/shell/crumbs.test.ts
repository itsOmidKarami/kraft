import { describe, expect, it } from "vitest";
import { crumbsFor } from "./crumbs";

const none = () => undefined;
const texts = (p: string, lookup: Parameters<typeof crumbsFor>[1] = none) => crumbsFor(p, lookup).map((c) => c.text);

describe("crumbsFor", () => {
  it("names the Board on its own", () => {
    expect(crumbsFor("/", none)).toEqual([{ text: "Board", kind: "current", title: "Board" }]);
  });

  it("puts Templates and Settings in front of their pages as text, not links", () => {
    expect(texts("/templates/chains")).toEqual(["Templates", "Chains"]);
    expect(texts("/settings/auto-intake")).toEqual(["Settings", "Auto-intake"]);
    expect(texts("/settings/about")).toEqual(["Settings", "About"]);
    const head = crumbsFor("/settings/policy", none)[0];
    expect(head.to).toBeUndefined();
    expect(head.href).toBeUndefined();
  });

  it("makes Archived a child of the /ng Board", () => {
    const [board, archived] = crumbsFor("/archived", none);
    expect(board).toMatchObject({ text: "Board", to: "/" });
    expect(board.href).toBeUndefined();
    expect(archived).toMatchObject({ text: "Archived", kind: "current" });
  });

  it("shows Board › repo › bead id for a work item; the title is the page's heading, not a crumb", () => {
    const item = () => ({ repo: "/home/me/code/kraft-api", title: "Add rate limiting", bead_id: "kraft-cb59" });
    const [board, repo, cur, ...rest] = crumbsFor("/work-items/abc", item);
    expect(board).toMatchObject({ text: "Board", to: "/" });
    expect(board.href).toBeUndefined();
    expect(repo).toMatchObject({ text: "kraft-api", kind: "repo", title: "/home/me/code/kraft-api" });
    expect(cur).toMatchObject({ text: "kraft-cb59", kind: "current", title: "abc" });
    expect(rest).toEqual([]);
  });

  it("ends in the short id without a bead, and never in a raw 32-hex id", () => {
    const id = "f3e4f5f5c6160858e4bc7116394e0f79";
    expect(texts(`/work-items/${id}`, () => ({ repo: "/r/x", title: "t" }))).toEqual(["Board", "x", "f3e4f5f5…e0f79"]);
    expect(texts(`/work-items/${id}`)).toEqual(["Board", "f3e4f5f5…e0f79"]);
  });

  it("in a node view, links the item back to its chain and ends in the node", () => {
    const [, , itemCrumb, node] = crumbsFor("/work-items/abc/nodes/verification", () => ({ repo: "/r/x", title: "t", bead_id: "kraft-cb59" }));
    expect(itemCrumb).toMatchObject({ text: "kraft-cb59", kind: "mid", to: "/work-items/abc" });
    expect(node).toMatchObject({ text: "verification", kind: "current" });
  });

  it("puts the merge request after the crumbs, merged once the item is done", () => {
    const mr = { number: 142, url: "https://forge/pr/142" };
    const at = (display_status: "running" | "done") => crumbsFor("/work-items/abc", () => ({ repo: "/r/x", title: "t", mr_ref: mr, display_status })).at(-1);
    expect(at("running")).toEqual({ text: "!142 ↗", kind: "ext", href: mr.url, title: mr.url });
    expect(at("done")?.text).toBe("!142 merged ↗");
  });

  it("says Not found for a path it does not know", () => {
    expect(texts("/nope")).toEqual(["Not found"]);
    expect(texts("/_tokens")).toEqual(["Tokens"]);
  });
});
