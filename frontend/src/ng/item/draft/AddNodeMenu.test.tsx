import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetLibrary } from "../../templates/useLibrary";
import { detail, inShell, stubFetch, V1, type Call } from "../testkit";
import { AddNodeMenu } from "./AddNodeMenu";
import { ItemDraftProvider } from "./context";
import type { DraftView } from "./types";

const library = { components: [
  { kind: "nodes", name: "security", definition: { steps: [{ id: "scan", tasks: [{ id: "sast" }] }] } },
  { kind: "nodes", name: "docs", definition: { steps: [{ id: "write", tasks: [{ id: "doc" }] }] } },
  { kind: "tasks", name: "lint", definition: {} },
] };
const scan = { id: "security", kind: "exec" as const, gate_after: null, tasks: ["security.scan.sast"], steps: [["security.scan.sast"]] };
const after = (ops: unknown[], nodes = [...V1.slice(0, 3), scan, V1[3]]): [number, DraftView] => [200, { ops, problems: [], checks: { budget: { spent_usd: 0, cap_usd: null } }, nodes, base_seq: 1, updated_at: null } as DraftView];
const empty = (): [number, DraftView] => [200, { ops: [], problems: [], checks: { budget: { spent_usd: 0, cap_usd: null } }, nodes: V1, base_seq: null, updated_at: null }];

let seam: HTMLButtonElement;
let calls: Call[];
function Host() {
  const [shown, setShown] = useState(true);
  return shown ? <AddNodeMenu at={3} seam={seam} onClose={() => setShown(false)} /> : null;
}
function Where() {
  const l = useLocation();
  return <output data-testid="at">{l.search}</output>;
}
const open = async (answers: Record<string, [number, unknown]> = {}) => {
  calls = stubFetch({ "GET /templates/library": [200, library], "GET /work-items/w1/draft": empty(), ...answers });
  seam = document.body.appendChild(document.createElement("button"));
  inShell(<ItemDraftProvider item={detail()} reload={() => {}}><Host /><Where /></ItemDraftProvider>);
  await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/draft")).toBe(true));
  return screen.findByRole("option", { name: /security/ });
};
beforeEach(() => resetLibrary());
afterEach(() => { vi.unstubAllGlobals(); seam?.remove(); });

describe("AddNodeMenu", () => {
  it("lists the library's nodes only, filtered by what is typed", async () => {
    await open();
    expect(screen.queryByRole("option", { name: /lint/ })).toBeNull();
    await userEvent.type(screen.getByRole("textbox", { name: "Search library nodes" }), "doc");
    expect(screen.queryByRole("option", { name: /security/ })).toBeNull();
    expect(screen.getByRole("option", { name: /docs/ })).toBeInTheDocument();
  });

  it("names the node the new one runs after", async () => {
    await open();
    expect(screen.getByText(/Runs after verification\./)).toBeInTheDocument();
  });

  it("fills the id with the library id, and Create sends add_node after the seam's node", async () => {
    const security = await open({ "PUT /work-items/w1/draft": after([{ op: "add_node", after: "verification", node: { id: "security", extends: "security" }, passed: false }]) });
    await userEvent.click(security);
    const id = screen.getByRole("textbox", { name: "Node id" });
    expect(id).toHaveValue("security");
    await waitFor(() => expect(id).toHaveFocus());
    await userEvent.clear(id);
    await userEvent.type(id, "security_scan");
    await userEvent.click(screen.getByRole("button", { name: "Create & open →" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ ops: [{ op: "add_node", after: "verification", node: { id: "security_scan", extends: "security" } }] }));
  });

  it("opens the new node's pane once it is on the canvas", async () => {
    const security = await open({ "PUT /work-items/w1/draft": after([{ op: "add_node", after: "verification", node: { id: "security", extends: "security" }, passed: false }]) });
    await userEvent.click(security);
    await userEvent.click(screen.getByRole("button", { name: "Create & open →" }));
    await waitFor(() => expect(screen.getByTestId("at")).toHaveTextContent("sel=security"));
  });

  it("selects the chain instead when the server could not place the node", async () => {
    const security = await open({ "PUT /work-items/w1/draft": after([{ op: "add_node", after: "verification", node: { id: "security", extends: "security" }, passed: false }], V1) });
    await userEvent.click(security);
    await userEvent.click(screen.getByRole("button", { name: "Create & open →" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(screen.getByTestId("at")).toHaveTextContent(/^$/);
  });

  it("refuses a taken id before sending", async () => {
    const security = await open();
    await userEvent.click(security);
    const id = screen.getByRole("textbox", { name: "Node id" });
    await waitFor(() => expect(id).toHaveFocus());
    await userEvent.clear(id);
    await userEvent.type(id, "plan");
    expect(screen.getByRole("button", { name: "Create & open →" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("taken");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("shows the server's refusal under the field", async () => {
    const security = await open({ "PUT /work-items/w1/draft": [422, { detail: "ops.0.node: no such library node" }] });
    await userEvent.click(security);
    await userEvent.click(screen.getByRole("button", { name: "Create & open →" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such library node");
  });

  it("returns focus to the seam on Escape", async () => {
    await open();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(document.activeElement).toBe(seam));
  });
});
