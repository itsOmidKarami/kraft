import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as browserNotify from "../../browserNotify";
import type { Health } from "../../types";
import { KraftDefaultBanner } from "./KraftDefaultBanner";

const health = (signature: string | null, count: number) => ({ status: "ok", kraft_default: { signature, count } }) as Health;
const lines = ["one", "two", "three", "four", "five"];

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "getKraftDefault").mockResolvedValue({ lines, signature: "aaa" });
});
afterEach(() => vi.restoreAllMocks());

describe("KraftDefaultBanner", () => {
  it("fetches the lines and shows three, then how many more", async () => {
    render(<KraftDefaultBanner health={health("aaa", 5)} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Kraft's own chains need attention");
    expect(screen.getByText("three")).toBeInTheDocument();
    expect(screen.queryByText("four")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("and 2 more");
    expect(screen.getByRole("status")).toHaveTextContent("kraft admin doctor");
    expect(localStorage.getItem("kraft.kraftDefault.notified")).toBe("aaa");
    // A <ul> is not phrasing content: its wrapper cannot be a <span>.
    expect(document.querySelector(".ng-offline-text")?.tagName).toBe("DIV");
  });

  it("keeps a repeated line as its own row", async () => {
    vi.mocked(api.getKraftDefault).mockResolvedValue({ lines: ["same", "same"], signature: "aaa" });
    render(<KraftDefaultBanner health={health("aaa", 2)} />);
    expect(await screen.findAllByText("same")).toHaveLength(2);
  });

  it("offers a new signature to the browser notification", async () => {
    const spy = vi.spyOn(browserNotify, "notifyServer");
    render(<KraftDefaultBanner health={health("aaa", 5)} />);
    await screen.findByRole("status");
    expect(spy).toHaveBeenCalledWith("aaa", 5);
  });

  it("asks again when the first read failed", async () => {
    vi.mocked(api.getKraftDefault).mockRejectedValueOnce(new Error("down"));
    const { rerender } = render(<KraftDefaultBanner health={health("aaa", 5)} />);
    await waitFor(() => expect(api.getKraftDefault).toHaveBeenCalledTimes(1));
    rerender(<KraftDefaultBanner health={{ ...health("aaa", 5) }} />);
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("shows nothing without drift", () => {
    render(<KraftDefaultBanner health={health(null, 0)} />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(api.getKraftDefault).not.toHaveBeenCalled();
  });

  it("hides until the signature changes", async () => {
    const { rerender } = render(<KraftDefaultBanner health={health("aaa", 5)} />);
    await userEvent.click(await screen.findByRole("button", { name: "Hide until it changes" }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(localStorage.getItem("kraft.kraftDefault.hidden")).toBe("aaa");
    rerender(<KraftDefaultBanner health={health("aaa", 5)} />);
    expect(screen.queryByRole("status")).toBeNull();
    vi.mocked(api.getKraftDefault).mockResolvedValue({ lines: ["new"], signature: "bbb" });
    rerender(<KraftDefaultBanner health={health("bbb", 1)} />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("new"));
  });
});
