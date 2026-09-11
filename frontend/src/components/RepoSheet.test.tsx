import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RepoSheet } from "./RepoSheet";

describe("RepoSheet", () => {
  it("shows a count and needs-you count per repo, with a checkmark on the current one", () => {
    render(
      <RepoSheet
        repos={[
          ["/repo-a", 18],
          ["/repo-b", 4],
        ]}
        needsYou={{ "/repo-a": 3 }}
        value={new Set(["/repo-a"])}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/18/)).toBeInTheDocument();
    expect(screen.getByText(/3 need you/i)).toBeInTheDocument();
  });
});
