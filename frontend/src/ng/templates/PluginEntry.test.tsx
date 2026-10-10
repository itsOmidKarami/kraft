import { render, screen, waitFor, within } from "@testing-library/react";
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
/** The default chain as a plugin would ship it: its own key and file, its plugin, and no `sources`. */
const SHIPPED: DraftView = {
  ...LOCAL,
  key: "release:ship",
  plugin: { id: "release@acme", version: "1.0.0" },
  files: { "chains/release:ship.yaml": LOCAL.files[FILE] },
  base: { "chains/release:ship.yaml": LOCAL.base[FILE] },
  result: { ...LOCAL.result, model: { "chains/release:ship.yaml": LOCAL.result.model[FILE] }, sources: {} },
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
  vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", nodes: [], gates: 0, plugin: null }, { id: "release:ship", nodes: [], gates: 0, plugin: SHIPPED.plugin }]);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation((_area, key) => ok(key === "release:ship" ? SHIPPED : LOCAL));
  useStore.setState({ workItems: {}, connection: "open" } as never);
});
afterEach(() => vi.unstubAllGlobals());

describe("A plugin's chain on the Chains page", () => {
  it.each([
    { chain: "release:ship", shipped: true },
    { chain: "default", shipped: false },
  ])("$chain: opens from its URL, badged and without edit controls only when a plugin ships it", async ({ chain, shipped }) => {
    mount(chain);
    // `release%3Aship` in the URL is the chain `release:ship` to the page and to the draft route.
    const canvas = await screen.findByRole("group", { name: chain });
    expect(d.getDraft).toHaveBeenCalledWith("chains", chain);
    expect(within(canvas).getByRole("button", { name: "implementation, node" })).toBeInTheDocument();
    const there = (el: unknown) => (shipped ? expect(el).toBeNull() : expect(el).not.toBeNull());

    expect(screen.queryAllByText("release@acme 1.0.0")).toHaveLength(shipped ? 1 : 0);
    expect(screen.queryAllByRole("button", { name: "Copy to my library" })).toHaveLength(shipped ? 1 : 0);
    there(screen.queryByRole("button", { name: /Review & publish/ }));
    there(screen.queryByRole("button", { name: "Chain settings" }));
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

  it("the switcher badges the plugin's chain and not the local one", async () => {
    mount("default");
    await userEvent.click(await screen.findByRole("button", { name: /switch chain/ }));
    expect(await screen.findByRole("option", { name: /release:ship/ })).toHaveTextContent("release@acme 1.0.0");
    expect(screen.getByRole("option", { name: /^default/ })).not.toHaveTextContent("release@acme");
  });

  it("Copy to my library asks for an id, sends new_chain from the plugin's chain, and opens the copy's draft", async () => {
    const posts = serve(200, { ...LOCAL, draft: true, ops: [] });
    mount("release:ship");
    await userEvent.click(await screen.findByRole("button", { name: "Copy to my library" }));
    const id = await screen.findByRole("textbox", { name: "Duplicate id" });
    // The bare name: `release:ship_copy` would not be an id.
    expect(id).toHaveValue("ship");
    await waitFor(() => expect(id).toHaveFocus());
    await userEvent.clear(id);
    await userEvent.type(id, "default");
    expect(screen.getByText("default is taken.")).toBeInTheDocument();
    await userEvent.clear(id);
    await userEvent.type(id, "my_ship{Enter}");
    await waitFor(() => expect(posts()).toEqual([["/api/drafts/chains/my_ship/ops", { ops: [{ op: "new_chain", from: "release:ship" }] }]]));
    await waitFor(() => expect(where).toBe("/templates/chains/my_ship"));
  });

  it("says why when the server refuses the copy, and stays on the plugin's chain", async () => {
    serve(409, { detail: "my_ship comes from plugin release@acme; extend it or copy it to your library" });
    mount("release:ship");
    await userEvent.click(await screen.findByRole("button", { name: "Copy to my library" }));
    const id = await screen.findByRole("textbox", { name: "Duplicate id" });
    await waitFor(() => expect(id).toHaveFocus());
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("my_ship comes from plugin release@acme; extend it or copy it to your library")).toBeInTheDocument();
    expect(where).toBe("/templates/chains/release%3Aship");
  });
});
