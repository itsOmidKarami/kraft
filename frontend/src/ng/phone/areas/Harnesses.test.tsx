import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ESCALATION, NEVER, NO_ENTRY, resolved, STATUS, view as hview } from "../../harnesses/testkit";
import { resetProviders } from "../../harnesses/useProviders";
import { HarnessesList, HarnessView, ProfileView } from "./Harnesses";
import { mountAt, posts } from "./testkit";

const OPS: [number, unknown] = [200, { ...hview(resolved(), { draft: true }), ops: [] }];
const ans = (v = hview(resolved()), more: Record<string, [number, unknown]> = {}) => ({ "GET /drafts/harnesses/harnesses": [200, v] as [number, unknown], "GET /harnesses": [200, STATUS] as [number, unknown], "POST /drafts/harnesses/harnesses/ops": OPS, ...more });
beforeEach(() => resetProviders());
afterEach(() => vi.unstubAllGlobals());

describe("Harnesses list (M.1)", () => {
  const list = (a = ans()) => mountAt(<HarnessesList />, "/templates/harnesses", "/templates/harnesses", a);

  it("lists harnesses with their Access chip, profiles with their models, tasks with their harness, and the defaults", async () => {
    list();
    const hs = await screen.findByRole("region", { name: "Harnesses" });
    expect(within(hs).getByRole("link", { name: /^claude-sandbox/ })).toHaveTextContent("Override");
    expect(within(hs).getByRole("link", { name: /^cursor/ })).toHaveTextContent("Never");
    expect(within(hs).getByRole("link", { name: /^gemini/ })).toHaveTextContent("not found on this machine");
    const ps = screen.getByRole("region", { name: "Profiles" });
    expect(within(ps).getByRole("link", { name: /^strong/ })).toHaveTextContent("claude sonnet · codex gpt-5.6-terra");
    const ts = screen.getByRole("region", { name: "Tasks" });
    expect(within(ts).getByText("implement.main.implementer")).toBeInTheDocument();
    expect(within(ts).getAllByText("claude · strong").length).toBeGreaterThan(0);
    const ds = screen.getByRole("region", { name: "Defaults" });
    expect(within(ds).getByRole("button", { name: /escalation runs on/ })).toHaveTextContent("claude");
    expect(within(ds).getByRole("button", { name: /allowed tools/ })).toHaveTextContent("git, shell, editor");
  });

  it("shows the server's problems on top, and on the harness row they are about", async () => {
    list(ans(hview(resolved(), { problems: [NEVER, NO_ENTRY, ESCALATION] })));
    const pr = await screen.findByRole("region", { name: "Problems" });
    expect(within(pr).getByText(/is not in its allowed_harnesses/)).toBeInTheDocument();
    expect(within(pr).getByText(/has no codex entry/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^claude(?!-)/ })).toHaveTextContent("problem");
  });

  it("changes escalation, tools and grants through the desktop's ops", async () => {
    const { calls } = list();
    await userEvent.click(await screen.findByRole("button", { name: /escalation runs on/ }));
    const sheet = screen.getByRole("dialog", { name: "Escalation runs on" });
    expect(within(sheet).queryByRole("radio", { name: /cursor/ })).toBeNull();
    await userEvent.click(within(sheet).getByRole("radio", { name: "codex" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_escalation", harness: "codex", grants: ["git-commit", "git-rebase"] }] });
    await userEvent.click(screen.getByRole("button", { name: /allowed tools/ }));
    const box = screen.getByLabelText("Allowed tools", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "git, shell{Enter}");
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1].body).toEqual({ ops: [{ op: "set_allowed_tools", tools: ["git", "shell"] }] });
    await userEvent.click(screen.getByRole("button", { name: /escalation grants/ }));
    const g = screen.getByLabelText("Escalation grants", { selector: "input" });
    await userEvent.clear(g);
    await userEvent.type(g, "git-commit{Enter}");
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2].body).toEqual({ ops: [{ op: "set_escalation", harness: "claude", grants: ["git-commit"] }] });
  });
});

describe("a harness (M.1, R65)", () => {
  const open = (id: string, a = ans()) => mountAt(<HarnessView />, `/templates/harnesses/${id}`, "/templates/harnesses/:id", a);

  it("edits Access only, with set_access, and shows its own fields read-only", async () => {
    const { calls } = open("claude");
    await userEvent.click(await screen.findByRole("button", { name: /^access/ }));
    const sheet = screen.getByRole("dialog", { name: "Access" });
    expect(within(sheet).getAllByRole("radio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["Available", "Override", "Never"]);
    await userEvent.click(within(sheet).getByRole("radio", { name: "Never" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_access", harness: "claude", state: "never" }] });
    expect(screen.getByText("permission_mode")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^permission_mode|^provider|^executable|^enabled/ })).toBeNull();
    expect(screen.getByText(/edited in YAML on a computer/)).toBeInTheDocument();
  });

  it("names the problems about it and its tasks", async () => {
    open("claude", ans(hview(resolved(), { problems: [NEVER] })));
    const g = await screen.findByRole("region", { name: "Problems" });
    expect(within(g).getByText(/is not in its allowed_harnesses/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /Tasks · 4/ })).toBeInTheDocument();
  });

  it("says there is no such harness", async () => {
    open("nope");
    expect(await screen.findByText("There is no harness nope.")).toBeInTheDocument();
  });
});

describe("a profile (M.1)", () => {
  const open = (name: string, a = ans()) => mountAt(<ProfileView />, `/templates/harnesses/profiles/${name}`, "/templates/harnesses/profiles/:name", a);

  it("edits a provider entry's model and effort with the one set_profile patch", async () => {
    const { calls } = open("strong");
    const claude = await screen.findByRole("region", { name: "claude" });
    await userEvent.click(within(claude).getByRole("button", { name: /^model/ }));
    const box = screen.getByLabelText("claude model", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "opus{Enter}");
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_profile", name: "strong", patch: { providers: { claude: { model: "opus", effort: "high" } } } }] });
    await userEvent.click(within(claude).getByRole("button", { name: /^effort/ }));
    const sheet = screen.getByRole("dialog", { name: "claude effort" });
    expect(within(sheet).getAllByRole("radio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["default", "low", "medium", "high", "xhigh", "max"]);
    await userEvent.click(within(sheet).getByRole("radio", { name: "low" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1].body).toEqual({ ops: [{ op: "set_profile", name: "strong", patch: { providers: { claude: { model: "sonnet", effort: "low" } } } }] });
  });

  it("draws no effort row for a provider that has none, and shows a profile's problem", async () => {
    open("fast", ans(hview(resolved(), { problems: [NO_ENTRY] })));
    expect(await screen.findByRole("region", { name: "Problems" })).toHaveTextContent("has no codex entry");
    open("strong", ans(hview(resolved({ profiles: { strong: { providers: { cursor: { model: "x" } }, effort: null, tasks: [], used_by_fallback: [] } } }))));
    const cursor = await screen.findByRole("region", { name: "cursor" });
    expect(within(cursor).queryByRole("button", { name: /^effort/ })).toBeNull();
  });
});
