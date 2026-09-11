import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · steering", () => {
  it("loads a file's body only when it is picked, and saves it back", async () => {
    const get = vi.spyOn(api, "getSteeringFile");
    const put = vi
      .spyOn(api, "putSteeringFile")
      .mockImplementation(async (name, body) => ({ name, body }));
    renderAt("/settings/steering");
    await screen.findByRole("heading", { name: "Steering" });

    // the list is a picker: every body at once would be the whole injection
    // budget over the wire on every page load
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByText("14 B")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /house-style/ }));
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    // unchanged body: nothing to save
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(box, { target: { value: "prefer stdlib, then native\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("house-style", "prefer stdlib, then native\n");
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });

  it("meters the open file, not the whole directory", async () => {
    // MAX_BYTES is the assembled budget of one repo or hook's steering list.
    // Summing every file in the directory against it reads as over budget when
    // nothing is, and under it when something is.
    vi.spyOn(api, "getSteering").mockResolvedValue({
      files: [
        { name: "house-style", bytes: 14 },
        { name: "other", bytes: 9000 },
      ],
      max_bytes: 8192,
    });
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByRole("button", { name: /house-style/ }));
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    // 14 B open, 9014 B on disk in total — the meter must say 14. Scoped to the
    // hint because the list row legitimately shows this file's size too.
    const hint = screen.getByText(/counts toward/);
    expect(hint).toHaveTextContent("14 B");
    expect(hint).not.toHaveTextContent("9014");

    fireEvent.change(box, { target: { value: "12345" } });
    await waitFor(() => expect(screen.getByText(/counts toward/)).toHaveTextContent("5 B"));
  });

  it("surfaces a refused delete instead of dropping the file from the list", async () => {
    // A hook still naming the file is exactly when the server says no, and it
    // is the case the operator most needs to read.
    vi.spyOn(api, "deleteSteeringFile").mockRejectedValue(
      new Error("registry.yaml: steering 'house-style' does not resolve"),
    );
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByRole("button", { name: /house-style/ }));
    await screen.findByLabelText("steering body");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/does not resolve/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /house-style/ })).toBeInTheDocument();
  });

  it("shows the unsaved steering change and discards it back to the saved body", async () => {
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByRole("button", { name: /house-style/ }));
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    fireEvent.change(box, { target: { value: "prefer stdlib\nthen native\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Changes" }));
    const diff = await screen.findByTestId("draft-diff");
    expect(within(diff).getByText("+then native")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(box.value).toBe("prefer stdlib\n");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(await screen.findByText("no unsaved changes")).toBeInTheDocument();
  });
});

describe("Settings · phone (mobile app shell)", () => {
  it("Steering hides the editable textarea the same way once a file is selected", async () => {
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByText("house-style"));
    expect(await screen.findByLabelText("steering body")).toHaveClass("desktop-only");
    expect(screen.getByText(/open on desktop to edit/i)).toBeInTheDocument();
  });
});
