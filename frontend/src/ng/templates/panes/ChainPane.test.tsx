import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { ConfigDraft } from "../draft/useConfigDraft";
import { DEFAULT_VIEW } from "../draft/fixture.default";
import type { DraftView, Result } from "../draft/types";
import { resolvedNode } from "../draft/view";
import { ChainPane } from "./ChainPane";
import { resetHarnessOptions } from "./useHarnessOptions";

const SIZE = { width: 380, overlay: false, handle: undefined };

function mount(path: string, result: Partial<Result> = {}) {
  const view: DraftView = { ...DEFAULT_VIEW, result: { ...DEFAULT_VIEW.result, ...result } };
  const draft = {
    view,
    field: vi.fn(),
    ops: vi.fn(() => Promise.resolve({ status: 200, body: {} })),
    flush: vi.fn(),
    resolvedNode: (id: string) => resolvedNode(view.result, id),
  } as unknown as ConfigDraft;
  const goTo = vi.fn();
  render(<ChainPane draft={draft} scope={{ area: "chains", key: "default" }} path={path} open size={SIZE} onCollapse={() => {}} onExpand={() => {}} goTo={goTo} />);
  return { draft, goTo };
}

const configTab = () => userEvent.click(screen.getByRole("tab", { name: "Config" }));
const rowOf = (label: string) => screen.getByText(label, { selector: ".cfg-k" }).closest(".cfg-row") as HTMLElement;
const rowLabels = () => [...document.querySelectorAll(".cfg-k")].map((e) => e.textContent);

beforeEach(() => {
  resetHarnessOptions();
  vi.restoreAllMocks();
  vi.spyOn(api, "getHarnesses").mockResolvedValue({
    file: "", error: null,
    profiles: [{ id: "claude", provider: "claude", enabled: true, executable: null, defaults: {}, used_by: [], chains: [] }, { id: "codex", provider: "codex", enabled: true, executable: null, defaults: {}, used_by: [], chains: [] }],
    agent_profiles: [{ id: "strong", effort: "high", model: {}, used_by: [], chains: [], problems: [] }, { id: "fast", effort: "low", model: {}, used_by: [], chains: [], problems: [] }],
  });
  vi.spyOn(api, "getHarnessProviders").mockResolvedValue({ valid: { claude: { id: "claude", kind: "cli", command: [], path: "", override: false, capabilities: { effort: { cli: [], values: ["low", "medium", "high"] } as never } } }, invalid: {} });
});

describe("Config tab", () => {
  it("lists the server's rows in its order, each with its source chip, and keeps the order when one becomes an override", async () => {
    mount("implementation.main.implement");
    await configTab();
    const before = rowLabels();
    expect(before.slice(0, 5)).toEqual(["runs", "steering", "skippable", "harness", "prompt"]);
    expect(within(rowOf("harness")).getByText("library")).toBeInTheDocument();
    expect(within(rowOf("model")).getByText("default")).toBeInTheDocument();
    expect(rowOf("harness").querySelector(".cfg-dot")).toBeNull();

    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources["implementation.main.implement"].model = { value: "opus", source: "chain" };
    document.body.innerHTML = "";
    mount("implementation.main.implement", { sources });
    await configTab();
    expect(rowLabels()).toEqual(before);
    expect(within(rowOf("model")).getByText("this chain")).toHaveClass("is-own");
    expect(rowOf("model").querySelector(".cfg-dot")).not.toBeNull();
  });

  it("✎ then Enter sets the dotted field; ↺ resets it", async () => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources["implementation.main.implement"]["policy.time_cap_minutes"] = { value: 120, source: "chain" };
    const { draft } = mount("implementation.main.implement", { sources });
    await configTab();
    await userEvent.click(within(rowOf("running cap")).getByRole("button", { name: "Edit running cap" }));
    const input = within(rowOf("running cap")).getByRole("textbox", { name: "running cap" });
    await userEvent.clear(input);
    await userEvent.type(input, "90{Enter}");
    expect(draft.field).toHaveBeenCalledWith("implementation.main.implement", "policy.time_cap_minutes", 90);
    await userEvent.click(within(rowOf("running cap")).getByRole("button", { name: "Reset running cap" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "reset_field", path: "implementation.main.implement", field: "policy.time_cap_minutes" }]);
  });

  it("refuses a malformed value before sending, and sends nothing for an untouched one", async () => {
    const { draft } = mount("spec_approval");
    await configTab();
    await userEvent.click(within(rowOf("timeout")).getByRole("button", { name: "Edit timeout" }));
    await userEvent.type(within(rowOf("timeout")).getByRole("textbox"), "two days{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("timeout: Like 30s, 5m, 2h or 1d.");
    expect(draft.field).not.toHaveBeenCalled();
    await userEvent.clear(within(rowOf("timeout")).getByRole("textbox"));
    await userEvent.type(within(rowOf("timeout")).getByRole("textbox"), "{Enter}");
    expect(draft.field).not.toHaveBeenCalled();
  });

  it("edits on base change as restart_from, among this and the earlier exec nodes", async () => {
    const { draft } = mount("verification");
    await configTab();
    await userEvent.click(within(rowOf("on base change")).getByRole("button", { name: "Edit on base change" }));
    const select = within(rowOf("on base change")).getByRole("combobox");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["not set", "spec", "implementation", "verification"]);
    await userEvent.selectOptions(select, "implementation");
    expect(draft.field).toHaveBeenCalledWith("verification", "on_base_changed.restart_from", "implementation");
  });

  it("shows a yes/no field as a switch that sets it with one click", async () => {
    const { draft } = mount("verification");
    await configTab();
    const sw = within(rowOf("skippable")).getByRole("switch", { name: "skippable" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(within(rowOf("skippable")).queryByRole("button", { name: /Edit skippable/ })).toBeNull();
    await userEvent.click(sw);
    expect(draft.field).toHaveBeenCalledWith("verification", "skippable", false);
  });

  it("says which caps apply only to a task's on-failure recovery", async () => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources["implementation.main.implement"]["policy.budget_usd"] = { value: 2, source: "chain", recovery: true };
    mount("implementation.main.implement", { sources });
    await configTab();
    expect(rowOf("budget ($)")).toHaveTextContent("applies to its on-failure recovery");
    expect(rowOf("running cap")).not.toHaveTextContent("applies to its on-failure recovery");
  });

  it("disables a locked yes/no field's switch", async () => {
    const { draft } = mount("verification");
    await configTab();
    const sw = within(rowOf("read only")).getByRole("switch", { name: "read only" });
    expect(sw).toBeDisabled();
    await userEvent.click(sw);
    expect(draft.field).not.toHaveBeenCalled();
  });
});

