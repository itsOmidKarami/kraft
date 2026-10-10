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
    expect(texts("/settings/storage")).toEqual(["Settings", "Storage"]);
    const head = crumbsFor("/settings/policy", none)[0];
    expect(head.to).toBeUndefined();
    expect(head.href).toBeUndefined();
  });

  it("makes Archived a child of the Board", () => {
    const [board, archived] = crumbsFor("/archived", none);
    expect(board).toMatchObject({ text: "Board", to: "/" });
    expect(board.href).toBeUndefined();
    expect(archived).toMatchObject({ text: "Archived", kind: "current" });
  });

  it("shows Board › repo › bead id for a work item, the repo linking to the board filtered to it; the title is the page's heading, not a crumb", () => {
    const item = () => ({ repo: "/home/me/code/kraft-api", title: "Add rate limiting", bead_id: "kraft-cb59" });
    const [board, repo, cur, ...rest] = crumbsFor("/work-items/abc", item);
    expect(board).toMatchObject({ text: "Board", to: "/" });
    expect(board.href).toBeUndefined();
    expect(repo).toMatchObject({ text: "kraft-api", kind: "repo", title: "/home/me/code/kraft-api", to: "/?repo=%2Fhome%2Fme%2Fcode%2Fkraft-api" });
    expect(cur).toMatchObject({ text: "kraft-cb59", kind: "current", title: "abc" });
    expect(rest).toEqual([]);
  });

  it("ends in the short id without a bead, and never in a raw 32-hex id", () => {
    const id = "f3e4f5f5c6160858e4bc7116394e0f79";
    expect(texts(`/work-items/${id}`, () => ({ repo: "/r/x", title: "t" }))).toEqual(["Board", "x", "f3e4f5f5…e0f79"]);
    expect(texts(`/work-items/${id}`)).toEqual(["Board", "f3e4f5f5…e0f79"]);
  });

  it("in a node view and on the review page, links the item back to its chain and ends in the node or Review changes", () => {
    const [, , onItem, review] = crumbsFor("/work-items/abc/review", () => ({ repo: "/r/x", title: "t", bead_id: "kraft-cb59" }));
    expect(onItem).toMatchObject({ text: "kraft-cb59", kind: "mid", to: "/work-items/abc" });
    expect(review).toMatchObject({ text: "Review changes", kind: "current" });
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

  it("names the draft item page Board › repo › new item, the repo linking back to the board filtered to it", () => {
    const [board, repo, cur] = crumbsFor("/work-items/new", (id) => (id === "new" ? { repo: "/code/kraft-plugins", title: "" } : undefined));
    expect(board).toMatchObject({ text: "Board", to: "/" });
    expect(repo).toMatchObject({ text: "kraft-plugins", to: "/?repo=%2Fcode%2Fkraft-plugins" });
    expect(cur).toMatchObject({ text: "new item", kind: "current" });
    expect(texts("/work-items/new")).toEqual(["Board", "new item"]);
  });

  it("says Not found for a path it does not know", () => {
    expect(texts("/nope")).toEqual(["Not found"]);
  });

  it("gives a chain's page Templates and a link back to Chains; the page adds the rest", () => {
    const cs = crumbsFor("/templates/chains/default/nodes/spec", none);
    expect(cs.map((c) => c.text)).toEqual(["Templates", "Chains"]);
    expect(cs[1].to).toBe("/templates/chains");
  });

  it("gives a library component's page Templates and a link back to Library; the page adds the name", () => {
    const cs = crumbsFor("/templates/library/tasks.implementer", none);
    expect(cs.map((c) => c.text)).toEqual(["Templates", "Library"]);
    expect(cs[1].to).toBe("/templates/library");
  });

  it("names Repos, Policy and Auto-intake under their group, whichever repo or section is open (the page adds that)", () => {
    expect(crumbsFor("/settings/repos", none).map((c) => c.text)).toEqual(["Settings", "Repos"]);
    expect(crumbsFor("/settings/repos/platform", none).map((c) => c.text)).toEqual(["Settings", "Repos"]);
    expect(crumbsFor("/settings/policy/loops", none).map((c) => c.text)).toEqual(["Settings", "Policy"]);
    expect(crumbsFor("/settings/auto-intake", none).map((c) => c.text)).toEqual(["Settings", "Auto-intake"]);
  });
});
