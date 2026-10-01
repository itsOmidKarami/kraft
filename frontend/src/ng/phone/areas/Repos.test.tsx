import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DETECTED, problemAt, REPOS, reposView } from "../../templates/repos/fixture";
import { FIELDS, patchFor } from "../../templates/repos/fields";
import { ReposList, RepoView } from "./Repos";
import { mountAt, posts, where } from "./testkit";

const OPS: [number, unknown] = [200, { ...reposView({}, true), ops: [] }];
const ans = (v = reposView(), more: Record<string, [number, unknown]> = {}) => ({ "GET /drafts/repos/repos": [200, v] as [number, unknown], "GET /templates/chains": [200, [{ id: "default" }, { id: "docs_only" }]] as [number, unknown], "POST /drafts/repos/repos/ops": OPS, ...more });
const ops = (calls: ReturnType<typeof mountAt>["calls"]) => posts(calls).map((c) => (c.body as { ops: unknown[] }).ops[0]);
afterEach(() => vi.unstubAllGlobals());

describe("Repos list (M.2)", () => {
  const list = (a = ans(), path = "/templates/repos") => mountAt(<ReposList />, path, "/templates/repos", a);

  it("lists the connected repos with their state, running count and chain, and the detected ones", async () => {
    list();
    const connected = await screen.findByRole("region", { name: "Connected" });
    expect(within(connected).getByRole("link", { name: /^product_root/ })).toHaveTextContent("on");
    expect(within(connected).getByRole("link", { name: /^platform/ })).toHaveTextContent("1 running");
    expect(within(connected).getByRole("link", { name: /^docs-site/ })).toHaveTextContent("off");
    expect(within(connected).getByRole("link", { name: /^docs-site/ })).toHaveTextContent("chain docs_only");
    expect(within(screen.getByRole("region", { name: "Detected" })).getByText("/src/plugins")).toBeInTheDocument();
  });

  it("connects a detected repo with connect_detected", async () => {
    const { calls } = list();
    await userEvent.click(await screen.findByRole("button", { name: /^plugins/ }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "connect_detected", path: "/src/plugins" }]));
  });

  it("filters by name or path, and keeps the filter in the URL", async () => {
    list();
    await userEvent.type(await screen.findByRole("searchbox", { name: "Filter repos" }), "docs");
    expect(where()).toBe("/templates/repos?q=docs");
    expect(screen.queryByRole("link", { name: /^platform/ })).toBeNull();
    expect(screen.getByRole("link", { name: /^docs-site/ })).toBeInTheDocument();
  });

  it("says connecting by path is done on a computer when none is connected", async () => {
    list(ans({ ...reposView(), result: { ...reposView().result, resolved: { repos: [], detected: [] } as never } }));
    expect(await screen.findByText(/No repository is connected\. Connect one from a computer/)).toBeInTheDocument();
  });

  it("marks a repo with a problem", async () => {
    list(ans(reposView({ problems: [problemAt("/src/platform", "test_command", "is not a command")] }, true)));
    expect(await screen.findByRole("link", { name: /^platform/ })).toHaveTextContent("problem");
  });

  it("connect_detected failure shows the server's words", async () => {
    list(ans(reposView(), { "POST /drafts/repos/repos/ops": [422, { detail: "already connected", op: 0 }] }));
    await userEvent.click(await screen.findByRole("button", { name: /^plugins/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already connected");
    void DETECTED;
  });
});

describe("a repo (M.2)", () => {
  const open = (name: string, a = ans()) => mountAt(<RepoView />, `/templates/repos/${name}`, "/templates/repos/:repo", a);

  it("shows every field the desktop's Config tab edits, with where each value comes from", async () => {
    open("platform");
    expect(await screen.findByRole("heading", { level: 1, name: "platform" })).toBeInTheDocument();
    for (const f of FIELDS) expect(screen.getByRole("button", { name: new RegExp(`^${f.label.replace(/[()]/g, "\\$&")}`) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^tasks running cap/ })).toHaveTextContent("60");
    expect(screen.getByRole("button", { name: /^tasks running cap/ })).toHaveTextContent("this repo");
    expect(screen.getByRole("button", { name: /^deny tools/ })).toHaveTextContent("default");
    expect(screen.getByText("running items")).toBeInTheDocument();
  });

  it("an edit sends the desktop's set_repo patch, checked first with a preview", async () => {
    const { calls } = open("platform");
    await userEvent.click(await screen.findByRole("button", { name: /^test command/ }));
    const box = screen.getByLabelText("test command", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "pytest -q{Enter}");
    const want = { op: "set_repo", path: "/src/platform", patch: patchFor(REPOS[1], FIELDS.find((f) => f.key === "test_command")!, "pytest -q") };
    await waitFor(() => expect(ops(calls)).toEqual([want, want]));
  });

  it("a policy field patches inside the policy block, and a bad number is refused before any call", async () => {
    const { calls } = open("platform");
    await userEvent.click(await screen.findByRole("button", { name: /^tasks running cap/ }));
    const box = screen.getByLabelText("tasks running cap (min)", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "abc{Enter}");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    await userEvent.clear(box);
    await userEvent.type(box, "120{Enter}");
    await waitFor(() => expect(ops(calls)[0]).toEqual({ op: "set_repo", path: "/src/platform", patch: { policy: { time_cap_minutes: 120 } } }));
  });

  it("a refusal the preview finds stays in the sheet and saves nothing", async () => {
    const withProblem = { ...reposView({ problems: [problemAt("/src/platform", "test_command", "is not a command")] }, true), ops: [] };
    const { calls } = open("platform", ans(reposView(), { "POST /drafts/repos/repos/ops": [200, withProblem] }));
    await userEvent.click(await screen.findByRole("button", { name: /^test command/ }));
    await userEvent.type(screen.getByLabelText("test command", { selector: "input" }), "x{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Refused: is not a command");
    expect(posts(calls)).toHaveLength(1);
  });

  it("a choice field offers the chains", async () => {
    open("product_root");
    await userEvent.click(await screen.findByRole("button", { name: /^default chain/ }));
    const sheet = screen.getByRole("dialog", { name: "default chain" });
    expect(within(sheet).getAllByRole("radio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["default", "docs_only"]);
  });

  it("enabled is a switch that patches the entry", async () => {
    const { calls } = open("product_root");
    await userEvent.click(await screen.findByRole("switch", { name: /^enabled/ }));
    await waitFor(() => expect(ops(calls)[0]).toEqual({ op: "set_repo", path: "/src/product_root", patch: { enabled: false } }));
  });

  it("Disconnect is refused while items run, with the desktop's words, and calls nothing", async () => {
    const { calls } = open("platform");
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect repo" }));
    const sheet = screen.getByRole("dialog", { name: "Disconnect platform?" });
    expect(sheet).toHaveTextContent("platform has 1 running item. Disconnect is refused until it finishes.");
    expect(within(sheet).getByRole("button", { name: "Disconnect" })).toBeDisabled();
    expect(posts(calls)).toEqual([]);
  });

  it("Disconnect removes the repo in the draft and goes back to the list, when nothing runs", async () => {
    const { calls } = open("product_root");
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect repo" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "remove_repo", path: "/src/product_root" }]));
    await waitFor(() => expect(where()).toBe("/templates/repos"));
  });

  it("says there is no such repo", async () => {
    open("nope");
    expect(await screen.findByText("There is no connected repo nope.")).toBeInTheDocument();
  });
});
