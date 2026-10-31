import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgesFixture } from "../../../../e2e/contract/mocks/ngForges";
import { EMPTY_FORGES, NAMED_BY, PHONE_READ_ONLY, PHONE_REMOVE } from "../../settings/forgeWords";
import { ForgesScreen } from "./Forges";
import { mountAt, posts } from "./testkit";

const ACCOUNTS = forgesFixture(new Date());
const REPOS = [{ name: "repo-zero", forge_account: "shared-gl" }, { name: "plain", forge_account: null }];
const TOKEN = "glpat-SECRET-0123456789";
const URL = "/settings/forge-accounts";
const answers = (more: Record<string, [number, unknown]> = {}) =>
  ({ "GET /forges": [200, { accounts: ACCOUNTS }], "GET /repos": [200, { repos: REPOS }], ...more }) as Record<string, [number, unknown]>;
const open = (more?: Record<string, [number, unknown]>) => mountAt(<ForgesScreen />, URL, URL, answers(more));
const tap = async (name: string) => userEvent.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) }));
const options = () => within(screen.getByRole("dialog")).getAllByRole("button").map((b) => b.textContent);
const TESTED: [number, unknown] = [200, { ok: true, output: "signed in as alice\nmore" }];

afterEach(() => vi.unstubAllGlobals());

