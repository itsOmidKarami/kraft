import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { AboutPage, updateVerdict, type UpdateState } from "./AboutPage";

const UPDATE: UpdateState = { installed: "1.4.0", latest: "v2.0.0", channel: "stable", behind: true, checked_at: new Date(Date.now() - 3_600_000).toISOString() };
const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let served: () => Response;
let calls: string[];
beforeEach(() => {
  calls = [];
  served = () => reply(200, UPDATE);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${String(url).replace(/^.*\/api/, "")}`);
    return Promise.resolve(served());
  });
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "1.4.0", installed: "1.4.0" });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("updateVerdict", () => {
  it("is unknown, never up to date, when the feed did not answer", () => {
    expect(updateVerdict(null).text).toBe("unknown");
    expect(updateVerdict({ ...UPDATE, latest: null, behind: null }).text).toMatch(/^unknown/);
    expect(updateVerdict({ ...UPDATE, latest: null, behind: null }).text).not.toMatch(/up to date/);
    expect(updateVerdict({ ...UPDATE, behind: null }).text).toMatch(/^unknown/);
  });
  it("says up to date only when the feed answered and nothing is newer", () => {
    expect(updateVerdict({ ...UPDATE, latest: "v1.4.0", behind: false }).text).toBe("up to date");
  });
  it("names the newer release", () => expect(updateVerdict(UPDATE).text).toBe("v2.0.0 is available"));
});

/** A server that holds updates for when Kraft is free: a release install on uv, with 2.0.0 available. */
const FREE: UpdateState = {
  ...UPDATE,
  latest_in_major: null,
  check_disabled: false,
  install_kind: "uv",
  dev_install: false,
  restart_kind: "service",
  marked: null,
  pending: { release: null, restart: [] },
  blocked: [],
  outcome: null,
};
const SETTINGS = { auto_update: false, restart_when_free: false, channel: null, allowed_hours: [] as string[] };
/** Answer each route with its own body, as the real server does. */
const serve = (state: UpdateState, settings = SETTINGS) => {
  served = () => reply(200, state);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api/, "");
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? ` ${init.body}` : ""}`);
    return Promise.resolve(path.startsWith("/update/settings") ? reply(200, init?.method === "PUT" ? { ...settings, ...JSON.parse(String(init.body)) } : settings) : reply(200, state));
  });
};

