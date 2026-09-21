import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, repo, setupSettingsMocks } from "./testing";
import { setPhoneWidth } from "../../testFixtures";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · steering (5c-bis, design 30)", () => {
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
    expect(screen.getByText(/14 B/)).toBeInTheDocument();

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

  it("shows who uses each steering file", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [repo({ steering: ["house-style"] })],
    });
    renderAt("/settings/steering");
    expect(await screen.findByText(/repo-a/)).toBeInTheDocument();
  });

  it("toggles between edit and diff vs saved via Tabs", async () => {
    renderAt("/settings/steering?file=house-style");
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));
    fireEvent.change(box, { target: { value: "prefer stdlib\nthen native\n" } });
    await userEvent.click(await screen.findByRole("tab", { name: /diff vs saved/i }));
    expect(screen.getByTestId("draft-diff")).toBeInTheDocument();
  });

  it("Delete lives behind the overflow menu and confirms first", async () => {
    const del = vi.spyOn(api, "deleteSteeringFile").mockResolvedValue({ deleted: "house-style" });
    renderAt("/settings/steering?file=house-style");
    await screen.findByLabelText("steering body");
    await userEvent.click(await screen.findByRole("button", { name: "More" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(del).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(del).toHaveBeenCalled();
  });

  it("surfaces a refused delete instead of dropping the file from the list", async () => {
    // A hook still naming the file is exactly when the server says no, and it
    // is the case the operator most needs to read.
    vi.spyOn(api, "deleteSteeringFile").mockRejectedValue(
      new Error("repos.yaml: steering 'house-style' does not resolve"),
    );
    renderAt("/settings/steering?file=house-style");
    await screen.findByLabelText("steering body");
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(await screen.findByText(/does not resolve/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /house-style/ })).toBeInTheDocument();
  });

  it("shows the unsaved steering change and discards it back to the saved body", async () => {
    renderAt("/settings/steering?file=house-style");
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    fireEvent.change(box, { target: { value: "prefer stdlib\nthen native\n" } });
    await userEvent.click(screen.getByRole("tab", { name: /diff vs saved/i }));
    const diff = await screen.findByTestId("draft-diff");
    expect(within(diff).getByText("+then native")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect((screen.getByLabelText("steering body") as HTMLTextAreaElement).value).toBe(
      "prefer stdlib\n",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("phone", () => {
  beforeEach(() => setPhoneWidth(true));

  it("the body is editable on phone, not the open-on-desktop notice", async () => {
    renderAt("/settings/steering?file=house-style");
    expect(await screen.findByLabelText("steering body")).toBeEnabled();
    expect(screen.queryByText(/open on desktop/i)).toBeNull();
  });

  it("saving on phone calls putSteeringFile", async () => {
    const put = vi.spyOn(api, "putSteeringFile").mockResolvedValue({ name: "house-style", body: "x" });
    renderAt("/settings/steering?file=house-style");
    await userEvent.type(await screen.findByLabelText("steering body"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalled();
  });
});
