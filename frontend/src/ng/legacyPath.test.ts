import { describe, expect, it } from "vitest";
import { legacyPath } from "./legacyPath";

const at = (pathname: string, search = "") => legacyPath({ pathname, search });

// One case per row of spec §2.6's table.
describe("legacyPath", () => {
  it.each([
    ["/ng", "/"],
    ["/ng/", "/"],
    ["/ng/archived", "/archived"],
    ["/ng/analytics", "/analytics"],
    ["/ng/search", "/search"],
    ["/ng/work-items/abc", "/work-items/abc"],
    ["/ng/work-items/abc/nodes/verify", "/work-items/abc#node=verify"],
    ["/ng/work-items/abc/review", "/work-items/abc#tab=changes"],
    ["/ng/work-items/new", "/"],
    ["/ng/templates/chains", "/settings/chains"],
    ["/ng/templates/library/tasks.implementer", "/settings/library"],
    ["/ng/templates/harnesses", "/settings/harnesses"],
    ["/ng/templates/repos", "/settings/repos"],
    ["/ng/settings/notifications", "/settings/notify"],
    ["/ng/settings/about", "/settings"],
    ["/ng/settings/policy", "/settings/policy"],
  ])("%s → %s", (from, to) => expect(at(from)).toBe(to));

  it("keeps the query string, before the hash", () => {
    expect(at("/ng/search", "?q=cache")).toBe("/search?q=cache");
    expect(at("/ng/work-items/abc/nodes/verify", "?x=1")).toBe("/work-items/abc?x=1#node=verify");
  });
});
