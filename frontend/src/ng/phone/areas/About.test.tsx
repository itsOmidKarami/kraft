import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { Toaster } from "../nav/Toaster";
import { AboutScreen, updateVerdict } from "./About";

const UPDATE = { installed: "1.4.0", latest: "v1.5.0", channel: "stable", behind: true as boolean | null, checked_at: new Date(Date.now() - 3_600_000).toISOString() as string | null };
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
  it("names the newer release", () => expect(updateVerdict(UPDATE).text).toBe("v1.5.0 is available"));
});

describe("About (O.5)", () => {
  it("shows the installed version and channel, the newer release, and the command to run", async () => {
    show();
    expect(await screen.findByText("v1.5.0 is available")).toBeInTheDocument();
    expect(screen.getByText("1.4.0")).toBeInTheDocument();
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
    await screen.findByText("v1.5.0 is available");
    served = () => reply(502, { detail: "feed unreachable" });
    await userEvent.click(screen.getByRole("button", { name: /^Check now/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("feed unreachable");
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    expect(calls.some((c) => c.startsWith("POST /update/check"))).toBe(true);
  });

  it("asks the chosen channel's feed", async () => {
    show();
    await screen.findByText("v1.5.0 is available");
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
    await screen.findByText("v1.5.0 is available");
    await userEvent.click(screen.getByRole("button", { name: /^Copy the command/ }));
    expect(write).toHaveBeenCalledWith("kraft admin update");
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined, userAgent: "jsdom" });
    await userEvent.click(screen.getByRole("button", { name: /^Copy the command/ }));
    expect(await screen.findByText(/Could not copy: select the text instead/)).toBeInTheDocument();
    expect(screen.getByText("kraft admin update")).toHaveAttribute("tabindex", "0");
  });

  it("has no install control", async () => {
    show();
    await screen.findByText("v1.5.0 is available");
    expect(screen.queryByRole("button", { name: /^(install|update now|upgrade)/i })).toBeNull();
  });
});
