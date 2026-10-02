import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useApply } from "../../apply/store";
import { mountAt, posts } from "../areas/testkit";
import { More } from "./More";

const open = (answers: Record<string, [number, unknown]> = {}) => mountAt(<More />, "/more", "/more", answers);
beforeEach(() => useApply.setState({ restart: [], reload: [], managed: false, loaded: false, phase: "idle", confirming: false, error: null }));
afterEach(() => vi.unstubAllGlobals());

describe("More (K.1)", () => {
  it("lists every area in the prototype's groups, About included", async () => {
    open();
    expect(await screen.findByRole("heading", { level: 1, name: "More" })).toBeInTheDocument();
    const t = screen.getByRole("region", { name: "Templates" });
    expect(within(t).getAllByRole("link").map((l) => l.textContent)).toEqual(["Chains", "Library", "Harnesses", "Repos"]);
    const s = screen.getByRole("region", { name: "Settings" });
    expect(within(s).getAllByRole("link").map((l) => l.textContent)).toEqual(["Policy", "Auto-intake", "Notifications", "Access", "Appearance", "About"]);
    expect(within(s).getByRole("link", { name: "About" })).toHaveAttribute("href", "/settings/about");
    expect(screen.getByRole("link", { name: "Archived" })).toHaveAttribute("href", "/archived");
  });

  it("marks an area with an open draft, or its problems, from GET /drafts", async () => {
    open({ "GET /drafts": [200, [{ area: "policy", key: "policy", files: ["policy.yaml"], changes: 2, problems: 0, updated_at: "x" }, { area: "repos", key: "repos", files: ["repos.yaml"], changes: 1, problems: 3, updated_at: "x" }]] });
    const policy = await screen.findByRole("link", { name: /Policy/ });
    await waitFor(() => expect(policy).toHaveTextContent("draft"));
    expect(screen.getByRole("link", { name: /Repos/ })).toHaveTextContent("3 problems");
    expect(screen.getByRole("link", { name: "Notifications" })).not.toHaveTextContent("draft");
  });

  it("shows the Apply group only while something is pending, and Reload calls the reload route", async () => {
    const { calls } = open({
      "GET /apply": [200, { restart: [], reload: [{ id: "disk:chains/default.yaml", file: "chains/default.yaml", text: "chains/default.yaml changed on disk" }], managed: true }],
      "POST /apply/reload": [200, { restart: [], reload: [], managed: true }],
    });
    const group = await screen.findByRole("region", { name: "Apply" });
    expect(within(group).getByText("chains/default.yaml changed on disk")).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/apply/reload"]));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Apply" })).toBeNull());
  });

  it("offers Restart only when Kraft can restart itself, and never without the confirm", async () => {
    const item = { id: "access.bind", file: "access.yaml", text: "bind 127.0.0.1 → 0.0.0.0" };
    const { calls } = open({
      "GET /apply": [200, { restart: [item], reload: [], managed: true }],
      "GET /access": [200, { port: 8765 }],
      "POST /apply/restart": [202, { restarting: true }],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Restart Kraft" }));
    const sheet = await screen.findByRole("dialog", { name: "Restart Kraft?" });
    expect(sheet).toHaveTextContent("bind 127.0.0.1 → 0.0.0.0");
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts(calls)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Restart Kraft" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Restart now" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/apply/restart"]));
  });

  it.each([
    [[], /No item is active, so no agent is stopped\./],
    [[{ status: "active" }, { status: "active" }], /2 items are active\. Restarting stops their agents; each stops as failed and needs Retry afterwards\./],
  ])("the Restart sheet counts the active items and does not promise they are picked up again (%j)", async (items, said) => {
    open({
      "GET /apply": [200, { restart: [{ id: "access.bind", file: "access.yaml", text: "bind changed" }], reload: [], managed: true }],
      "GET /access": [200, { port: 8765 }],
      "GET /work-items": [200, { items, cursor: 0 }],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Restart Kraft" }));
    const sheet = await screen.findByRole("dialog", { name: "Restart Kraft?" });
    await waitFor(() => expect(sheet).toHaveTextContent(said));
    expect(sheet).not.toHaveTextContent("picked up again");
  });

  it("opens the Restart sheet only once the active count is in, so it never shows the wrong text first", async () => {
    open({ "GET /apply": [200, { restart: [{ id: "access.bind", file: "access.yaml", text: "bind changed" }], reload: [], managed: true }] });
    let counted!: () => void;
    await screen.findByRole("button", { name: "Restart Kraft" });
    useApply.setState({ askRestart: () => new Promise<void>((r) => { counted = () => { useApply.setState({ active: 2 }); r(); }; }) });
    await userEvent.click(screen.getByRole("button", { name: "Restart Kraft" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    counted();
    expect(await screen.findByRole("dialog", { name: "Restart Kraft?" })).toHaveTextContent("2 items are active.");
  });

  it("does not offer Restart when Kraft was started from a terminal, and says so", async () => {
    open({ "GET /apply": [200, { restart: [{ id: "access.bind", file: "access.yaml", text: "bind changed" }], reload: [], managed: false }] });
    expect(await screen.findByText(/cannot restart itself\. Run kraft admin restart; .*Ctrl-C and start it again the same way/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart Kraft" })).toBeNull();
  });
});
