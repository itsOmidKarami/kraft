import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { detail, inShell, stubFetch, V1 } from "../testkit";
import { ItemDraftProvider, useDraft } from "./context";
import { DraftState, ReviewButton } from "./DraftBar";
import type { DraftView, MarkedOp } from "./types";

afterEach(() => vi.unstubAllGlobals());

const answer = (ops: MarkedOp[], problems: DraftView["problems"] = []): [number, DraftView] => [200, { ops, problems, checks: { budget: { spent_usd: 0, cap_usd: null } }, nodes: V1, base_seq: 1, updated_at: null }];
const ov = (path: string, passed = false): MarkedOp => ({ op: "override", path, task_config: { model: "opus" }, passed });

function Probe() {
  const { search } = useLocation();
  const d = useDraft();
  return <><output data-testid="at">{search}</output><output data-testid="rv">{String(d?.reviewing)}</output></>;
}
const show = (draft: [number, DraftView]) => {
  stubFetch({ "GET /work-items/w1/draft": draft });
  inShell(
    <ItemDraftProvider item={detail()} reload={() => {}}>
      <DraftState />
      <ReviewButton />
      <Probe />
    </ItemDraftProvider>,
  );
};

describe("DraftBar", () => {
  it("shows nothing without a draft", async () => {
    show(answer([]));
    await waitFor(() => expect(screen.getByTestId("rv")).toBeInTheDocument());
    expect(screen.queryByText(/DRAFT/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Review & apply/ })).toBeNull();
  });

  it("counts lines, singular at one", async () => {
    show(answer([ov("merge_request.open.open_draft")]));
    expect(await screen.findByText("DRAFT · 1 CHANGE")).toBeInTheDocument();
  });

  it("counts every overridden field", async () => {
    show(answer([{ op: "override", path: "merge_request.open.open_draft", task_config: { model: "m", effort: "high" }, policy: { budget_usd: 2 }, passed: false }]));
    expect(await screen.findByText("DRAFT · 3 CHANGES")).toBeInTheDocument();
  });

  it("opens Review & apply from the chip and the button", async () => {
    show(answer([ov("merge_request.open.open_draft")]));
    await userEvent.click(await screen.findByText("DRAFT · 1 CHANGE"));
    expect(screen.getByTestId("rv")).toHaveTextContent("true");
  });

  it("disables Review & apply while a problem stands, and says why", async () => {
    show(answer([ov("merge_request.open.open_draft")], [{ op: 0, message: "over the cap" }]));
    const b = await screen.findByRole("button", { name: "Review & apply" });
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute("title", "Fix the problems first");
    expect(screen.getByText("1 PROBLEM")).toBeInTheDocument();
  });

  it("counts a passed op as a problem though the server lists none, and leaves Review open to take it out", async () => {
    show(answer([ov("plan.write.plan", true)]));
    expect(await screen.findByText("1 PROBLEM")).toBeInTheDocument();
    const b = screen.getByRole("button", { name: "Review & apply" });
    expect(b).toBeEnabled();
    await userEvent.click(b);
    expect(screen.getByTestId("rv")).toHaveTextContent("true");
  });

  it("steps the badge through the problems, selecting each one's node", async () => {
    show(answer([ov("merge_request.open.open_draft"), ov("plan.write.plan", true)], [{ op: 0, message: "over the cap" }]));
    const badge = await screen.findByText("2 PROBLEMS");
    await userEvent.click(badge);
    expect(screen.getByTestId("at")).toHaveTextContent("sel=merge_request");
    await userEvent.click(badge);
    expect(screen.getByTestId("at")).toHaveTextContent("sel=plan");
    await userEvent.click(badge);
    expect(screen.getByTestId("at")).toHaveTextContent("sel=merge_request");
  });

  it("sends a passed added node, which is not on the canvas, to the chain pane", async () => {
    show(answer([{ op: "add_node", after: "plan", node: { id: "scan", extends: "s" }, passed: true }]));
    await userEvent.click(await screen.findByText("1 PROBLEM"));
    expect(screen.getByTestId("at")).toHaveTextContent(/^$/);
  });
});
