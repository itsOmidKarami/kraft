import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Bell } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useConfigDraft } from "../../templates/draft/useConfigDraft";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor } from "./kit";
import { mountAt, posts, problem, view, where } from "./testkit";

const CHANGES = [
  { path: "caps.tasks.time", kind: "change" as const, summary: "90 → 120 min" },
  { path: "loops.default", kind: "add" as const, summary: "attempts 3" },
  { path: "caps.nodes.usd", kind: "remove" as const, summary: "removed" },
];

function Demo({ edits = true }: { edits?: boolean }) {
  const draft = useConfigDraft("policy", "policy");
  const { edit, node } = useEditor();
  if (!draft.view) return null;
  return (
    <AreaScreen title="Policy" sub="Limits." draft={draft}>
      <Group
        title="Instance"
        rows={[
          { label: "read only", value: "42" },
          { label: "link", to: "/settings/policy/loops", sub: "a sub line" },
          { label: "budget per item", value: "$20", changed: true, onEdit: () => edit({ kind: "text", title: "Budget per item", value: "20", set: async (v) => { const a = await draft.ops([{ op: "set_value", scope: "budget", key: "item", value: Number(v) }], { quiet: true }); return a.status === 200 ? null : String((a.body as { detail?: string }).detail); } }) },
          { label: "stuck detection", sw: true, onSwitch: () => {} },
          { label: "mode", value: "hybrid", onEdit: () => edit({ kind: "choice", title: "Mode", value: "hybrid", options: [{ value: "hybrid", label: "Hybrid" }, { value: "fts", label: "Keywords" }], set: async () => null }) },
          { label: "password", value: "••••", onEdit: () => edit({ kind: "text", title: "New password", value: "", secret: true, set: async () => "too short" }) },
          { label: "channel", icon: Bell, chips: [{ label: "on", tone: "ok" }] },
        ]}
      />
      {edits && node}
    </AreaScreen>
  );
}
const open = (answers: Record<string, [number, unknown]> = {}, path = "/settings/policy") => mountAt(<Demo />, path, "/settings/policy", answers);
afterEach(() => vi.unstubAllGlobals());

describe("the area scaffold (K.2)", () => {
  it("draws each row kind: static, link, edit with the changed word, switch, chips", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy")] });
    expect(await screen.findByRole("heading", { level: 1, name: "Policy" })).toBeInTheDocument();
    expect(screen.getByText("read only")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /link/ })).toHaveAttribute("href", "/settings/policy/loops");
    expect(screen.getByRole("button", { name: /budget per item/ })).toHaveTextContent("$20 (changed)");
    expect(screen.getByRole("switch", { name: /stuck detection/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("on")).toBeInTheDocument();
  });

  it("says published, a draft's change count, or its problems, and shows the bar only with changes", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy", {})] });
    expect(await screen.findByText("published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Review/ })).toBeNull();
  });

  it("with a draft: the chip counts changes, and Discard and Review & publish appear", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy", { draft: true }, { changes: CHANGES })] });
    expect(await screen.findByText("draft · 3 changes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review & publish/ })).toBeInTheDocument();
  });

  it("a problem with no change is shown as a count", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy", { draft: true }, { problems: [problem("caps", "bad")] })] });
    expect(await screen.findByText("1 problem")).toBeInTheDocument();
  });

  it("the YAML toggle shows the draft's file and puts ?yaml=1 in the URL, and back", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy", { files: { "policy.yaml": "budget:\n  item: 20\n", "harnesses.yaml": null as never } })] });
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    expect(where()).toBe("/settings/policy?yaml=1");
    expect(screen.getByText("policy.yaml")).toBeInTheDocument();
    expect(screen.getByText(/item: 20/)).toBeInTheDocument();
    expect(screen.getByText("(deleted by this draft)")).toBeInTheDocument();
    expect(screen.queryByText("read only")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(where()).toBe("/settings/policy");
    expect(screen.getByText("read only")).toBeInTheDocument();
  });

  it("shows a YAML syntax error with its line", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy", {}, { yaml_error: { file: "policy.yaml", line: 3, col: 1, message: "mapping values are not allowed" } })] }, "/settings/policy?yaml=1");
    expect(await screen.findByRole("alert")).toHaveTextContent("policy.yaml, line 3: mapping values are not allowed");
  });
});

