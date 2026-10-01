import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useNavigationType } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { readBoardQuery, useBoardQuery, writeBoardQuery } from "./url";

describe("board query", () => {
  it("reads every field, with defaults for what is missing or unknown", () => {
    const q = readBoardQuery(new URLSearchParams("repo=/r/a&chain=docs&q=cache&group=repo&sort=title&sel=w1&new=1"));
    expect(q).toEqual({ repo: "/r/a", chain: "docs", q: "cache", group: "repo", sort: "title", sel: "w1", new: true });
    expect(readBoardQuery(new URLSearchParams("group=bogus&sort=bogus"))).toMatchObject({ group: "status", sort: "attention", new: false });
  });

  it("starts Group from the theme's board.group_by when the query has none, template reading as chain", () => {
    expect(readBoardQuery(new URLSearchParams(""), "template").group).toBe("chain");
    expect(readBoardQuery(new URLSearchParams(""), "repo").group).toBe("repo");
    expect(readBoardQuery(new URLSearchParams("group=status"), "repo").group).toBe("status");
  });

  it("writes only what is set, dropping empty values and the default sort", () => {
    const p = writeBoardQuery(new URLSearchParams("repo=/r/a&sel=w1"), { repo: "", q: "x", sort: "attention", new: true, sel: "" });
    expect(p.toString()).toBe("q=x&new=1");
  });

  it("replaces the history entry instead of pushing one", () => {
    const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={["/"]}>{children}</MemoryRouter>;
    const { result } = renderHook(() => ({ nav: useNavigationType(), board: useBoardQuery() }), { wrapper });
    act(() => result.current.board[1]({ q: "cache" }));
    expect(result.current.board[0].q).toBe("cache");
    expect(result.current.nav).toBe("REPLACE");
  });
});
