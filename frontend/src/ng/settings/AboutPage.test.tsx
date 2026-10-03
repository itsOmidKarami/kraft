import { render, screen, waitFor, within } from "@testing-library/react";
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

  it("says to restart when an update was installed under this running server", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "1.4.0", installed: "1.5.0" });
    render(<AboutPage />);
    expect(await screen.findByText(/This server is still running 1\.4\.0, and 1\.5\.0 is installed/)).toHaveTextContent("kraft admin restart");
  });

  it("says to restart when the server is too old to report what is installed (R10c-01)", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "1.5.0rc14" });
    render(<AboutPage />);
    expect(await screen.findByText(/This server runs a release older than the Kraft installed/)).toHaveTextContent("kraft admin restart");
  });

  it("says a rollback needs the database restored, not a restart (R10c-03)", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", invalid_templates: {}, invalid_policy: [], version: "1.5.0", installed: "1.4.0" });
    render(<AboutPage />);
    const note = await screen.findByText(/An older Kraft, 1\.4\.0, is installed under this 1\.5\.0 server/);
    expect(note).toHaveTextContent("restore the database from before the upgrade");
    expect(note).not.toHaveTextContent("kraft admin restart");
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

  it("draws the version as a card of its own, the instance as labelled rows and the links as a list", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ ...instance, index: { documents: 214, last_scan_at: new Date(Date.now() - 120_000).toISOString(), errors: [] } });
    render(<AboutPage />);
    const version = await screen.findByRole("region", { name: "Version" });
    expect(version).toHaveClass("set-about-version");
    expect(version.querySelector(".set-version-channel")).toHaveTextContent("stable");
    expect(within(version).getByRole("status")).toHaveTextContent("v1.5.0 is available");
    expect(within(version).getByText("kraft admin update")).toBeInTheDocument();
    const rows = (await screen.findByText("Health")).closest("dl") as HTMLElement;
    expect([...rows.querySelectorAll("dt")].map((d) => d.textContent)).toEqual(["Health", "Address", "Run directory", "Process", "Search index"]);
    expect(screen.getByRole("link", { name: "Documentation" })).toHaveAttribute("href", expect.stringContaining("github.io/kraft"));
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
