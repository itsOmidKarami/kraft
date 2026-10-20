import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { Toaster } from "../ui/Toast";
import { ChainsPage, chainUrl } from "./ChainsPage";
import * as d from "./draft/draftApi";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView } from "./draft/types";

const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });
const LOCAL: DraftView = { ...DEFAULT_VIEW, draft: false };
const FILE = "chains/default.yaml";
/** The default chain as an add-on would ship it: its own key and file, its add-on, and no `sources`. */
const SHIPPED: DraftView = {
  ...LOCAL,
  key: "release:ship",
  addon: { id: "release@acme", version: "1.0.0" },
  files: { "chains/release:ship.yaml": LOCAL.files[FILE] },
  base: { "chains/release:ship.yaml": LOCAL.base[FILE] },
  result: { ...LOCAL.result, model: { "chains/release:ship.yaml": LOCAL.result.model[FILE] }, sources: {} },
};

/** The same chain from a repository's `.kraft/`: read-only here, never copied. */
const REPO_SHIPPED: DraftView = {
  ...SHIPPED,
  key: "app:ship",
  addon: { id: "library@repo:app", version: "", repo: "app" },
  files: { "chains/app:ship.yaml": LOCAL.files[FILE] },
  base: { "chains/app:ship.yaml": LOCAL.base[FILE] },
  result: { ...SHIPPED.result, model: { "chains/app:ship.yaml": LOCAL.result.model[FILE] } },
};

let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}

const mount = (chain: string) =>
  render(
    <MemoryRouter initialEntries={[chainUrl(chain)]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
        </Route>
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );

/** The one request a copy sends, answered by `answer`; every other call is a spy above. */
const serve = (status: number, body: unknown) => {
  const fetched = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetched);
  return () => fetched.mock.calls.filter(([, init]) => init?.method === "POST").map(([url, init]) => [url, JSON.parse(String(init!.body))]);
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", nodes: [], gates: 0, addon: null }, { id: "release:ship", nodes: [], gates: 0, addon: SHIPPED.addon }]);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation((_area, key) => ok(key === "release:ship" ? SHIPPED : LOCAL));
  useStore.setState({ workItems: {}, connection: "open" } as never);
});
afterEach(() => vi.unstubAllGlobals());

