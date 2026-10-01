import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { ChainsPage } from "./ChainsPage";
import * as d from "./draft/draftApi";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView, Result } from "./draft/types";

const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });
const view = (draft: boolean, extra: Partial<Result> = {}, base: Record<string, string | null> = DEFAULT_VIEW.base): DraftView => ({ ...DEFAULT_VIEW, draft, base, result: { ...DEFAULT_VIEW.result, changes: draft ? [{ path: "spec", kind: "change", summary: "x" }] : [], ...extra } });
let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}
const mount = () =>
  render(
    <MemoryRouter initialEntries={["/templates/chains/default"]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
const crumb = () => screen.findByRole("button", { name: "Chain default, switch chain" });

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(api, "getTemplates").mockResolvedValue([
    { id: "default", nodes: new Array(19).fill({}), gates: 5 },
    { id: "quick-task", nodes: new Array(3).fill({}), gates: 0 },
  ] as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [{ area: "chains", key: "quick-task", files: [], changes: 1, problems: 0, updated_at: "" }] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(view(false)));
  useStore.setState({ connection: "open", workItems: {
    a: { id: "a", chain_template: "default", status: "active" },
    b: { id: "b", chain_template: "default", status: "completed" },
  } } as never);
});

describe("chain switcher", () => {
  it("lists every chain with its size and running items, ✓ on the current one, a dot on a draft; search filters", async () => {
    mount();
    await userEvent.click(await crumb());
    const list = await screen.findByRole("listbox", { name: "Chains" });
    await within(list).findByRole("option", { name: /quick-task/ });
    const def = within(list).getByRole("option", { name: /^default/ });
    expect(def).toHaveAttribute("aria-selected", "true");
    expect(def).toHaveTextContent("19 nodes · 1 running");
    expect(within(list).getByRole("option", { name: /quick-task/ }).querySelector(".sw-dot")).not.toBeNull();
    expect(def.querySelector(".sw-dot")).toBeNull();
    await userEvent.type(screen.getByRole("textbox", { name: "Search chains" }), "quick");
    expect(within(list).getAllByRole("option")).toHaveLength(1);
  });

  it("switches by keyboard with no draft open, without asking", async () => {
    mount();
    await userEvent.click(await crumb());
    await screen.findByRole("option", { name: /quick-task/ });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Search chains" })).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    await waitFor(() => expect(where).toBe("/templates/chains/quick-task"));
  });

  it("New chain and Duplicate send new_chain on the new key, refuse a taken id, and go there", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...view(true), ops: [] }));
    mount();
    await userEvent.click(await crumb());
    await screen.findByRole("option", { name: /quick-task/ });
    await userEvent.click(within(screen.getByRole("dialog", { name: "Switch chain" })).getByRole("button", { name: "Duplicate" }));
    const id = await screen.findByRole("textbox", { name: "Duplicate id" });
    expect(id).toHaveValue("default_copy");
    await userEvent.clear(id);
    await userEvent.type(id, "quick-task");
    expect(screen.getByRole("alert")).toHaveTextContent("quick-task is taken.");
    await userEvent.clear(id);
    await userEvent.type(id, "mine{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("chains", "mine", [{ op: "new_chain", from: "default" }]));
    await waitFor(() => expect(where).toBe("/templates/chains/mine"));
  });
});

describe("unpublished changes dialog", () => {
  const openSwitchTo = async (name: RegExp) => {
    await userEvent.click(await crumb());
    await userEvent.click(await screen.findByRole("option", { name }));
    return screen.findByRole("dialog", { name: "You have unpublished changes" });
  };

  it("asks before leaving a chain with a draft: Stay stays", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view(true)));
    mount();
    const dlg = await openSwitchTo(/quick-task/);
    expect(dlg).toHaveTextContent("default has 1 unpublished change. Publish or discard them before switching to quick-task.");
    await userEvent.click(within(dlg).getByRole("button", { name: "Stay" }));
    expect(where).toBe("/templates/chains/default");
  });

  it("Discard & continue discards, then switches; a never-published chain says so", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view(true, {}, { "chains/default.yaml": null })));
    const del = vi.spyOn(d, "discard").mockResolvedValue({ status: 204, body: undefined });
    mount();
    const dlg = await openSwitchTo(/quick-task/);
    expect(dlg).toHaveTextContent("default has never been published.");
    await userEvent.click(within(dlg).getByRole("button", { name: "Discard chain & continue" }));
    expect(del).toHaveBeenCalled();
    await waitFor(() => expect(where).toBe("/templates/chains/quick-task"));
  });

  it("Publish & continue publishes, then switches", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view(true)));
    const pub = vi.spyOn(d, "publish").mockResolvedValue({ status: 200, body: { published: [] } });
    mount();
    const dlg = await openSwitchTo(/quick-task/);
    await userEvent.click(within(dlg).getByRole("button", { name: "Publish & continue" }));
    expect(pub).toHaveBeenCalled();
    await waitFor(() => expect(where).toBe("/templates/chains/quick-task"));
  });

  it("with problems, offers Review problems instead, which opens the review", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view(true, { problems: [{ path: "spec", field: null, message: "x", file: "f", line: 1, col: 1 }] })));
    vi.spyOn(api, "getTemplate").mockResolvedValue({ id: "default", file: "", text: "", chain: {} });
    mount();
    const dlg = await openSwitchTo(/quick-task/);
    expect(within(dlg).queryByRole("button", { name: "Publish & continue" })).toBeNull();
    await userEvent.click(within(dlg).getByRole("button", { name: "Review problems" }));
    expect(await screen.findByRole("complementary", { name: "Draft · 1 change pane" })).toBeInTheDocument();
    expect(where).toBe("/templates/chains/default");
  });
});

describe("chain settings footer", () => {
  it("refuses to delete a chain a repo defaults to; otherwise confirms, then reviews the deletion", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view(false, { impact: { running: 0, repos: ["/code/kraft"] } })));
    const { unmount } = mount();
    const del = await screen.findByRole("button", { name: "Delete chain" });
    expect(del).toBeDisabled();
    expect(del).toHaveAttribute("title", "Can't delete: /code/kraft default to it");
    unmount();

    vi.mocked(d.getDraft).mockImplementation(() => ok(view(false)));
    vi.spyOn(api, "getTemplate").mockResolvedValue({ id: "default", file: "", text: "", chain: {} });
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...view(true), ops: [] }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Delete chain" }));
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(post).toHaveBeenCalledWith("chains", "default", [{ op: "delete_chain" }], undefined);
    expect(await screen.findByRole("complementary", { name: /^Draft · 1 change/ })).toBeInTheDocument();
  });
});
