import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import type { Problem, Result } from "../templates/draft/types";
import { libView } from "./fixture";
import { draftWith, mount, ok, setup, where } from "./testSupport";

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

const CHAIN_PROBLEM: Problem = { path: "implementation.main.implement", field: "profile", message: "extends tasks.implementer, whose profile 'strong' does not exist", file: "chains/default.yaml", line: 14, col: 9, chain: "default", repo: null, component: "tasks.implementer" };
const REPO_PROBLEM: Problem = { path: "steering", field: "steering", message: "names the profile 'project-standards', which has no instructions", file: "repos.yaml", line: 8, col: 5, chain: null, repo: "kraft", component: "steering.project-standards" };
const OWN_PROBLEM: Problem = { path: "tasks.verify", field: "command", message: "Field required", file: "library.yaml", line: 3, col: 1, chain: null, repo: null, component: null };
const CHANGES: Result["changes"] = [
  { path: "tasks.implementer.prompt", kind: "change", summary: "prompt", reaches: ["default", "quick-task"] },
  { path: "steering.project-standards", kind: "change", summary: "instructions", reaches: ["default"] },
];
const IMPACT = { chains: ["default", "quick-task"], repos: ["kraft"] };

const review = async (u: ReturnType<typeof userEvent.setup>) => {
  await screen.findByRole("listbox", { name: "Library components" });
  await u.click(screen.getByRole("button", { name: /Review & publish/ }));
  return screen.findByRole("heading", { name: /Draft ·/ });
};

