import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WORKERS_FIXTURE } from "../../../e2e/contract/mocks/fixtures";
import type { Machine } from "../../types";
import { stubFetch } from "../item/testkit";
import { WorkersPage } from "./WorkersPage";
import { LOCAL_ROW, WithHeader } from "./testkit";

const mount = () =>
  render(
    <WithHeader>
      <MemoryRouter>
        <WorkersPage />
      </MemoryRouter>
    </WithHeader>,
  );
afterEach(() => vi.unstubAllGlobals());

const DISABLED = { ...WORKERS_FIXTURE[1], name: "old-box", enabled: false } as Machine;
const rows = (...extra: Machine[]) => [200, { workers: [LOCAL_ROW, ...(WORKERS_FIXTURE as Machine[]), ...extra] }] as [number, unknown];

describe("Settings › Workers", () => {
  it("is one table, this machine first, with each machine's connection, state, running steps, groups, repos and version", async () => {
    stubFetch({ "GET /workers": rows(DISABLED) });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Connects", "State", "Running", "Groups", "Repos", "Version", "Actions"]);
    const [mine, online, offline, off] = within(table).getAllByRole("row").slice(1);
    expect(mine).toHaveTextContent(/^localthis machineonline3 · no limitgpuevery repo2\.5\.0Edit$/);
    expect(online).toHaveTextContent(/^gpu-boxover sshonline1 of 2gpu, linuxkraft2\.4\.0Edit$/);
    expect(offline).toHaveTextContent(/^home-serverdials inoffline\s*disconnected: end of stream0 of 1noneevery repo2\.4\.0Edit$/);
    expect(off).toHaveTextContent(/^old-boxdials indisabledoffline: disconnected: end of stream/);
    expect(screen.getByText("4 machines · 1 disabled")).toBeInTheDocument();
  });

  it("says a disabled machine takes no new steps, in words next to its badge", async () => {
    stubFetch({ "GET /workers": rows({ ...WORKERS_FIXTURE[0], name: "idle-box", enabled: false } as Machine) });
    mount();
    const row = (await screen.findByText("idle-box")).closest("tr")!;
    expect(within(row).getByText("disabled")).toHaveClass("badge");
    expect(row).toHaveTextContent("takes no new steps");
  });

  it("has Add machine at the head of the block, an Edit button on every row, and the note about the terminal", async () => {
    stubFetch({ "GET /workers": rows() });
    mount();
    await screen.findByRole("table", { name: "Machines" });
    expect(screen.getByRole("button", { name: "Add machine" })).toBeInTheDocument();
    for (const name of ["local", "gpu-box", "home-server"]) expect(screen.getByRole("button", { name: `Edit ${name}` })).toBeInTheDocument();
    expect(screen.getByText(/The terminal does the same with kraft admin worker, and both write workers\.yaml\./)).toBeInTheDocument();
  });

  it("says when the machine's own limit is the one in use", async () => {
    const limited = { ...WORKERS_FIXTURE[0], capacity: 1, capacity_source: "machine", machine_capacity: 1 } as Machine;
    stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, limited] }] });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    expect(within(table).getAllByRole("row")[2]).toHaveTextContent("1 of 1 · machine's limit");
  });

  it("with no machine at all, says to add one; the aside counts none", async () => {
    stubFetch({ "GET /workers": [200, { workers: [] satisfies Machine[] }] });
    mount();
    expect(await screen.findByText(/No machines yet/)).toBeInTheDocument();
    expect(screen.getByText("0 machines")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("button", { name: "Add machine" })).toBeInTheDocument();
  });

  it("reads the list again after a machine is added", async () => {
    const calls = stubFetch({
      "GET /workers": rows(),
      "POST /workers": [201, { worker: { ...WORKERS_FIXTURE[1], name: "new-box" }, token: "t0k", serve_command: "kraft admin worker serve --server http://h:1 --token t0k", note: "" }],
    });
    mount();
    await screen.findByRole("table", { name: "Machines" });
    fireEvent.click(screen.getByRole("button", { name: "Add machine" }));
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), { target: { value: "new-box" } });
    fireEvent.click(screen.getByRole("radio", { name: "It dials in to Kraft" }));
    const gets = () => calls.filter((c) => c.path === "/workers" && c.method === "GET").length;
    const before = gets();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add machine" }));
    expect(await screen.findByText("new-box is added")).toBeInTheDocument();
    expect(gets()).toBe(before + 1);
  });

  it("shows why the server could not list them", async () => {
    stubFetch({ "GET /workers": [403, { detail: "a Kraft session cannot manage workers" }] });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("a Kraft session cannot manage workers");
  });
});