describe("Forge accounts (phone)", () => {
  it.each([
    ["fixture", ACCOUNTS, "13 accounts"],
    ["empty", {}, null],
  ])("lists each account with its state, or the empty words: %s", async (_id, accounts, title) => {
    open({ "GET /forges": [200, { accounts }] });
    if (!title) {
      expect(await screen.findByText(EMPTY_FORGES)).toBeInTheDocument();
    } else {
      expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
      const row = (n: string) => screen.getByRole("button", { name: new RegExp(`^${n}`) });
      // The state is one chip (toneless when muted); the value is only the client.
      expect(within(row("soon-gl")).getAllByText("expires in 3 days")).toHaveLength(1);
      expect(within(row("old-gl")).getAllByText("refused")).toHaveLength(1);
      expect(within(row("fresh-gl")).getAllByText("not checked yet")).toHaveLength(1);
      expect(within(row("down-gl")).getByText("unreachable", { selector: ".ph-badge-tag" })).toHaveAttribute("class", "ph-badge-tag");
      expect(within(row("fresh-gl")).getByText("not checked yet", { selector: ".ph-badge-tag" })).toBeInTheDocument();
      expect(within(row("corp-gl")).getByText("api")).toBeInTheDocument();
      expect(within(row("fresh-gl")).getByText("api")).toBeInTheDocument();
      expect(within(row("blind-gl")).getByText("cannot see 1 repo")).toHaveClass("ph-tone-warn");
      expect(within(row("github-cli")).getByText("cli")).toBeInTheDocument();
      expect(within(row("shared-gl")).getByText(/used by repo-zero/)).toBeInTheDocument();
      expect(within(row("acme-gh")).getAllByText("needs SSO")).toHaveLength(1);
      expect(row("acme-gh").textContent).not.toContain("authorization_request"); // the SSO address lives in the menu, not the row
      expect(document.body.textContent).not.toContain("op read");
    }
    expect(screen.getByText(PHONE_READ_ONLY)).toBeInTheDocument();
  });

  it("says why when forges.yaml never loaded (409), under the heading", async () => {
    open({ "GET /forges": [409, { detail: "forges.yaml has an edit that is not applied" }] });
    expect(await screen.findByRole("heading", { level: 1, name: "Forge accounts" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("forges.yaml has an edit that is not applied");
    expect(screen.queryByRole("button", { name: /^corp-gl/ })).toBeNull();
  });

  it.each([
    ["api-file", "corp-gl", ["Test", "Paste a new token", "Remove"], null],
    // PUT /token would erase a command (the server refuses it too): the menu offers no paste for a command account.
    ["command", "vault-gl", ["Test", "Remove"], null],
    ["cli", "github-cli", ["Test", "Remove"], null],
    ["named", "shared-gl", ["Test"], NAMED_BY(["repo-zero"])],
    ["sso", "acme-gh", ["Authorize for single sign-on", "Test", "Paste a new token", "Remove"], null],
  ])("tapping an account offers what it can do: %s", async (_id, name, expected, help) => {
    open();
    await tap(name);
    expect(options()).toEqual(expected);
    if (help) expect(within(screen.getByRole("dialog")).getByText(help)).toBeInTheDocument();
  });

  it("Paste a new token sends one PUT, keeps no value, then shows a fresh check", async () => {
    const { calls } = open({ "PUT /forges/corp-gl/token": [200, {}], "POST /forges/corp-gl/test": TESTED });
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Paste a new token" }));
    const field = screen.getByLabelText(/Paste a new token for corp-gl/, { selector: "input" });
    // Chrome ignores autocomplete="off" on a password field and offers to save the token when the sheet closes.
    expect(field).toHaveAttribute("autocomplete", "new-password");
    expect(field).toHaveAttribute("data-1p-ignore");
    expect(field).toHaveAttribute("data-lpignore", "true");
    expect(field).toHaveAttribute("spellcheck", "false");
    await userEvent.type(field, `${TOKEN}{Enter}`);
    expect(await screen.findByText("Pasted a new token for corp-gl. signed in as alice")).toBeInTheDocument();
    expect(posts(calls).map((c) => `${c.method} ${c.path}`)).toEqual(["PUT /forges/corp-gl/token", "POST /forges/corp-gl/test"]);
    expect(posts(calls)[0].body).toEqual({ value: TOKEN, kind: "pat" });
    expect(screen.queryByDisplayValue(TOKEN)).toBeNull();
    expect(document.body.innerHTML).not.toContain(TOKEN);
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/forges")).toHaveLength(2));
  });

  it("a refused paste closes the sheet, toasts the reason and leaves the token nowhere", async () => {
    const { calls } = open({ "PUT /forges/corp-gl/token": [422, { detail: "A token is one line." }] });
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Paste a new token" }));
    await userEvent.type(screen.getByLabelText(/Paste a new token for corp-gl/, { selector: "input" }), `${TOKEN}{Enter}`);
    expect(await screen.findByText("A token is one line.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByDisplayValue(TOKEN)).toBeNull();
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(posts(calls)).toHaveLength(1);
  });

  it.each([
    ["removed", [204, undefined], null],
    ["refused", [409, { detail: "repo-zero names corp-gl: give it another forge account first." }], "repo-zero names corp-gl"],
  ] as [string, [number, unknown], string | null][])("Remove asks first, saying a pasted token goes with it: %s", async (_id, answer, refusal) => {
    const { calls } = open({ "DELETE /forges/corp-gl": answer });
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText(PHONE_REMOVE)).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));
    if (refusal) {
      expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(refusal);
    } else {
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByText("Removed corp-gl")).toBeInTheDocument();
    }
    expect(posts(calls).map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /forges/corp-gl"]);
  });

  it("Test is disabled and reads Testing… while the probe runs, then says how it went and re-reads the list", async () => {
    let done: (r: Response) => void = () => {};
    const { calls } = open({ "POST /forges/corp-gl/test": TESTED });
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", (u: string, init?: RequestInit) => (init?.method === "POST" ? new Promise<Response>((r) => { done = r; }) : real(u, init)));
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    const busy = await screen.findByRole("button", { name: "Testing…" });
    expect(busy).toBeDisabled();
    done(new Response(JSON.stringify(TESTED[1]), { status: 200 }));
    expect(await screen.findByText("signed in as alice")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/forges")).toHaveLength(2));
  });

  it("after a paste, the account's menu reopened during the check reads Testing… and is disabled", async () => {
    let done: (r: Response) => void = () => {};
    open({ "PUT /forges/corp-gl/token": [200, {}] });
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", (u: string, init?: RequestInit) => (init?.method === "POST" ? new Promise<Response>((r) => { done = r; }) : real(u, init)));
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Paste a new token" }));
    await userEvent.type(screen.getByLabelText(/Paste a new token for corp-gl/, { selector: "input" }), `${TOKEN}{Enter}`);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await tap("corp-gl");
    expect(await screen.findByRole("button", { name: "Testing…" })).toBeDisabled();
    done(new Response(JSON.stringify(TESTED[1]), { status: 200 }));
    expect(await screen.findByText(/signed in as alice/)).toBeInTheDocument();
  });

  it("a test running for one account does not busy or close another's sheet", async () => {
    let done: (r: Response) => void = () => {};
    const { calls } = open({ "POST /forges/corp-gl/test": TESTED });
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", (u: string, init?: RequestInit) => (init?.method === "POST" && String(u).includes("corp-gl") ? new Promise<Response>((r) => { done = r; }) : real(u, init)));
    await tap("corp-gl");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByRole("button", { name: "Testing…" });
    await userEvent.keyboard("{Escape}");
    await tap("acme-gh");
    expect(screen.getByRole("button", { name: "Test" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Paste a new token" }));
    done(new Response(JSON.stringify(TESTED[1]), { status: 200 }));
    expect(await screen.findByText("signed in as alice")).toBeInTheDocument();
    // test() re-reads the list right after it would have closed the sheet.
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/forges")).toHaveLength(2));
    expect(screen.getByLabelText(/Paste a new token for acme-gh/, { selector: "input" })).toBeInTheDocument();
  });
});
