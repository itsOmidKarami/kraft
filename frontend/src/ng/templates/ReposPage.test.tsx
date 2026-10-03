import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import * as d from "./draft/draftApi";
import { fieldsFrom, gainsFrom, type Probe } from "./repos/ConnectForm";
import { DETECTED, problemAt, repo, REPOS, reposView } from "./repos/fixture";
import { ReposPage } from "./ReposPage";

let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}
const ok = <T,>(body: T, status = 200) => Promise.resolve({ status, body });

const mount = (path = "/templates/repos/platform") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/repos" element={<ReposPage />} />
          <Route path="/templates/repos/:repo" element={<ReposPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const probe = (over: Partial<Probe> = {}): Probe => ({ path: "/src/new", name: "new", branch: "main", test_command: "pytest", setup_command: "uv sync", test_scopes: [{ paths: ["**"], command: "pytest" }], forge: "gitlab", project: "a/new", ...over });

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(reposView()));
  vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...reposView({}, true), ops: [] }));
  vi.spyOn(http, "request").mockImplementation(((path: string) => {
    if (path === "/templates/chains") return ok([{ id: "default" }, { id: "docs_only" }]);
    if (path === "/repos/probe") return ok(probe());
    return ok({});
  }) as never);
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

const row = (name: string) => screen.getByRole("option", { name: new RegExp(`^${name},`) });

describe("Repos page: the table", () => {
  it("lists the repos with chain, steering, tests and state, the open count and the detected ones", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(reposView({ changes: [{ path: "/src/platform", kind: "change", summary: "policy", fields: ["policy"] }, { path: "/src/docs-site", kind: "add", summary: "" }] }, true)));
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    // Open, not running: the server counts every item not ended, and the board's Running group is only some of them.
    expect(row("platform")).toHaveTextContent("enabled · 1 open");
    expect(row("platform")).not.toHaveTextContent("running");
    expect(row("docs-site")).toHaveTextContent("disabled");
    expect(row("docs-site")).toHaveTextContent("docs_only");
    expect(row("platform").querySelector(".rp-mark.is-change")).not.toBeNull();
    expect(row("docs-site").querySelector(".rp-mark.is-add")).not.toBeNull();
    expect(row("product_root").querySelector(".rp-mark")).toBeNull();
    // A cell cut to one line carries the whole text in its title and is on the ellipsis allowlist.
    const steer = within(row("product_root")).getByText("project-standards");
    expect(steer).toHaveAttribute("title", "project-standards");
    expect(steer).toHaveAttribute("data-allow-ellipsis");
    expect(screen.getByRole("region", { name: "Detected, not connected" })).toHaveTextContent("plugins");
    expect(screen.getByText("DRAFT · 2 CHANGES")).toBeInTheDocument();
  });

  it("hides Detected while the search box has text, and says when nothing matches", async () => {
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.type(screen.getByLabelText("Search repos"), "docs");
    expect(screen.queryByRole("region", { name: "Detected, not connected" })).toBeNull();
    expect(screen.queryByRole("option", { name: /^platform,/ })).toBeNull();
    await userEvent.type(screen.getByLabelText("Search repos"), "zzz");
    expect(screen.getByText("No repo matches.")).toBeInTheDocument();
  });

  it("opens the repo in the URL and moves the selection with a click", async () => {
    mount("/templates/repos/platform");
    await screen.findByRole("listbox", { name: "Repos" });
    expect(row("platform")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("complementary", { name: "platform pane" })).toBeInTheDocument();
    await userEvent.click(row("docs-site"));
    expect(where).toBe("/templates/repos/docs-site");
  });
});