describe("An add-on's chain on the Chains page", () => {
  it.each([
    { chain: "release:ship", shipped: true },
    { chain: "default", shipped: false },
  ])("$chain: opens from its URL, badged and without edit controls only when an add-on ships it", async ({ chain, shipped }) => {
    mount(chain);
    // `release%3Aship` in the URL is the chain `release:ship` to the page and to the draft route.
    const canvas = await screen.findByRole("group", { name: chain });
    expect(d.getDraft).toHaveBeenCalledWith("chains", chain);
    expect(within(canvas).getByRole("button", { name: "implementation, node" })).toBeInTheDocument();
    const there = (el: unknown) => (shipped ? expect(el).toBeNull() : expect(el).not.toBeNull());

    expect(screen.queryAllByText("release@acme 1.0.0")).toHaveLength(shipped ? 1 : 0);
    expect(screen.queryAllByRole("button", { name: "Add to my library" })).toHaveLength(shipped ? 1 : 0);
    there(screen.queryByRole("button", { name: /Review & publish/ }));
    expect(within(canvas).queryAllByRole("button", { name: "Add a node or gate here" })).toHaveLength(shipped ? 0 : 7);
    // The chain's pane: no rename on its title, no description field, no Duplicate or Delete.
    const pane = screen.getByRole("complementary", { name: `${chain} pane` });
    there(within(pane).queryByRole("button", { name: chain }));
    there(within(pane).queryByRole("textbox", { name: "description" }));
    there(within(pane).queryByRole("button", { name: "Delete chain" }));

    // A gate's pane reads its message; only a local one edits or removes it.
    await userEvent.click(within(canvas).getByRole("button", { name: "spec_approval, gate" }));
    const gate = screen.getByRole("complementary", { name: "spec_approval pane" });
    expect(within(gate).getByText("Review and approve the specification.")).toBeInTheDocument();
    there(within(gate).queryByRole("textbox", { name: "message" }));
    there(within(gate).queryByRole("button", { name: "Remove gate" }));

    // The YAML is still there to read, and only a local chain's can be typed in.
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    const yaml = screen.getByRole("textbox", { name: `chains/${chain}.yaml, YAML` });
    expect(yaml).toHaveValue(LOCAL.files[FILE]);
    expect(yaml.hasAttribute("readonly")).toBe(shipped);
  });

  it("a repo library's chain is badged with its repository and offers no copy", async () => {
    vi.spyOn(d, "getDraft").mockImplementation(() => ok(REPO_SHIPPED));
    mount("app:ship");
    await screen.findByRole("group", { name: "app:ship" });
    expect(screen.getAllByText("repo library app").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Add to my library" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Review & publish/ })).toBeNull();
  });

  it("the switcher badges the add-on's chain and not the local one", async () => {
    mount("default");
    await userEvent.click(await screen.findByRole("button", { name: /switch chain/ }));
    expect(await screen.findByRole("option", { name: /release:ship/ })).toHaveTextContent("release@acme 1.0.0");
    expect(screen.getByRole("option", { name: /^default/ })).not.toHaveTextContent("release@acme");
  });

  describe("a child add-on's chain", () => {
    const INHERITED = { id: "company@corp", version: "3.2.0", inheritance: { origin: "inherited" as const, parent: { id: "company@corp", namespace: "company" }, author: { id: "company@corp", version: "3.2.0" } } };
    const withMark = (addon: typeof INHERITED | typeof SHIPPED.addon) => {
      vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", nodes: [], gates: 0, addon: null }, { id: "release:ship", nodes: [], gates: 0, addon }]);
      vi.spyOn(d, "getDraft").mockImplementation((_area, key) => ok(key === "release:ship" ? { ...SHIPPED, addon } : LOCAL));
    };

    it.each([
      { where: "switcher", find: async () => { await userEvent.click(await screen.findByRole("button", { name: /switch chain/ })); return screen.findByRole("option", { name: /release:ship/ }); }, chain: "default" },
      { where: "page-header", find: async () => { await screen.findByRole("group", { name: "release:ship" }); return document.body; }, chain: "release:ship" },
    ])("$where shows the inherited mark with its author and a root add-on's chain none", async ({ find, chain }) => {
      withMark(INHERITED);
      const first = mount(chain);
      const row = await find();
      expect(within(row).getByText("inherited from company")).toHaveAttribute("title", "Written by company@corp 3.2.0");
      first.unmount();
      cleanup();

      withMark(SHIPPED.addon);
      mount(chain);
      const root = await find();
      expect(within(root).queryByText(/inherited from|overridden here|added here/)).toBeNull();
    });
  });

  it.each([
    // An add-on's chain is added as one that extends it unless Copy is chosen.
    { how: "Extend, the default", choice: undefined, initial: "ship_ext", source: { extends: "release:ship" } },
    { how: "a copy, chosen", choice: "Copy", initial: "ship", source: { from: "release:ship" } },
  ])("Add to my library as $how asks for an id, sends new_chain on the add-on's chain, and opens the new draft", async ({ choice, initial, source }) => {
    const posts = serve(200, { ...LOCAL, draft: true, ops: [] });
    mount("release:ship");
    await userEvent.click(await screen.findByRole("button", { name: "Add to my library" }));
    expect(screen.getByRole("radio", { name: "Extend" })).toBeChecked();
    if (choice) await userEvent.click(screen.getByRole("radio", { name: choice }));
    const id = await screen.findByRole("textbox", { name: "Duplicate id" });
    // From the bare name: `release:ship_copy` would not be an id.
    expect(id).toHaveValue(initial);
    // The id field takes focus as the step opens; choosing moves it to the choice.
    if (!choice) await waitFor(() => expect(id).toHaveFocus());
    await userEvent.clear(id);
    await userEvent.type(id, "default");
    expect(screen.getByText("default is taken.")).toBeInTheDocument();
    await userEvent.clear(id);
    await userEvent.type(id, "my_ship{Enter}");
    await waitFor(() => expect(posts()).toEqual([["/api/drafts/chains/my_ship/ops", { ops: [{ op: "new_chain", ...source }] }]]));
    await waitFor(() => expect(where).toBe("/templates/chains/my_ship"));
  });

  it("says why when the server refuses the copy, and stays on the add-on's chain", async () => {
    serve(409, { detail: "my_ship comes from add-on release@acme; extend it or copy it to your library" });
    mount("release:ship");
    await userEvent.click(await screen.findByRole("button", { name: "Add to my library" }));
    await userEvent.click(screen.getByRole("radio", { name: "Copy" }));
    const id = await screen.findByRole("textbox", { name: "Duplicate id" });
    await userEvent.click(id);
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("my_ship comes from add-on release@acme; extend it or copy it to your library")).toBeInTheDocument();
    expect(where).toBe("/templates/chains/release%3Aship");
  });
});
