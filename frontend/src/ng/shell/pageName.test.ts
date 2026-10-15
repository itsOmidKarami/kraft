import { describe, expect, it } from "vitest";
import { shortId } from "../../format";
import { nearestPage, pageName, pageTitle } from "./pageName";

const item = (id: string) => (id === "a" ? { title: "Fix login" } : undefined);

describe("pageName and pageTitle", () => {
  it.each([
    ["/", "Board · Kraft"],
    ["/search", "Search · Kraft"],
    ["/settings/policy/limits", "Policy · Kraft"],
    ["/work-items/a?tab=log", "Fix login · Kraft"],
    ["/work-items/a/nodes/merge_request", "merge request · Fix login · Kraft"],
    ["/work-items/a/review", "Review · Fix login · Kraft"],
    ["/templates/chains/default", "default · Kraft"],
    ["/nope", "Not found · Kraft"],
  ])("titles %s as %s", (href, title) => expect(pageTitle(href, item)).toBe(title));

  it("leads with the Needs you count when it is not zero", () => {
    expect(pageTitle("/", item, 2)).toBe("(2) Board · Kraft");
    expect(pageTitle("/", item, 0)).toBe("Board · Kraft");
  });

  it("names an item with no title by its short id, and an overlay by its screen", () => {
    expect(pageName("/work-items/abcdef123456789")).toBe(shortId("abcdef123456789"));
    expect(pageName("/work-items/a/nodes/n?sel=n.s.test_api&scope=x", item)).toBe("test api");
    expect(pageName("/work-items/a?doc=x", item)).toBe("Fix login");
  });
});

describe("nearestPage", () => {
  it.each([
    ["/settings/nope", "/settings/policy"],
    ["/settings/repos/x/y", "/settings/repos"],
    ["/templates/nope", "/templates/chains"],
    ["/nope", "/"],
    ["/nope/deeper", "/"],
  ])("sends %s up to %s", (path, to) => expect(nearestPage(path).to).toBe(to));
});