describe("Repos page: connecting", () => {
  it("connects a detected repo with connect_detected", async () => {
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(within(screen.getByRole("region", { name: "Detected, not connected" })).getByRole("button", { name: "Connect" }));
    expect(d.postOps).toHaveBeenCalledWith("repos", "repos", [{ op: "connect_detected", path: DETECTED[0].path }], undefined);
  });

  it("probes a path, shows what it found, then sends add_repo with the probe's fields", async () => {
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(http.request).toHaveBeenCalledWith("/repos/probe", expect.objectContaining({ method: "POST" }));
    const found = await screen.findByLabelText("What was found");
    expect(found).toHaveTextContent("pytest");
    expect(found).toHaveTextContent("setupuv sync");
    expect(d.postOps).not.toHaveBeenCalled();
    await userEvent.click(within(screen.getByRole("dialog", { name: "Connect a repo" })).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalled());
    const [, , ops] = vi.mocked(d.postOps).mock.calls[0];
    expect(ops[0]).toMatchObject({ op: "add_repo", path: "/src/new", fields: { name: "new", test_command: "pytest", forge: "gitlab", enabled: true } });
  });

  it("says where the probe's commands came from and that it found others", async () => {
    const cand = { dir: "", tier: "toolchain", marker: "uv.lock", detector: "uv", family: "python", corroborated: false } as const;
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe"
      ? ok(probe({ candidates: [
        { ...cand, role: "test", command: "pytest", source: "uv.lock", chosen: true },
        { ...cand, role: "setup", command: "uv sync", source: "uv.lock", chosen: true },
        { ...cand, role: "test", command: "make test", source: "Makefile target `test`", tier: "runner", chosen: false },
      ] }))
      : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const found = await screen.findByLabelText("What was found");
    expect(found).toHaveTextContent("pytest — from uv.lock");
    expect(found).toHaveTextContent("uv sync — from uv.lock");
    expect(found).toHaveTextContent("make test (Makefile target `test`)");
  });

  it("shows each nested scope's command it will save", async () => {
    const scopes = [{ paths: ["README.md", "src/**"], command: "pytest" }, { paths: ["web/**"], command: "sh -c 'cd web && npm test'" }];
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe" ? ok(probe({ test_scopes: scopes })) : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const found = await screen.findByLabelText("What was found");
    expect(found).toHaveTextContent("web/**sh -c 'cd web && npm test'");
  });

  it("says why it proposes no tests, what it cannot prepare, and which commit it read", async () => {
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe"
      ? ok(probe({
        test_command: null,
        test_scopes: [],
        read_from: "refs/remotes/origin/main",
        setup_command: null,
        missing_setup: ["web"],
        candidates: [{ dir: "", role: "setup", command: "uv sync", tier: "toolchain", source: "uv.lock", marker: "uv.lock", detector: "uv", family: "python", corroborated: false, chosen: true }],
        stopped: [{ dir: ".", reason: "a project (Gemfile) with no test command found", detector: "ruby" }],
      }))
      : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    const found = await screen.findByLabelText("What was found");
    expect(found).toHaveTextContent("the root is a project (Gemfile) with no test command found");
    expect(found).toHaveTextContent("uv sync found, but web/ has nothing to prepare it");
    expect(found).toHaveTextContent("origin/main, where work items start");
    // None proposed, not none found: the form says which, without the detector's word "stopped".
    expect(found).toHaveTextContent("No test command proposed: connects disabled until you set a test command in Templates › Repos.");
    expect(found).not.toHaveTextContent(/stopped/i);
  });

  it("says a repo with no tests connects disabled, as add_repo sends it, though the probe answers an empty scope list", async () => {
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe" ? ok(probe({ test_command: null, test_scopes: [] })) : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByLabelText("What was found")).toHaveTextContent("No tests found: connects disabled until you set a test command in Templates › Repos.");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Connect a repo" })).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalled());
    expect(vi.mocked(d.postOps).mock.calls[0][2][0]).toMatchObject({ op: "add_repo", fields: { enabled: false } });
  });

  it("checks a connected repo again and offers to save what its entry leaves undecided", async () => {
    // "Commit one (uv lock) and connect again" led to "already in the list", and nothing changed.
    const pyproj = repo("pyproj", { test_command: null, setup_command: null, enabled: false });
    vi.mocked(d.getDraft).mockImplementation(() => ok(reposView({ resolved: { repos: [...REPOS, pyproj], detected: DETECTED } as never })));
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe"
      ? ok(probe({ path: "/src/pyproj", test_command: "uv run pytest", setup_command: "uv sync" }))
      : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/pyproj");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByLabelText("What was found")).toHaveTextContent("Already connected: Update saves its test command and setup command, and enables it.");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Connect a repo" })).getByRole("button", { name: "Update" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalled());
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/pyproj", patch: { test_command: "uv run pytest", setup_command: "uv sync", enabled: true } }]);
  });

  it("leaves what a connected repo already has as it is", () => {
    expect(gainsFrom({ test_command: "make test", setup_command: "", enabled: false }, probe())).toEqual({});
    expect(gainsFrom({ test_command: "make test" }, probe())).toEqual({ setup_command: "uv sync" });
  });

  it("says a repo with no commit cannot be connected yet, and sends nothing", async () => {
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe" ? ok(probe({ read_from: null })) : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/src/new");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByLabelText("What was found")).toHaveTextContent("the repo has no commit yet, and a work item's branch starts from one");
    expect(within(screen.getByRole("dialog", { name: "Connect a repo" })).getByRole("button", { name: "Connect" })).toBeDisabled();
    expect(d.postOps).not.toHaveBeenCalled();
  });

  it("shows a probe's refusal inline and sends nothing", async () => {
    vi.mocked(http.request).mockImplementation(((path: string) => (path === "/repos/probe" ? ok({ detail: "/nowhere is not a git repository" }, 400) : ok([{ id: "default" }]))) as never);
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /Connect repo/ }));
    await userEvent.type(screen.getByLabelText("Path to a git repository"), "/nowhere");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("/nowhere is not a git repository");
    expect(d.postOps).not.toHaveBeenCalled();
  });

  it("keeps only a probed test scope with a nested path, and enables a repo that has tests", () => {
    // A lone root ** scope repeats test_command and would shadow later edits of it (Kraft-9wzy).
    expect(fieldsFrom(probe())).toMatchObject({ enabled: true });
    expect(fieldsFrom(probe())).not.toHaveProperty("test_scopes");
    const nested = [{ paths: ["web/**"], command: "npm test" }];
    expect(fieldsFrom(probe({ test_command: null, test_scopes: nested }))).toMatchObject({ test_scopes: nested, enabled: true });
    expect(fieldsFrom(probe({ test_command: null, test_scopes: null }))).toMatchObject({ enabled: false });
  });
});

