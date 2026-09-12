import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { GateCard } from "./GateCard";
import { item } from "./testFixtures";

describe("GateCard", () => {
  it("approve calls the API", async () => {
    const spy = vi.spyOn(api, "approveGate").mockResolvedValue(undefined);
    render(
      <GateCard
        item={item()}
        gate="plan_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(spy).toHaveBeenCalledWith("w1", "plan_approval");
  });

  it("reject submit is disabled until a note is entered", async () => {
    render(
      <GateCard
        item={item()}
        gate="plan_approval"
        open
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
    const submit = screen.getByRole("button", { name: /reject and/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/composer message/i), "fix the error path");
    expect(submit).toBeEnabled();
  });

  it("names the node a rejection sends the chain back to", () => {
    const withTarget = item({
      chain_definition: {
        template_id: "d",
        nodes: [
          { id: "spec", tasks: [], gate_after: null },
          { id: "plan", tasks: [], gate_after: "plan_approval", reject_to: "spec" },
        ],
      },
    });
    render(
      <GateCard
        item={withTarget}
        gate="plan_approval"
        open
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
    expect(screen.getByText(/re-enters at/)).toHaveTextContent("spec");
    expect(screen.getByRole("button", { name: /reject and send back/i })).toBeInTheDocument();
  });

  const card = (over: Parameters<typeof item>[0] = {}) =>
    render(
      <GateCard
        item={item(over)}
        gate="human_review_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
  const deferred = [
    { severity: "minor", message: "naming nit", file: "a.py", line: 3, source_plugin: "fake" },
  ];

  it("lists deferred minor findings at the gate", () => {
    card({ deferred_findings: [...deferred] });
    expect(screen.getByText(/naming nit/)).toBeInTheDocument();
    expect(screen.getByText(/a\.py:3/)).toBeInTheDocument();
  });

  it("shows concerns in the same panel as deferred findings", () => {
    card({ deferred_findings: [...deferred], concerns: ["the retry path is untested"] });
    expect(screen.getByText(/the retry path is untested/)).toBeInTheDocument();
    expect(screen.getByText(/naming nit/)).toBeInTheDocument();
    expect(document.querySelectorAll(".gate-deferred")).toHaveLength(1);
  });

  it("renders no list when there are no findings and no concerns", () => {
    const { container } = card({ deferred_findings: [], concerns: [] });
    expect(container.querySelector(".gate-deferred")).toBeNull();
  });

  it("shows a failed Approve", async () => {
    vi.spyOn(api, "approveGate").mockRejectedValue(new Error("409 gate already resolved"));
    card();
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(await screen.findByText(/409 gate already resolved/)).toBeInTheDocument();
  });

  it("offers Read document as a link to the gate document when the gate has an artifact", () => {
    render(
      <GateCard
        item={item({ gate_artifact: "docs/plan.md" })}
        gate="plan_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
    expect(screen.getByRole("link", { name: /review plan/i })).toBeInTheDocument();
  });

  it("Review spec navigates to the gate document in the Documents tab", () => {
    render(
      <GateCard
        item={item({ gate_artifact: "docs/spec.md" })}
        gate="spec_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={(node, tab) => `#node=${node}&tab=${tab}`}
      />,
    );
    const link = screen.getByRole("link", { name: /review spec/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("tab=documents"));
  });

  it("offers Skip alongside Approve/Reject", () => {
    card();
    expect(screen.getByRole("button", { name: /skip/i })).toBeInTheDocument();
  });

  it("disables Review spec with 'not written yet' when the artifact is absent", () => {
    render(
      <GateCard
        item={item({ gate_artifact: null })}
        gate="spec_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={() => "#"}
      />,
    );
    const btn = screen.getByText(/Review spec/);
    expect(btn).toHaveAttribute("aria-disabled", "true");
    expect(btn.getAttribute("title")).toMatch(/not written yet/);
  });
});
