import { render, screen, within } from "@testing-library/react";
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
  render(<ChainPane draft={draft} chain="default" path={path} open size={SIZE} onCollapse={() => {}} onExpand={() => {}} goTo={goTo} />);
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

  it("toggles a boolean with one click", async () => {
    const { draft } = mount("verification");
    await configTab();
    await userEvent.click(within(rowOf("skippable")).getByRole("button", { name: "Turn skippable off" }));
    expect(draft.field).toHaveBeenCalledWith("verification", "skippable", false);
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
