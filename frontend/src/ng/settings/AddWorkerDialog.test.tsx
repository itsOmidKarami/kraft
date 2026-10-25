import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WORKERS_FIXTURE } from "../../../e2e/contract/mocks/fixtures";
import type { Machine } from "../../types";
import { stubFetch } from "../item/testkit";
import { AddWorkerDialog } from "./AddWorkerDialog";

const SERVE = "kraft admin worker serve --server http://10.0.0.5:8080 --token abc123";
const ROW = WORKERS_FIXTURE[0] as Machine;
const REPOS = { repos: [{ name: "kraft", path: "/r/kraft" }, { name: "docs", path: "/r/docs" }] };

const mount = (answers: Parameters<typeof stubFetch>[0] = {}) => {
  const calls = stubFetch({ "GET /repos": [200, REPOS], ...answers });
  const props = { onClose: vi.fn(), onAdded: vi.fn(), onEdit: vi.fn() };
  render(<AddWorkerDialog {...props} />);
  return { calls, posts: () => calls.filter((c) => c.method === "POST"), ...props };
};
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

afterEach(() => vi.unstubAllGlobals());

describe("AddWorkerDialog", () => {
  it("names the naming rule, offers ssh and dial-in, and takes ssh host only for ssh", () => {
    mount();
    expect(screen.getByText(/Lowercase letters, digits, - or _, 1 to 39 characters\./)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Kraft connects over ssh" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: /ssh host/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "It dials in to Kraft" }));
    expect(screen.queryByRole("textbox", { name: /ssh host/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Add machine" })).toBeDisabled();
  });

  it("holds the dialog while it connects: the button reads Connecting… and Escape does not close it", async () => {
    let answer!: (r: Response) => void;
    const { onClose } = mount();
    const real = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "POST" ? new Promise<Response>((r) => (answer = r)) : real(url, init))));
    type(/^Name/, "gpu-box");
    type(/ssh host/, "gpu-box.lan");
    const go = screen.getByRole("button", { name: "Add and connect" });
    expect(go).toBeEnabled();
    fireEvent.click(go);
    expect(await screen.findByRole("button", { name: "Connecting…" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    answer(new Response(JSON.stringify({ worker: ROW, probe: { connected: true, detail: "connected" } }), { status: 201 }));
    expect(await screen.findByText("gpu-box is added")).toBeInTheDocument();
  });

  it("sends the capacity, give-up time, groups and repos that were filled in", async () => {
    const { posts } = mount({ "POST /workers": [201, { worker: ROW, probe: { connected: true, detail: "connected" } }] });
    type(/^Name/, "gpu-box");
    type(/ssh host/, "gpu-box.lan");
    fireEvent.change(screen.getByRole("spinbutton", { name: /Steps at once/ }), { target: { value: "3" } });
    type(/Give up after/, "30m");
    const groups = screen.getByRole("textbox", { name: "Add to Groups" });
    fireEvent.change(groups, { target: { value: "gpu" } });
    fireEvent.keyDown(groups, { key: "Enter" });
    fireEvent.click(screen.getByRole("radio", { name: "Only some" }));
    const repos = screen.getByRole("combobox", { name: "Add to Repos" });
    await waitFor(() => expect(repos).toBeInTheDocument());
    fireEvent.change(repos, { target: { value: "kraft" } });
    fireEvent.keyDown(repos, { key: "Enter" });
    press("Add and connect");
    await screen.findByText("gpu-box is added");
    expect(posts()).toEqual([{ method: "POST", path: "/workers", body: { name: "gpu-box", ssh: "gpu-box.lan", capacity: 3, give_up_after: "30m", groups: ["gpu"], repos: ["kraft"] } }]);
  });

  it("sends only the name and host when nothing else was set", async () => {
    const { posts, onAdded } = mount({ "POST /workers": [201, { worker: ROW, probe: { connected: true, detail: "connected" } }] });
    type(/^Name/, "gpu-box");
    type(/ssh host/, "gpu-box.lan");
    press("Add and connect");
    await screen.findByText("gpu-box is added");
    expect(posts()[0].body).toEqual({ name: "gpu-box", ssh: "gpu-box.lan" });
    expect(onAdded).toHaveBeenCalledTimes(1);
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("keeps a machine that did not connect: the badge, the ssh error as the server worded it, and the ways on", async () => {
    const { onEdit, onClose } = mount({ "POST /workers": [201, { worker: ROW, probe: { connected: false, detail: "ssh: connect to host gpu-box.lan port 22: Connection refused" } }] });
    type(/^Name/, "gpu-box");
    type(/ssh host/, "gpu-box.lan");
    press("Add and connect");
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByText("not connected")).toHaveClass("badge");
    expect(within(dlg).getByText("ssh: connect to host gpu-box.lan port 22: Connection refused")).toBeInTheDocument();
    expect(dlg).toHaveTextContent("Kraft keeps trying");
    expect(dlg).toHaveTextContent("kraft admin worker check");
    press("Edit gpu-box");
    expect(onClose).toHaveBeenCalled();
    expect(onEdit).toHaveBeenCalledWith("gpu-box");
  });

  it("a dial-in machine: its name, the command on three lines, Copy, and a shown-once badge", async () => {
    const write = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText: write } });
    const { posts } = mount({ "POST /workers": [201, { worker: { ...ROW, name: "home-server", mode: "dial-in" }, token: "abc123", serve_command: SERVE, note: "Replace the address." }] });
    type(/^Name/, "home-server");
    fireEvent.click(screen.getByRole("radio", { name: "It dials in to Kraft" }));
    press("Add machine");
    expect(await screen.findByText("home-server is added")).toBeInTheDocument();
    expect(posts()[0].body).toEqual({ name: "home-server" });
    expect(screen.getByText("shown once")).toHaveClass("badge");
    expect(screen.getByLabelText("Command to run on home-server").textContent).toBe("kraft admin worker serve \\\n  --server http://10.0.0.5:8080 \\\n  --token abc123");
    expect(screen.getByText("Replace the address.")).toBeInTheDocument();
    press("Copy");
    expect(write).toHaveBeenCalledWith(SERVE);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("shows the server's refusal above the form and keeps what was typed", async () => {
    mount({ "POST /workers": [422, { detail: "worker name 'Bad' must be lowercase letters, digits, - or _, 1-39 characters" }] });
    type(/^Name/, "Bad");
    type(/ssh host/, "h");
    press("Add and connect");
    expect(await screen.findByRole("alert")).toHaveTextContent("worker name 'Bad' must be lowercase letters, digits, - or _, 1-39 characters");
    expect(screen.getByRole("textbox", { name: /^Name/ })).toHaveValue("Bad");
    expect(screen.getByRole("button", { name: "Add and connect" })).toBeEnabled();
  });
});