describe("Overview tab", () => {
  it("picks a gate's document from what earlier nodes produce; required shows only with a document", async () => {
    const { draft } = mount("local_review");
    const doc = screen.getByRole("combobox", { name: "document" });
    expect([...doc.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["none", ...DEFAULT_VIEW.result.resolved!.documents.local_review]);
    await userEvent.click(screen.getByRole("checkbox", { name: "required" }));
    expect(draft.field).toHaveBeenCalledWith("local_review", "artifact_required", true);
    await userEvent.selectOptions(doc, "none");
    expect(draft.field).toHaveBeenCalledWith("local_review", "artifact", null);
  });

  it("has no required box on a gate that needs no document", () => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources.local_review.artifact = { value: null, source: "default" };
    mount("local_review", { sources });
    expect(screen.queryByRole("checkbox", { name: "required" })).toBeNull();
    expect(screen.getByText("Nothing is needed to approve.")).toBeInTheDocument();
  });

  it("offers only earlier exec nodes to reject to; gates are disabled", () => {
    mount("local_review");
    const opts = [...screen.getByRole("combobox", { name: "reject to" }).querySelectorAll("option")];
    expect(opts.map((o) => o.textContent)).toEqual(["not set", "spec", "spec_approval (gate)", "implementation", "verification"]);
    expect(opts.find((o) => o.value === "spec_approval")).toBeDisabled();
  });

  it("focuses an empty prompt and says it is required", () => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources["implementation.main.implement"].prompt = { value: null, source: "default" };
    mount("implementation.main.implement", { sources });
    const prompt = screen.getByRole("textbox", { name: "prompt" });
    expect(prompt).toHaveFocus();
    expect(screen.getByText("Required.")).toBeInTheDocument();
  });

  it("routes an agent task by profile or by model + effort, never both", async () => {
    const { draft } = mount("implementation.main.implement");
    expect(await screen.findByRole("combobox", { name: "profile" })).toHaveValue("strong");
    expect(screen.queryByRole("textbox", { name: "model" })).toBeNull();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "route" }), "model");
    expect(draft.field).toHaveBeenCalledWith("implementation.main.implement", "profile", null);
    expect(screen.getByRole("textbox", { name: "model" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "profile" })).toBeNull();
    expect([...screen.getByRole("combobox", { name: "effort" }).querySelectorAll("option")].map((o) => o.textContent)).toEqual(["default", "low", "medium", "high"]);
  });

  it("sends a typed field on a pause, and flushes on blur", async () => {
    const { draft } = mount("spec_approval");
    const msg = screen.getByRole("textbox", { name: "message" });
    await userEvent.type(msg, "!");
    expect(draft.field).toHaveBeenLastCalledWith("spec_approval", "message", "Review and approve the specification.!", true);
    await userEvent.tab();
    expect(draft.flush).toHaveBeenCalled();
  });
});

