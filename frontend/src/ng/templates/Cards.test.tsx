import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { ChainsPage, DOUBLE_CLICK_MS } from "./ChainsPage";
import * as d from "./draft/draftApi";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView } from "./draft/types";
import { resetLibrary } from "./useLibrary";

const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });
const opsAnswer = (result: Record<string, unknown> = {}) => ok({ ...DEFAULT_VIEW, draft: true, ops: [{ op: "x", result }] });
let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}
const mount = (path = "/templates/chains/default") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/chains" element={<p>index</p>} />
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainsPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
const canvas = () => screen.findByRole("group", { name: "default" });
let toasts: string[] = [];
window.addEventListener("kraft:toast", (e) => toasts.push((e as CustomEvent<{ message: string }>).detail.message));
const toasted = (re: RegExp | string) => waitFor(() => expect(toasts.some((x) => (typeof re === "string" ? x === re : re.test(x)))).toBe(true));

beforeEach(() => {
  toasts = [];
  vi.restoreAllMocks();
  resetLibrary();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(api, "getLibrary").mockResolvedValue({ file: "", text: "", components: [
    { id: "nodes.verification", kind: "nodes", name: "verification", definition: { kind: "exec", tasks: [] }, used_by: [], issues: [] },
    { id: "nodes.implementation", kind: "nodes", name: "implementation", definition: { kind: "exec", tasks: [] }, used_by: [], issues: [] },
  ] });
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(DEFAULT_VIEW));
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

describe("rename", () => {
  const pane = () => screen.getByRole("complementary", { name: "spec pane" });
  const pause = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

  it("a click on the pane's title renames in place, naming the references Enter updates", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer({ updated: [{}, {}] }));
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getByRole("button", { name: "spec, node" }));
    await userEvent.click(within(pane()).getByRole("button", { name: "spec" }));
    const id = within(pane()).getByRole("textbox", { name: "Rename node" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(pane()).getByText(/Enter also updates 1 reference: spec_approval\.reject_to/)).toBeInTheDocument();
    await waitFor(() => expect(id).toHaveFocus());
    await userEvent.clear(id);
    await userEvent.type(id, "specification{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "rename", path: "spec", id: "specification" }], undefined));
    await toasted(/Renamed spec → specification · 2 references updated/);
  });

  it("Esc keeps the id and the pane open; a blur renames, as a Config row saves", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer());
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getByRole("button", { name: "spec, node" }));
    await userEvent.click(within(pane()).getByRole("button", { name: "spec" }));
    await userEvent.type(within(pane()).getByRole("textbox", { name: "Rename node" }), "x{Escape}");
    expect(within(pane()).queryByRole("textbox", { name: "Rename node" })).toBeNull();
    expect(within(pane()).getByRole("button", { name: "spec" })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(within(pane()).getByRole("button", { name: "spec" }));
    await userEvent.type(within(pane()).getByRole("textbox", { name: "Rename node" }), "x");
    await userEvent.tab();
    await waitFor(() => expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "rename", path: "spec", id: "specx" }], undefined));
  });

  it("a click on the selected node's name, with its pane open, renames it in the pane", async () => {
    mount();
    const g = await canvas();
    const spec = within(g).getByRole("button", { name: "spec, node" });
    await userEvent.click(spec);
    await pause(DOUBLE_CLICK_MS + 50);
    await userEvent.click(spec);
    expect(await within(pane()).findByRole("textbox", { name: "Rename node" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it.each([
    ["an unselected node", false],
    ["the selected node", true],
  ])("a double-click on %s opens its node view and renames nothing", async (_, selected) => {
    mount();
    const g = await canvas();
    const spec = within(g).getByRole("button", { name: "spec, node" });
    if (selected) {
      await userEvent.click(spec);
      await pause(DOUBLE_CLICK_MS + 50);
    }
    await userEvent.dblClick(spec);
    await waitFor(() => expect(where).toBe("/templates/chains/default/nodes/spec"));
    await pause(DOUBLE_CLICK_MS + 50);
    expect(screen.queryByRole("textbox", { name: "Rename node" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("with the pane collapsed, the first click on the selected node expands it instead", async () => {
    mount();
    const g = await canvas();
    const spec = within(g).getByRole("button", { name: "spec, node" });
    await userEvent.click(spec);
    await userEvent.click(screen.getByRole("button", { name: "Collapse pane" }));
    await userEvent.click(spec);
    expect(pane()).toBeInTheDocument();
    await pause(DOUBLE_CLICK_MS + 50);
    expect(screen.queryByRole("textbox", { name: "Rename node" })).toBeNull();
  });

  it("a renamed chain's publish goes to the new id", async () => {
    const view: DraftView = { ...DEFAULT_VIEW, draft: true, files: { "chains/default.yaml": null, "chains/mine.yaml": "id: mine\n" }, result: { ...DEFAULT_VIEW.result, model: { "chains/mine.yaml": { ...DEFAULT_VIEW.result.model["chains/default.yaml"], id: "mine" } } } };
    vi.mocked(d.getDraft).mockImplementation(() => ok(view));
    vi.spyOn(api, "getTemplate").mockResolvedValue({ id: "default", file: "", text: "", chain: {} });
    const pub = vi.spyOn(d, "publish").mockResolvedValue({ status: 200, body: { published: [] } });
    mount();
    await canvas();
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    expect(pub).toHaveBeenCalled();
    await waitFor(() => expect(where).toBe("/templates/chains/mine"));
    // The new key's draft loads after the move lands, so wait for it before counting.
    await waitFor(() => expect(d.getDraft).toHaveBeenLastCalledWith("chains", "mine"));
    expect(d.getDraft).toHaveBeenCalledTimes(2); // this key once, then the new one: no reload of the moved key
  });
});

describe("remove", () => {
  it("lists what points at a node, marks it red on the canvas, removes, and falls back to the floor", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer({ broken: [{}] }));
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getByRole("button", { name: "spec, node" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove node" }));
    const card = await screen.findByRole("dialog", { name: "Remove node" });
    expect(within(card).getByText("spec_approval.reject_to")).toBeInTheDocument();
    expect(within(g).getByRole("button", { name: "spec_approval, gate" }).querySelector(".is-prob")).not.toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "Remove node" }));
    expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "remove", path: "spec" }], undefined);
    await toasted("Removed spec · 1 reference now broken · ⌘Z undoes it");
    expect(screen.queryByRole("complementary", { name: "spec pane" })).toBeNull();
    expect(within(g).getByRole("button", { name: "spec_approval, gate" }).querySelector(".is-prob")).toBeNull();
  });
});

describe("reorder", () => {
  it("⌥→ checks the move first, then moves the selected node one place; a refusal says why and moves nothing", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation((_a, _k, _o, preview) => (preview ? opsAnswer() : opsAnswer()));
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getByRole("button", { name: "implementation, node" }));
    await userEvent.keyboard("{Alt>}{ArrowRight}{/Alt}");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[0]).toEqual(["chains", "default", [{ op: "move", path: "implementation", to: 3 }], true]);
    expect(post.mock.calls[1]).toEqual(["chains", "default", [{ op: "move", path: "implementation", to: 3 }], undefined]);

    post.mockImplementation(() => Promise.resolve({ status: 422, body: { detail: "reject_to of local_review would come after it", op: 0 } }) as never);
    await userEvent.keyboard("{Alt>}{ArrowLeft}{/Alt}");
    await toasted("Can't move implementation: reject_to of local_review would come after it");
    expect(post).toHaveBeenCalledTimes(3);
  });

  it("drags a node past the threshold, checks the drop live in the strip, and moves it on release", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer());
    mount();
    const g = await canvas();
    const spec = within(g).getByRole("button", { name: "spec, node" });
    vi.spyOn(g, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(spec, { button: 0, clientX: 100, clientY: 100 });
    await act(async () => {
      window.dispatchEvent(new PointerEvent("pointermove", { clientX: 102, clientY: 100 }));
      await new Promise((r) => setTimeout(r, 20));
    });
    // Within the 4px threshold nothing lifts.
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByRole("status", { name: "" })?.textContent ?? "").not.toMatch(/Move spec/);
    // Far right: past every other node.
    act(() => void window.dispatchEvent(new PointerEvent("pointermove", { clientX: 5000, clientY: 100 })));
    await waitFor(() => expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "move", path: "spec", to: 5 }], true));
    expect(await screen.findByText("Move spec here")).toBeInTheDocument();
    act(() => void window.dispatchEvent(new PointerEvent("pointerup", {})));
    await waitFor(() => expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "move", path: "spec", to: 5 }], undefined));
    expect(screen.queryByText("Move spec here")).toBeNull();
  });
});

describe("change base", () => {
  it("previews what the new base keeps and drops, then applies", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation((_a, _k, _o, preview) => opsAnswer(preview ? { kept: ["skippable"], dropped: [{ key: "steps", why: "implementation has no step tests" }] } : {}));
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getByRole("button", { name: "verification, node" }));
    await userEvent.click(screen.getByRole("button", { name: "Change base…" }));
    await userEvent.click(await screen.findByRole("option", { name: /implementation/ }));
    const card = await screen.findByRole("dialog", { name: "Change base of verification" });
    expect(card).toHaveTextContent("skippable");
    expect(card).toHaveTextContent("steps · implementation has no step tests");
    expect(post).toHaveBeenCalledTimes(1);
    await userEvent.click(within(card).getByRole("button", { name: "Change base" }));
    expect(post).toHaveBeenLastCalledWith("chains", "default", [{ op: "change_base", node: "verification", base: "implementation" }], undefined);
    await toasted("Base is now implementation");
  });
});
