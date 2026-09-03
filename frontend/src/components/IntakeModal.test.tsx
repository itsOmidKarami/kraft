import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
});
