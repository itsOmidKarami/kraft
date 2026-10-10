import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Compare, CompareFile, ReviewThread } from "../../types";
import * as http from "../http";
import { FileTree } from "./FileTree";
import { useViewed, type Fetched } from "./useReview";

const file = (path: string, viewed = false): CompareFile => ({ path, insertions: 3, deletions: 1, touched_by: ["implementation"], viewed });
const FILES = [file("search/cache.py"), file("search/embed.py", true), file("tests/test_cache.py"), file("pyproject.toml")];
const thread = (file_path: string, state: ReviewThread["state"], draft = false) => ({ id: `${file_path}${state}`, file_path, state, draft, comments: [] }) as unknown as ReviewThread;

afterEach(() => vi.restoreAllMocks());

const tree = (o: Partial<Parameters<typeof FileTree>[0]> = {}) => {
  const onSelect = vi.fn();
  render(
    <FileTree
      files={FILES}
      untracked={["notes.txt"]}
      notShown={new Set(["pyproject.toml"])}
      threads={[thread("search/cache.py", "open"), thread("search/cache.py", "resolved"), thread("search/cache.py", "open", true)]}
      selected="search/embed.py"
      isViewed={(p) => FILES.find((f) => f.path === p)!.viewed}
      onSelect={onSelect}
      error={null}
      {...o}
    />,
  );
  return { onSelect };
};

describe("FileTree", () => {
  it("a one-file diff counts 1 file, not 1 files", () => {
    tree({ files: [FILES[0]] });
    expect(screen.getByText("1 file")).toBeInTheDocument();
  });

  it("groups by folder, top level last; counts, open threads, viewed, each kind's icon, the cut and untracked files", () => {
    tree();
    expect(screen.getAllByRole("button", { expanded: true }).map((b) => b.textContent)).toEqual(["▾search/", "▾tests/"]);
    expect([...document.querySelectorAll(".rv-file-name")].map((b) => b.getAttribute("title"))).toEqual(["search/cache.py", "search/embed.py (viewed)", "tests/test_cache.py", "pyproject.toml"]);
    expect([...document.querySelectorAll(".rv-file-icon")].map((i) => i.getAttribute("data-lang"))).toEqual(["python", "python", "python", "yaml"]);
    expect([...document.querySelectorAll(".rv-file-row.is-viewed .rv-file-name")].map((b) => b.textContent)).toEqual(["embed.py"]);
    expect(screen.getByText("4 files")).toBeInTheDocument();
    expect(screen.getByTitle("2 open threads")).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: /^pyproject.toml/ })).toHaveTextContent("not shown");
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 viewed")).toBeInTheDocument();
    expect(screen.getByText("3 threads: 1 pending, 1 open, 1 resolved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "embed.py" })).toHaveAttribute("aria-current", "true");
  });

  it("folds a folder, filters by path and selects", () => {
    const { onSelect } = tree();
    fireEvent.click(screen.getByRole("button", { name: "search/" }));
    expect(screen.queryByRole("button", { name: "cache.py" })).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter files" }), { target: { value: "test" } });
    expect(screen.queryByRole("button", { name: "search/" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "test_cache.py" }));
    expect(onSelect).toHaveBeenCalledWith("tests/test_cache.py");
  });
});

describe("useViewed", () => {
  const ready: Fetched<Compare> = { state: "ready", data: { files: FILES } as Compare };
  it("marks with PUT and unmarks with DELETE against the compare's `to`; a refusal takes it back with the server's words", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const { result } = renderHook(() => useViewed("w1", "attempt:2", ready));
    await act(() => result.current.toggle("search/cache.py", true));
    expect(result.current.isViewed("search/cache.py")).toBe(true);
    await act(() => result.current.toggle("search/embed.py", false));
    expect(req.mock.calls.map(([p, i]) => [p, i?.method])).toEqual([
      ["/work-items/w1/viewed?file=search%2Fcache.py&to=attempt%3A2", "PUT"],
      ["/work-items/w1/viewed?file=search%2Fembed.py&to=attempt%3A2", "DELETE"],
    ]);
    req.mockResolvedValue({ status: 409, body: { detail: "the worker still has this worktree open" } });
    await act(() => result.current.toggle("tests/test_cache.py", true));
    expect(result.current.isViewed("tests/test_cache.py")).toBe(false);
    expect(result.current.error).toBe("the worker still has this worktree open");
  });
});
