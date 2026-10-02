import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Policy } from "../../../types";
import { resetProviders } from "../../harnesses/useProviders";
import { resetHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { API_ITEM } from "../fixture.api";
import { resetRepoEntries } from "../useRepoEntry";
import { acceptWrites, detail, fresh, FROZEN, stubFetch, V1, type Call } from "../testkit";
import type { ItemDetail } from "../useItem";
import { usePaneMemory, Workspace } from "../Workspace";
import { ChainConfig } from "./ChainPane";
import { ItemAgentRows, NodeOverrideRows } from "./ItemOverrides";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("PATCH /work-items/w1");

const answers = {
  "GET /harnesses/profiles": [200, { profiles: [{ id: "claude", provider: "claude", defaults: { model: "sonnet" } }], agent_profiles: [{ id: "strong", effort: "high", model: { claude: "opus-4" } }] }],
  "GET /harnesses": [200, [{ id: "claude", models: [], efforts: ["low", "high"] }]],
} as Record<string, [number, unknown]>;
let calls: Call[];
const patches = () => calls.filter((c) => c.method === "PATCH").map((c) => c.body);
const row = (label: string) => screen.getByRole("button", { name: `Override ${label}` }).closest(".cfg-row") as HTMLElement;
const policy = { default: { attempts: 3 } } as Policy;
const verification = V1.find((n) => n.id === "verification")!;
beforeEach(() => {
  resetHarnessOptions();
  resetProviders();
  resetRepoEntries();
  calls = stubFetch({ ...WRITES, ...answers });
});
afterEach(() => vi.unstubAllGlobals());

describe("ItemAgentRows", () => {
  it("shows what the chain's agents run on, and sets the item's model in one PATCH that keeps its effort", async () => {
    const reload = vi.fn();
    render(<ItemAgentRows item={fresh({ agent_overrides: { effort: "high" } })} reload={reload} />);
    expect(await screen.findByText("each task's own")).toBeInTheDocument();
    expect(row("effort")).toHaveTextContent("highthis item");
    await userEvent.click(screen.getByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("combobox", { name: "model" }), "opus-4{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { effort: "high", model: "opus-4" } }]));
    expect(reload).toHaveBeenCalled();
  });

  it("builds a second save on the first one's field before the item reloads", async () => {
    render(<ItemAgentRows item={fresh()} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("combobox", { name: "model" }), "opus-4{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Override effort" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "effort" }), "low");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { model: "opus-4" } }, { agent_overrides: { model: "opus-4", effort: "low" } }]));
  });

  it("sends a second save only once the first is answered, so the two never land out of order", async () => {
    // Each PATCH waits until the test answers it; `sent` is what reached the server.
    const sent: unknown[] = [];
    const held: (() => void)[] = [];
    const inner = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (init?.method !== "PATCH") return inner(url, init);
      sent.push(JSON.parse(String(init.body)));
      return new Promise<Response>((done) => held.push(() => done(new Response("{}", { status: 200 }))));
    }));
    render(<ItemAgentRows item={fresh()} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("combobox", { name: "model" }), "opus-4{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Override effort" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "effort" }), "low");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(sent).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(sent).toEqual([{ agent_overrides: { model: "opus-4" } }]);
    held[0]();
    await waitFor(() => expect(sent).toEqual([{ agent_overrides: { model: "opus-4" } }, { agent_overrides: { model: "opus-4", effort: "low" } }]));
  });

  it("builds the next save on what the server last took, not on a refused one", async () => {
    let n = 0;
    const inner = globalThis.fetch;
    const sent: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method !== "PATCH") return inner(url, init);
      sent.push(JSON.parse(String(init.body)));
      return n++ === 0 ? new Response(JSON.stringify({ detail: "'bad one' is not a model id" }), { status: 422 }) : new Response("{}", { status: 200 });
    }));
    render(<ItemAgentRows item={fresh({ agent_overrides: { effort: "high" } })} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("combobox", { name: "model" }), "bad one{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Override effort" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "effort" }), "low");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(sent).toEqual([{ agent_overrides: { effort: "high", model: "bad one" } }, { agent_overrides: { effort: "low" } }]));
    expect(within(row("model")).getByRole("alert")).toHaveTextContent("is not a model id");
  });

  it("drops one field on ↺ by sending the rest, and says why the server refused on that row", async () => {
    calls = stubFetch({ ...answers, "PATCH /work-items/w1": [422, { detail: "agent_overrides 'effort' must be one of [...]" }] });
    render(<ItemAgentRows item={fresh({ agent_overrides: { model: "opus", effort: "high" } })} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reset effort" }));
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { model: "opus" } }]));
    expect(within(row("effort")).getByRole("alert")).toHaveTextContent("must be one of");
    expect(within(row("model")).queryByRole("alert")).toBeNull();
  });

  it("steps through the efforts without saving, keeps the pick on Enter, and gives ✎ the focus back", async () => {
    render(<ItemAgentRows item={fresh()} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Override effort" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "effort" }), "low");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "effort" }), "high");
    expect(patches()).toEqual([]);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { effort: "high" } }]));
    expect(screen.getByRole("button", { name: "Override effort" })).toHaveFocus();
  });

  it("is on the chain's Config before the item starts, and not after", () => {
    const { unmount } = render(<ChainConfig item={fresh()} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    expect(screen.getByRole("heading", { name: "Every agent task" })).toBeInTheDocument();
    unmount();
    render(<ChainConfig item={detail({ materialized_chain: FROZEN, agent_overrides: { model: "opus" } })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    expect(screen.queryByRole("heading", { name: "Every agent task" })).toBeNull();
    // Started: the override is listed with its reset, as before.
    expect(screen.getByText("model opus")).toBeInTheDocument();
  });
});

describe("NodeOverrideRows", () => {
  const show = (item: ItemDetail = fresh()) => render(<NodeOverrideRows item={item} node={verification} policy={policy} reload={() => {}} />);

  it("shows the node's settings with where each comes from", async () => {
    const { unmount } = show();
    expect(await screen.findByText("opus")).toBeInTheDocument();
    unmount();
    show(fresh({ agent_overrides: { effort: "low", model: "haiku" } }));
    expect(row("model")).toHaveTextContent("haikuitem-wide");
    expect(row("fix loop attempts")).toHaveTextContent("2chain");
    expect(row("auto-escalate")).toHaveTextContent("onpolicy");
    expect(row("effort")).toHaveTextContent("lowitem-wide");
    expect(row("extra prompt")).toHaveTextContent("none");
  });

  it("reads the attempts from the item's own policy, as the API sends it", async () => {
    render(<NodeOverrideRows item={detail({ ...API_ITEM })} node={{ ...verification, fix_loop: "verification.fix_loop" }} policy={policy} reload={() => {}} />);
    expect(await screen.findByRole("button", { name: "Override fix loop attempts" })).toBeInTheDocument();
    expect(row("fix loop attempts")).toHaveTextContent("5item policy");
    expect(row("effort")).toHaveTextContent("lowthis item");
  });

  it("sets one field on the node, merged by the server", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Override fix loop attempts" }));
    await userEvent.type(screen.getByRole("textbox", { name: "fix loop attempts" }), "4{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Override auto-escalate" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "auto-escalate" }), "off");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ node_overrides: { verification: { attempts: 4 } } }, { node_overrides: { verification: { auto_escalate_stuck: false } } }]));
  });

  it("drops one field with a null, in one PATCH", async () => {
    show(fresh({ node_overrides: { verification: { attempts: 4, model: "haiku" } } }));
    expect(row("model")).toHaveTextContent("haikuthis item");
    await userEvent.click(screen.getByRole("button", { name: "Reset model" }));
    await waitFor(() => expect(patches()).toEqual([{ node_overrides: { verification: { model: null } } }]));
  });

  it("is on an exec node's Config before the item starts, and not after", () => {
    usePaneMemory.setState({ pane: { open: true, userCollapsed: false } });
    const mount = (item: ItemDetail) => render(
      <MemoryRouter initialEntries={["/work-items/w1?sel=verification&tab=config"]}>
        <Routes><Route path="/work-items/:id" element={<Workspace item={item} version="1" reload={() => {}} />} /></Routes>
      </MemoryRouter>,
    );
    const first = mount(fresh());
    const pane = () => screen.getByRole("complementary", { name: "verification pane" });
    expect(within(pane()).getByRole("heading", { name: "This node, for this item" })).toBeInTheDocument();
    first.unmount();
    mount(detail({ materialized_chain: FROZEN, current_node_id: "plan" }));
    expect(within(pane()).queryByRole("heading", { name: "This node, for this item" })).toBeNull();
  });
});
