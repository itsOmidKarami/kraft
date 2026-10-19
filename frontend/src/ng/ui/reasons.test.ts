import { describe, expect, it } from "vitest";
import { cantMove } from "./reasons";

describe("cantMove", () => {
  it.each([
    ["verification", "verification comes from default, so it keeps its place. Detach the chain to reorder it", undefined, "Can't move verification: it comes from default, so it keeps its place. Detach the chain to reorder it"],
    ["implementation", "can't move implementation: reject_to of local_review would come after it", undefined, "Can't move implementation: reject_to of local_review would come after it"],
    ["code_review", "verification.review.code_review is inherited from default", "verification.review.code_review", "Can't move code_review: it is inherited from default"],
    ["lint", "step main already has a task called lint", undefined, "Can't move lint: step main already has a task called lint"],
  ])("names %s once", (name, why, path, text) => {
    expect(cantMove(name, why, path)).toBe(text);
  });
});
