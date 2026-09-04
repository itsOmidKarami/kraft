import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { DocumentModal } from "./DocumentModal";

const doc = {
  id: "d1",
  repo: "/r",
  source_kind: "artifact" as const,
  kind: "specs",
  title: "WS transport design",
  path: ".engineering/specs/ws.md",
  content: "# WS transport\n\nthe reconnect backoff schedule\n",
  metadata: { owner: "omid" },
  source_created_at: null,
  source_updated_at: "2026-09-03T00:00:00Z",
  indexed_at: "2026-09-04T00:00:00Z",
  links: [],
};

afterEach(() => vi.restoreAllMocks());

describe("DocumentModal", () => {
  it("renders the body and metadata", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue(doc);
    render(<DocumentModal id="d1" onClose={() => {}} />);
    expect(await screen.findByText(/the reconnect backoff schedule/)).toBeInTheDocument();
    expect(screen.getByText(".engineering/specs/ws.md")).toBeInTheDocument();
    expect(screen.getByText("specs")).toBeInTheDocument();
  });

  it("shows the error message on failure", async () => {
    vi.spyOn(api, "getDocument").mockRejectedValue(new Error("unknown document"));
    render(<DocumentModal id="nope" onClose={() => {}} />);
    expect(await screen.findByText(/unknown document/)).toBeInTheDocument();
  });

  it("calls onClose from the close button", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue(doc);
    const onClose = vi.fn();
    render(<DocumentModal id="d1" onClose={onClose} />);
    await screen.findByText(/reconnect backoff/);
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
