import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ESCALATION, NEVER, NO_ENTRY, TASKS, problem, renderPage, resolved, serve, view } from "./testkit";

afterEach(() => vi.unstubAllGlobals());

describe("problems", () => {
  it("a Never harness with a task marks the list row, the lane and the task's glyph, and names the task", async () => {
    serve(view(resolved({ access: { claude: "never" } }), { problems: [NEVER] }));
    const { tail } = renderPage();
    const nav = await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    expect(within(nav).getByRole("button", { name: "claude, Never, has a problem" })).toBeInTheDocument();
    expect(screen.getByLabelText("claude", { selector: "section" })).toHaveClass("is-red");
    expect(screen.getAllByRole("link", { name: /implement\.main\.implementer.*has a problem/ }).length).toBeGreaterThan(0);
    const pane = screen.getByRole("complementary", { name: "harnesses pane" });
    expect(pane).toHaveTextContent("implement.main.implementer: harness 'claude' is not in its allowed_harnesses");
    expect(tail).toHaveTextContent("1 PROBLEM");
  });

  it("escalation running on a harness marks that harness's row and lane, with no task problem at all", async () => {
    serve(view(resolved({ access: { codex: "never" }, escalation: { harness: "codex", grants: null }, escalation_effective: { harness: "codex", set: true } }), { problems: [problem({ ...ESCALATION, message: "escalation runs on 'codex', which is set to Never" })] }));
    renderPage();
    const nav = await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    expect(within(nav).getAllByRole("button", { name: /has a problem/ }).map((b) => b.getAttribute("aria-label"))).toEqual(["codex, Never, has a problem"]);
    expect(screen.getByLabelText("codex", { selector: "section" })).toHaveClass("is-red");
  });

  it("the harness's own pane shows the problem at the top", async () => {
    serve(view(resolved(), { problems: [NEVER] }));
    renderPage("?harness=claude");
    const pane = await screen.findByRole("complementary", { name: "claude pane" });
    expect(pane).toHaveTextContent("harness 'claude' is not in its allowed_harnesses");
  });

  it("a profile without an entry for a task's provider: red lane, red glyph, the profile's row", async () => {
    serve(view(resolved(), { problems: [NO_ENTRY] }));
    renderPage("?profile=fast");
    expect(await screen.findByLabelText("no codex entry")).toHaveClass("is-red");
    const nav = screen.getByRole("navigation", { name: "Harnesses and profiles" });
    expect(within(nav).getByRole("button", { name: "fast, 1 task, has a problem" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /finish\.main\.write_summary.*has a problem/ }).length).toBeGreaterThan(0);
  });

  it("escalation on a Never harness is in the Overview and on the runs-on control", async () => {
    serve(view(resolved({ access: { claude: "never" } }), { problems: [ESCALATION] }));
    renderPage();
    const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
    expect(within(pane).getByRole("button", { name: /escalation runs on 'claude'/ })).toBeInTheDocument();
    await userEvent.click(within(pane).getByRole("button", { name: /escalation runs on 'claude'/ }));
    await userEvent.click(within(pane).getByRole("tab", { name: "Config" }));
    expect(within(pane).getByText(/set to Never, so escalation cannot run there/)).toBeInTheDocument();
  });

  it("the Overview lists every problem, in the server's order, and one that cannot be placed stays listed", async () => {
    const odd = problem({ path: "profiles.x", message: "profiles.x: something nobody can place", file: "harnesses.yaml" });
    serve(view(resolved(), { problems: [odd, NEVER, NO_ENTRY] }));
    renderPage();
    const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
    const items = within(pane).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("something nobody can place");
    expect(items).toHaveLength(3);
    expect(pane).toHaveTextContent("Problems · 3");
  });

  it("clicking a problem selects what it is about", async () => {
    serve(view(resolved(), { problems: [NO_ENTRY] }));
    renderPage();
    const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
    await userEvent.click(within(pane).getByRole("button", { name: /has no codex entry/ }));
    expect(await screen.findByRole("complementary", { name: "fast pane" })).toBeInTheDocument();
  });

  it("header badge, Overview and list agree on how many", async () => {
    serve(view(resolved(), { problems: [NO_ENTRY, NEVER] }));
    const { tail } = renderPage();
    const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
    expect(tail).toHaveTextContent("2 PROBLEMS");
    expect(pane).toHaveTextContent("Problems · 2");
  });

  it("shows no problem and says every task can run, once cleared", async () => {
    serve(view(resolved(), { problems: [] }));
    const { tail } = renderPage();
    const pane = await screen.findByRole("complementary", { name: "harnesses pane" });
    expect(within(pane).getByText("Every task can run where it is set to.")).toBeInTheDocument();
    expect(tail).not.toHaveTextContent("PROBLEM");
    await waitFor(() => expect(tail).toHaveTextContent("published"));
    void TASKS;
  });
});
