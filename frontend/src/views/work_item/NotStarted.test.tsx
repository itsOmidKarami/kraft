import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { item } from "./ActionBar/testFixtures";
import { ChainDescription, NotStartedCard } from "./NotStarted";

const notStarted = item({
  id: "w9",
  status: "paused",
  current_node_id: null,
  repo: "/Users/dev/code/kraft",
  budget_cap: { cap_usd: 10, source: "policy", spent_usd: 0 },
});

describe("NotStartedCard (spec 21, Kraft-pfqdb)", () => {
  it("shows template, repo, budget, where it starts and its first gate", () => {
    render(<NotStartedCard item={notStarted} />);
    const card = screen.getByTestId("not-started-card");
    expect(card).toHaveTextContent("default");
    expect(card).toHaveTextContent("kraft");
    expect(card).toHaveTextContent("$10 · policy default");
    expect(card).toHaveTextContent("will start atspec");
    expect(card).toHaveTextContent("first gatespec_approval");
  });

  it("Start resumes the item it is on", async () => {
    const resume = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({ id: "w9", node_id: "spec", steer: null });
    render(<NotStartedCard item={notStarted} />);
    await userEvent.click(screen.getByRole("button", { name: /start/i }));
    expect(resume).toHaveBeenCalledWith("w9");
  });

  it("describes the chain it will walk", () => {
    render(<ChainDescription item={notStarted} />);
    expect(screen.getByText(/3 nodes, 2 gates/)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText(/then gate plan_approval/)).toBeInTheDocument();
  });
});
