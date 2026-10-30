import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch, inShell } from "./testkit";
import { OneOffReview } from "./OneOffReview";
import type { OneOffReviewData } from "./oneOff";

const DATA: OneOffReviewData = {
  extends: "default",
  custom: [{ path: "add[0].node", what: "adds 'k6', a node with its own content", value: { id: "k6", prompt: "run k6 against staging, then report p95" } }],
  provenance: { parent: "default", nodes: { k6: "added" }, overrides: {}, removed: ["work_brief"] },
  digest: "f".repeat(64),
  needs_review: true,
  added: [{ node: "k6", harnesses: ["claude"], estimate_usd: null, estimate_runs: null }],
  redrafted: [],
};
const GET = "GET /work-items/w1/one-off";
const POST = "POST /work-items/w1/one-off/approve";
afterEach(() => vi.unstubAllGlobals());
const show = (over: Partial<{ onApproved: () => void; onClose: () => void }> = {}) =>
  inShell(<OneOffReview itemId="w1" onApproved={over.onApproved ?? (() => {})} onClose={over.onClose ?? (() => {})} />);
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

describe("OneOffReview", () => {
  it("shows what was added and removed, and every custom part in full", async () => {
    stubFetch({ [GET]: [200, DATA] });
    show();
    const d = await screen.findByRole("dialog", { name: /Approve this chain, then start/ });
    expect(within(d).getByText("Custom: read these")).toBeInTheDocument();
    expect(within(d).getByText(/run k6 against staging, then report p95/)).toBeInTheDocument();
    expect(within(d).getByText("work_brief")).toBeInTheDocument();
    expect(within(d).getByText(/k6: harness claude is allowed · no estimate yet/)).toBeInTheDocument();
  });

  it("Approve and start posts the digest it was shown, as the web door, then reports it approved", async () => {
    const calls = stubFetch({ [GET]: [200, DATA], [POST]: [200, {}] });
    const onApproved = vi.fn();
    show({ onApproved });
    await userEvent.click(await screen.findByRole("button", { name: "Approve and start" }));
    await waitFor(() => expect(onApproved).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/one-off/approve", body: { digest: "f".repeat(64), door: "web" } }]);
  });

  it("a 409 says why, reads the chain again, and approves nothing", async () => {
    const calls = stubFetch({ [GET]: [200, DATA], [POST]: [409, { detail: "the one-off chain changed since it was reviewed; review it again" }] });
    const onApproved = vi.fn();
    show({ onApproved });
    await userEvent.click(await screen.findByRole("button", { name: "Approve and start" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since it was reviewed");
    expect(onApproved).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.method === "GET" && c.path === "/work-items/w1/one-off")).toHaveLength(2);
  });

  it("lists what changed since filing when a draft revised the chain", async () => {
    stubFetch({ [GET]: [200, { ...DATA, redrafted: [{ diff: "+ k6 prompt changed" }] }] });
    show();
    expect(await screen.findByText("Changed since filing")).toBeInTheDocument();
    expect(screen.getByText("+ k6 prompt changed")).toBeInTheDocument();
  });
});