describe("pane head", () => {
  it("crumbs a task in a step's on_failure handler back to each level", async () => {
    const { goTo } = mount("merge_request_feedback.on_failure.repair.repair_feedback");
    const crumbs = within(screen.getByRole("navigation", { name: "Pane path" }));
    expect(crumbs.getAllByRole("button").map((b) => b.textContent)).toEqual(["default", "merge_request_feedback", "on failure", "repair"]);
    await userEvent.click(crumbs.getByRole("button", { name: "on failure" }));
    expect(goTo).toHaveBeenLastCalledWith("merge_request_feedback");
    await userEvent.click(crumbs.getByRole("button", { name: "repair" }));
    expect(goTo).toHaveBeenLastCalledWith("merge_request_feedback.on_failure.repair");
  });

  it("hides a lone main step in the crumb and leads with the problem row", () => {
    mount("implementation.main.implement", { problems: [{ path: "implementation.main.implement", field: "model", message: "Value error, unknown model", file: "f", line: 12, col: 3 }] });
    const crumbs = within(screen.getByRole("navigation", { name: "Pane path" }));
    expect(crumbs.getAllByRole("button").map((b) => b.textContent)).toEqual(["default", "implementation"]);
    expect(screen.getByRole("alert")).toHaveTextContent("unknown model");
    expect(screen.getByRole("alert")).toHaveTextContent("at model, line 12");
  });
});

describe("closed-set fields", () => {
  const TASK = "verification.tests.test_changed_scopes";
  /** The fixture's subprocess task, read as a builtin one: `sources` is what the pane reads first. */
  const builtin = (result: Partial<Result> = {}) => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources[TASK] = { ...sources[TASK], kind: { value: "builtin", source: "chain" }, ref: { value: "kraft.mr_rebase", source: "chain" } };
    return mount(TASK, { sources, ...result });
  };
  const action = () => screen.getByRole("combobox", { name: "action" });
  const sentRefs = (draft: ConfigDraft) => vi.mocked(draft.field).mock.calls.filter(([, f]) => f === "ref").map(([, , v]) => v);

  it("lists a builtin task's actions as the draft's choices give them, summaries included", async () => {
    const choices = structuredClone(DEFAULT_VIEW.result.choices!);
    choices.ref.push({ value: "kraft.from_the_server", summary: "Only the server knows this one" });
    builtin({ choices });
    await userEvent.click(action());
    const list = screen.getByRole("listbox", { name: "Actions" });
    expect(within(list).getAllByRole("option").map((o) => o.querySelector(".cbx-value")!.textContent)).toEqual(["kraft.verify_changed_test_scopes", "kraft.mr_rebase", "kraft.from_the_server"]);
    expect(within(list).getByText("Only the server knows this one")).toBeInTheDocument();
  });

  it("never sends an action that is not listed: it is flagged in place, and a pick from the list goes at once", async () => {
    const { draft } = builtin();
    await userEvent.clear(action());
    await userEvent.type(action(), "sds");
    expect(action()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("status")).toHaveTextContent("No action matches “sds”.");
    await userEvent.tab();
    expect(screen.getByText("“sds” is not an action. Pick one from the list.")).toHaveClass("is-bad");
    expect(sentRefs(draft)).toEqual([]);
    vi.mocked(draft.flush).mockClear();
    await userEvent.clear(action());
    await userEvent.type(action(), "verify");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(sentRefs(draft)).toEqual(["kraft.verify_changed_test_scopes"]);
    expect(draft.flush).toHaveBeenCalled();
    expect(screen.queryByText(/is not an action/)).toBeNull();
  });

  it("never sends a required action empty: clearing it on the way to another value saves nothing", async () => {
    const { draft } = builtin();
    await userEvent.clear(action());
    await userEvent.tab();
    expect(screen.getByText("Pick an action from the list.")).toHaveClass("is-bad");
    expect(sentRefs(draft)).toEqual([]);
  });

  it("asks a forge wait for its polling when the draft's choices say its target waits", () => {
    mount("merge_request_feedback.ci.await_ci");
    expect(screen.getByRole("combobox", { name: "target" })).toHaveValue("mr.ci");
    expect(screen.getByRole("textbox", { name: "check every" })).toBeInTheDocument();
  });

  it("asks a forge task for no polling when its target does not wait", () => {
    const choices = structuredClone(DEFAULT_VIEW.result.choices!);
    choices.target.find((t) => t.value === "mr.ci")!.waits = false;
    mount("merge_request_feedback.ci.await_ci", { choices });
    expect(screen.queryByRole("textbox", { name: "check every" })).toBeNull();
  });

  it("refuses an unlisted action on the Config tab, and saves a listed one", async () => {
    const { draft } = builtin();
    await configTab();
    await userEvent.click(within(rowOf("action")).getByRole("button", { name: "Edit action" }));
    const input = within(rowOf("action")).getByRole("combobox", { name: "action" });
    await userEvent.clear(input);
    await userEvent.type(input, "sds{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("action: “sds” is not an action. Pick one from the list.");
    expect(draft.field).not.toHaveBeenCalled();
    await userEvent.clear(input);
    await userEvent.type(input, "verify");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(draft.field).toHaveBeenCalledWith(TASK, "ref", "kraft.verify_changed_test_scopes");
  });

  it("completes an agent task's inputs on the Config tab, and refuses one Kraft does not deliver", async () => {
    const { draft } = mount("verification.review.code_review");
    await configTab();
    await userEvent.click(within(rowOf("inputs")).getByRole("button", { name: "Edit inputs" }));
    const input = within(rowOf("inputs")).getByRole("combobox", { name: "inputs" });
    await userEvent.clear(input);
    await userEvent.type(input, "review_package, ");
    expect(within(screen.getByRole("listbox", { name: "inputs" })).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "carried_findingsThe findings the node's last measurement reported",
      "previous_reviewThis task's previous result and summary",
    ]);
    await userEvent.type(input, "sds{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("inputs: “sds” is not an input.");
    expect(draft.field).not.toHaveBeenCalled();
    await userEvent.clear(input);
    await userEvent.type(input, "review_package, prev");
    await userEvent.keyboard("{ArrowDown}{Enter}{Enter}");
    expect(draft.field).toHaveBeenCalledWith("verification.review.code_review", "inputs", ["review_package", "previous_review"]);
  });
});

