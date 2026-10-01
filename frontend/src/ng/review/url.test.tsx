import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../types";
import { readReview, reviewUrl, useReviewPlace } from "./url";

const item = (o: Partial<WorkItem> = {}) => ({ id: "w1", last_review_sha: null, head_sha: "h2", pending_gate: "final_review", ...o }) as WorkItem;
const q = (s: string) => new URLSearchParams(s);

describe("review URL", () => {
  it("defaults: base to latest at the pending gate; from the last review on a re-review", () => {
    expect(readReview(q(""), item())).toEqual({ from: "base", to: "latest", nodes: null, file: null, gate: "final_review", doc: false });
    expect(readReview(q(""), item({ last_review_sha: "h1" })).from).toBe("last_review");
    // Nothing changed since that review: it would compare HEAD with itself.
    expect(readReview(q(""), item({ last_review_sha: "h2" })).from).toBe("base");
  });

  it("reads every parameter, and drops a target it does not know", () => {
    const p = readReview(q("from=attempt:1&to=attempt:2&nodes=implementation,verification&file=a/b.py&gate=local_review&doc=1"), item());
    expect(p).toEqual({ from: "attempt:1", to: "attempt:2", nodes: ["implementation", "verification"], file: "a/b.py", gate: "local_review", doc: true });
    expect(readReview(q("from=latest&to=attempt:0"), item())).toMatchObject({ from: "base", to: "latest" });
  });

  it("writes only what differs from the defaults, and round-trips", () => {
    expect(reviewUrl("w1", readReview(q(""), item()), item())).toBe("/work-items/w1/review");
    const url = reviewUrl("w1", { from: "attempt:1", to: "latest", nodes: ["implementation"], file: "x.py", gate: "final_review", doc: true }, item());
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
