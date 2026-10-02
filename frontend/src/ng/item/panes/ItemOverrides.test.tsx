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
  it("shows what the chain's agents run on, and sets the item's model alone, the server keeping its effort", async () => {
    const reload = vi.fn();
    render(<ItemAgentRows item={fresh({ agent_overrides: { effort: "high" } })} reload={reload} />);
    expect(await screen.findByText("each task's own")).toBeInTheDocument();
    expect(row("effort")).toHaveTextContent("highthis item");
    await userEvent.click(screen.getByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("combobox", { name: "model" }), "opus-4{Enter}");
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { model: "opus-4" } }]));
    expect(reload).toHaveBeenCalled();
  });

  it("drops one field on ↺ with a null, and says why the server refused on that row", async () => {
    calls = stubFetch({ ...answers, "PATCH /work-items/w1": [422, { detail: "agent_overrides 'effort' must be one of [...]" }] });
    render(<ItemAgentRows item={fresh({ agent_overrides: { model: "opus", effort: "high" } })} reload={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reset effort" }));
    await waitFor(() => expect(patches()).toEqual([{ agent_overrides: { effort: null } }]));
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
