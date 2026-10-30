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
const show = (over: Partial<{ onApproved: () => void; onClose: () => void; fromLink: boolean }> = {}) =>
  inShell(<OneOffReview itemId="w1" fromLink={over.fromLink} onApproved={over.onApproved ?? (() => {})} onClose={over.onClose ?? (() => {})} />);
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

describe("OneOffReview", () => {
  it("shows what was added and removed, and every custom part in full", async () => {
    stubFetch({ [GET]: [200, DATA] });
    show();
    const d = await screen.findByRole("dialog", { name: /Approve this chain, then start/ });
    expect(within(d).getByText("custom content")).toBeInTheDocument();
    // The part is named in words: its raw path in the chain is not shown.
    expect(within(d).queryByText(/add\[0\]/)).toBeNull();
    // A long part scrolls in a box a keyboard can reach.
    expect(within(d).getByRole("region", { name: DATA.custom[0].what })).toHaveAttribute("tabindex", "0");
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

  it("lists what changed since filing when a draft revised the chain, one line of the revision per line", async () => {
    stubFetch({ [GET]: [200, { ...DATA, redrafted: [{ diff: ["  implementation", "+ k6"] }] }] });
    show();
    expect(await screen.findByText("changed since filing")).toBeInTheDocument();
    expect(document.querySelector("pre.idr-lines:not(.idr-custom-value)")!.textContent).toBe("  implementation\n+ k6");
  });

  it.each([
    { id: "a Start pressed: starts at once, no dialog", fromLink: false, dialog: false, started: true },
    { id: "opened from an address: asks, starts nothing", fromLink: true, dialog: true, started: false },
  ])("a read that needs no approval ($id)", async ({ fromLink, dialog, started }) => {
    const calls = stubFetch({ [GET]: [200, { ...DATA, needs_review: false }] });
    const onApproved = vi.fn();
    show({ onApproved, fromLink });
    await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/one-off")).toBe(true));
    if (dialog) expect(await screen.findByRole("dialog", { name: "Start this item?" })).toBeInTheDocument();
    else await waitFor(() => expect(onApproved).toHaveBeenCalled());
    expect(onApproved).toHaveBeenCalledTimes(started ? 1 : 0);
    expect(screen.queryByRole("dialog") !== null).toBe(dialog);
    expect(writes(calls)).toEqual([]);
  });

  it("a failed read says why in place of the chain, and Approve and start stays off", async () => {
    stubFetch({ [GET]: [404, { detail: "this work item has no one-off chain" }] });
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("no one-off chain");
    expect(screen.getByRole("button", { name: "Approve and start" })).toBeDisabled();
  });
});
