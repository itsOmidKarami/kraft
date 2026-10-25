import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Machine } from "../../types";
import { stubFetch } from "../item/testkit";
import { Toaster } from "../ui/Toast";
import { EditWorkerDialog } from "./EditWorkerDialog";
import { fixture, LOCAL_ROW } from "./testkit";

const SSH = { ...(fixture("gpu-box") as Machine), machine_capacity: 1, capacity: 1, capacity_source: "machine", remove_blocked: null, holders: [] } as Machine;
const BUSY = fixture("gpu-box") as Machine;
const DIAL = fixture("home-server") as Machine;

const open = async (m: Machine, answers: Parameters<typeof stubFetch>[0] = {}) => {
  const calls = stubFetch({ "GET /workers": [200, { workers: [LOCAL_ROW, SSH, DIAL] }], "GET /repos": [200, { repos: [{ name: "kraft" }, { name: "docs" }] }], ...answers });
  const props = { onClose: vi.fn(), onSaved: vi.fn(), onRemove: vi.fn() };
  render(<><EditWorkerDialog name={m.name} {...props} /><Toaster /></>);
  await screen.findByRole("heading", { name: `Edit ${m.name}` });
  await screen.findByRole("switch", { name: "Takes new steps" });
  return { calls, patches: () => calls.filter((c) => c.method === "PATCH"), ...props };
};
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));
const field = (label: RegExp) => screen.getByRole("textbox", { name: label });
const giveUp = () => screen.getByRole("combobox", { name: /Give up after/ });

afterEach(() => vi.unstubAllGlobals());

