import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { TemplateSummary } from "../../types";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · templates (5b)", () => {
  it("validates a draft without saving it", async () => {
    const validate = vi.spyOn(api, "validateTemplate").mockResolvedValue({
      id: "quick-task",
      valid: false,
      error: "hook(s) ['on.nope'] are not in the registry",
      unresolved: [{ node: "verify", task: "on.nope" }],
    });
    const put = vi.spyOn(api, "putTemplate");
    renderAt("/settings/templates");
    await screen.findByRole("button", { name: /quick-task/ });

    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(validate).toHaveBeenCalled();
    expect(await screen.findByText(/not in the registry/)).toBeInTheDocument();
    expect(screen.getByText("verify: on.nope does not resolve")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("refuses to save a draft that is not even JSON", async () => {
    renderAt("/settings/templates");
    const box = await screen.findByLabelText("template nodes");
    await userEvent.clear(box);
    await userEvent.type(box, "{{ broken");
    expect(screen.getByText(/not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("does not clobber a fresh edit with the re-fetch of the previous save", async () => {
    // A fake backend with a network-like gap: `putTemplate` commits quickly,
    // `getTemplates` (re-fetched by `reload`) reads it back more slowly. If
    // `busy` clears as soon as the PUT resolves, Save re-enables while the
    // page still holds pre-save state — the user types the next edit, and
    // then the late re-fetch lands and `setDraft(original)` wipes it.
    const backend: TemplateSummary[] = [
      {
        id: "quick-task",
        gates: 0,
        nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null }],
      },
    ];
    vi.spyOn(api, "getTemplates").mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve(backend.map((t) => ({ ...t }))), 40),
        ),
    );
    const put = vi.spyOn(api, "putTemplate").mockImplementation(
      (_id, nodes) =>
        new Promise((resolve) =>
          setTimeout(() => {
            backend[0] = { ...backend[0], nodes };
            resolve(undefined as never);
          }, 5),
        ),
    );
    renderAt("/settings/templates");

    await screen.findByRole("button", { name: /quick-task/ });
    const box = screen.getByLabelText("template nodes") as HTMLTextAreaElement;
    // fireEvent, not userEvent.type: `[` and `{` are key-descriptor syntax
    // for userEvent's keyboard parser, and this draft is JSON.
    fireEvent.change(box, { target: { value: '[{"id":"one"}]' } });
    const save = screen.getByRole("button", { name: "Save" });
    await userEvent.click(save);
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));

    // As soon as Save is live again, make the next edit — in the broken
    // version this lands after the PUT but before the re-fetch.
    await waitFor(() => expect(save).not.toBeDisabled());
    fireEvent.change(box, { target: { value: '[{"id":"two"}]' } });

    // give the slow re-fetch every chance to land on top of the new edit
    await new Promise((r) => setTimeout(r, 80));
    expect(box.value).toBe('[{"id":"two"}]');
  });

  it("shows the unsaved template change", async () => {
    renderAt("/settings/templates");
    const box = (await screen.findByLabelText("template nodes")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: `${box.value}\n` } });

    await userEvent.click(screen.getByRole("button", { name: "Changes" }));
    expect(await screen.findByTestId("draft-diff")).toBeInTheDocument();
  });

  it("draws the parsed draft as a chain bar and marks the node that does not resolve", async () => {
    vi.spyOn(api, "validateTemplate").mockResolvedValue({
      id: "quick-task",
      valid: false,
      error: "hook(s) ['on.nope'] are not in the registry",
      unresolved: [{ node: "verify", task: "on.nope" }],
    });
    renderAt("/settings/templates");
    await screen.findByRole("button", { name: /quick-task/ });

    // the diagram tracks what is typed, before any validation has run —
    // `findBy`, not `getBy`: the draft populates one effect tick after the
    // template list does, and a loaded CI runner can lose that race.
    expect(await screen.findByTestId("chain-bar")).toBeInTheDocument();
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "todo");

    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() =>
      expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "invalid"),
    );
  });
});

describe("Settings · route rename (UI v2 · 01)", () => {
  it("redirects the old /settings/templates path to /settings/chains", async () => {
    renderAt("/settings/templates");
    expect(await screen.findByRole("heading", { name: /template/i })).toBeInTheDocument();
  });
});

describe("Settings · phone (mobile app shell)", () => {
  it("Templates hides the editable textarea behind desktop-only and shows a read-only notice", async () => {
    renderAt("/settings/templates");
    await screen.findByText(/nodes · validated/i);
    expect(screen.getByLabelText("template nodes")).toHaveClass("desktop-only");
    expect(screen.getByText(/open on desktop to edit/i)).toBeInTheDocument();
    expect(screen.getByText(/open on desktop to edit/i)).toHaveClass("phone-only");
  });
});