describe("Repos page: the Config rows", () => {
  const cleanPreview = () => vi.mocked(d.postOps).mockImplementation(() => ok({ ...reposView({}, true), ops: [] }));

  it("checks an edit with ?preview=1 first and sends it only when nothing new is wrong", async () => {
    cleanPreview();
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /^test command, make test/ }));
    await userEvent.clear(screen.getByLabelText("test command"));
    await userEvent.type(screen.getByLabelText("test command"), "pytest -q{Enter}");
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    const [first, second] = vi.mocked(d.postOps).mock.calls;
    expect(first[3]).toBe(true);
    expect(second[3]).toBeFalsy();
    expect(first[2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { test_command: "pytest -q" } }]);
  });

  it("leaves the input open with the server's message, and saves nothing, when the preview adds a problem", async () => {
    vi.mocked(d.postOps).mockImplementation((_a, _k, _o, preview) => ok({ ...reposView({ problems: preview ? [problemAt("/src/platform", "allowed_tools", "'allowed_tools' cannot widen the inherited safety ceiling ['Read']; ['Bash'] is not allowed")] : [] }, true), ops: [] }));
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /^allowed tools, not set/ }));
    await userEvent.type(screen.getByRole("textbox", { name: "allowed tools" }), "Bash{Enter}");
    expect(await screen.findByText(/Refused: 'allowed_tools' cannot widen the inherited safety ceiling/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "allowed tools" })).toBeInTheDocument();
    expect(vi.mocked(d.postOps).mock.calls.every((c) => c[3] === true)).toBe(true);
  });

  it("says a repo needs no setup with a checkbox, or a typed \"\", both the empty command, never two quote marks", async () => {
    cleanPreview();
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    const none = screen.getByRole("checkbox", { name: "No setup needed" });
    expect(none).not.toBeChecked();
    await userEvent.click(none);
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { setup_command: "" } }]);
    await userEvent.click(screen.getByRole("button", { name: /^setup command, not set/ }));
    await userEvent.type(screen.getByLabelText("setup command"), '""{Enter}');
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(4));
    expect(vi.mocked(d.postOps).mock.calls[2][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { setup_command: "" } }]);
  });

  it("ticks No setup needed for a repo whose setup command is empty, and unticking clears it", async () => {
    cleanPreview();
    const repos = REPOS.map((r) => (r.name === "platform" ? { ...r, entry: { ...r.entry, setup_command: "" } } : r));
    vi.mocked(d.getDraft).mockImplementation(() => ok(reposView({ resolved: { repos, detected: DETECTED } as never })));
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    expect(screen.getByRole("button", { name: /^setup command, ""/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "No setup needed" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { setup_command: null } }]);
  });

  it("says a repo whose test command is \"\" has no tests and passes verify, where clearing it would stop its items", async () => {
    cleanPreview();
    const repos = REPOS.map((r) => (r.name === "platform" ? { ...r, entry: { ...r.entry, test_command: "" } } : r));
    vi.mocked(d.getDraft).mockImplementation(() => ok(reposView({ resolved: { repos, detected: DETECTED } as never })));
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    expect(screen.getByRole("button", { name: /^test command, no tests \(passes verify\)/ })).toBeInTheDocument();
    const none = screen.getByRole("checkbox", { name: "No tests" });
    expect(none).toBeChecked();
    await userEvent.click(none);
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { test_command: null } }]);
  });

  it("clears a value this repo sets with Reset, which sends null", async () => {
    cleanPreview();
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: "Reset test command" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { test_command: null } }]);
  });

  it("sets a policy key inside the entry's policy block", async () => {
    cleanPreview();
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: /^tasks running cap \(min\), 60/ }));
    await userEvent.clear(screen.getByLabelText("tasks running cap (min)"));
    await userEvent.type(screen.getByLabelText("tasks running cap (min)"), "45{Enter}");
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(2));
    expect(vi.mocked(d.postOps).mock.calls[0][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { policy: { time_cap_minutes: 45 } } }]);
    // A list-valued policy key goes inside the block too.
    await userEvent.click(screen.getByRole("button", { name: /^allowed tools, not set/ }));
    await userEvent.type(screen.getByRole("textbox", { name: "allowed tools" }), "Read, Grep{Enter}");
    await waitFor(() => expect(d.postOps).toHaveBeenCalledTimes(4));
    expect(vi.mocked(d.postOps).mock.calls[2][2]).toEqual([{ op: "set_repo", path: "/src/platform", patch: { policy: { time_cap_minutes: 60, allowed_tools: ["Read", "Grep"] } } }]);
  });
});