describe("EditWorkerDialog", () => {
  it("reads /workers when it opens and fills the fields from the machine", async () => {
    const { calls } = await open(SSH);
    expect(calls.filter((c) => c.path === "/workers")).toHaveLength(1);
    expect(screen.getByRole("switch", { name: "Takes new steps" })).toBeChecked();
    expect(screen.getByText(/running steps finish, nothing new starts/)).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: /Steps at once/ })).toHaveValue(2);
    expect(screen.getByText(/In use: 1\. The machine's own limit is 1\./)).toBeInTheDocument();
    expect(giveUp()).toHaveValue("2h");
    expect(screen.getByText("Changes apply from the next step; running steps finish where they are.")).toBeInTheDocument();
    expect(field(/ssh host/)).toHaveValue("gpu-box.lan");
    expect(screen.getByText("gpu")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Only some" })).toBeChecked();
    expect(screen.queryByText("Token")).toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("saves only what changed", async () => {
    const { patches, onSaved, onClose } = await open(SSH, { "PATCH /workers/gpu-box": [200, { worker: SSH }] });
    fireEvent.click(screen.getByRole("switch", { name: "Takes new steps" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: /Steps at once/ }), { target: { value: "" } });
    save();
    await screen.findByText(/Saved gpu-box/);
    expect(patches()).toEqual([{ method: "PATCH", path: "/workers/gpu-box", body: { enabled: false, capacity: null } }]);
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("sends groups, repos, give-up time and host when they change, and every repo as null", async () => {
    const { patches } = await open(SSH, { "PATCH /workers/gpu-box": [200, { worker: SSH }] });
    fireEvent.change(giveUp(), { target: { value: "never" } });
    fireEvent.change(field(/ssh host/), { target: { value: "box2.lan" } });
    const groups = screen.getByRole("textbox", { name: "Add to Groups" });
    fireEvent.change(groups, { target: { value: "big" } });
    fireEvent.keyDown(groups, { key: "Enter" });
    fireEvent.click(screen.getByRole("radio", { name: "Every repo" }));
    save();
    await screen.findByText(/Saved gpu-box/);
    expect(patches()[0].body).toEqual({ give_up_after: "never", groups: ["gpu", "linux", "big"], repos: null, ssh: "box2.lan" });
  });

  it("keeps a give-up time that is no preset as an extra option with its own text, and sends nothing for it", async () => {
    const odd = { ...SSH, give_up_after: "45m" } as Machine;
    const { patches } = await open(odd, { "GET /workers": [200, { workers: [LOCAL_ROW, odd, DIAL] }] });
    expect(giveUp()).toHaveValue("45m");
    expect(within(giveUp()).getAllByRole("option").map((o) => o.textContent)).toEqual(["30 minutes", "1 hour", "2 hours", "8 hours", "1 day", "never", "45m"]);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(patches()).toEqual([]);
  });

  it("shows the server's refusal above the form and stays open", async () => {
    const { onClose } = await open(SSH, { "PATCH /workers/gpu-box": [409, { detail: "gpu-box is running 1 step of X. Change its host once they end." }] });
    fireEvent.change(field(/ssh host/), { target: { value: "box2.lan" } });
    save();
    expect(await screen.findByRole("alert")).toHaveTextContent("gpu-box is running 1 step of X. Change its host once they end.");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a dial-in machine has no ssh host and a Token row that rotates only after a confirm, then shows the new token once", async () => {
    const { calls, onSaved } = await open(DIAL, { "POST /workers/home-server/rotate-token": [200, { worker: DIAL, token: "new", serve_command: "kraft admin worker serve --server http://h:1 --token new", note: "" }] });
    expect(screen.queryByRole("textbox", { name: /ssh host/ })).toBeNull();
    expect(screen.getByText(/Rotating stops the current token at once; run serve again with the new one/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rotate token" }));
    expect(calls.some((c) => c.path.endsWith("rotate-token"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.getByRole("button", { name: "Rotate token" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rotate token" }));
    fireEvent.click(screen.getByRole("button", { name: "Rotate now" }));
    expect(await screen.findByText("New token for home-server")).toBeInTheDocument();
    expect(screen.getByLabelText("Command to run on home-server")).toHaveTextContent("--token new");
    expect(screen.getByText("shown once")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalled();
  });

  it("the local worker offers only Takes new steps, steps at once (blank is no limit) and groups, and no Remove", async () => {
    const { patches } = await open(LOCAL_ROW, { "PATCH /workers/local": [200, { worker: LOCAL_ROW }] });
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).queryByRole("textbox", { name: /Give up after|ssh host/ })).toBeNull();
    expect(within(dlg).queryByRole("radio")).toBeNull();
    expect(within(dlg).queryByRole("button", { name: /Remove machine|Rotate/ })).toBeNull();
    expect(within(dlg).getByText(/Blank: no limit\./)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("spinbutton", { name: /Steps at once/ }), { target: { value: "4" } });
    save();
    await screen.findByText(/Saved local/);
    expect(patches()[0].body).toEqual({ capacity: 4 });
  });

  it("Remove machine sits apart from Save; held steps disable it and the server's reason is shown verbatim", async () => {
    await open(BUSY, { "GET /workers": [200, { workers: [LOCAL_ROW, BUSY, DIAL] }] });
    expect(screen.getByRole("button", { name: "Remove machine" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove machine" })).toHaveAccessibleDescription(BUSY.remove_blocked!);
    const why = screen.getByText(BUSY.remove_blocked!);
    expect(why).toBeInTheDocument();
    expect(why).toHaveAttribute("id", screen.getByRole("button", { name: "Remove machine" }).getAttribute("aria-describedby"));
    expect(why.closest(".dialog-footer")).not.toBeNull();
  });

  it("Remove machine closes the dialog and hands the removal to the page", async () => {
    const { onRemove, onClose } = await open(DIAL);
    fireEvent.click(screen.getByRole("button", { name: "Remove machine" }));
    expect(onClose).toHaveBeenCalled();
    expect(onRemove).toHaveBeenCalledWith("home-server");
  });

  it("says why when the machine cannot be read", async () => {
    stubFetch({ "GET /workers": [403, { detail: "a Kraft session cannot manage workers" }] });
    render(<EditWorkerDialog name="gpu-box" onClose={vi.fn()} onSaved={vi.fn()} onRemove={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("a Kraft session cannot manage workers");
  });
});
