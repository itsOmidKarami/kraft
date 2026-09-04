import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { DocumentModal } from "./DocumentModal";

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

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
    wrap(<DocumentModal id="d1" onClose={() => {}} />);
    expect(await screen.findByText(/the reconnect backoff schedule/)).toBeInTheDocument();
    expect(screen.getByText(".engineering/specs/ws.md")).toBeInTheDocument();
    expect(screen.getByText("specs")).toBeInTheDocument();
  });

  it("shows the error message on failure", async () => {
    vi.spyOn(api, "getDocument").mockRejectedValue(new Error("unknown document"));
    wrap(<DocumentModal id="nope" onClose={() => {}} />);
    expect(await screen.findByText(/unknown document/)).toBeInTheDocument();
  });

  it("calls onClose from the close button", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue(doc);
    const onClose = vi.fn();
    wrap(<DocumentModal id="d1" onClose={onClose} />);
    await screen.findByText(/reconnect backoff/);
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders breadcrumbs back to the linked work items", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue({
      ...doc,
      links: [
        { work_item_id: "w1", node_id: null, hook_point: null, worker_session_id: null },
        { work_item_id: "w2", node_id: null, hook_point: null, worker_session_id: null },
        {
          work_item_id: null,
          node_id: "implementation",
          hook_point: "on.implementation.start",
          worker_session_id: "s1",
        },
      ],
    });
    const onNavigate = vi.fn();
    wrap(<DocumentModal id="d1" onClose={() => {}} onNavigate={onNavigate} />);
    const w1 = await screen.findByRole("link", { name: "w1" });
    expect(screen.getByRole("link", { name: "w2" })).toBeInTheDocument();
    expect(screen.getByText("implementation")).toBeInTheDocument();
    await userEvent.click(w1);
    expect(onNavigate).toHaveBeenCalled();
  });

  it("renders session metadata and no link when a link has no work item", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue({
      ...doc,
      links: [
        {
          work_item_id: null,
          node_id: "verify",
          hook_point: "on.test.run",
          worker_session_id: "s9",
        },
      ],
    });
    wrap(<DocumentModal id="d1" onClose={() => {}} />);
    expect(await screen.findByText("verify")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
