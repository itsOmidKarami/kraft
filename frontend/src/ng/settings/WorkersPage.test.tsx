import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WORKERS_FIXTURE } from "../../../e2e/contract/mocks/fixtures";
import type { Machine } from "../../types";
import { stubFetch } from "../item/testkit";
import { _resetLaterForTests, UNDO_WINDOW_MS } from "../later";
import { Toaster } from "../ui/Toast";
import { WorkersPage } from "./WorkersPage";
import { fixture, LOCAL_ROW, WithHeader } from "./testkit";

const mount = () =>
  render(
    <WithHeader>
      <MemoryRouter>
        <WorkersPage />
        <Toaster />
      </MemoryRouter>
    </WithHeader>,
  );
afterEach(() => {
  _resetLaterForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const GPU = fixture("gpu-box");
const HOME = fixture("home-server");
const DISABLED = { ...HOME, name: "old-box", enabled: false, groups: ["linux"] } as Machine;
const rows = (...extra: Machine[]) => [200, { workers: [LOCAL_ROW, GPU, HOME, ...extra] }] as [number, unknown];

describe("Settings › Workers", () => {
  it("is one table, this machine first, with each machine's connection, state, running steps, groups, repos and version", async () => {
    stubFetch({ "GET /workers": rows(DISABLED) });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Connects", "State", "Running", "Groups", "Repos", "Version", "Actions"]);
    const [mine, online, offline, off] = within(table).getAllByRole("row").slice(1);
    expect(mine).toHaveTextContent(/^localthis machineonline3 · no limitgpuevery repo2\.5\.0Edit$/);
    expect(online).toHaveTextContent(/^gpu-boxover sshonline1 of 2gpu, linuxkraft2\.4\.0Edit$/);
    expect(offline).toHaveTextContent(/^home-serverdials inoffline\s*disconnected: end of stream0 of 1—every repo2\.4\.0Edit$/);
    expect(off).toHaveTextContent(/^old-boxdials indisabledoffline: disconnected: end of stream/);
    expect(screen.getByText("4 machines · 1 disabled")).toBeInTheDocument();
  });

  it("shows offline in the neutral tone, not red, and keeps green for online", async () => {
    stubFetch({ "GET /workers": rows() });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    expect(within(table).getByText("offline")).toHaveClass("is-muted");
    expect(within(table).getAllByText("online")[0]).toHaveClass("is-ok");
    expect(within(table).queryByText("offline")).not.toHaveClass("is-bad");
  });

  it("shows an unknown version and an empty Groups cell as a muted dash, and right-aligns Running", async () => {
    stubFetch({ "GET /workers": rows({ ...HOME, name: "fresh-box", kraft_version: null } as Machine) });
    mount();
    const row = (await screen.findByText("fresh-box")).closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    for (const i of [4, 6]) expect(cells[i].querySelector(".set-wk-dash")).toHaveTextContent("—");
    expect(cells[3]).toHaveClass("is-num");
    expect(screen.getByRole("columnheader", { name: "Running" })).toHaveClass("is-num");
  });

  it("dims a disabled row's name, connects, running, groups, repos and version, but not its Edit button", async () => {
    stubFetch({ "GET /workers": rows(DISABLED) });
    mount();
    const row = (await screen.findByText("old-box")).closest("tr")!;
    expect(row).toHaveClass("is-off");
    const dim = [...row.querySelectorAll("td.is-dim")].map((c) => c.textContent);
    expect(dim).toEqual(["old-box", "dials in", "0 of 1", "linux", "every repo", "2.4.0"]);
    expect(within(row).getByRole("button", { name: "Edit old-box" }).closest("td")).not.toHaveClass("is-dim");
    expect(screen.getByText("gpu-box").closest("tr")).not.toHaveClass("is-off");
  });

  describe("the demo's fixture", () => {
    it("has this machine's worker first, with no holders, and a disabled machine", () => {
      const [first, ...rest] = WORKERS_FIXTURE as Machine[];
      expect(first).toMatchObject({ name: "local", mode: "local", holders: [] });
      expect(first.remove_blocked).toMatch(/cannot be removed/);
      expect(rest.filter((m) => !m.enabled).map((m) => m.name)).toEqual(["build-box"]);
      expect(rest.map((m) => m.mode)).toEqual(["ssh", "dial-in", "ssh"]);
    });
  });

  it("says a disabled machine takes no new steps, in words next to its badge", async () => {
    stubFetch({ "GET /workers": rows({ ...GPU, name: "idle-box", enabled: false } as Machine) });
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
    const note = screen.getByText(/The terminal does the same with/);
    expect(note).toHaveTextContent("The terminal does the same with kraft admin worker, and both write workers.yaml.");
    expect([...note.querySelectorAll("code")].map((c) => c.textContent)).toEqual(["kraft admin worker", "workers.yaml"]);
    expect(note.closest("section")).toBeNull();
  });

  it("says when the machine's own limit is the one in use", async () => {
    const limited = { ...GPU, capacity: 1, capacity_source: "machine", machine_capacity: 1 } as Machine;
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
      "POST /workers": [201, { worker: { ...HOME, name: "new-box" }, token: "t0k", serve_command: "kraft admin worker serve --server http://h:1 --token t0k", note: "" }],
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

  describe("removing a machine", () => {
    const FREE = { ...HOME } as Machine;
    const deletes = (calls: ReturnType<typeof stubFetch>) => calls.filter((c) => c.method === "DELETE");
    /** The page with `home-server` up for removal: its Edit dialog open, then Remove machine pressed. */
    const removeHome = async (answers: Parameters<typeof stubFetch>[0] = {}) => {
      const calls = stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, FREE] }], "DELETE /workers/home-server": [200, { removed: "home-server" }], ...answers });
      mount();
      await screen.findByRole("table", { name: "Machines" });
      fireEvent.click(screen.getByRole("button", { name: "Edit home-server" }));
      await screen.findByRole("switch", { name: "Takes new steps" });
      vi.useFakeTimers();
      fireEvent.click(screen.getByRole("button", { name: "Remove machine" }));
      return calls;
    };
    const fetchSpy = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;

    it("takes the row off at once, says Removed with Undo, and sends the DELETE when the window ends", async () => {
      const calls = await removeHome();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.queryByRole("button", { name: "Edit home-server" })).toBeNull();
      expect(screen.getByText("Removed home-server")).toBeInTheDocument();
      expect(screen.getByText("1 machine")).toBeInTheDocument();
      await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS - 1));
      expect(deletes(calls)).toHaveLength(0);
      await act(async () => vi.advanceTimersByTime(1));
      expect(deletes(calls)).toHaveLength(1);
    });

    it("Undo inside the window brings the row back and sends nothing", async () => {
      const calls = await removeHome();
      await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS - 100));
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      expect(screen.getByRole("button", { name: "Edit home-server" })).toBeInTheDocument();
      await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS * 2));
      expect(deletes(calls)).toHaveLength(0);
    });

    it("leaving the page inside the window sends the DELETE at once with keepalive; an Undo after that is too late", async () => {
      const calls = await removeHome();
      await act(async () => void window.dispatchEvent(new Event("pagehide")));
      expect(deletes(calls)).toHaveLength(1);
      expect(fetchSpy().find((c) => c[1]?.method === "DELETE")![1].keepalive).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      expect(screen.getByText("Too late to undo: home-server was removed.")).toBeInTheDocument();
      await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS * 2));
      expect(deletes(calls)).toHaveLength(1);
    });

    it("a removal the server refuses when it is sent puts the row back and shows the server's reason", async () => {
      await removeHome({ "DELETE /workers/home-server": [409, { detail: "home-server is running 1 step of Fix it. Disable it so nothing new starts." }] });
      await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS));
      expect(screen.getByRole("button", { name: "Edit home-server" })).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent("home-server is running 1 step of Fix it. Disable it so nothing new starts.");
    });

    it("keeps the row off the page across a re-read inside the window", async () => {
      const calls = await removeHome({ "PATCH /workers/local": [200, { worker: LOCAL_ROW }] });
      fireEvent.click(screen.getByRole("button", { name: "Edit local" }));
      await act(async () => {});
      fireEvent.change(screen.getByRole("spinbutton", { name: /Steps at once/ }), { target: { value: "4" } });
      const reads = () => calls.filter((c) => c.path === "/workers" && c.method === "GET").length;
      const before = reads();
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await act(async () => {});
      expect(reads()).toBe(before + 1);
      expect(screen.queryByRole("button", { name: "Edit home-server" })).toBeNull();
    });
  });
});
