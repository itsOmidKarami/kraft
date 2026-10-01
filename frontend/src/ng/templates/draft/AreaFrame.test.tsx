import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { useStore } from "../../../store";
import { Shell } from "../../shell/Shell";
import { reposView } from "../repos/fixture";
import type { Result } from "./types";
import { AreaFrame } from "./AreaFrame";
import * as d from "./draftApi";
import type { ConfigDraft } from "./useConfigDraft";

const AREA = { crumb: "Repos", files: ["repos.yaml"], toast: "Published repos", affects: () => <p>nothing runs</p> };

function mount(over: Partial<Result> = {}, draftFlag = false) {
  const draft = {
    view: reposView(over, draftFlag),
    scope: { area: "repos", key: "repos" },
    undo: vi.fn(),
    flush: vi.fn(),
    text: vi.fn(),
    publish: vi.fn(),
    keepMine: vi.fn(),
    discard: vi.fn(),
    stale: null,
  } as unknown as ConfigDraft;
  const onFix = vi.fn();
  render(
    <MemoryRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<AreaFrame draft={draft} area={AREA} pageKey="repos" onFix={onFix}>{() => <p>the page</p>}</AreaFrame>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  return { draft, onFix };
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

const change = { path: "/src/platform", kind: "change" as const, summary: "x" };

describe("AreaFrame", () => {
  it("says published, or DRAFT · N CHANGES, and counts problems as a badge that steps through them", async () => {
    const { onFix } = mount();
    expect(await screen.findByText("published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /PROBLEM/ })).toBeNull();
    document.body.innerHTML = "";
    const p = { path: "", field: "x", message: "m", file: "repos.yaml", line: 1, col: 1, repo: "/src/a" };
    const second = { ...p, repo: "/src/b" };
    const both = mount({ changes: [change], problems: [p, second] }, true);
    expect(await screen.findByText("DRAFT · 1 CHANGE")).toBeInTheDocument();
    const badge = screen.getByRole("button", { name: "2 PROBLEMS" });
    await userEvent.click(badge);
    await userEvent.click(badge);
    expect(both.onFix.mock.calls.map((c) => c[0].repo)).toEqual(["/src/a", "/src/b"]);
    expect(onFix).not.toHaveBeenCalled();
  });

  it("disables Review & publish until there is a change or a problem, and opens the area's review pane", async () => {
    mount();
    expect(await screen.findByRole("button", { name: "Review & publish" })).toBeDisabled();
    document.body.innerHTML = "";
    mount({ changes: [change] }, true);
    await userEvent.click(await screen.findByRole("button", { name: "Review & publish" }));
    expect(await screen.findByRole("heading", { name: "Draft · 1 change" })).toBeInTheDocument();
    expect(screen.getByText("nothing runs")).toBeInTheDocument();
  });

  it("undoes with ⌘Z outside a text field, and leaves the browser's own undo inside one", async () => {
    const { draft } = mount({ changes: [change] }, true);
    await screen.findByText("the page");
    await userEvent.keyboard("{Meta>}z{/Meta}");
    expect(draft.undo).toHaveBeenCalledTimes(1);
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    const ta = await screen.findByRole("textbox");
    await userEvent.click(ta);
    await userEvent.keyboard("{Meta>}z{/Meta}");
    expect(draft.undo).toHaveBeenCalledTimes(1);
  });

  it("opens the area's file in the YAML view and returns to the page, and Escape leaves review", async () => {
    mount({ changes: [change] }, true);
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    expect(await screen.findByRole("textbox", { name: "repos.yaml, YAML" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "⇄ Page" }));
    expect(await screen.findByText("the page")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    expect(await screen.findByRole("heading", { name: "Draft · 1 change" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Draft · 1 change" })).toBeNull());
  });

  it("gives a two-file area a tab per file", async () => {
    const draft = { view: { ...reposView({ changes: [change] }, true), files: { "intake.yaml": "a: 1\n", "policy.yaml": "b: 2\n" }, published: { "intake.yaml": "a: 1\n", "policy.yaml": "b: 1\n" } }, scope: { area: "intake", key: "intake" }, undo: vi.fn(), flush: vi.fn(), text: vi.fn(), stale: null } as unknown as ConfigDraft;
    render(
      <MemoryRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<AreaFrame draft={draft} area={{ ...AREA, files: ["intake.yaml", "policy.yaml"] }} pageKey="intake" onFix={vi.fn()}>{() => <p>page</p>}</AreaFrame>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    expect(screen.getByRole("textbox", { name: "intake.yaml, YAML" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "policy.yaml" }));
    await userEvent.type(await screen.findByRole("textbox", { name: "policy.yaml, YAML" }), "x");
    expect(draft.text).toHaveBeenLastCalledWith("policy.yaml", "b: 2\nx");
  });
});