describe("Repos page: disconnecting and enabling", () => {
  it("refuses to disconnect a repo with an open item, and says why, without sending anything", async () => {
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByRole("alert")).toHaveTextContent("platform has 1 open item; finish or cancel it first. Disconnect is refused until then.");
    expect(d.postOps).not.toHaveBeenCalled();
  });

  it("disconnects an idle repo with remove_repo and moves to a neighbour", async () => {
    mount("/templates/repos/docs-site");
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(d.postOps).toHaveBeenCalledWith("repos", "repos", [{ op: "remove_repo", path: "/src/docs-site" }], undefined));
    await waitFor(() => expect(where).not.toBe("/templates/repos/docs-site"));
  });

  it("enables and disables with set_repo", async () => {
    mount("/templates/repos/docs-site");
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));
    expect(d.postOps).toHaveBeenCalledWith("repos", "repos", [{ op: "set_repo", path: "/src/docs-site", patch: { enabled: true } }], undefined);
  });
});

describe("Repos page: problems", () => {
  it("marks a repo with a problem, shows it on its field row, and Fix → in review selects it", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(reposView({ changes: [{ path: "/src/docs-site", kind: "change", summary: "", fields: [] }], problems: [problemAt("/src/docs-site", "test_command", "docs-site: not a command")] }, true)));
    mount("/templates/repos/platform");
    await screen.findByRole("listbox", { name: "Repos" });
    expect(within(row("docs-site")).getByRole("img", { name: "has a problem" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    await userEvent.click(await screen.findByRole("button", { name: "Fix →" }));
    expect(where).toBe("/templates/repos/docs-site");
    const rows = await screen.findAllByText("docs-site: not a command");
    expect(rows.some((e) => e.closest(".rp-row-cfg"))).toBe(true);
  });

  it("shows the repo's published YAML read-only in the YAML tab", async () => {
    vi.spyOn(d, "fragment").mockResolvedValue({ status: 200, body: { path: "/src/platform", text: "path: /src/platform\n" } });
    mount();
    await screen.findByRole("listbox", { name: "Repos" });
    await userEvent.click(screen.getByRole("tab", { name: "YAML" }));
    expect(await screen.findByText(/path: \/src\/platform/)).toBeInTheDocument();
    expect(d.fragment).toHaveBeenCalledWith("repos", "repos", "/src/platform");
  });

  it("offers a retry when the draft does not load", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok({ detail: "gone" }, 500) as never);
    mount();
    expect(await screen.findByRole("heading", { name: "Could not load repos" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("Repos fixture", () => {
  it("keeps the three repos the table reads", () => {
    expect(REPOS.map((r) => r.name)).toEqual(["product_root", "platform", "docs-site"]);
    expect(repo("x").path).toBe("/src/x");
  });
});

describe("Repos page: the pane's first state", () => {
  it("loads on the list with the pane on its rail, and a link to one repo opens it", async () => {
    mount("/templates/repos");
    await screen.findByRole("listbox", { name: "Repos" });
    expect(screen.getByRole("complementary", { name: /pane, collapsed$/ })).toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: /sections$/ })).toBeNull();
    document.body.innerHTML = "";
    mount("/templates/repos/platform");
    expect(await screen.findByRole("tablist", { name: /sections$/ })).toBeInTheDocument();
  });
});
