import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WORKERS_FIXTURE } from "../../../../e2e/contract/mocks/fixtures";
import { WorkersScreen } from "./Workers";
import { mountAt } from "./testkit";

const fixture = (name: string) => WORKERS_FIXTURE.find((m) => m.name === name)!;
const open = (workers: unknown[]) => mountAt(<WorkersScreen />, "/settings/workers", "/settings/workers", { "GET /workers": [200, { workers }] });

const LOCAL_ROW = { name: "local", mode: "local", online: true, reason: null, enabled: true, slots_used: 3, capacity: null, repos: null, last_seen: null, kraft_version: "2.5.0", give_up_after: "never" };

describe("Workers (phone)", () => {
  it("lists each machine with whether it is reachable, and offers no control", async () => {
    open([fixture("gpu-box"), fixture("home-server")]);
    expect(await screen.findByText("gpu-box")).toBeInTheDocument();
    expect(screen.getByText("home-server")).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
    expect(screen.getByText(/disconnected: end of stream/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add|edit|remove/i })).toBeNull();
    expect(screen.getByText("Add and edit machines in Settings on a wider screen, or with kraft admin worker in a terminal.")).toBeInTheDocument();
    expect(screen.queryByText(/only lists/)).toBeNull();
  });

  it("reads a disabled machine as disabled, not online", async () => {
    open([{ ...fixture("gpu-box"), enabled: false }]);
    expect(await screen.findByText("gpu-box")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.queryByText("online")).toBeNull();
    expect(screen.getByText(/takes no new steps/)).toBeInTheDocument();
  });

  it("says how to add a machine when there is none", async () => {
    open([]);
    expect(await screen.findByText(/kraft admin worker add NAME --ssh HOST/)).toBeInTheDocument();
  });
});

describe("Workers (phone) with the local worker", () => {
  it("shows the local worker apart, and keeps the add hint when it is the only row", async () => {
    open([LOCAL_ROW]);
    expect(await screen.findByText(/kraft admin worker add NAME --ssh HOST/)).toBeInTheDocument();
    expect(screen.getByText(/this machine · 3 · no limit · 2\.5\.0/)).toBeInTheDocument();
    expect(screen.getByText("0 machines")).toBeInTheDocument();
  });
});
