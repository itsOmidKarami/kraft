import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { PausedCard } from "./PausedCard";

const started = { id: "w1", current_node_id: "implement" } as never;
const neverStarted = { id: "w2", current_node_id: null } as never;

const session = {
  node_id: "implement",
  status: "paused",
  attempt: 1,
  hook_point: "implement",
} as never;

describe("PausedCard", () => {
  it("an interrupted item offers to resume the attempt it killed", () => {
    render(<PausedCard item={started} sessions={[session]} />);
    expect(screen.getByRole("button", { name: /resume with steer/i })).toBeTruthy();
    expect(screen.getByText(/as attempt 2/i)).toBeTruthy();
  });

  it("an item that never ran offers to start it, not resume it", () => {
    render(<PausedCard item={neverStarted} sessions={[]} />);
    expect(screen.getByRole("button", { name: /^start/i })).toBeTruthy();
    expect(screen.queryByText(/attempt/i)).toBeNull();
    expect(screen.queryByText(/relaunches/i)).toBeNull();
  });

  it("starting a never-run item resumes it through the same endpoint", async () => {
    const spy = vi
      .spyOn(api, "resumeWorkItem")
      .mockResolvedValue({ id: "w2", node_id: null, steer: null });
    render(<PausedCard item={neverStarted} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /^start/i }));
    expect(spy).toHaveBeenCalledWith("w2", undefined);
  });
});
