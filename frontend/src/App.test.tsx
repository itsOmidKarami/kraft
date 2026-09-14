import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { useStore } from "./store";
import { App } from "./App";

beforeEach(() => {
  useStore.setState({ workItems: {}, connection: "reconnecting" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({
    status: "degraded",
    invalid_templates: { broken: "broken.yaml: bad hook" },
    invalid_policy: [],
  });
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 0 });
});

describe("App", () => {
  it("shows the reconnecting badge and a degraded-health flag", async () => {
    render(<App />);
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(await screen.findByText(/broken/)).toBeInTheDocument();
  });

  it("opens the search overlay on Ctrl-K and closes it on Escape", async () => {
    render(<App />);
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole("dialog", { name: "Search" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
  });

  it("opens the search overlay from the header button", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /search/i }));
    expect(screen.getByRole("dialog", { name: "Search" })).toBeInTheDocument();
  });

  it("mounts straight into Login when boot's one probe came back 401", async () => {
    const list = vi.spyOn(api, "listWorkItems");
    render(<App initiallyLocked />);
    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new work item/i })).toBeNull();
    expect(list).not.toHaveBeenCalled();
  });

  it("opens intake from the header's primary action", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }, { id: "default", nodes: [], gates: 0 }]);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /new work item/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
