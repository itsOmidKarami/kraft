import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgesFixture } from "../../../e2e/contract/mocks/ngForges";
import { stubFetch } from "../item/testkit";
import { Toaster } from "../ui/Toast";
import { EditForgeDialog } from "./EditForgeDialog";
import { COMMAND_READ_ONLY, DEFAULT_REMOVABLE, ENV_RESTART, NAMED_BY, SWITCH_DELETES_PASTED, TO_CLI_DROPS } from "./forgeWords";

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
  it("name, forge and web address are read-only, and a Save sends only what changed", async () => {
    const { of, onSaved, onClose } = mount("corp-gl");
    expect(screen.getByRole("textbox", { name: /^Name/ })).toHaveAttribute("readonly");
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

  // The server does not stop a browser replacing or erasing a command (PUT /token never calls _no_command, and a PATCH
  // with client cli or token null would): leaving paste and the client switch out here is the guard. Do not simplify.
  it("a command source shows its command read-only and no Save ever sends token or client", async () => {
    const { of, onClose } = mount("vault-gl", { "PATCH /forges/vault-gl": [200, {}] });
    expect(screen.getByLabelText("Token command")).toHaveTextContent("op read op://dev/gl/token");
    expect(screen.getByText(COMMAND_READ_ONLY("vault-gl"))).toBeInTheDocument();
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

  it("Test is disabled and reads Testing… while the check runs, then shows the output and re-reads the list", async () => {
    let answer!: (r: Response) => void;
    const { onSaved } = mount("corp-gl");
    const real = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => (init?.method === "POST" ? new Promise<Response>((r) => (answer = r)) : real(url, init))));
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    const busy = await screen.findByRole("button", { name: "Testing…" });
    expect(busy).toBeDisabled();
    answer(new Response(JSON.stringify(TESTED), { status: 200 }));
    expect(await screen.findByLabelText("Test output")).toHaveTextContent("sees 2 repos");
    expect(screen.getByRole("button", { name: "Test" })).toBeEnabled();
    expect(onSaved).toHaveBeenCalled();
  });

  it.each([
    ["named", "shared-gl", [{ name: "repo-zero", forge_account: "shared-gl" }], false, NAMED_BY(["repo-zero"])],
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
