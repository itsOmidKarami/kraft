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

beforeEach(() => {
  vi.stubGlobal("fetch", () => Promise.reject(new TypeError("down")));
  useApply.setState({ restart: [], reload: [], managed: true, loaded: true, phase: "idle", confirming: false, error: null });
});
afterEach(() => {
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
    await userEvent.click(await screen.findByRole("button", { name: "+ add" }));
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

  it("offers no Restart for a server started from a terminal", async () => {
    useApply.setState({ restart: [PORT_ITEM], managed: false });
    setup({ port: 9000 }, { port: 8765 });
    await screen.findByText("port changes from 8765 to 9000");
    expect(screen.queryByRole("button", { name: "Restart Kraft" })).toBeNull();
    expect(screen.getByText(/Run/)).toHaveTextContent("kraft admin restart");
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
});
