import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Access, AuthSession } from "../../types";
import { useApply } from "../apply/store";
import { AccessPage, portProblem } from "./AccessPage";
import { WithHeader } from "./testkit";

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
  vi.spyOn(api, "getNotify").mockResolvedValue({ enabled: false, url_set: false, base_url: null, events: [], last_test: null });
  const put = vi.spyOn(api, "putAccess").mockImplementation(async (body) => ({ ...served, ...body }) as Access);
  render(<WithHeader><AccessPage /></WithHeader>);
  return put;
}

// A case may swap a store action for a stub; each case gets the store, actions included, back as it found it.
let saved: ReturnType<typeof useApply.getState>;
beforeEach(() => {
  saved = useApply.getState();
  vi.stubGlobal("fetch", () => Promise.reject(new TypeError("down")));
  useApply.setState({ restart: [], reload: [], managed: true, loaded: true, phase: "idle", confirming: false, error: null });
});
afterEach(() => {
  useApply.setState(saved, true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("portProblem", () => {
  it.each([["8765", null], ["1024", null], ["65535", null], ["80", "use 1024–65535"], ["65536", "use 1024–65535"], ["88a", "digits only"], ["", "digits only"]])("%s -> %s", (t, p) => expect(portProblem(t)).toBe(p));
});

describe("ng AccessPage", () => {
  it("sends only the key each control changes", async () => {
    const put = setup();
    await screen.findByRole("heading", { name: "Access" });
    await userEvent.click(screen.getByRole("radio", { name: "30d" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("radio", { name: /This machine only/ }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map((c) => c[0])).toEqual([{ session_expiry_days: 30 }, { bind: "127.0.0.1" }]);
  });

  it("saves the port on Enter, not per keystroke, and refuses one out of range without asking the server", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Port 8765, edit" }));
    const box = screen.getByRole("textbox", { name: "Port" });
    await userEvent.clear(box);
    await userEvent.type(box, "80");
    expect(put).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("use 1024–65535");
    expect(put).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.type(box, "9000{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ port: 9000 }));
  });

  it("leaves the control where it was when the save is refused", async () => {
    const put = setup();
    put.mockRejectedValueOnce(new Error("a password is required to bind off loopback"));
    await userEvent.click(await screen.findByRole("radio", { name: /This machine only/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a password is required");
    expect(screen.getByRole("radio", { name: /Local network/ })).toHaveAttribute("aria-checked", "true");
  });

  it("adds a host and will not remove the one this browser is on", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: "add" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Add a host or IP" }), "10.0.0.5{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ allowed_hosts: ["localhost", "kraft.local", "10.0.0.5"] }));
    await userEvent.click(screen.getByRole("button", { name: "Remove localhost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("can't remove the host you're connected as");
    expect(put).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Remove kraft.local" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ allowed_hosts: ["localhost", "10.0.0.5"] }));
  });

  it("sets a password only on Save and never keeps it", async () => {
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Change" }));
    await userEvent.type(screen.getByLabelText("New password"), "hunter2");
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ password: "hunter2" }));
    expect(screen.queryByLabelText("New password")).toBeNull();
    expect(screen.queryByDisplayValue("hunter2")).toBeNull();
  });

  it("shows the server's restart item, with Undo to the running values and Restart only when managed", async () => {
    useApply.setState({ restart: [PORT_ITEM], managed: true });
    const put = setup({ port: 9000 }, { port: 8765 });
    expect(await screen.findByText("port changes from 8765 to 9000")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", port: 8765 }));
    const askRestart = vi.fn();
    useApply.setState({ askRestart });
    await userEvent.click(screen.getByRole("button", { name: "Restart Kraft" }));
    expect(askRestart).toHaveBeenCalled();
  });

  it.each([
    ["::1", "[::1]"],
    ["192.168.1.5", "192.168.1.5"],
  ])("says where a restart brings Kraft back, an IPv6 bind (%s) in brackets", async (bind, host) => {
    useApply.setState({ restart: [{ id: "access.bind", file: "access.yaml", text: `bind changes from 127.0.0.1 to ${bind}` }] });
    setup({ bind, port: 9000 }, { bind: "127.0.0.1" });
    expect(await screen.findByText(new RegExp(`^Kraft comes back at`))).toHaveTextContent(`Kraft comes back at ${location.protocol}//${host}:9000 and`);
  });

  it("offers no Restart for a server started from a terminal", async () => {
    useApply.setState({ restart: [PORT_ITEM], managed: false });
    setup({ port: 9000 }, { port: 8765 });
    await screen.findByText("port changes from 8765 to 9000");
    expect(screen.queryByRole("button", { name: "Restart Kraft" })).toBeNull();
    expect(screen.getByText(/cannot restart itself/)).toHaveTextContent(/kraft admin restart; .*stop it there with Ctrl-C and start it again the same way/);
  });

  it("says an environment setting wins when the saved port differs from the running one and no item is pending", async () => {
    setup({ port: 9000 }, { port: 8765 });
    expect(await screen.findByText(/KRAFT_PORT environment setting wins/)).toBeInTheDocument();
  });

  it("does not claim an override while the port restart is pending", async () => {
    useApply.setState({ restart: [PORT_ITEM] });
    setup({ port: 9000 }, { port: 8765 });
    await screen.findByText("port changes from 8765 to 9000");
    expect(screen.queryByText(/environment setting wins/)).toBeNull();
  });

  it("confirms before revoking, and offers Sign out for the current session", async () => {
    setup();
    const revoke = vi.spyOn(api, "revokeSession").mockResolvedValue(undefined as never);
    expect(await screen.findByRole("button", { name: /Sign out Mac/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Revoke iPhone/ }));
    expect(revoke).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "Revoke this session?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("s2"));
  });

  it("asks for the phone's host with the password when switching to Local network, pre-filled with this machine's address, and saves all three at once", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: false, allowed_hosts: [], lan_hosts: ["192.168.1.20", "mybox"] });
    await userEvent.click(await screen.findByRole("radio", { name: /Local network/ }));
    const host = screen.getByRole("textbox", { name: "Host or IP the phone will use" });
    expect(host).toHaveValue("192.168.1.20");
    expect(screen.getByText(/This machine is 192.168.1.20 or mybox on the network/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Password for the local network"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Set and switch" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", password: "hunter2", allowed_hosts: ["192.168.1.20"] }));
  });

  it("asks for the host alone when a password is set but the list is empty, and switches at once when both are there", async () => {
    const put = setup({ bind: "127.0.0.1", allowed_hosts: [], lan_hosts: ["192.168.1.20"] });
    await userEvent.click(await screen.findByRole("radio", { name: /Local network/ }));
    expect(screen.queryByLabelText("Password for the local network")).toBeNull();
    await userEvent.clear(screen.getByRole("textbox", { name: "Host or IP the phone will use" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Host or IP the phone will use" }), "kraft.lan{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", allowed_hosts: ["kraft.lan"] }));
  });

  it("warns that an empty list refuses every other device, not this machine, and offers this machine's address", async () => {
    const put = setup({ allowed_hosts: [], lan_hosts: ["192.168.1.20"] });
    expect(await screen.findByText(/An empty list refuses every other device \(403\)\. This machine still gets in at 127\.0\.0\.1\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "add 192.168.1.20" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ allowed_hosts: ["192.168.1.20"] }));
  });

  it("says auth is off only on a 127.0.0.1 bind, not for this machine's browser everywhere", async () => {
    setup();
    expect(await screen.findByText("Auth is off on a 127.0.0.1 bind. Once Kraft listens on the network, every browser signs in, this machine's too.")).toBeInTheDocument();
    expect(screen.queryByText(/Auth is off on localhost/)).toBeNull();
  });

  it("names the port an environment setting keeps, not the saved one, where a restart brings Kraft back", async () => {
    useApply.setState({ restart: [{ id: "access.bind", file: "access.yaml", text: "bind changes from 127.0.0.1 to 0.0.0.0" }] });
    setup({ port: 8765 }, { bind: "127.0.0.1", port: 8771 });
    expect(await screen.findByText(/^Kraft comes back at/)).toHaveTextContent(`Kraft comes back at ${location.protocol}//${location.hostname}:8771 and`);
    expect(screen.getByRole("radio", { name: /This machine only/ })).toHaveTextContent("127.0.0.1:8771");
  });

  it("on a loopback bind says what is not used, and lists no sessions", async () => {
    setup({ bind: "127.0.0.1" });
    expect(await screen.findByText("Not used on 127.0.0.1")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sessions" })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /Local network/ }));
  });

  it("shows access.yaml from the page's state under the header's YAML button, the password never", async () => {
    setup();
    await screen.findByRole("heading", { name: "Access" });
    expect(screen.queryByLabelText("access.yaml")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByLabelText("access.yaml").textContent).toBe('bind: 0.0.0.0\nport: 8765\nallowed_hosts:\n  - localhost\n  - kraft.local\npassword: "********"  # stored hashed, never shown\nsession_expiry_days: 7');
  });

  it("says in the YAML what is running while a bind or port waits for a restart, and when no password is set", async () => {
    setup({ password_set: false, port: 9000 }, { port: 8765 });
    await screen.findByRole("heading", { name: "Access" });
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    await waitFor(() => expect(screen.getByLabelText("access.yaml").textContent).toContain("# running now: bind 0.0.0.0, port 8765, until a restart"));
    expect(screen.getByLabelText("access.yaml").textContent).toContain("port: 9000\n");
    expect(screen.getByLabelText("access.yaml").textContent).toContain("# password: not set");
  });

  it("draws Port, Allowed hosts, Password and Sessions as cards, each with its aside at the top right", async () => {
    setup();
    await screen.findByRole("heading", { name: "Access" });
    const card = (name: string) => screen.getByRole("region", { name }) as HTMLElement;
    for (const [name, aside] of [["Port", "saved on change"], ["Allowed hosts", "saved on change"], ["Password", "writes access.yaml"], ["Sessions", "live"]]) {
      expect(card(name)).toHaveClass("is-card");
      expect(within(card(name)).getByText(aside)).toBeInTheDocument();
    }
    expect(within(card("Sessions")).getByText("live")).toHaveClass("set-block-pill");
    expect(within(card("Port")).getByText("Used by both binds. Takes effect on restart. 1024–65535.")).toBeInTheDocument();
  });

  it("says in the Port card's aside when the port waits for a restart", async () => {
    useApply.setState({ restart: [PORT_ITEM] });
    setup({ port: 9000 }, { port: 8765 });
    expect(await within(await screen.findByRole("region", { name: "Port" })).findByText("waits for a restart")).toBeInTheDocument();
  });

  it("offers the two reaches as radio cards with their address and what each means, and Sign out here for the current session", async () => {
    setup();
    const machine = await screen.findByRole("radio", { name: /This machine only/ });
    expect(machine).toHaveTextContent("127.0.0.1:8765");
    expect(machine).toHaveTextContent("No password. Only loopback names are accepted.");
    expect(machine).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /Local network/ })).toHaveAttribute("aria-checked", "true");
    expect(await screen.findByRole("button", { name: /Sign out Mac/ })).toHaveTextContent("Sign out here");
  });

  it("says 0.0.0.0 listens on every network and points to Remote access, never that Kraft cannot bind publicly", async () => {
    setup();
    const reach = await screen.findByRole("radiogroup", { name: "Reach" });
    const hint = reach.parentElement!;
    expect(hint).toHaveTextContent("0.0.0.0 listens on every network this machine is on.");
    expect(within(hint).getByRole("link", { name: "Remote access" })).toHaveAttribute("href", "https://itsomidkarami.github.io/kraft/guides/remote-access");
    expect(hint).not.toHaveTextContent(/never binds publicly/);
  });

  it("on loopback with no password, Local network asks for one and sends it with the bind in one save", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: false, auth_required: false });
    await userEvent.click(await screen.findByRole("radio", { name: /Local network/ }));
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Set and switch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("enter a password");
    expect(put).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Password for the local network"), "hunter2{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0", password: "hunter2" }));
    expect(put).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Password for the local network")).toBeNull();
  });

  it("drops the password prompt's refusal when the prompt is cancelled or closed with Escape", async () => {
    setup({ bind: "127.0.0.1", password_set: false, auth_required: false });
    for (const close of [() => userEvent.click(screen.getByRole("button", { name: "Cancel" })), () => userEvent.type(screen.getByLabelText("Password for the local network"), "{Escape}")]) {
      await userEvent.click(await screen.findByRole("radio", { name: /Local network/ }));
      await userEvent.click(screen.getByRole("button", { name: "Set and switch" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("enter a password");
      await close();
      expect(screen.queryByLabelText("Password for the local network")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
    }
  });

  it("on loopback with a password set, Local network switches at once", async () => {
    const put = setup({ bind: "127.0.0.1", password_set: true, auth_required: false });
    await userEvent.click(await screen.findByRole("radio", { name: /Local network/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0" }));
    expect(screen.queryByLabelText("Password for the local network")).toBeNull();
  });

  it("keeps what loopback does not use in a dashed card of its own", async () => {
    setup({ bind: "127.0.0.1" });
    const unused = await screen.findByRole("region", { name: "Not used on 127.0.0.1" });
    expect(unused).toHaveClass("is-card", "is-dashed");
  });
});