describe("Library: Review & publish", () => {
  it("lists the changes with the chains each reaches, and who the draft affects", async () => {
    const u = userEvent.setup();
    draftWith({ changes: CHANGES, impact: IMPACT }, true);
    mount();
    await review(u);
    expect(screen.getByRole("heading", { name: "Draft · 2 changes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tasks.implementer.prompt/ })).toHaveTextContent("reaches 2 chains");
    expect(screen.getByRole("button", { name: /steering.project-standards/ })).toHaveTextContent("reaches 1 chain");
    expect(screen.getByText("default · 2 changes, quick-task · 1 change")).toBeInTheDocument();
    expect(screen.getByText("kraft name a changed profile")).toBeInTheDocument();
    expect(screen.getByText("items keep the version they started on")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /steering.project-standards/ }));
    expect(where()).toBe("/templates/library/steering.project-standards");
    expect(screen.queryByRole("heading", { name: /Draft ·/ })).toBeNull();
  });

  it("names the chain, the path and the component of a problem it would break, and the repo of another", async () => {
    const u = userEvent.setup();
    draftWith({ changes: CHANGES, impact: IMPACT, problems: [CHAIN_PROBLEM, REPO_PROBLEM, OWN_PROBLEM] }, true);
    mount();
    await review(u);
    const chain = screen.getByText("extends tasks.implementer, whose profile 'strong' does not exist").closest(".tpl-rv-prob") as HTMLElement;
    expect(within(chain).getByText("implementation.main.implement · profile")).toBeInTheDocument();
    expect(within(chain).getByRole("link", { name: "default" })).toHaveAttribute("href", "/templates/chains/default");
    expect(chain).toHaveTextContent("from tasks.implementer");
    const repo = screen.getByText(/which has no instructions/).closest(".tpl-rv-prob") as HTMLElement;
    expect(repo).toHaveTextContent("breaks repo kraft");
    expect(repo).toHaveTextContent("from steering.project-standards");
    // A problem in library.yaml itself names neither.
    expect(screen.getByText("Field required").closest(".tpl-rv-prob")).not.toHaveTextContent("breaks");
    expect(screen.getByText("A chain or repo problem is fixed in the library component it names.")).toBeInTheDocument();
  });

  it("selects the library component a problem names when Fix is pressed, and leaves the draft", async () => {
    const u = userEvent.setup();
    draftWith({ changes: CHANGES, problems: [CHAIN_PROBLEM] }, true);
    mount();
    await review(u);
    await u.click(screen.getByRole("button", { name: "Fix →" }));
    expect(where()).toBe("/templates/library/tasks.implementer");
    expect(screen.queryByRole("heading", { name: /Draft ·/ })).toBeNull();
    expect(screen.getByText("DRAFT · 2 CHANGES")).toBeInTheDocument();
  });

  it("cannot publish with a problem, and publishes without one, then reads the published library again", async () => {
    const u = userEvent.setup();
    const publish = vi.spyOn(d, "publish").mockResolvedValue({ status: 200, body: { published: ["library.yaml"] } } as never);
    draftWith({ changes: CHANGES, problems: [CHAIN_PROBLEM] }, true);
    const { unmount } = mount();
    await review(u);
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText(/1 problem block publishing/)).toBeInTheDocument();
    unmount();
    draftWith({ changes: CHANGES }, true);
    mount();
    await review(u);
    const before = vi.mocked(fetch).mock.calls.filter((c) => String(c[0]).endsWith("/templates/library")).length;
    await u.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(publish).toHaveBeenCalledWith("library", "library"));
    expect(await screen.findByText("Published the library · new items use it from now on")).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter((c) => String(c[0]).endsWith("/templates/library")).length).toBeGreaterThan(before));
    expect(screen.queryByRole("heading", { name: /Draft ·/ })).toBeNull();
  });

  it("draws the problems a refused publish answers, naming the chain, and toasts nothing", async () => {
    const u = userEvent.setup();
    vi.spyOn(d, "publish").mockResolvedValue({ status: 422, body: { detail: "1 problem(s)", problems: [CHAIN_PROBLEM] } } as never);
    draftWith({ changes: CHANGES }, true);
    mount();
    await review(u);
    await u.click(screen.getByRole("button", { name: "Publish" }));
    const row = (await screen.findByText(/whose profile 'strong' does not exist/)).closest(".tpl-rv-prob") as HTMLElement;
    expect(within(row).getByRole("link", { name: "default" })).toBeInTheDocument();
    expect(screen.queryByText(/Published the library/)).toBeNull();
  });

  it("shows each file's diff of a stale publish, including a chain file a rename joined, and keeps the draft", async () => {
    const u = userEvent.setup();
    vi.spyOn(d, "publish").mockResolvedValue({ status: 409, body: { detail: "published since this draft began: library.yaml", files: { "library.yaml": { published: "a", draft: "b", diff: "--- published/library.yaml\n+++ draft/library.yaml\n@@ -1 +1 @@\n-a\n+b\n" }, "chains/default.yaml": { published: "c", draft: "d", diff: "--- published/chains/default.yaml\n+++ draft/chains/default.yaml\n@@ -1 +1 @@\n-c\n+d\n" } } } } as never);
    const rebase = vi.spyOn(d, "rebase").mockImplementation(() => ok(libView({}, true)) as never);
    draftWith({ changes: CHANGES }, true);
    mount();
    await review(u);
    await u.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Published since this draft began")).toBeInTheDocument();
    expect(screen.getByText("library.yaml", { selector: ".tpl-rv-file" })).toBeInTheDocument();
    expect(screen.getByText("chains/default.yaml", { selector: ".tpl-rv-file" })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Keep my version and publish" }));
    await waitFor(() => expect(rebase).toHaveBeenCalledWith("library", "library"));
  });

  it("diffs library.yaml, not a chain's file, against the published text", async () => {
    const u = userEvent.setup();
    vi.mocked(d.getDraft).mockImplementation(() => ok({ ...libView({ changes: CHANGES }, true), files: { "library.yaml": "tasks:\n  added_one: {}\n" } }) as never);
    mount();
    await review(u);
    await u.click(screen.getByRole("tab", { name: "YAML diff" }));
    const lines = (cls: string) => [...document.querySelectorAll(`.tpl-rv-line.${cls}`)].map((e) => e.textContent);
    await waitFor(() => expect(lines("is-add")).toContain("+   added_one: {}\n"));
    expect(lines("is-del")).toEqual(["- tasks: {}\n"]);
  });

  it("asks before it discards, and discards for good", async () => {
    const u = userEvent.setup();
    const discard = vi.spyOn(d, "discard").mockResolvedValue({ status: 204, body: undefined } as never);
    draftWith({ changes: CHANGES }, true);
    mount();
    await review(u);
    await u.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(discard).not.toHaveBeenCalled();
    expect(screen.getByText("Discard 2 changes?")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(discard).toHaveBeenCalledWith("library", "library"));
  });
});
