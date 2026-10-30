import { describe, expect, it } from "vitest";
import { approveBody, changedNodes, customText } from "./oneOff";

describe("customText", () => {
  it("writes a prompt as a block, so it reads as it was written", () => {
    expect(customText({ id: "k6", tasks: [{ id: "run", kind: "agent", prompt: "Run k6.\nReport p95.\n" }] })).toBe(
      ["id: k6", "tasks:", "  - id: run", "    kind: agent", "    prompt: |", "      Run k6.", "      Report p95."].join("\n"),
    );
  });
  it("leaves a string as it is, and quotes a scalar that needs it", () => {
    expect(customText("run k6")).toBe("run k6");
    expect(customText({ command: "npm test -- --ci" })).toBe('command: "npm test -- --ci"');
    expect(customText({ steps: [], on: null })).toBe("steps: []\non: null");
  });
});

describe("the review model", () => {
  it("names the digest it was shown and the door", () => {
    expect(approveBody({ digest: "abc" } as never, "phone")).toEqual({ digest: "abc", door: "phone" });
  });
  it("sorts nodes by what the chain did to them", () => {
    expect(changedNodes({ parent: "d", nodes: { a: "added", b: "overridden", c: "inherited" }, overrides: {}, removed: ["z"] })).toEqual({ added: ["a"], overridden: ["b"], removed: ["z"] });
  });
});
