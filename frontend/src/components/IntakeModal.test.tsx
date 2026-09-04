import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { IntakeModal } from "./IntakeModal";

describe("IntakeModal", () => {
  it("submits and shows an inline error on failure", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }]);
    vi.spyOn(api, "createWorkItem").mockRejectedValue(new Error("repo path does not exist"));
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/nope");
    await userEvent.type(screen.getByLabelText("title"), "do a thing");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(await screen.findByText(/repo path does not exist/)).toBeInTheDocument();
  });

  it("closes and navigates on success, omitting chain_template for quick-task", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }, { id: "default" }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    const onClose = vi.fn();
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<IntakeModal onClose={onClose} />} />
          <Route path="/work-items/:id" element={<p>detail for w9</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "do a thing");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({ repo: "/r", title: "do a thing" }));
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText("detail for w9")).toBeInTheDocument();
  });

  it("sends chain_template when a non-default template is picked", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }, { id: "default" }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.selectOptions(await screen.findByLabelText("template"), "default");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ repo: "/r", title: "t", chain_template: "default" }),
    );
  });

  it("does not submit a template the server never offered", async () => {
    // The select defaults to "quick-task" before /templates answers. If the
    // server does not offer it, the rendered select falls back to its first
    // option while state still says quick-task — submitting the wrong chain.
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default" }, { id: "release" }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    const select = (await screen.findByLabelText("template")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("default"));
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ repo: "/r", title: "t", chain_template: "default" }),
    );
  });
});
