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
});
