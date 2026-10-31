import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgesFixture } from "../../../e2e/contract/mocks/ngForges";
import { stubFetch } from "../item/testkit";
import { _resetLaterForTests, UNDO_WINDOW_MS } from "../later";
import { Toaster } from "../ui/Toast";
import { ForgesPage, useForges } from "./ForgesPage";
import { WithHeader } from "./testkit";

const ACCOUNTS = forgesFixture(new Date());
const REPOS = [{ name: "repo-zero", forge_account: "shared-gl" }, { name: "stray", forge_account: "gone-gl" }, { name: "stray2", forge_account: "gone-gl" }, { name: "plain", forge_account: null }];
const answers = { "GET /forges": [200, { accounts: ACCOUNTS }], "GET /repos": [200, { repos: REPOS }] } as Record<string, [number, unknown]>;

const mount = (ui = <ForgesPage />) =>
  render(
    <WithHeader>
      <MemoryRouter>
        {ui}
        <Toaster />
      </MemoryRouter>
    </WithHeader>,
  );
afterEach(() => {
  _resetLaterForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const rowOf = async (name: string) => (await screen.findByText(name, { selector: "td" })).closest("tr")!;

describe("Settings › Forge accounts", () => {
  it("with no forges.yaml, says Kraft uses gh and glab as before, and offers Add account", async () => {
    stubFetch({ "GET /forges": [200, { accounts: {} }] });
    mount();
    expect(await screen.findByText(/No forge accounts\. Kraft uses gh and glab/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add account" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("is one table: each account's name, state, forge and address, token source (the client is in it), last check and the repos that name it", async () => {
    stubFetch(answers);
    mount();
    const table = await screen.findByRole("table", { name: "Forge accounts" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "State", "Forge", "Token", "Checked", "Used by", "Actions"]);
    expect(within(table).getAllByRole("row")).toHaveLength(Object.keys(ACCOUNTS).length + 1);
    const shared = await rowOf("shared-gl");
    expect(shared).toHaveTextContent(/^shared-glTest to checkgitlab https:\/\/code\.corp\.example\/gitlabglab's own sign-in—repo-zeroEdit$/);
    const corp = await rowOf("corp-gl");
    expect(corp).toHaveTextContent(/signed inas alicegitlab https:\/\/gitlab\.corp\.examplefile \/home\/kraft\/\.kraft\/secrets\/corp-gl5m ago—Edit/);
    expect(within(await rowOf("vault-gl")).getAllByRole("cell")[3]).toHaveTextContent(/^command$/);
    expect(document.body.textContent).not.toContain("op read");
    for (const name of Object.keys(ACCOUNTS)) expect(screen.getByRole("button", { name: `Edit ${name}` })).toBeInTheDocument();
    expect(screen.getByText(`${Object.keys(ACCOUNTS).length} accounts`)).toBeInTheDocument();
  });

  it.each([
    ["soon-gl", "expires in 3 days", "badge is-warn"],
    ["old-gl", "refused", "401 Unauthorized"],
    ["fresh-gl", "not checked yet", "is-muted"],
    ["blind-gl", "cannot see 1 repo", "cannot see team/app"],
    ["github-cli", null, "Test to check"],
  ])("the state carries its word: %s", async (name, word, extra) => {
    stubFetch(answers);
    mount();
    const row = await rowOf(name);
    if (word) expect(within(row).getByText(word)).toHaveClass("badge");
    else expect(row.querySelector(".badge")).toBeNull();
    if (extra.startsWith("badge ")) expect(within(row).getByText(word!)).toHaveClass(extra.split(" ")[1]);
    else if (extra.startsWith("is-")) expect(within(row).getByText(word!)).toHaveClass(extra);
    else expect(row).toHaveTextContent(extra);
  });

  it("an SSO address is a link that opens a new tab and passes no opener", async () => {
    stubFetch(answers);
    mount();
    const link = within(await rowOf("acme-gh")).getByRole("link");
    expect(link).toHaveTextContent("Authorize for single sign-on");
    expect(link).toHaveAttribute("href", ACCOUNTS["acme-gh"].probe!.sso_url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("lists repos whose forge_account is not in forges.yaml, one line each above the table, from the repos it already loads", async () => {
    stubFetch(answers);
    mount();
    await rowOf("old-gl");
    const line = screen.getByText("stray names gone-gl, which is not in forges.yaml: it uses gh or glab.");
    expect(screen.getByText(/^stray2 names gone-gl/)).not.toBe(line);
    expect(line.compareDocumentPosition(screen.getByRole("table"))).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(screen.queryByText(/repo-zero names/)).toBeNull();
  });

  it.each([
    ["never-loaded", "forges.yaml has an edit that is not applied: line 3: unknown key"],
    ["forbidden", "a Kraft session cannot manage forges"],
  ])("shows why the server could not list them: %s", async (_id, detail) => {
    stubFetch({ "GET /forges": [409, { detail }] });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("a check that outlives its dialog", () => {
  const TOKEN = "glpat-" + "x1y2z3w4v5u6t7s8r9q0";
  it.each([
    ["after-save", () => {
      fireEvent.click(screen.getByRole("radio", { name: "variable" }));
      fireEvent.change(screen.getByRole("textbox", { name: /Variable name/ }), { target: { value: "CORP_GL" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    }],
    ["after-replace", () => {
      fireEvent.change(document.querySelector<HTMLInputElement>('input[type="password"]')!, { target: { value: TOKEN } });
      fireEvent.click(screen.getByRole("button", { name: "Replace token" }));
    }],
  ])("a late answer never closes the dialog a person opened since: %s", async (_id, start) => {
    let answer!: (r: Response) => void;
    stubFetch({ ...answers, "PATCH /forges/corp-gl": [200, {}], "PUT /forges/corp-gl/token": [200, {}] });
    const real = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "POST" && String(url).endsWith("/test") ? new Promise<Response>((r) => (answer = r)) : real(url, init))));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit corp-gl" }));
    start();
    await screen.findByRole("button", { name: "Testing…" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit old-gl" }));
    await screen.findByRole("dialog", { name: "Edit old-gl" });
    await act(async () => answer(new Response(JSON.stringify({ ok: true, output: "signed in" }), { status: 200 })));
    expect(screen.getByRole("dialog", { name: "Edit old-gl" })).toBeInTheDocument();
  });
});

describe("removing an account", () => {
  /** A harness over the page's own hook: Remove is the Edit dialog's button; this drives the hook directly. */
  function Harness() {
    const { accounts, remove } = useForges();
    return <>{accounts?.map((a) => <button key={a.name} onClick={() => remove(a.name)}>Remove {a.name}</button>)}</>;
  }
  const removeShared = async (del: [number, unknown]) => {
    const calls = stubFetch({ ...answers, "DELETE /forges/shared-gl": del });
    mount(<Harness />);
    await screen.findByRole("button", { name: "Remove shared-gl" });
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Remove shared-gl" }));
    return calls;
  };
  const deletes = (calls: ReturnType<typeof stubFetch>) => calls.filter((c) => c.method === "DELETE");

  it("takes the row off, offers Undo, and sends the DELETE when the window ends (204, no error toast)", async () => {
    const calls = await removeShared([204, undefined]);
    expect(screen.queryByRole("button", { name: "Remove shared-gl" })).toBeNull();
    expect(screen.getByText("Removed shared-gl")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS - 1));
    expect(deletes(calls)).toHaveLength(0);
    await act(async () => vi.advanceTimersByTime(1));
    expect(deletes(calls)).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    ["named-meanwhile", { detail: "forge account 'shared-gl' is named by: /r", repos: ["/r"] }],
  ])("a removal the server refuses brings the row back with its reason: %s", async (_id, body) => {
    await removeShared([409, body]);
    await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS));
    expect(screen.getByRole("button", { name: "Remove shared-gl" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(body.detail);
  });

  it("Undo while the DELETE is in flight says one thing, not Too late and then an error", async () => {
    let answer!: (r: Response) => void;
    stubFetch(answers);
    const real = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "DELETE" ? new Promise<Response>((r) => (answer = r)) : real(url, init))));
    mount(<Harness />);
    await screen.findByRole("button", { name: "Remove shared-gl" });
    vi.useFakeTimers();
    // A hovered toast outlives the window, so its Undo can be pressed with the DELETE out: take its action as the toast got it.
    let undo: (() => void) | undefined;
    window.addEventListener("kraft:toast", (e) => void (undo = (e as CustomEvent).detail.action?.run), { once: true });
    fireEvent.click(screen.getByRole("button", { name: "Remove shared-gl" }));
    await act(async () => vi.advanceTimersByTime(UNDO_WINDOW_MS));
    act(() => undo!()); // the window is over and the DELETE is out
    expect(screen.queryByText(/Too late to undo/)).toBeNull();
    expect(screen.getByText("shared-gl is being removed.")).toBeInTheDocument();
    await act(async () => answer(new Response(JSON.stringify({ detail: "named" }), { status: 409 })));
    expect(screen.getByRole("alert")).toHaveTextContent("named");
  });
});
