import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../types";
import { readReview, reviewUrl, roundReviewUrl, useReviewPlace } from "./url";

const item = (o: Partial<WorkItem> = {}) => ({ id: "w1", last_review_sha: null, head_sha: "h2", pending_gate: "final_review", ...o }) as WorkItem;
const q = (s: string) => new URLSearchParams(s);

describe("review URL", () => {
  it("defaults: base to latest at the pending gate; from the last review on a re-review", () => {
    const reReview = { attempts: [{ n: 1, sha: "h1", base_sha: "b", at: "" }] };
    expect(readReview(q(""), item())).toEqual({ from: "base", to: "latest", nodes: null, file: null, gate: "final_review", doc: false, round: null });
    expect(readReview(q(""), item({ last_review_sha: "h1", ...reReview })).from).toBe("last_review");
    // Nothing changed since that review: it would compare HEAD with itself.
    expect(readReview(q(""), item({ last_review_sha: "h2", ...reReview })).from).toBe("base");
    // A review at another gate (local_review before final_review): this gate sees the whole change.
    expect(readReview(q(""), item({ last_review_sha: "h0", ...reReview })).from).toBe("base");
  });

  it("reads every parameter, and drops a target it does not know", () => {
    const p = readReview(q("from=attempt:1&to=attempt:2&nodes=implementation,verification&file=a/b.py&gate=local_review&doc=1"), item());
    expect(p).toEqual({ from: "attempt:1", to: "attempt:2", nodes: ["implementation", "verification"], file: "a/b.py", gate: "local_review", doc: true, round: null });
    expect(readReview(q("from=latest&to=attempt:0"), item())).toMatchObject({ from: "base", to: "latest" });
  });

  it.each([
    ["a round of a gate", "gate=final_review&round=2", { gate: "final_review", round: 2 }],
    ["a round of zero is none", "round=0", { round: null }],
    ["a round that is no number is none", "round=x", { round: null }],
  ])("reads %s", (_name, search, expected) => {
    expect(readReview(q(search), item())).toMatchObject(expected);
  });

  it.each([
    // The gate is the pending one, the default, and still written: without it the round would read as the review.
    ["a round writes its gate though it is the pending one", reviewUrl("w1", { ...readReview(q(""), item()), round: 2 }, item()), "/work-items/w1/review?gate=final_review&round=2"],
    ["a round of a gate no longer pending", reviewUrl("w1", { ...readReview(q(""), item({ pending_gate: null })), gate: "final_review", round: 2 }, item({ pending_gate: null })), "/work-items/w1/review?gate=final_review&round=2"],
    ["the round link encodes its parts", roundReviewUrl("w 1", "final_review", 2), "/work-items/w%201/review?gate=final_review&round=2"],
  ])("%s", (_name, url, expected) => {
    expect(url).toBe(expected);
  });

  it("writes only what differs from the defaults, and round-trips", () => {
    expect(reviewUrl("w1", readReview(q(""), item()), item())).toBe("/work-items/w1/review");
    const url = reviewUrl("w1", { from: "attempt:1", to: "latest", nodes: ["implementation"], file: "x.py", gate: "final_review", doc: true, round: null }, item());
    expect(url).toBe("/work-items/w1/review?from=attempt%3A1&nodes=implementation&file=x.py&doc=1");
    expect(readReview(q(url.split("?")[1]), item())).toMatchObject({ from: "attempt:1", nodes: ["implementation"], file: "x.py", doc: true });
  });

  it("moves on the page by replace, not push", () => {
    const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={["/work-items/w1/review"]}>{children}</MemoryRouter>;
    const { result } = renderHook(() => ({ place: useReviewPlace(item()), loc: useLocation(), how: useNavigationType() }), { wrapper });
    act(() => result.current.place[1]({ file: "x.py" }));
    expect(result.current.place[0].file).toBe("x.py");
    expect(result.current.loc.search).toBe("?file=x.py");
    expect(result.current.how).toBe("REPLACE");
  });
});
