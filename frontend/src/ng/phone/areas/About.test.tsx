import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { Toaster } from "../nav/Toaster";
import { AboutScreen, updateVerdict } from "./About";

const UPDATE = { installed: "1.4.0", latest: "v2.0.0", channel: "stable", behind: true as boolean | null, checked_at: new Date(Date.now() - 3_600_000).toISOString() as string | null };
const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let served: () => Response;
let calls: string[];
const show = () => render(<MemoryRouter><AboutScreen /><Toaster /></MemoryRouter>);

beforeEach(() => {
  calls = [];
  served = () => reply(200, UPDATE);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${String(url).replace(/^.*\/api/, "")}`);
    return Promise.resolve(served());
  });
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "1.4.0" });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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

const FREE = {
  ...UPDATE,
  latest_in_major: null,
  check_disabled: false,
  install_kind: "uv",
  dev_install: false,
  restart_kind: "service",
  marked: null,
  pending: { release: null as string | null, restart: [] as string[] },
  blocked: [] as string[],
  outcome: null as unknown,
};
const SETTINGS = { auto_update: false, restart_when_free: false, channel: null, allowed_hours: [] as string[] };
const serve = (state: object, settings = SETTINGS) => {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api/, "");
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? ` ${init.body}` : ""}`);
    return Promise.resolve(path.startsWith("/update/settings") ? reply(200, init?.method === "PUT" ? { ...settings, ...JSON.parse(String(init.body)) } : settings) : reply(200, state));
  });
};

describe("About: update when Kraft is free", () => {
  it("release-install-shows-install-when-free: tapping it marks the release", async () => {
    serve(FREE);
    show();
    await userEvent.click(await screen.findByRole("button", { name: /^Install when free/ }));
    expect(calls).toContain('POST /update/mark {"tag":"v2.0.0","channel":"stable"}');
  });

  it("dev-install-hides-release-controls", async () => {
    serve({ ...FREE, dev_install: true });
    show();
    await screen.findByRole("switch", { name: /^Restart when free/ });
    expect(screen.queryByRole("button", { name: /^Install when free/ })).toBeNull();
    expect(screen.queryByRole("switch", { name: /^Update automatically/ })).toBeNull();
  });

  it("switches-save-update-settings", async () => {
    serve(FREE);
    show();
    await userEvent.click(await screen.findByRole("switch", { name: /^Update automatically/ }));
    expect(calls).toContain('PUT /update/settings {"auto_update":true}');
    await userEvent.click(screen.getByRole("switch", { name: /^Restart when free/ }));
    expect(calls).toContain('PUT /update/settings {"restart_when_free":true}');
  });

  it("the allowed hours are typed as ranges and refused when they are not", async () => {
    serve(FREE, { ...SETTINGS, allowed_hours: ["01:00-06:00"] });
    show();
    await userEvent.click(await screen.findByRole("button", { name: /^Allowed hours/ }));
    const field = await screen.findByRole("textbox");
    await userEvent.clear(field);
    await userEvent.type(field, "1-6");
    await userEvent.click(screen.getByRole("button", { name: "Set" }));
    expect(await screen.findByText("1-6 is not a range like 01:00-06:00.")).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "22:00-02:00, 12:00-13:00");
    await userEvent.click(screen.getByRole("button", { name: "Set" }));
    await waitFor(() => expect(calls).toContain('PUT /update/settings {"allowed_hours":["22:00-02:00","12:00-13:00"]}'));
  });

  it("waiting-line-names-the-reason, and the check-disabled note", async () => {
    serve({ ...FREE, check_disabled: true, marked: { tag: "v2.0.0", by: "you", at: "" }, pending: { release: "v2.0.0", restart: [] }, blocked: ["waiting for 2 running items"] });
    show();
    expect(await screen.findByText(/v2\.0\.0 is marked: it installs, then Kraft restarts\. Not yet: waiting for 2 running items\./)).toBeInTheDocument();
    expect(screen.getByText(/KRAFT_NO_UPDATE_CHECK is set/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Don’t install/ }));
    expect(calls).toContain("DELETE /update/mark");
  });

  it("newer-major-shown-beside: the in-major release and the new major are each offered", async () => {
    serve({ ...FREE, installed: "1.4.0", latest: "v2.0.0", latest_in_major: "v1.4.2" });
    show();
    expect(await screen.findByText("v1.4.2")).toBeInTheDocument();
    expect(screen.getByText("v2.0.0, a new major version")).toBeInTheDocument();
  });

  it("last-outcome-failed-shows-reason", async () => {
    serve({ ...FREE, outcome: { status: "failed", detail: "the new install would not start", from: "1.4.0", to: "1.4.1", at: new Date().toISOString() } });
    show();
    expect(await screen.findByText(/^Last update failed, just now: the new install would not start/)).toBeInTheDocument();
  });
});

