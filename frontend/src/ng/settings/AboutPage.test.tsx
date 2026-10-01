import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { AboutPage, updateVerdict, type UpdateState } from "./AboutPage";

const UPDATE: UpdateState = { installed: "1.4.0", latest: "v1.5.0", channel: "stable", behind: true, checked_at: new Date(Date.now() - 3_600_000).toISOString() };
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
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "1.4.0" });
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
  it("names the newer release", () => expect(updateVerdict(UPDATE).text).toBe("v1.5.0 is available"));
});

describe("ng AboutPage", () => {
  it("shows the installed version, the newer one, and the command to run", async () => {
    render(<AboutPage />);
    expect(await screen.findByText("v1.5.0 is available")).toBeInTheDocument();
    expect(screen.getByText("1.4.0")).toBeInTheDocument();
    expect(screen.getByText("kraft admin update")).toBeInTheDocument();
    expect(screen.getByText("ok · all chains and policy valid")).toBeInTheDocument();
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
    await screen.findByText("v1.5.0 is available");
    served = () => reply(502, { detail: "feed unreachable" });
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("feed unreachable");
    expect(screen.getByRole("status")).toHaveTextContent("unknown");
    expect(calls.some((c) => c.startsWith("POST /update/check"))).toBe(true);
  });

  it("asks a chosen channel and names it in the command", async () => {
    render(<AboutPage />);
    await screen.findByText("v1.5.0 is available");
    served = () => reply(200, { ...UPDATE, channel: "beta", latest: "v1.6.0b1" });
    await userEvent.click(screen.getByRole("radio", { name: "beta" }));
    await waitFor(() => expect(calls).toContain("GET /update?channel=beta"));
    expect(await screen.findByText("v1.6.0b1 is available")).toBeInTheDocument();
  });

  it("copies the command and the diagnostics from the keyboard", async () => {
    const write = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write }, userAgent: "jsdom" });
    render(<AboutPage />);
    await screen.findByText("v1.5.0 is available");
    screen.getByRole("button", { name: "Copy diagnostics" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(write).toHaveBeenCalledWith(expect.stringContaining("kraft 1.4.0 (stable)"));
    expect(write.mock.calls[0][0]).toContain("update: v1.5.0 is available");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenLastCalledWith("kraft admin update");
  });
});
