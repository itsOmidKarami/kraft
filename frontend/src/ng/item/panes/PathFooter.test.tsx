import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptWrites, detail, stubFetch, type Call } from "../testkit";
import { PathFooter } from "./PathFooter";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/retry", "POST /work-items/w1/skip");

afterEach(() => vi.unstubAllGlobals());
const posts = (c: Call[]) => c.filter((x) => x.method === "POST");

describe("PathFooter", () => {
  it.each([
    ["running", ["Pause", "Skip task"]],
    ["paused", ["Resume", "Skip task", "Retry"]],
    ["stopped", ["Retry"]],
  ] as const)("%s shows %j, in that order, and no Retry while running", (state, names) => {
    render(<PathFooter item={detail()} path="v.r.code_review" what="task" state={state} reload={() => {}} />);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(names);
  });

  it("confirms Skip in the pane before sending it, by path", async () => {
    const calls = stubFetch(WRITES);
    const reload = vi.fn();
    render(<PathFooter item={detail()} path="verification" what="node" state="running" reload={reload} />);
    await userEvent.click(screen.getByRole("button", { name: "Skip node" }));
    expect(posts(calls)).toEqual([]);
    expect(screen.getByRole("group", { name: "Skip verification?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/skip", body: { path: "verification" } }]);
  });

  it("retries by path with the steer, and without the steer field when no agent can read it", async () => {
    const calls = stubFetch(WRITES);
    const { unmount } = render(<PathFooter item={detail()} path="v.r.code_review" what="task" state="stopped" reload={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await userEvent.type(screen.getByLabelText("Steer for the retry"), "check reindex");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "v.r.code_review", steer: "check reindex" } }]));
    unmount();
    render(<PathFooter item={detail({ steerable: false })} path="m.o.open" what="task" state="stopped" reload={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.queryByLabelText("Steer for the retry")).toBeNull();
  });

  it("retries on ⌘↵ from the steer", async () => {
    const calls = stubFetch(WRITES);
    render(<PathFooter item={detail()} path="v.r.code_review" what="task" state="stopped" reload={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await userEvent.type(screen.getByLabelText("Steer for the retry"), "check reindex");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "v.r.code_review", steer: "check reindex" } }]));
  });

  it("shows the server's refusal in the pane", async () => {
    stubFetch({ "POST /work-items/w1/pause": [409, { detail: "already paused" }] });
    render(<PathFooter item={detail()} path="verification" what="node" state="running" reload={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already paused");
  });
});