describe("the edit sheet (K.3)", () => {
  it("a refusal stays inside the sheet and the sheet stays open; a taken value closes it", async () => {
    const { calls } = open({
      "GET /drafts/policy/policy": [200, view("policy", "policy")],
      "POST /drafts/policy/policy/ops": [422, { detail: "budget must be positive", op: 0 }],
    });
    await userEvent.click(await screen.findByRole("button", { name: /budget per item/ }));
    const sheet = screen.getByRole("dialog", { name: "Budget per item" });
    await userEvent.clear(within(sheet).getByLabelText("Budget per item", { selector: "input" }));
    await userEvent.type(within(sheet).getByLabelText("Budget per item", { selector: "input" }), "-1{Enter}");
    expect(await within(sheet).findByRole("alert")).toHaveTextContent("budget must be positive");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(posts(calls).map((c) => c.body)).toEqual([{ ops: [{ op: "set_value", scope: "budget", key: "item", value: -1 }] }]);
  });

  it("a choice sheet marks the current value and closes on a pick", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy")] });
    await userEvent.click(await screen.findByRole("button", { name: /mode/ }));
    expect(screen.getByRole("radio", { name: "Hybrid", checked: true })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Keywords" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("masks a secret and shows its refusal", async () => {
    open({ "GET /drafts/policy/policy": [200, view("policy", "policy")] });
    await userEvent.click(await screen.findByRole("button", { name: /password/ }));
    const input = screen.getByLabelText("New password", { selector: "input" });
    expect(input).toHaveAttribute("type", "password");
    await userEvent.type(input, "x{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("too short");
  });
});

describe("Review & publish (K.4)", () => {
  const drafted = (r = {}, over = {}) => ({ "GET /drafts/policy/policy": [200, view("policy", "policy", { draft: true, ...over }, { changes: CHANGES, ...r })] as [number, unknown] });

  it("lists the server's changes with their signs, and publishes", async () => {
    const { calls } = open({ ...drafted(), "POST /drafts/policy/policy/publish": [200, { published: ["policy.yaml"] }] });
    await userEvent.click(await screen.findByRole("button", { name: /Review & publish/ }));
    const sheet = screen.getByRole("dialog", { name: "Review & publish" });
    const lines = within(sheet).getByLabelText("Changes");
    expect(within(lines).getByLabelText("change")).toHaveTextContent("~");
    expect(within(lines).getByLabelText("add")).toHaveTextContent("+");
    expect(within(lines).getByLabelText("remove")).toHaveTextContent("−");
    expect(within(lines).getByText("90 → 120 min", { exact: false })).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toContain("/drafts/policy/policy/publish"));
    expect(await screen.findByText("Published. Applies without a restart.")).toBeInTheDocument();
  });

  it("a problem blocks Publish and is listed (mutate the block to see this fail)", async () => {
    const { calls } = open(drafted({ problems: [problem("caps.tasks.time", "must be at least 1")] }));
    await userEvent.click(await screen.findByRole("button", { name: /Review & publish/ }));
    const sheet = screen.getByRole("dialog");
    expect(within(sheet).getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(within(sheet).getByLabelText("Problems")).toHaveTextContent("caps.tasks.time");
    expect(within(sheet).getByLabelText("Problems")).toHaveTextContent("must be at least 1");
    expect(posts(calls)).toEqual([]);
  });

  it("a 409 shows what changed and offers Keep my version and publish, which re-bases first", async () => {
    const stale = { detail: "stale", files: { "policy.yaml": { published: "a", draft: "b", diff: "@@\n-a\n+b" } } };
    const { calls } = open({
      ...drafted(),
      "POST /drafts/policy/policy/publish": [409, stale],
      "POST /drafts/policy/policy/rebase": [200, view("policy", "policy", { draft: true }, { changes: CHANGES })],
    });
    await userEvent.click(await screen.findByRole("button", { name: /Review & publish/ }));
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByText(/policy\.yaml changed on disk after this draft began/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep my version and publish" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/drafts/policy/policy/publish", "/drafts/policy/policy/rebase", "/drafts/policy/policy/publish"]));
  });

  it("Discard asks first, then discards", async () => {
    const { calls } = open({ ...drafted(), "DELETE /drafts/policy/policy": [204, null] });
    await userEvent.click(await screen.findByRole("button", { name: "Discard" }));
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Discard this draft?" })).getByRole("button", { name: "Discard draft" }));
    await waitFor(() => expect(posts(calls).map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /drafts/policy/policy"]));
  });
});
