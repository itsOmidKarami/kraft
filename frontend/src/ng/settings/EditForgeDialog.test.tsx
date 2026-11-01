import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgesFixture } from "../../../e2e/contract/mocks/ngForges";
import { stubFetch } from "../item/testkit";
import { Toaster } from "../ui/Toast";
import { EditForgeDialog } from "./EditForgeDialog";
import { COMMAND_READ_ONLY, COMMAND_SET_WITH, DEFAULT_REMOVABLE, ENV_RESTART, NAMED_BY, SWITCH_DELETES_PASTED, TO_CLI_DROPS } from "./forgeWords";

const TOKEN = "glpat-" + "x1y2z3w4v5u6t7s8r9q0";
const FIXTURE = forgesFixture(new Date());
const TESTED = { ok: true, output: "signed in as alice\nsees 2 repos" };

const mount = (name: string, answers: Parameters<typeof stubFetch>[0] = {}, repos: { name: string; forge_account: string | null }[] = []) => {
  const calls = stubFetch({ "PATCH /forges/corp-gl": [200, {}], "POST /forges/corp-gl/test": [200, TESTED], ...answers });
  const props = { onClose: vi.fn(), onSaved: vi.fn(), onRemove: vi.fn() };
  render(<><EditForgeDialog account={{ name, ...FIXTURE[name] }} repos={repos} {...props} /><Toaster /></>);
  return { calls, of: (m: string) => calls.filter((c) => c.method === m), ...props };
};
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));
const pick = (name: string) => fireEvent.click(screen.getByRole("radio", { name }));
const pw = () => document.querySelector<HTMLInputElement>('input[type="password"]');

afterEach(() => vi.unstubAllGlobals());

