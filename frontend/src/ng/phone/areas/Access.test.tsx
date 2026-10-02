import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { Access, AuthSession } from "../../../types";
import { useApply } from "../../apply/store";
import { AccessScreen, portProblem } from "./Access";
import { mountAt } from "./testkit";

const LAN: Access = { bind: "0.0.0.0", port: 8765, session_expiry_days: 7, password_set: true, auth_required: true, allowed_hosts: ["localhost", "kraft.local"] };
const SESSIONS: AuthSession[] = [
  { id: "s1", label: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0", ip: "192.168.1.20", created_at: "2026-09-30T08:00:00Z", last_seen_at: "2026-09-30T09:00:00Z", expires_at: "2026-10-07T08:00:00Z", current: true },
  { id: "s2", label: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1", ip: "192.168.1.31", created_at: "2026-09-30T08:00:00Z", last_seen_at: "2026-09-30T06:00:00Z", expires_at: "2026-10-05T08:00:00Z", current: false },
];
const PORT_ITEM = { id: "access.port", file: "access.yaml", text: "port changes from 8765 to 9000" };

function setup(access: Partial<Access> = {}, running: { bind?: string; port?: number } = {}) {
  const served = { ...LAN, ...access };
  vi.spyOn(api, "getAccess").mockResolvedValue(served);
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], bind: running.bind ?? served.bind, port: running.port ?? served.port });
  vi.spyOn(api, "getAuthSessions").mockResolvedValue({ sessions: SESSIONS });
  const put = vi.spyOn(api, "putAccess").mockImplementation(async (body) => ({ ...served, ...body }) as Access);
  mountAt(<AccessScreen />, "/settings/access", "/settings/access");
  return put;
}
// A case may swap a store action for a stub; each case gets the store, actions included, back as it found it.
let saved: ReturnType<typeof useApply.getState>;
beforeEach(() => {
  saved = useApply.getState();
  useApply.setState({ restart: [], reload: [], managed: true, loaded: true, phase: "idle", confirming: false, error: null });
});
afterEach(() => {
  useApply.setState(saved, true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("portProblem", () => {
  it.each([["8765", null], ["1024", null], ["65535", null], ["80", "Use a port from 1024 to 65535."], ["65536", "Use a port from 1024 to 65535."], ["88a", "Digits only."], ["", "Digits only."]])("%s -> %s", (t, p) => expect(portProblem(t)).toBe(p));
});

describe("Access (O.3)", () => {
  it("on a LAN bind shows Reach, Port, hosts, password, expiry and the sessions", async () => {
    setup();
    expect(await screen.findByRole("switch", { name: /Local network/ })).toBeChecked();
    expect(screen.getByRole("switch", { name: /This machine only/ })).not.toBeChecked();
    expect(screen.getByRole("button", { name: /^port/ })).toHaveTextContent("8765");
    expect(screen.getByRole("button", { name: /^hosts/ })).toHaveTextContent("localhost, kraft.local");
    expect(screen.getByRole("button", { name: /^password/ })).toHaveTextContent("•••••••• set");
    expect(screen.getByRole("button", { name: /^session expiry/ })).toHaveTextContent("7 days");
    expect(screen.getByRole("button", { name: /^Mac/ })).toHaveTextContent("current");
    expect(screen.getByRole("button", { name: /^iPhone/ })).toHaveTextContent("192.168.1.31");
  });

  it("Reach says 0.0.0.0 listens on every network and points to Remote access, never that Kraft cannot bind publicly", async () => {
    setup();
    await screen.findByRole("switch", { name: /Local network/ });
    expect(screen.getByText(/0\.0\.0\.0 listens on every network this machine is on\. .*prefer a Tailscale address: see Remote access/)).toBeInTheDocument();
    expect(screen.queryByText(/never binds publicly/)).toBeNull();
  });

  it("on loopback with no password, Local network asks for one and sends it with the bind in one save", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: false, auth_required: false });
    await userEvent.click(await screen.findByRole("switch", { name: /Local network/ }));
    expect(put).not.toHaveBeenCalled();
    const box = screen.getByLabelText("Set a password", { selector: "input" });
    expect(box).toHaveAttribute("type", "password");
    await userEvent.type(box, "hunter2{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", password: "hunter2" }));
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("on loopback with a password set, Local network switches at once", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: true, auth_required: false });
    await userEvent.click(await screen.findByRole("switch", { name: /Local network/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0" }));
  });

  it("on loopback with a password set and no hosts, Local network asks for the phone's host first, pre-filled with this machine's address", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: true, auth_required: false, allowed_hosts: [], lan_hosts: ["192.168.1.20", "mybox.local"] });
    await userEvent.click(await screen.findByRole("switch", { name: /Local network/ }));
    expect(put).not.toHaveBeenCalled();
    const box = screen.getByLabelText("Phone's host", { selector: "input" });
    expect(box).toHaveValue("192.168.1.20");
    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", allowed_hosts: ["192.168.1.20"] }));
  });

  it("on loopback with no password and no hosts, saves this machine's address with the password and the bind", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: false, auth_required: false, allowed_hosts: [], lan_hosts: ["192.168.1.20"] });
    await userEvent.click(await screen.findByRole("switch", { name: /Local network/ }));
    expect(screen.getByText(/192\.168\.1\.20, this machine's address, goes on Allowed hosts/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Set a password", { selector: "input" }), "hunter2{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", password: "hunter2", allowed_hosts: ["192.168.1.20"] }));
  });

  it("on loopback says the hosts, password and sessions are not used", async () => {
    setup({ bind: "127.0.0.1", auth_required: false });
    expect(await screen.findByRole("region", { name: "Not used on 127.0.0.1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^hosts/ })).toBeNull();
  });

  it("each control sends only its own key", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^session expiry/ }));
    await userEvent.click(screen.getByRole("radio", { name: "30 days" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ session_expiry_days: 30 }));
    await userEvent.click(screen.getByRole("switch", { name: /This machine only/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ bind: "127.0.0.1" }));
    expect(put).toHaveBeenCalledTimes(2);
  });

  it("the port is checked before any call, then saved; a refusal stays in the sheet", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^port/ }));
    const box = screen.getByLabelText("Port", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "80{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Use a port from 1024 to 65535.");
    expect(put).not.toHaveBeenCalled();
    put.mockRejectedValueOnce(new Error("port 9000 is in use"));
    await userEvent.clear(box);
    await userEvent.type(box, "9000{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("port 9000 is in use");
    await userEvent.clear(box);
    await userEvent.type(box, "9001{Enter}");
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ port: 9001 }));
  });

  it("the Restart needed group shows only with an access.* item, with Undo saving the running values back", async () => {
    const put = setup({ port: 9000 }, { port: 8765 });
    expect(screen.queryByRole("region", { name: "Restart needed" })).toBeNull();
    useApply.setState({ restart: [{ id: "policy.max_concurrent", file: "policy.yaml", text: "max active items changes" }] });
    await screen.findByRole("button", { name: /^port/ });
    expect(screen.queryByRole("region", { name: "Restart needed" })).toBeNull();
    useApply.setState({ restart: [PORT_ITEM] });
    const group = await screen.findByRole("region", { name: "Restart needed" });
    expect(group).toHaveTextContent("port changes from 8765 to 9000");
    await userEvent.click(within(group).getByRole("button", { name: /^Undo/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", port: 8765 }));
  });

  it("Restart Kraft is offered only when managed, and asks first", async () => {
    setup();
    useApply.setState({ restart: [PORT_ITEM], managed: false });
    const group = await screen.findByRole("region", { name: "Restart needed" });
    expect(within(group).queryByRole("button", { name: /Restart Kraft/ })).toBeNull();
    expect(group).toHaveTextContent(/kraft admin restart; .*stop it there with Ctrl-C and start it again the same way/);
    const run = vi.fn();
    useApply.setState({ managed: true, askRestart: async () => {}, runRestart: run as never });
    await userEvent.click(await within(group).findByRole("button", { name: /Restart Kraft/ }));
    expect(run).not.toHaveBeenCalled();
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Restart Kraft" }));
    expect(run).toHaveBeenCalledTimes(1);
  });

  it.each([
    [0, /No item is active, so no agent is stopped\./],
    [2, /2 items are active\. Restarting stops their agents; each stops as failed and needs Retry afterwards\./],
  ])("the Restart sheet says what a restart does to %i active items", async (active, said) => {
    setup();
    useApply.setState({ restart: [PORT_ITEM], managed: true, active, askRestart: async () => {} });
    await userEvent.click(await screen.findByRole("button", { name: /Restart Kraft/ }));
    const sheet = await screen.findByRole("dialog");
    expect(sheet).toHaveTextContent(said);
    expect(sheet).not.toHaveTextContent("Running work is interrupted");
  });

  it("opens the Restart sheet only once the active count is in, so it never shows the wrong text first", async () => {
    setup();
    let counted!: () => void;
    useApply.setState({ restart: [PORT_ITEM], managed: true, askRestart: () => new Promise<void>((r) => { counted = () => { useApply.setState({ active: 0 }); r(); }; }) });
    await userEvent.click(await screen.findByRole("button", { name: /Restart Kraft/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    counted();
    expect(await screen.findByRole("dialog")).toHaveTextContent("No item is active, so no agent is stopped.");
  });

  it("says when the environment wins over the saved port", async () => {
    setup({ port: 9000 }, { port: 8765 });
    expect(await screen.findByRole("button", { name: /^port/ })).toHaveTextContent("Set by the environment: running on 8765");
  });

  it("hosts: a sheet lists them, tap removes one, the connected host cannot be removed, Add takes a field", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^hosts/ }));
    await userEvent.click(screen.getByRole("button", { name: "Remove kraft.local" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ allowed_hosts: ["localhost"] }));
    await userEvent.click(await screen.findByRole("button", { name: /^hosts/ }));
    await userEvent.click(screen.getByRole("button", { name: "Remove localhost" }));
    expect(put).toHaveBeenCalledTimes(1);
    await userEvent.click(await screen.findByRole("button", { name: /^hosts/ }));
    await userEvent.click(screen.getByRole("button", { name: /Add a host/ }));
    await userEvent.type(screen.getByLabelText("Add a host", { selector: "input" }), "nas.lan{Enter}");
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ allowed_hosts: ["localhost", "nas.lan"] }));
  });

  it("with no hosts, says only other devices are refused and pre-fills Add a host with this machine's address", async () => {
    const put = setup({ allowed_hosts: [], lan_hosts: ["192.168.1.20"] });
    expect(await screen.findByText(/An empty list refuses every other device \(403\)\. This machine still gets in at 127\.0\.0\.1\. Add 192\.168\.1\.20/)).toBeInTheDocument();
    expect(screen.getByText("Auth is off on a 127.0.0.1 bind. Once Kraft listens on the network, every browser signs in, this machine's too.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^hosts/ }));
    await userEvent.click(screen.getByRole("button", { name: /Add a host/ }));
    expect(screen.getByLabelText("Add a host", { selector: "input" })).toHaveValue("192.168.1.20");
    await userEvent.type(screen.getByLabelText("Add a host", { selector: "input" }), "{Enter}");
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ allowed_hosts: ["192.168.1.20"] }));
  });

  it("the password is typed into a masked field, saved by the sheet, and warns that sessions end", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^password/ }));
    expect(screen.getByText(/Every session is signed out, this one too\./)).toBeInTheDocument();
    const box = screen.getByLabelText("Change password", { selector: "input" });
    expect(box).toHaveAttribute("type", "password");
    await userEvent.type(box, "hunter2hunter2{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ password: "hunter2hunter2" }));
    expect(document.body.textContent).not.toContain("hunter2hunter2");
  });

  it("revoking another session asks first, then deletes it", async () => {
    setup();
    const del = vi.spyOn(api, "revokeSession").mockResolvedValue(undefined as never);
    await userEvent.click(await screen.findByRole("button", { name: /^iPhone/ }));
    const dialog = await screen.findByRole("dialog");
    expect(del).not.toHaveBeenCalled();
    expect(dialog).toHaveTextContent(/iPhone · .+ has to sign in again\./);
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("s2"));
  });

  it("signing out the current session says you will be asked for the password again", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /^Mac/ }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("You will be asked for the password again.");
  });
});
