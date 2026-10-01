import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApplyChip, ApplyDialogs } from "./ApplyChip";
import { useApply } from "./store";

const PORT = { id: "access.port", file: "access.yaml", text: "port changes from 8765 to 9000" };
const DISK = { id: "disk:policy.yaml", file: "policy.yaml", text: "changed on disk since it was loaded" };
const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let calls: string[];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api/, "");
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/access") return Promise.resolve(reply(200, { port: 9000 }));
    if (path === "/apply/restart") return Promise.resolve(reply(202, { restarting: true }));
    if (path === "/apply/reload") return Promise.resolve(reply(200, { restart: [], reload: [{ ...DISK, problem: "policy: max_concurrent must be 1 or more" }], managed: true }));
    return Promise.reject(new TypeError("down")); // the restart wait: nothing answers
  });
  useApply.setState({ restart: [], reload: [], managed: false, loaded: true, phase: "idle", confirming: false, address: "", error: null });
});
afterEach(() => vi.unstubAllGlobals());

const show = (state: Partial<ReturnType<typeof useApply.getState>>) => {
  useApply.setState(state);
  return render(<><ApplyChip /><ApplyDialogs /></>);
};

describe("ApplyChip", () => {
  it("renders nothing when nothing is waiting", () => {
    const { container } = show({});
    expect(container).toBeEmptyDOMElement();
  });

  it("names a changed file and reloads it without any restart", async () => {
    show({ reload: [DISK], managed: true });
    await userEvent.click(screen.getByRole("button", { name: "Changed on disk, 1" }));
    expect(screen.getByText("policy.yaml")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restart Kraft/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    // The server answers with the policy still refused: the chip turns into the problem.
    expect(await screen.findByRole("button", { name: "Reload found a problem, 1" })).toBeInTheDocument();
    expect(calls).toContain("POST /apply/reload");
    expect(calls).not.toContain("POST /apply/restart");
  });

  it("shows a reload problem and offers Reload again", async () => {
    show({ reload: [{ ...DISK, problem: "chain lint: no_such_node" }], managed: true });
    await userEvent.click(screen.getByRole("button", { name: /Reload found a problem/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("chain lint: no_such_node");
    expect(screen.getByRole("button", { name: "Reload again" })).toBeInTheDocument();
  });

  it("asks before restarting, and restarts only on the dialog's button", async () => {
    show({ restart: [PORT], managed: true });
    await userEvent.click(screen.getByRole("button", { name: "Restart needed, 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Restart Kraft" }));
    const dialog = await screen.findByRole("dialog", { name: "Restart Kraft?" });
    expect(dialog).toHaveTextContent("kraft admin restart");
    expect(calls).not.toContain("POST /apply/restart");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).not.toContain("POST /apply/restart");
    await userEvent.click(screen.getByRole("button", { name: "Restart needed, 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Restart Kraft" }));
    await userEvent.click(await screen.findByRole("button", { name: "Restart" }));
    await waitFor(() => expect(calls).toContain("POST /apply/restart"));
    expect(await screen.findByRole("dialog", { name: "Restarting Kraft…" })).toBeInTheDocument();
  });

  it("offers no Restart for a server started from a terminal, and says how", async () => {
    show({ restart: [PORT], managed: false });
    await userEvent.click(screen.getByRole("button", { name: "Restart needed, 1" }));
    expect(screen.queryByRole("button", { name: /Restart/, hidden: false })?.textContent ?? "").not.toMatch(/Restart Kraft/);
    expect(screen.getByText(/Run kraft admin restart there/)).toBeInTheDocument();
  });

  it("opens with Enter and closes with Escape, handing focus back", async () => {
    show({ reload: [DISK], managed: true });
    const chip = screen.getByRole("button", { name: "Changed on disk, 1" });
    chip.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("dialog", { name: "Waiting to apply" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Waiting to apply" })).toBeNull();
    expect(chip).toHaveFocus();
  });
});
