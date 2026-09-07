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
});