describe("an agent task's fallback list", () => {
  const TASK = "implementation.main.implement";
  const withFallback = (value: unknown) => {
    const sources = structuredClone(DEFAULT_VIEW.result.sources);
    sources[TASK] = { ...sources[TASK], fallback: { value, source: "chain" } };
    return mount(TASK, { sources });
  };
  const ENTRIES = [{ harness: "codex", profile: "strong" }, { harness: "claude", model: "opus", effort: "high" }];
  /** As the resolved chain reports them: every key, unset ones null. */
  const RESOLVED = ENTRIES.map((e) => ({ profile: null, model: null, effort: null, ...e }));
  const pick = (name: string) => screen.findByRole("combobox", { name });

  it("shows each entry with its harness and route, never as text", async () => {
    withFallback(ENTRIES);
    expect(await pick("Fallback 1 harness")).toHaveValue("codex");
    expect(await pick("Fallback 1 profile")).toHaveValue("strong");
    expect(await pick("Fallback 2 harness")).toHaveValue("claude");
    expect(within(await pick("Fallback 2 profile")).getByRole("option", { selected: true })).toHaveTextContent("model opus · effort high");
    expect(screen.queryByText(/object Object/)).toBeNull();
    expect(screen.queryByRole("textbox", { name: "fallback" })).toBeNull();
  });

  it("sends the list as entry mappings, only what each sets: a harness keeps the route, a profile replaces a model and effort", async () => {
    const { draft } = withFallback(RESOLVED);
    await userEvent.selectOptions(await pick("Fallback 1 harness"), "claude");
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", [{ harness: "claude", profile: "strong" }, ENTRIES[1]]);
    await userEvent.selectOptions(await pick("Fallback 2 profile"), "fast");
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", [ENTRIES[0], { harness: "claude", profile: "fast" }]);
    await userEvent.click(screen.getByRole("button", { name: "Remove fallback 1" }));
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", [ENTRIES[1]]);
  });

  it("adds an entry on another harness than the task's", async () => {
    const { draft } = mount(TASK);
    const add = await screen.findByRole("button", { name: "+ Add a fallback" });
    await waitFor(() => expect(add).toBeEnabled());
    await userEvent.click(add);
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", [{ harness: "codex" }]);
  });

  it("unsets the list when its last entry is removed, so an agent profile's own list applies", async () => {
    const { draft } = withFallback([{ harness: "codex" }]);
    await userEvent.click(await screen.findByRole("button", { name: "Remove fallback 1" }));
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", null);
  });

  it("reads a bare name a draft wrote as the harness it meant, so a change writes the schema's shape", async () => {
    const { draft } = withFallback(["codex"]);
    await userEvent.selectOptions(await pick("Fallback 1 profile"), "strong");
    expect(draft.field).toHaveBeenLastCalledWith(TASK, "fallback", [{ harness: "codex", profile: "strong" }]);
  });

  it("on the Config tab, shows the entries and sends the edit to the Overview", async () => {
    withFallback(ENTRIES);
    await configTab();
    expect(rowOf("fallback")).toHaveTextContent("codex · strong, claude · model opus · effort high");
    expect(within(rowOf("fallback")).queryByRole("button", { name: "Edit fallback" })).toBeNull();
    expect(within(rowOf("fallback")).getByText("on Overview")).toBeInTheDocument();
  });
});
