import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "./draft/draftApi";
import type { ConfigDraft } from "./draft/useConfigDraft";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView, Result } from "./draft/types";
import { ItemYaml } from "./panes/ItemYaml";
import { YamlView } from "./YamlView";

const FILE = "chains/default.yaml";
const TEXT = "id: default\nnodes:\n  - id: spec\n    kind: exec\n";
const viewWith = (text: string, result: Partial<Result> = {}): DraftView => ({ ...DEFAULT_VIEW, draft: true, updated_at: text, files: { [FILE]: text }, result: { ...DEFAULT_VIEW.result, ...result } });
const fake = (v: DraftView) => ({ view: v, text: vi.fn(), flush: vi.fn(), ops: vi.fn(() => Promise.resolve({ status: 200, body: {} })) }) as unknown as ConfigDraft & { text: ReturnType<typeof vi.fn> };
const area = () => screen.getByRole("textbox", { name: `${FILE}, YAML` }) as HTMLTextAreaElement;

beforeEach(() => vi.restoreAllMocks());

describe("YAML view", () => {
  it("sends what is typed, marks lines against the published file, and follows an answer when not typing", async () => {
    const draft = fake(viewWith(TEXT));
    const { rerender } = render(<YamlView draft={draft} chain="default" published={"id: default\nnodes:\n  - id: spec\n    kind: gate\n"} />);
    expect([...document.querySelectorAll(".yv-mark")].map((m) => m.textContent)).toEqual(["", "", "", "~", ""]);
    await userEvent.type(area(), "x");
    expect(draft.text).toHaveBeenLastCalledWith(FILE, `${TEXT}x`);
    await userEvent.keyboard("{Escape}");
    expect(area()).not.toHaveFocus();
    expect(draft.flush).toHaveBeenCalled();
    // A canvas op or an undo answers with new text: shown, since nothing is being typed.
    rerender(<YamlView draft={fake(viewWith("id: default\nnodes: []\n"))} chain="default" published={null} />);
    expect(area()).toHaveValue("id: default\nnodes: []\n");
    expect([...document.querySelectorAll(".yv-mark")].map((m) => m.textContent)).toEqual(["+", "+", ""]);
  });

  it("marks a problem on its line, and its row puts the caret there", async () => {
    render(<YamlView draft={fake(viewWith(TEXT, { problems: [{ path: "spec", field: "kind", message: "Value error, unknown kind", file: FILE, line: 4, col: 5 }] }))} chain="default" published={TEXT} />);
    expect(screen.getByText("unknown kind", { selector: ".yv-msg" })).toBeInTheDocument();
    expect(document.querySelectorAll(".yv-bgl")[3]).toHaveClass("is-bad");
    await userEvent.click(within(screen.getByRole("list")).getByRole("button", { name: /line 4 · spec · unknown kind/ }));
    expect(area()).toHaveFocus();
    expect(area().selectionStart).toBe(TEXT.indexOf("    kind"));
  });

  it("on a syntax error says the canvas keeps the last valid draft, and Revert puts that text back", async () => {
    const draft = fake(viewWith(TEXT));
    const { rerender } = render(<YamlView draft={draft} chain="default" published={TEXT} />);
    const broken = "id: default\nnodes: [\n";
    rerender(<YamlView draft={Object.assign(draft, { view: viewWith(broken, { yaml_error: { file: FILE, line: 3, col: 1, message: "expected ']'" } }) })} chain="default" published={TEXT} />);
    expect(screen.getByRole("status")).toHaveTextContent("syntax error · the canvas keeps the last valid draft");
    expect(screen.getByRole("alert")).toHaveTextContent("Line 3: expected ']'");
    await userEvent.click(screen.getByRole("button", { name: "Revert to last valid draft" }));
    expect(draft.text).toHaveBeenLastCalledWith(FILE, TEXT);
    expect(area()).toHaveValue(TEXT);
  });

  it("inserts two spaces for Tab instead of leaving the field", async () => {
    const draft = fake(viewWith("a"));
    render(<YamlView draft={draft} chain="default" published={null} />);
    await userEvent.click(area());
    await userEvent.keyboard("{Tab}");
    expect(draft.text).toHaveBeenLastCalledWith(FILE, "a  ");
    expect(area()).toHaveFocus();
  });
});

describe("a component's YAML tab", () => {
  it("shows the server's fragment, sends set_fragment on a pause, and shows a refusal with Revert", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const frag = vi.spyOn(d, "fragment").mockResolvedValue({ status: 200, body: { path: "spec", text: "id: spec\nkind: exec\n" } });
    const draft = fake(viewWith(TEXT));
    (draft.ops as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 422, body: { detail: "rename it with rename" } });
    render(<ItemYaml draft={draft} chain="default" path="spec" />);
    const ta = await screen.findByRole("textbox", { name: "spec, YAML" });
    expect(ta).toHaveValue("id: spec\nkind: exec\n");
    expect(frag).toHaveBeenCalledWith("chains", "default", "spec");
    await userEvent.clear(ta);
    await userEvent.type(ta, "id: other");
    expect(draft.ops).not.toHaveBeenCalled();
    await act(async () => void vi.advanceTimersByTime(800));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "set_fragment", path: "spec", yaml: "id: other" }], { quiet: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("Not applied · rename it with rename");
    expect(ta).toHaveValue("id: other");
    await userEvent.click(screen.getByRole("button", { name: "Revert" }));
    await waitFor(() => expect(ta).toHaveValue("id: spec\nkind: exec\n"));
    vi.useRealTimers();
  });
});
