import { describe, expect, it } from "vitest";
import { crumbsFor } from "./crumbs";

const none = () => undefined;
const texts = (p: string, lookup = none) => crumbsFor(p, lookup).map((c) => c.text);

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

  it("makes Archived a child of the shipped Board", () => {
    const [board, archived] = crumbsFor("/archived", none);
    expect(board).toMatchObject({ text: "Board", href: "/" });
    expect(archived).toMatchObject({ text: "Archived", kind: "current" });
  });

  it("shows repo › Board › title for a work item, with the full text in titles", () => {
    const item = () => ({ repo: "/home/me/code/kraft-api", title: "Add rate limiting" });
    const [repo, board, cur] = crumbsFor("/work-items/abc", item);
    expect(repo).toMatchObject({ text: "kraft-api", kind: "repo", title: "/home/me/code/kraft-api" });
    expect(board).toMatchObject({ text: "Board", href: "/" });
    expect(cur).toMatchObject({ text: "Add rate limiting", kind: "current", title: "Add rate limiting" });
  });

  it("falls back to the id, with no repo crumb, until the store has the item", () => {
    expect(texts("/work-items/abc")).toEqual(["Board", "abc"]);
  });

  it("says Not found for a path it does not know", () => {
    expect(texts("/nope")).toEqual(["Not found"]);
    expect(texts("/_tokens")).toEqual(["Tokens"]);
  });
});
