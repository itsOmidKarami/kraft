import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { showToast } from "../ui/Toast";
import { ESCALATION, NEVER, renderPage, resolved, serve, view } from "./testkit";

vi.mock("../ui/Toast", async (orig) => ({ ...(await orig<object>()), showToast: vi.fn() }));
afterEach(() => { vi.unstubAllGlobals(); vi.mocked(showToast).mockClear(); });

const CHANGES = [{ path: "harnesses.claude", kind: "change" as const, summary: "available → never" }];
const FILES = { "harnesses.yaml": "harnesses:\n  claude: {}\n", "policy.yaml": "maxima:\n  allowed_harnesses: [codex]\n" };
const PUBLISHED = { "harnesses.yaml": "harnesses:\n  claude: {}\n", "policy.yaml": "maxima:\n  allowed_harnesses: [claude, codex]\n" };
const open = async () => {
  await screen.findByRole("navigation", { name: "Harnesses and profiles" });
  await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
};

describe("Review & publish over two files", () => {
  it("shows only the file that changed in the YAML diff", async () => {
    serve(view(resolved(), { changes: CHANGES, files: FILES, published: PUBLISHED }));
    renderPage();
    await open();
    await userEvent.click(await screen.findByRole("tab", { name: "YAML diff" }));
    expect(screen.getByText("policy.yaml")).toBeInTheDocument();
    expect(screen.queryByText("harnesses.yaml")).not.toBeInTheDocument();
  });

  it("blocks Publish on the Never harness's problem, which names the task, and Fix → goes to the harness", async () => {
    serve(view(resolved({ access: { claude: "never" } }), { changes: CHANGES, problems: [NEVER] }));
    renderPage();
    await open();
    expect(await screen.findByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText(/implement\.main\.implementer: harness 'claude' is not in its allowed_harnesses/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fix →" }));
    expect(await screen.findByRole("complementary", { name: "claude pane" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
  });

  it("Fix → on the escalation problem opens the area's Config tab", async () => {
    serve(view(resolved({ access: { claude: "never" } }), { changes: CHANGES, problems: [ESCALATION] }));
    renderPage();
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Fix →" }));
    expect(await screen.findByRole("tab", { name: "Config", selected: true })).toBeInTheDocument();
  });

  it("a publish toasts that new launches use it, and the page reloads", async () => {
    const s = serve(view(resolved(), { changes: CHANGES }));
    renderPage();
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Published harnesses · new launches use it from now on"));
    expect(s.calls).toContain("POST /drafts/harnesses/harnesses/publish");
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
  });

  it("a 422 keeps the pane open and lists the server's problems", async () => {
    serve(view(resolved(), { changes: CHANGES }), { publishAnswer: () => new Response(JSON.stringify({ problems: [{ ...NEVER, message: "refused: claude is Never" }] }), { status: 422, headers: { "content-type": "application/json" } }) });
    renderPage();
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    expect(await screen.findByText(/refused: claude is Never/)).toBeInTheDocument();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("a 409 shows each file's server diff and keeps the draft", async () => {
    const body = { files: { "policy.yaml": { published: "a", draft: "b", diff: "@@\n-old\n+new" } } };
    serve(view(resolved(), { changes: CHANGES, files: FILES, published: PUBLISHED }), { publishAnswer: () => new Response(JSON.stringify(body), { status: 409, headers: { "content-type": "application/json" } }) });
    renderPage();
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("policy.yaml changed on disk");
    expect(screen.getByRole("button", { name: "Keep my version and publish" })).toBeInTheDocument();
  });
});

describe("YAML over two files", () => {
  it("offers a tab per file and shows the one picked", async () => {
    serve(view(resolved(), { files: FILES, published: PUBLISHED }));
    renderPage();
    await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    const tabs = within(await screen.findByRole("tablist", { name: "Files" })).getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["harnesses.yaml", "policy.yaml"]);
    await userEvent.click(screen.getByRole("tab", { name: "policy.yaml" }));
    expect((screen.getByRole("textbox", { name: "policy.yaml, YAML" }) as HTMLTextAreaElement).value).toContain("allowed_harnesses");
  });
});

describe("Edit in YAML", () => {
  it.each([["?harness=claude", "claude"], ["?profile=fast", "fast"]])("the %s pane's link opens harnesses.yaml", async (q) => {
    serve(view(resolved(), { files: FILES, published: PUBLISHED }));
    renderPage(q);
    await userEvent.click(await screen.findByRole("button", { name: "Edit in YAML" }));
    expect(await screen.findByRole("tab", { name: "harnesses.yaml", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "harnesses.yaml, YAML" })).toBeInTheDocument();
  });
});

describe("Escape", () => {
  it("goes up one level on the page, and closes Review first", async () => {
    serve(view(resolved(), { changes: CHANGES }));
    renderPage("?harness=claude");
    await open();
    expect(screen.queryByRole("complementary", { name: "claude pane" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByRole("complementary", { name: "claude pane" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByRole("complementary", { name: "harnesses pane" })).toBeInTheDocument();
  });
});
