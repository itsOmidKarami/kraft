import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderPage, resolved, serve, view } from "./testkit";

afterEach(() => vi.unstubAllGlobals());

async function openConfig() {
  const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
  await userEvent.click(within(pane).getByRole("tab", { name: "Config" }));
  return pane;
}

describe("area pane Config", () => {
  it("adds and removes allowed tools with one set_allowed_tools each", async () => {
    const server = serve(view(resolved()));
    renderPage();
    const pane = await openConfig();
    await userEvent.type(within(pane).getByRole("textbox", { name: "Add to allowed tools" }), "web{Enter}");
    await userEvent.click(within(pane).getByRole("button", { name: "Remove shell" }));
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops).toEqual([[{ op: "set_allowed_tools", tools: ["git", "shell", "editor", "web"] }], [{ op: "set_allowed_tools", tools: ["git", "editor"] }]]);
  });

  it("draws a tool new in the draft green, against the published policy", async () => {
    serve(view(resolved()));
    renderPage();
    const pane = await openConfig();
    await waitFor(() => expect(within(pane).getByText("editor").closest("li")).toHaveClass("is-added"));
    expect(within(pane).getByText("git").closest("li")).not.toHaveClass("is-added");
  });

  it("says an empty list allows no tools", async () => {
    serve(view(resolved({ allowed_tools: [] })));
    renderPage();
    const pane = await openConfig();
    expect(within(pane).getByText(/Empty allows no tools/)).toBeInTheDocument();
  });

  it("escalation's runs-on lists the item's harness, then every harness not set to Never", async () => {
    serve(view(resolved()));
    renderPage();
    const pane = await openConfig();
    const select = within(pane).getByRole("combobox", { name: "runs on" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["item's harness", "claude", "claude-sandbox", "codex"]);
    expect(select).toHaveValue("claude");
  });

  it("keeps the current harness in the list when it is Never, so the problem can be seen", async () => {
    serve(view(resolved({ access: { claude: "never" } })));
    renderPage();
    const pane = await openConfig();
    const select = within(pane).getByRole("combobox", { name: "runs on" });
    expect(within(select).getByRole("option", { name: "claude (Never)" })).toBeInTheDocument();
  });

  it("shows claude when escalation_harness is unset, and a pick writes the other value unchanged", async () => {
    const server = serve(view(resolved({ escalation: { harness: null, grants: ["git-commit"] }, escalation_effective: { harness: "claude", set: false } })));
    renderPage();
    const pane = await openConfig();
    const select = within(pane).getByRole("combobox", { name: "runs on" });
    expect(select).toHaveValue("claude");
    await userEvent.selectOptions(select, "item");
    await waitFor(() => expect(server.ops).toHaveLength(1));
    expect(server.ops[0]).toEqual([{ op: "set_escalation", harness: "item", grants: ["git-commit"] }]);
  });

  it("grants are pills too, and adding one keeps the harness", async () => {
    const server = serve(view(resolved()));
    renderPage();
    const pane = await openConfig();
    await userEvent.type(within(pane).getByRole("textbox", { name: "Add to escalation grants" }), "git-push{Enter}");
    await waitFor(() => expect(server.ops).toHaveLength(1));
    expect(server.ops[0]).toEqual([{ op: "set_escalation", harness: "claude", grants: ["git-commit", "git-rebase", "git-push"] }]);
  });
});