describe("EditForgeDialog", () => {
  it("name, forge and web address are text, focus opens on Client, and a Save sends only what changed", async () => {
    const { of, onSaved, onClose } = mount("corp-gl");
    expect(screen.queryByRole("textbox", { name: /^(Name|Forge|Web address)/ })).toBeNull();
    expect(screen.getByText("Web address").nextElementSibling).toHaveTextContent("https://gitlab.corp.example");
    expect(screen.getByRole("radio", { name: "api" })).toHaveFocus();
    expect(screen.getByText(/To change these, remove the account and add it again\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /^API address/ }), { target: { value: "https://gl.example/api/v4" } });
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ api_url: "https://gl.example/api/v4" });
    expect(onSaved).toHaveBeenCalled();
    expect(of("POST")).toEqual([]); // the token did not change: no new check
  });

  it("an emptied API address is sent as null", async () => {
    const { of, onClose } = mount("corp-gl");
    fireEvent.change(screen.getByRole("textbox", { name: /^API address/ }), { target: { value: "" } });
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ api_url: null });
  });

  // The server refuses a browser that replaces or erases a command (403); the UI leaves paste and the client switch off
  // a command account so a person never meets that. Do not simplify.
  it("a command source shows its command read-only and no Save ever sends token or client", async () => {
    const { of, onClose } = mount("vault-gl", { "PATCH /forges/vault-gl": [200, {}] });
    // A command can embed a token: its text is never in the page, only that there is one.
    expect(screen.getByText(COMMAND_SET_WITH)).toBeInTheDocument();
    expect(screen.getByText(COMMAND_READ_ONLY)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("op read");
    expect(screen.queryByRole("radio", { name: "paste" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Replace token" })).toBeNull();
    expect(pw()).toBeNull();
    expect(screen.getByRole("radio", { name: "cli" })).toBeDisabled();
    fireEvent.click(screen.getByRole("switch", { name: "Default for its host" }));
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ default_for_host: true });
  });

  it("an env source names its variable and says a new value needs a restart", () => {
    mount("old-gl");
    expect(screen.getByRole("textbox", { name: /Variable name/ })).toHaveValue("OLD_GL_TOKEN");
    expect(screen.getByText(ENV_RESTART)).toBeInTheDocument();
  });

  it.each([
    ["accepted", [200, {}], null],
    ["refused-422", [422, { detail: "the token holds whitespace or a control character" }], "the token holds whitespace or a control character"],
  ] as const)("Replace token PUTs once, keeps nothing and checks the new token: %s", async (_id, put, refusal) => {
    const { calls, of, onClose } = mount("corp-gl", { "PUT /forges/corp-gl/token": put as [number, unknown] });
    fireEvent.change(pw()!, { target: { value: TOKEN } });
    fireEvent.change(screen.getByRole("combobox", { name: /New token kind/ }), { target: { value: "oauth" } });
    fireEvent.click(screen.getByRole("button", { name: "Replace token" }));
    if (refusal) {
      expect(await screen.findByRole("alert")).toHaveTextContent(refusal);
      expect(screen.queryByDisplayValue(TOKEN)).toBeNull();
      expect(onClose).not.toHaveBeenCalled();
    } else {
      await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
      await screen.findByText(/Pasted a new token for corp-gl\. signed in as alice/);
      expect(of("POST").map((c) => c.path)).toEqual(["/forges/corp-gl/test"]);
    }
    expect(of("PUT")).toEqual([{ method: "PUT", path: "/forges/corp-gl/token", body: { value: TOKEN, kind: "oauth" } }]);
    expect(of("PATCH")).toEqual([]);
    expect(calls.filter((c) => JSON.stringify(c.body ?? "").includes(TOKEN))).toHaveLength(1);
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("a cli account offers no paste and no Replace token: going to api is a Save, then Replace token", async () => {
    const { of, onClose } = mount("github-cli", { "PATCH /forges/github-cli": [200, {}], "POST /forges/github-cli/test": [200, TESTED] });
    expect(pw()).toBeNull();
    expect(screen.queryByRole("button", { name: "Replace token" })).toBeNull();
    pick("api");
    expect(screen.queryByRole("radio", { name: "paste" })).toBeNull();
    expect(pw()).toBeNull();
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ client: "api", token: { from: "git-credential", kind: "unknown" } });
    await screen.findByText(/Saved github-cli\. signed in as alice/);
  });

  it("a Save that changes the token source checks the new token", async () => {
    const { of, onClose } = mount("corp-gl");
    pick("variable");
    fireEvent.change(screen.getByRole("textbox", { name: /Variable name/ }), { target: { value: "CORP_GL" } });
    expect(screen.getByText(SWITCH_DELETES_PASTED)).toBeInTheDocument();
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ token: { from: "env", env: "CORP_GL", kind: "pat" } });
    await screen.findByText(/Saved corp-gl\. signed in as alice/);
  });

  it("switching to cli says what is dropped, and sends only the client", async () => {
    const { of, onClose } = mount("corp-gl");
    pick("cli");
    expect(screen.getByText(TO_CLI_DROPS)).toBeInTheDocument();
    save();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PATCH")[0].body).toEqual({ client: "cli" });
    expect(of("POST")).toEqual([]);
  });

  // A check can take 90 s. Test stays focused and announced (aria-disabled, never disabled), Cancel stays usable, and the
  // check that follows a new token holds the same flag so a second Test cannot run beside it.
  it.each([
    ["test", () => fireEvent.click(screen.getByRole("button", { name: "Test" })), {}],
    ["after-replace", () => { fireEvent.change(pw()!, { target: { value: TOKEN } }); fireEvent.click(screen.getByRole("button", { name: "Replace token" })); }, { "PUT /forges/corp-gl/token": [200, {}] }],
    ["after-save", () => { pick("variable"); fireEvent.change(screen.getByRole("textbox", { name: /Variable name/ }), { target: { value: "CORP_GL" } }); save(); }, {}],
  ] as const)("a running check is announced, keeps Test focused and Cancel usable: %s", async (id, start, more) => {
    let answer!: (r: Response) => void;
    const { onSaved, onClose } = mount("corp-gl", more as Record<string, [number, unknown]>);
    const real = fetch;
    const posts: string[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "POST" ? (posts.push(url), new Promise<Response>((r) => (answer = r))) : real(url, init))));
    if (id === "test") screen.getByRole("button", { name: "Test" }).focus();
    start();
    const busy = await screen.findByRole("button", { name: "Testing…" });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).not.toBeDisabled();
    if (id === "test") expect(busy).toHaveFocus();
    expect(screen.getByRole("status", { name: "Test status" })).toHaveTextContent("Testing corp-gl…");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    fireEvent.click(busy); // ignored: one check at a time
    expect(posts).toHaveLength(1);
    if (id !== "test") expect(onClose).not.toHaveBeenCalled(); // the dialog waits for the check
    answer(new Response(JSON.stringify(TESTED), { status: 200 }));
    if (id === "test") {
      expect(await screen.findByLabelText("Test output")).toHaveTextContent("sees 2 repos");
      expect(screen.getByRole("status", { name: "Test status" })).toContainElement(screen.getByLabelText("Test output"));
      expect(screen.getByRole("button", { name: "Test" })).not.toHaveAttribute("aria-disabled");
    } else await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalled();
  });

  it("Cancel and Esc close the dialog while a check runs", async () => {
    const { onClose } = mount("corp-gl");
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByRole("button", { name: "Testing…" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  // The Save has answered, so nothing is left to lose: Esc closes like Cancel instead of waiting out a 90 s check.
  it.each([
    ["after-save", () => { pick("variable"); fireEvent.change(screen.getByRole("textbox", { name: /Variable name/ }), { target: { value: "CORP_GL" } }); save(); }, {}],
    ["after-replace", () => { fireEvent.change(pw()!, { target: { value: TOKEN } }); fireEvent.click(screen.getByRole("button", { name: "Replace token" })); }, { "PUT /forges/corp-gl/token": [200, {}] }],
  ] as const)("Esc closes the dialog during the check that follows a new token: %s", async (_id, start, more) => {
    const { onClose } = mount("corp-gl", more as Record<string, [number, unknown]>);
    const real = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "POST" ? new Promise<Response>(() => {}) : real(url, init))));
    start();
    await screen.findByRole("button", { name: "Testing…" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["with-other-edits", true],
    ["alone", false],
  ])("Enter in the new token replaces it and never saves other edits: %s", async (_id, edited) => {
    const { of, onClose } = mount("corp-gl", { "PUT /forges/corp-gl/token": [200, {}] });
    if (edited) fireEvent.change(screen.getByRole("textbox", { name: /^API address/ }), { target: { value: "https://gl.example/api/v4" } });
    fireEvent.change(pw()!, { target: { value: TOKEN } });
    fireEvent.submit(pw()!.closest("form")!); // what Enter in the field does
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("PUT")).toHaveLength(1);
    expect(of("PATCH")).toEqual([]);
  });

  it.each([
    ["named", "shared-gl", [{ name: "repo-zero", forge_account: "shared-gl" }], false, "Can't remove: repo-zero uses it. Point it at another account first."],
    ["unnamed-default", "shared-gl", [], true, DEFAULT_REMOVABLE],
  ])("Remove: %s", (_id, name, repos, enabled, hint) => {
    const { onRemove } = mount(name, {}, repos);
    const remove = screen.getByRole("button", { name: "Remove account" });
    expect(screen.getByText(hint)).toBeInTheDocument();
    if (enabled) expect(screen.queryByText(NAMED_BY(["repo-zero"]))).toBeNull();
    else expect(remove).toBeDisabled();
    if (enabled) {
      fireEvent.click(remove);
      expect(onRemove).toHaveBeenCalledWith(name);
    }
  });
});
