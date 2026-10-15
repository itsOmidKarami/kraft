import { describe, expect, it } from "vitest";
import { homePath } from "./path";

describe("homePath", () => {
  it.each([
    ["linux home", "/home/omid/code/kraft", "~/code/kraft"],
    ["mac home", "/Users/omid/code/kraft", "~/code/kraft"],
    ["root's home", "/root/kraft", "~/kraft"],
    ["the home itself", "/home/omid", "~"],
    ["outside any home", "/srv/kraft", "/srv/kraft"],
    ["a name that only starts like one", "/homework/omid/x", "/homework/omid/x"],
  ])("%s", (_, path, shown) => expect(homePath(path)).toBe(shown));
});
