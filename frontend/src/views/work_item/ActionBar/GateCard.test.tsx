import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { KraftEvent } from "../../../types";
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
        reviewHref={(node, tab) => `#node=${node}&tab=${tab}`}
      />,
    );
  // Ten findings, matching the live item's scale (5.4k characters of prose)
  // that pushed the graph and both panes off the viewport (Kraft-a4js).
  const tenFindings = Array.from({ length: 10 }, (_, i) => ({
    severity: "minor",
    message: `finding number ${i} with a fair bit of prose explaining why it matters and where`,
    file: "a.py",
    line: i,
    source_plugin: "fake",
  }));

  it("counts deferred findings in one line instead of listing them", () => {
    card({ deferred_findings: tenFindings });
    expect(screen.getByText(/10 findings deferred/)).toBeInTheDocument();
    expect(screen.queryByText(/finding number 0/)).toBeNull();
    expect(document.querySelectorAll(".gate-deferred")).toHaveLength(1);
    expect(document.querySelectorAll(".gate-deferred li")).toHaveLength(0);
  });

  it("links the one-liner to the Timeline tab", () => {
    card({ deferred_findings: tenFindings });
    const link = screen.getByRole("link", { name: /see timeline/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("tab=timeline"));
  });

  // human_review never emits findings_measured (those come from verify/mr_checks)
  // -- the link must select the node that actually carries them, not the gate
  // node, or the deferred findings the card counts are unreachable.
  it("targets the node whose findings_measured event holds the findings, not the gate node", () => {
    const events: KraftEvent[] = [
      {
        seq: 1,
        work_item_id: "w1",
        type: "findings_measured",
        payload: { node_id: "verify", findings: tenFindings },
        created_at: "t",
      },
    ];
    render(
      <GateCard
        item={item({ deferred_findings: tenFindings, current_node_id: "human_review" })}
        gate="human_review_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={(node, tab, id) => `#node=${node}&tab=${tab}&tnode=${id}`}
        events={events}
      />,
    );
    const link = screen.getByRole("link", { name: /see timeline/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("tnode=verify"));
  });

  // the default chain measures at both verify and mr_checks -- mr_checks is
  // commonly clean, and a newer *empty* findings_measured there must not
  // shadow the earlier verify event that actually carries the findings.
  it("skips a later findings_measured event that carries no findings", () => {
    const events: KraftEvent[] = [
      {
        seq: 1,
        work_item_id: "w1",
        type: "findings_measured",
        payload: { node_id: "verify", findings: tenFindings },
        created_at: "t1",
      },
      {
        seq: 2,
        work_item_id: "w1",
        type: "findings_measured",
        payload: { node_id: "mr_checks", findings: [] },
        created_at: "t2",
      },
    ];
    render(
      <GateCard
        item={item({ deferred_findings: tenFindings, current_node_id: "human_review" })}
        gate="human_review_approval"
        open={false}
        onOpen={() => {}}
        onCancel={() => {}}
        reviewHref={(node, tab, id) => `#node=${node}&tab=${tab}&tnode=${id}`}
        events={events}
      />,
    );
    const link = screen.getByRole("link", { name: /see timeline/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("tnode=verify"));
  });

  it("counts concerns alongside deferred findings in the same one-liner", () => {
    card({ deferred_findings: tenFindings, concerns: ["the retry path is untested"] });
    expect(screen.getByText(/10 findings deferred · 1 concern/)).toBeInTheDocument();
  });

  it("renders no summary line when there are no findings and no concerns", () => {
    const { container } = card({ deferred_findings: [], concerns: [] });
    expect(container.querySelector(".gate-deferred")).toBeNull();
  });

  const judgeNote = [
    {
      node_id: "verify",
      reasoning: "real but not worth chasing further",
      findings: [
        { severity: "important", message: "still broken", file: "a.py", line: 4, source_plugin: "on.check" },
      ],
    },
  ];

  it("renders a judge-stop note distinct from deferred findings, counted rather than listed", () => {
    card({ deferred_findings: [...tenFindings], judge_stop_note: judgeNote });
    expect(screen.getByText(/real but not worth chasing further/)).toBeInTheDocument();
    expect(screen.getByText(/1 finding not chased/)).toBeInTheDocument();
    expect(screen.queryByText(/still broken/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".gate-deferred")).toHaveLength(1);
    expect(document.querySelectorAll(".gate-judge-note")).toHaveLength(1);
  });

  it("renders no judge block when there is no judge-stop note", () => {
    const { container } = card({ judge_stop_note: [] });
    expect(container.querySelector(".gate-judge-note")).toBeNull();
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
