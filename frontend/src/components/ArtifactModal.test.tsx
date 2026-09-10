import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { ArtifactModal } from "./ArtifactModal";

describe("ArtifactModal", () => {
  it("renders the document's title and markdown body", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: ".engineering/specs/w1.md",
      title: "A spec",
      content: "# Heading\n\nbody text",
      truncated: false,
    });
    render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("A spec")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Heading" })).toBeTruthy();
  });

  it("says so when the server has nothing to show", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockRejectedValue(new Error("404: no artifact"));
    render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/no artifact/)).toBeTruthy();
  });

  it("renders a GFM pipe table as a table", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: ".engineering/specs/w1.md",
      title: "A spec",
      content: "| File | Change |\n|---|---|\n| db.py | branch column |\n",
      truncated: false,
    });
    const { container } = render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("branch column");
    expect(container.querySelector(".doc-modal-body table th")?.textContent).toBe("File");
  });

  it("renders a chain_review JSON envelope as prose plus a formatted block, not a wall of text", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: ".engineering/chain_reviews/w1.md",
      title: "Chain unchanged",
      content: JSON.stringify({
        status: "ready_for_approval",
        revised_chain_nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: null }],
        rationale: "Docs-only change, tail fits.",
      }),
      truncated: false,
    });
    const { container } = render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("Docs-only change, tail fits.")).toBeTruthy();
    expect(await screen.findByText("ready_for_approval")).toBeTruthy();
    expect(container.querySelector(".doc-modal-body pre code")?.textContent).toContain(
      "revised_chain_nodes",
    );
  });
});