describe("ng AboutPage: update when Kraft is free", () => {
  it("release-install-shows-install-when-free: clicking it marks the release", async () => {
    serve(FREE);
    render(<AboutPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Install when free" }));
    expect(calls).toContain('POST /update/mark {"tag":"v2.0.0","channel":"stable"}');
  });

  it("marked: names the mark and offers Don't, which drops it", async () => {
    serve({ ...FREE, marked: { tag: "v2.0.0", by: "auto", at: "2026-10-22T10:00:00Z" }, pending: { release: "v2.0.0", restart: [] }, blocked: ["waiting for 1 running item"] });
    render(<AboutPage />);
    expect(await screen.findByText("v2.0.0 is marked by auto-update.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install when free" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Don’t" }));
    expect(calls).toContain("DELETE /update/mark");
  });

  it("dev-install-hides-release-controls: only the restart switch and the hours remain", async () => {
    serve({ ...FREE, dev_install: true });
    render(<AboutPage />);
    await screen.findByRole("switch", { name: "Restart when free" });
    expect(screen.queryByRole("button", { name: "Install when free" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "Update automatically" })).toBeNull();
    expect(screen.getByRole("group", { name: "Allowed hours" })).toBeInTheDocument();
  });

  it("a server that is not a uv install says why it cannot take the release", async () => {
    serve({ ...FREE, install_kind: "brew" });
    render(<AboutPage />);
    expect(await screen.findByText("Installed with Homebrew: update with brew upgrade kraft.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install when free" })).toBeNull();
  });

  it("switches-save-update-settings: each switch and the hours PUT their own key", async () => {
    serve(FREE);
    render(<AboutPage />);
    await userEvent.click(await screen.findByRole("switch", { name: "Update automatically" }));
    expect(calls).toContain('PUT /update/settings {"auto_update":true}');
    await userEvent.click(screen.getByRole("switch", { name: "Restart when free" }));
    expect(calls).toContain('PUT /update/settings {"restart_when_free":true}');
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(calls).toContain('PUT /update/settings {"allowed_hours":["01:00-06:00"]}');
  });

  it("shows the saved hours as chips and removes one", async () => {
    serve(FREE, { ...SETTINGS, allowed_hours: ["01:00-06:00", "22:00-23:00"] });
    render(<AboutPage />);
    expect(await screen.findByText(/01:00–06:00/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove 01:00–06:00" }));
    expect(calls).toContain('PUT /update/settings {"allowed_hours":["22:00-23:00"]}');
  });

  it("waiting-line-names-the-reason", async () => {
    serve({ ...FREE, marked: { tag: "v2.0.0", by: "you", at: "" }, pending: { release: "v2.0.0", restart: [] }, blocked: ["outside 01:00-06:00"] });
    render(<AboutPage />);
    expect(await screen.findByText("v2.0.0 is marked: it installs, then Kraft restarts. Not yet: outside 01:00-06:00.")).toBeInTheDocument();
  });

  it("a pending restart that nothing blocks says it happens at the next free moment", async () => {
    serve({ ...FREE, pending: { release: null, restart: ["installed 2.0.0, running 1.4.0"] } });
    render(<AboutPage />);
    expect(await screen.findByText("A restart is waiting (installed 2.0.0, running 1.4.0). It happens at the next free moment.")).toBeInTheDocument();
  });

  it("newer-major-shown-beside: the in-major release and the new major each have their own button", async () => {
    serve({ ...FREE, installed: "1.4.0", latest: "v2.0.0", latest_in_major: "v1.4.2" });
    render(<AboutPage />);
    await screen.findByText("v1.4.2.");
    expect(screen.getByText("v2.0.0 is a new major version.")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Install when free" });
    expect(buttons).toHaveLength(2);
    await userEvent.click(buttons[0]);
    expect(calls).toContain('POST /update/mark {"tag":"v1.4.2","channel":"stable"}');
  });

  it("check-disabled-noted", async () => {
    serve({ ...FREE, check_disabled: true });
    render(<AboutPage />);
    expect(await screen.findByText(/KRAFT_NO_UPDATE_CHECK is set/)).toBeInTheDocument();
  });

  it("an attached server says it cannot restart itself", async () => {
    serve({ ...FREE, restart_kind: "attached" });
    render(<AboutPage />);
    expect(await screen.findByText(/started in a terminal, so it cannot restart itself/)).toBeInTheDocument();
  });

  it.each([
    ["failed", { status: "failed" as const, detail: "the new install would not start: port 'x'", from: "1.4.0", to: "1.4.1", at: new Date().toISOString() }, /^Last update failed, just now: the new install would not start/],
    ["applied", { status: "applied" as const, detail: "", from: "1.4.0", to: "1.4.1", at: new Date().toISOString() }, /^Last update: applied 1\.4\.0 → 1\.4\.1, just now\./],
  ])("last-outcome-%s-shows-reason", async (_name, outcome, text) => {
    serve({ ...FREE, outcome });
    render(<AboutPage />);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("a server older than this change shows none of it and asks nothing of /update/mark", async () => {
    render(<AboutPage />);
    await screen.findByText("v2.0.0 is available");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "Install when free" })).toBeNull();
  });

  it("choosing a channel also keeps it in update.yaml", async () => {
    serve(FREE);
    render(<AboutPage />);
    await screen.findByText("v2.0.0 is available");
    await userEvent.click(screen.getByRole("radio", { name: "rc" }));
    await waitFor(() => expect(calls).toContain('PUT /update/settings {"channel":"rc"}'));
  });
});

describe("ng AboutPage", () => {
  it("shows the installed version, the newer one, and the command to run", async () => {
    render(<AboutPage />);
    expect(await screen.findByText("v2.0.0 is available")).toBeInTheDocument();
    expect(screen.getByText("1.4.0")).toBeInTheDocument();
    expect(screen.getByText("kraft admin update")).toBeInTheDocument();
    expect(screen.getByText("ok · all chains and policy valid")).toBeInTheDocument();
  });

  it("names held storage as the problem when nothing else is wrong", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "degraded", invalid_templates: {}, invalid_policy: [], version: "1.4.0", installed: "1.4.0", storage: { state: "held", used_bytes: 12, quota_bytes: 8, limit_bytes: 10, measured_at: "2026-10-10T09:00:00+00:00" } });
    render(<AboutPage />);
    expect(await screen.findByText(/degraded · 1 problem · worktrees over the storage limit, starts held/)).toBeInTheDocument();
  });

  it("says to restart when an update was installed under this running server", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "1.4.0", installed: "2.0.0" });
    render(<AboutPage />);
    expect(await screen.findByText(/This server is still running 1\.4\.0, and 2\.0\.0 is installed/)).toHaveTextContent("kraft admin restart");
  });

  it("says to restart when the server is too old to report what is installed (R10c-01)", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "1.4.0" });
    render(<AboutPage />);
    expect(await screen.findByText(/This server runs a release older than the Kraft installed/)).toHaveTextContent("kraft admin restart");
  });

  it("says a rollback needs the database restored, not a restart (R10c-03)", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "2.0.0", installed: "1.4.0" });
    render(<AboutPage />);
    const note = await screen.findByText(/An older Kraft, 1\.4\.0, is installed under this 2\.0\.0 server/);
    expect(note).toHaveTextContent("restore the database from before the upgrade");
    expect(note).not.toHaveTextContent("kraft admin restart");
  });

  // R11b-09: the command and "Run this" sat beside "up to date", and under a rollback's advice.
  it.each([
    ["behind", { version: "1.4.0", installed: "1.4.0" }, UPDATE, /^Run this in a terminal, then restart Kraft/],
    ["up to date", { version: "2.0.0", installed: "2.0.0" }, { ...UPDATE, installed: "2.0.0", behind: false }, /^To update later, run this in a terminal/],
    ["rolling back", { version: "2.0.0", installed: "1.4.0" }, UPDATE, null],
  ] as const)("%s: offers kraft admin update as what it is", async (_name, versions, answer, hint) => {
    served = () => reply(200, answer);
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], ...versions });
    render(<AboutPage />);
    await screen.findByText(/checked/);
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    if (hint) {
      expect(screen.getByText(hint)).toBeInTheDocument();
      expect(screen.getByText("kraft admin update")).toBeInTheDocument();
    } else {
      await screen.findByText(/An older Kraft/);
      expect(screen.queryByText("kraft admin update")).toBeNull();
    }
  });

  it("reads an unreachable feed as unknown", async () => {
    served = () => reply(200, { ...UPDATE, latest: null, behind: null, checked_at: null });
    render(<AboutPage />);
    expect(await screen.findByRole("status")).toHaveTextContent("unknown");
    expect(screen.queryByText("up to date")).toBeNull();
    expect(screen.getByText("not checked yet")).toBeInTheDocument();
  });

  it("checks now with a POST, and says unknown with the reason when the server cannot ask", async () => {
    render(<AboutPage />);
    await screen.findByText("v2.0.0 is available");
    served = () => reply(502, { detail: "feed unreachable" });
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("feed unreachable");
    expect(screen.getByRole("status")).toHaveTextContent("unknown");
    expect(calls.some((c) => c.startsWith("POST /update/check"))).toBe(true);
  });

  it("asks a chosen channel and names it in the command", async () => {
    render(<AboutPage />);
    await screen.findByText("v2.0.0 is available");
    served = () => reply(200, { ...UPDATE, channel: "beta", latest: "v1.6.0b1" });
    await userEvent.click(screen.getByRole("radio", { name: "beta" }));
    await waitFor(() => expect(calls).toContain("GET /update?channel=beta"));
    expect(await screen.findByText("v1.6.0b1 is available")).toBeInTheDocument();
  });

  it("copies the command and the diagnostics from the keyboard", async () => {
    const write = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write }, userAgent: "jsdom" });
    render(<AboutPage />);
    await screen.findByText("v2.0.0 is available");
    screen.getByRole("button", { name: "Copy diagnostics" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(write).toHaveBeenCalledWith(expect.stringContaining("kraft 1.4.0 (stable)"));
    expect(write.mock.calls[0][0]).toContain("update: v2.0.0 is available");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenLastCalledWith("kraft admin update");
  });

  const instance = { status: "ok" as const, invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "1.4.0", installed: "1.4.0", run_dir: "/Users/you/.kraft", pid: 41822, uptime_s: 3 * 86_400 + 4 * 3600 + 120 };

  it("draws the run directory, the process and the search index from /health", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 214, last_scan_at: new Date(Date.now() - 120_000).toISOString(), errors: [] } });
    render(<AboutPage />);
    expect(await screen.findByText("/Users/you/.kraft")).toBeInTheDocument();
    expect(screen.getByText("pid 41822 · up 3d 4h")).toBeInTheDocument();
    expect(screen.getByText("214 documents · scanned 2m ago")).toBeInTheDocument();
  });

  it("says when the index has not scanned yet and counts its errors", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 0, last_scan_at: null, errors: ["a: unreadable", "b: unreadable"] } });
    render(<AboutPage />);
    expect(await screen.findByText("0 documents · not scanned yet · 2 scan errors")).toHaveClass("set-warn");
  });

  it("adds the same three lines to the copied diagnostics", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 214, last_scan_at: null, errors: [] } });
    const write = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write }, userAgent: "jsdom" });
    render(<AboutPage />);
    await screen.findByText("/Users/you/.kraft");
    await userEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));
    expect(write.mock.calls[0][0]).toContain("run dir: /Users/you/.kraft\nprocess: pid 41822 · up 3d 4h\nindex: 214 documents · not scanned yet");
  });

  it("leaves the three rows out for a server that does not send them, and shows a pid without an uptime", async () => {
    render(<AboutPage />);
    await screen.findByText("ok · all chains and policy valid");
    expect(screen.queryByText("Run directory")).toBeNull();
    expect(screen.queryByText("Process")).toBeNull();
    expect(screen.queryByText("Search index")).toBeNull();
    document.body.innerHTML = "";
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, uptime_s: undefined });
    render(<AboutPage />);
    expect(await screen.findByText("pid 41822")).toBeInTheDocument();
  });

  // ST-7
  it.each([
    ["off on a loopback bind", false, "127.0.0.1:8765 · sign-in off on localhost"],
    ["on once Kraft listens on the network", true, "127.0.0.1:8765 · sign-in on"],
  ])("writes whether sign-in is on beside the address: %s", async (_, auth_required, text) => {
    vi.spyOn(api, "getAccess").mockResolvedValue({ bind: "127.0.0.1", port: 8765, session_expiry_days: 7, password_set: false, auth_required, allowed_hosts: [] });
    render(<AboutPage />);
    const dd = (await screen.findByText("Address")).nextElementSibling as HTMLElement;
    await waitFor(() => expect(dd).toHaveTextContent(text));
  });

  it("names the Python in the footer, from the update state, and leaves it out for a server that does not say", async () => {
    served = () => reply(200, { ...UPDATE, python: "3.14" });
    render(<AboutPage />);
    expect(await screen.findByText("macOS and Linux · Python 3.14 · Only the latest release gets fixes.")).toBeInTheDocument();
    document.body.innerHTML = "";
    served = () => reply(200, UPDATE);
    render(<AboutPage />);
    expect(await screen.findByText("macOS and Linux · Only the latest release gets fixes.")).toBeInTheDocument();
  });

  it("draws the version as a card of its own, the instance as labelled rows and the links as a list", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 214, last_scan_at: new Date(Date.now() - 120_000).toISOString(), errors: [] } });
    render(<AboutPage />);
    const version = await screen.findByRole("region", { name: "Version" });
    expect(version).toHaveClass("set-about-version");
    expect(version.querySelector(".set-version-channel")).toHaveTextContent("stable");
    expect(within(version).getByRole("status")).toHaveTextContent("v2.0.0 is available");
    expect(within(version).getByText("kraft admin update")).toBeInTheDocument();
    const rows = (await screen.findByText("Health")).closest("dl") as HTMLElement;
    expect([...rows.querySelectorAll("dt")].map((d) => d.textContent)).toEqual(["Health", "Address", "Run directory", "Process", "Search index"]);
    expect(screen.getByRole("link", { name: "Documentation" })).toHaveAttribute("href", expect.stringContaining("kraft-sdlc.dev"));
    expect(screen.getByRole("link", { name: "Status and support" })).toHaveClass("set-linkrow");
  });

  it("marks the search index with a warning dot until it has scanned cleanly", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 3, last_scan_at: null, errors: [] } });
    render(<AboutPage />);
    const dot = (await screen.findByText("3 documents · not scanned yet")).previousElementSibling;
    expect(dot).toHaveClass("is-warn");
    document.body.innerHTML = "";
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 3, last_scan_at: new Date().toISOString(), errors: [] } });
    render(<AboutPage />);
    expect((await screen.findByText("3 documents · scanned just now")).previousElementSibling).toHaveClass("is-ok");
  });
});
