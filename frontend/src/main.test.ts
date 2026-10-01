import { describe, expect, it } from "vitest";
import { withoutNg } from "./main";

describe("withoutNg", () => {
  it.each([
    ["/ng", "", "", "/"],
    ["/ng/", "", "", "/"],
    ["/ng/work-items/a", "?x=1", "#node=n", "/work-items/a?x=1#node=n"],
    ["/ng/settings/chains", "", "", "/settings/chains"],
  ])("%s%s%s → %s", (pathname, search, hash, to) => {
    expect(withoutNg({ pathname, search, hash })).toBe(to);
  });

  it.each(["/", "/ngx", "/ngrok/a", "/work-items/ng"])("leaves %s alone", (pathname) => {
    expect(withoutNg({ pathname, search: "", hash: "" })).toBeNull();
  });
});