describe("About (O.5)", () => {
  it("shows the installed version and channel, the newer release, and the command to run", async () => {
    show();
    expect(await screen.findByText("v2.0.0 is available")).toBeInTheDocument();
    expect(screen.getByText("1.4.0")).toHaveClass("ph-row-figure");
    expect(screen.getByRole("button", { name: /^Check now/ }).querySelector(".ph-row-icon")).not.toBeNull();
    expect(screen.getAllByText("stable").length).toBeGreaterThan(0);
    expect(screen.getByText("kraft admin update")).toBeInTheDocument();
    expect(screen.getByText("all chains and policy valid")).toBeInTheDocument();
  });

  it("an equal version reads up to date", async () => {
    served = () => reply(200, { ...UPDATE, latest: "v1.4.0", behind: false });
    show();
    expect(await screen.findByText("up to date")).toBeInTheDocument();
  });

  it("an unreachable feed reads unknown, never up to date", async () => {
    served = () => reply(200, { ...UPDATE, latest: null, behind: null, checked_at: null });
    show();
    expect(await screen.findByText(/^unknown: the release feed did not answer/)).toBeInTheDocument();
    expect(screen.queryByText("up to date")).toBeNull();
    expect(screen.getByText("not checked yet")).toBeInTheDocument();
  });

  it("Check now posts, and says why when the server cannot ask", async () => {
    show();
    await screen.findByText("v2.0.0 is available");
    served = () => reply(502, { detail: "feed unreachable" });
    await userEvent.click(screen.getByRole("button", { name: /^Check now/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("feed unreachable");
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    expect(calls.some((c) => c.startsWith("POST /update/check"))).toBe(true);
  });

  it("asks the chosen channel's feed", async () => {
    show();
    await screen.findByText("v2.0.0 is available");
    served = () => reply(200, { ...UPDATE, channel: "beta", latest: "v1.6.0b1" });
    await userEvent.click(screen.getByRole("button", { name: /^channel/ }));
    await userEvent.click(screen.getByRole("radio", { name: "beta" }));
    await waitFor(() => expect(calls).toContain("GET /update?channel=beta"));
    expect(await screen.findByText("v1.6.0b1 is available")).toBeInTheDocument();
  });

  it("copies the command; with no clipboard it says to select the text, which is selectable", async () => {
    const write = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write }, userAgent: "jsdom" });
    show();
    await screen.findByText("v2.0.0 is available");
    await userEvent.click(screen.getByRole("button", { name: /^Copy the command/ }));
    expect(write).toHaveBeenCalledWith("kraft admin update");
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined, userAgent: "jsdom" });
    await userEvent.click(screen.getByRole("button", { name: /^Copy the command/ }));
    expect(await screen.findByText(/Could not copy: select the text instead/)).toBeInTheDocument();
    expect(screen.getByText("kraft admin update")).toHaveAttribute("tabindex", "0");
  });

  it("has no install control for a server that does not hold the request", async () => {
    show();
    await screen.findByText("v2.0.0 is available");
    expect(screen.queryByRole("button", { name: /^(install|update now|upgrade)/i })).toBeNull();
  });
});
