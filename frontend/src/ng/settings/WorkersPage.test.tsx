import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WORKERS_FIXTURE } from "../../../e2e/contract/mocks/fixtures";
import type { Machine } from "../../types";
import { stubFetch } from "../item/testkit";
import { WorkersPage } from "./WorkersPage";
import { WithHeader } from "./testkit";

const mount = () =>
  render(
    <WithHeader>
      <MemoryRouter>
        <WorkersPage />
      </MemoryRouter>
    </WithHeader>,
  );
afterEach(() => vi.unstubAllGlobals());

/** `GET /workers` always lists this machine's own worker first. */
const LOCAL_ROW: Machine = { name: "local", mode: "local", online: true, reason: null, slots_used: 3, capacity: null, repos: null, last_seen: "2026-10-24T09:00:00+00:00", kraft_version: "2.5.0", give_up_after: "never" };

describe("Settings › Workers", () => {
  it("lists each machine with its state, slots, repos and what it does when Kraft is gone", async () => {
    stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, ...WORKERS_FIXTURE] }] });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    const [online, offline] = within(table).getAllByRole("row").slice(1);
    expect(online).toHaveTextContent(/^gpu-boxsshonline1 of 2kraft.*2\.4\.0gives up after 2h$/);
    expect(offline).toHaveTextContent(/^home-serverdial-inoffline: disconnected: end of stream0 of 1every repo/);
    expect(offline).toHaveTextContent("never gives up");
  });

  it("says when the machine's own limit is the one in use", async () => {
    const limited = { ...WORKERS_FIXTURE[0], capacity: 1, capacity_source: "machine", machine_capacity: 1 } as Machine;
    stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, limited] }] });
    mount();
    const table = await screen.findByRole("table", { name: "Machines" });
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("1 of 1 · machine's limit");
  });

  it("says how to add a machine when there is none", async () => {
    stubFetch({ "GET /workers": [200, { workers: [] satisfies Machine[] }] });
    mount();
    expect(await screen.findByText(/kraft admin worker add NAME --ssh HOST/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("local-only: keeps the add hint and counts no machines, the local worker standing apart", async () => {
    stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW] }] });
    mount();
    expect(await screen.findByText(/kraft admin worker add NAME --ssh HOST/)).toBeInTheDocument();
    expect(screen.getByText("0 machines")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Machines" })).toBeNull();
    const mine = within(screen.getByRole("table", { name: "This machine" })).getAllByRole("row")[1];
    expect(mine).toHaveTextContent(/^localthis machineonline3 · no limit2\.5\.0$/);
    expect(mine).not.toHaveTextContent(/gives up|never gives up/);
  });

  it("is read-only: no control to add, edit or remove a machine", async () => {
    stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, ...WORKERS_FIXTURE] }] });
    mount();
    await screen.findByRole("table", { name: "Machines" });
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  it("shows why the server could not list them", async () => {
    stubFetch({ "GET /workers": [403, { detail: "a Kraft session cannot manage workers" }] });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("a Kraft session cannot manage workers");
  });
});
