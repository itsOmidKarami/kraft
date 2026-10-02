import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { draftWith, mount, ok, served, setup } from "./testSupport";
import { libView } from "./fixture";

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

const open = () => mount("/templates/library/steering.project-standards");
const rowsText = async () => {
  await waitFor(() => expect(document.querySelector(".lib-section-row")).not.toBeNull());
  return [...document.querySelectorAll(".lib-section-row")].map((x) => x.textContent);
};

describe("Library: a steering profile", () => {
  it("shows what an agent task reads at launch, in the server's order, each row expanding to its text", async () => {
    const u = userEvent.setup();
    open();
    const rows = await rowsText();
    expect(rows).toEqual(["contractkraft", "skillkraft:spec", "steeringrepo:never-signal-processes-you-didnt-start", "steeringtask:project-standardsthis profile"]);
    expect(document.querySelector(".lib-section-text")).toBeNull();
    const mine = screen.getByRole("button", { name: /task:project-standards/ });
    expect(mine).toHaveTextContent("this profile");
    await u.click(mine);
    expect(mine).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector(".lib-section-text")).toHaveTextContent("Keep changes focused.");
    await u.click(mine);
    expect(document.querySelector(".lib-section-text")).toBeNull();
  });

  it("asks for the first use and the first repository, and again when either select changes", async () => {
    const u = userEvent.setup();
    const calls: string[] = [];
    served.preview = (url) => { calls.push(decodeURIComponent(url)); return { status: 200, body: { sections: [] } }; };
    open();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toContain("chain=default&task=implementation.main.implement&repo=kraft");
    await u.selectOptions(await screen.findByLabelText("Repository"), "other");
    await waitFor(() => expect(calls[1]).toContain("repo=other"));
    await u.selectOptions(screen.getByLabelText("Use"), "quick-task · build.main.go");
    await waitFor(() => expect(calls[2]).toContain("chain=quick-task&task=build.main.go&repo=other"));
  });

  it("says the preview is the published text when the profile has draft changes", async () => {
    draftWith({ changes: [{ path: "steering.project-standards", kind: "change", summary: "instructions" }] }, true);
    open();
    expect(await screen.findByText("Preview shows the published text. Publish to see this change.")).toBeInTheDocument();
  });

  it("says nothing about the published text when only another component has draft changes", async () => {
    draftWith({ changes: [{ path: "nodes.verification", kind: "change", summary: "tasks" }] }, true);
    open();
    await rowsText();
    expect(screen.queryByText(/Preview shows the published text/)).toBeNull();
  });

  it("says what to do with no use, and with no repository", async () => {
    mount("/templates/library/steering.never-signal-processes-you-didnt-start");
    expect(await screen.findByText("Use this profile on a task to preview it.")).toBeInTheDocument();
  });

  it("says to connect a repository when there is none", async () => {
    served.repos = [];
    open();
    expect(await screen.findByText("Connect a repository to preview.")).toBeInTheDocument();
  });

  it.each([[422, "the harness 'x' cannot resolve"], [404, "unknown repository 'kraft'"], [503, "repos.yaml does not load"]])("shows the server's reason for a %i, never a blank", async (status, detail) => {
    served.preview = () => ({ status, body: { detail } });
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(document.querySelector(".lib-sections")).toBeNull();
  });
});

describe("Library: a steering profile's pane", () => {
  it("edits the instructions on a pause, and Used by lists chains only", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "set_field" }] }) as never);
    open();
    const field = await screen.findByLabelText("instructions");
    expect(field).toHaveValue("Keep changes focused.");
    await u.type(field, "!");
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "set_field", path: "steering.project-standards", field: "instructions", value: "Keep changes focused.!" }]), { timeout: 3000 });
    await u.click(screen.getByRole("tab", { name: "Used by" }));
    const used = await screen.findByRole("link", { name: "default" });
    expect(used).toHaveAttribute("href", "/templates/chains/default/nodes/implementation");
    expect(within(used.closest(".lib-use")!.parentElement!).queryByText(/repo/)).toBeNull();
  });

  it("calls an empty instruction a problem", async () => {
    draftWith({ model: { "library.yaml": { steering: { "project-standards": { instructions: "" } } } } });
    open();
    expect(await screen.findByText("Required.")).toBeInTheDocument();
  });
});
