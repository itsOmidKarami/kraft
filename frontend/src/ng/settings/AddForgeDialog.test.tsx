import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../item/testkit";
import { Toaster } from "../ui/Toast";
import { AddForgeDialog } from "./AddForgeDialog";
import { COMMAND_ADD_HINT, PASTE_ONCE } from "./forgeWords";

const TOKEN = "glpat-" + "x1y2z3w4v5u6t7s8r9q0"; // well over redact.MIN_LEN
const ROW = { forge: "gitlab", client: "api" };
const TESTED = { ok: true, output: "signed in as alice\nsees 2 repos" };

const mount = (answers: Parameters<typeof stubFetch>[0] = {}) => {
  const calls = stubFetch(answers);
  const props = { onClose: vi.fn(), onAdded: vi.fn() };
  render(<><AddForgeDialog {...props} /><Toaster /></>);
  return { calls, of: (m: string) => calls.filter((c) => c.method === m), ...props };
};
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
const pick = (name: string) => fireEvent.click(screen.getByRole("radio", { name }));
const pw = () => document.querySelector<HTMLInputElement>('input[type="password"]')!;
const fill = (name = "corp-gl") => {
  type(/^Name/, name);
  pick("gitlab");
  type(/^Web address/, "https://gitlab.corp.example");
};
const add = () => fireEvent.click(screen.getByRole("button", { name: "Add account" }));
const pasteIt = () => {
  fill();
  pick("api");
  pick("paste");
  fireEvent.change(pw(), { target: { value: TOKEN } });
  fireEvent.change(screen.getByRole("combobox", { name: /Token kind/ }), { target: { value: "pat" } });
  add();
};
const bodyHasToken = () => document.body.innerHTML.includes(TOKEN);

afterEach(() => vi.unstubAllGlobals());

describe("AddForgeDialog", () => {
  it.each([
    ["cli", () => {}, { client: "cli" }],
    ["api-git-credential", () => pick("api"), { client: "api", token: { from: "git-credential", kind: "unknown" } }],
    ["api-env", () => { pick("api"); pick("variable"); type(/Variable name/, "CORP_GL"); fireEvent.change(screen.getByRole("combobox", { name: /Token kind/ }), { target: { value: "pat" } }); }, { client: "api", token: { from: "env", env: "CORP_GL", kind: "pat" } }],
    ["api-file", () => { pick("api"); pick("file"); type(/File path/, "/etc/kraft/tok"); }, { client: "api", token: { from: "file", file: "/etc/kraft/tok", kind: "unknown" } }],
  ])("sends what the account needs and nothing more: %s", async (_id, setup, extra) => {
    const { of, onAdded, onClose } = mount({ "POST /forges": [201, ROW] });
    fill();
    setup();
    add();
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(of("POST")).toEqual([{ method: "POST", path: "/forges", body: { name: "corp-gl", forge: "gitlab", web_url: "https://gitlab.corp.example", default_for_host: false, ...extra } }]);
    expect(onAdded).toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    ["accepted", [200, ROW], null],
    ["refused-422", [422, { detail: "the token holds whitespace or a control character" }], "the token holds whitespace or a control character"],
  ] as const)("pasting sends the token once, in the PUT, and leaves it nowhere on the page: %s", async (_id, put, refusal) => {
    const { calls, of, onClose } = mount({
      "POST /forges": [201, ROW], "PUT /forges/corp-gl/token": put as [number, unknown], "DELETE /forges/corp-gl": [204, undefined], "POST /forges/corp-gl/test": [200, TESTED],
    });
    pasteIt();
    if (refusal) {
      expect(await screen.findByRole("alert")).toHaveTextContent(refusal);
      expect(screen.queryByDisplayValue(TOKEN)).toBeNull(); // the field is empty on any refusal
      expect(onClose).not.toHaveBeenCalled();
    } else {
      await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
      await screen.findByText(/Added corp-gl\. signed in as alice/); // a fresh check, not the old state
      expect(of("POST").map((c) => c.path)).toEqual(["/forges", "/forges/corp-gl/test"]);
    }
    expect(of("POST")[0].body).not.toHaveProperty("token");
    expect(of("PUT")).toEqual([{ method: "PUT", path: "/forges/corp-gl/token", body: { value: TOKEN, kind: "pat" } }]);
    expect(calls.filter((c) => JSON.stringify(c.body ?? "").includes(TOKEN))).toHaveLength(1);
    expect(bodyHasToken()).toBe(false);
  });

  it.each([
    ["removed", [204, undefined], /^the token is shorter than 8 characters$/],
    ["delete-refused-409", [409, { detail: "forge account 'corp-gl' is named by: /r" }], /shorter than 8 characters.*could not be removed again.*named by: \/r/],
  ] as const)("a refused paste takes the account it just added back out: %s", async (_id, del, shown) => {
    const { of, onAdded } = mount({
      "POST /forges": [201, ROW], "PUT /forges/corp-gl/token": [422, { detail: "the token is shorter than 8 characters" }], "DELETE /forges/corp-gl": del as [number, unknown],
    });
    pasteIt();
    expect(await screen.findByRole("alert")).toHaveTextContent(shown);
    expect(of("DELETE")).toHaveLength(1);
    // A 409 rollback leaves the account, so the list is read again to show it as it is.
    expect(onAdded).toHaveBeenCalledTimes(del[0] === 409 ? 1 : 0);
    expect(bodyHasToken()).toBe(false);
  });

  it.each([
    ["source", () => pick("file")],
    ["client", () => pick("cli")],
  ])("clears the pasted value when the %s moves off paste, and says what paste does", (_id, leave) => {
    mount();
    fill();
    pick("api");
    pick("paste");
    expect(screen.getByText(PASTE_ONCE)).toBeInTheDocument();
    expect(pw()).toHaveAttribute("autocomplete", "new-password");
    expect(pw()).toHaveAttribute("data-1p-ignore");
    fireEvent.change(pw(), { target: { value: TOKEN } });
    leave();
    pick(_id === "client" ? "api" : "paste");
    if (_id === "client") pick("paste");
    expect(pw()).toHaveValue("");
  });

  it("shows a refused add and stays open", async () => {
    const { onClose } = mount({ "POST /forges": [409, { detail: "forge account 'corp-gl' already exists" }] });
    fill();
    add();
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(onClose).not.toHaveBeenCalled();
  });

  // A command source is code run on the server's machine. POST refuses one too: the form never offers it.
  it("never offers a command source, and says where one is set", () => {
    mount();
    pick("api");
    expect(screen.queryByRole("radio", { name: /command/i })).toBeNull();
    expect(screen.getByText(COMMAND_ADD_HINT)).toBeInTheDocument();
  });
});
