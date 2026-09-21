import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PhoneHeader, usePhone } from "./shared";
import { setPhoneWidth } from "../../testFixtures";

function Probe() {
  const phone = usePhone();
  return <span>{phone ? "phone" : "desktop"}</span>;
}

describe("usePhone", () => {
  it("reads the current matchMedia state", () => {
    setPhoneWidth(true);
    render(<Probe />);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("reads desktop when the query does not match", () => {
    setPhoneWidth(false);
    render(<Probe />);
    expect(screen.getByText("desktop")).toBeInTheDocument();
  });
});

describe("PhoneHeader", () => {
  it("renders the back link, title, subtitle and action", () => {
    render(
      <MemoryRouter>
        <PhoneHeader
          back="Settings"
          backTo="/settings"
          title="Repos"
          subtitle="3 connected"
          action={<button>Add</button>}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Repos")).toBeInTheDocument();
    expect(screen.getByText("3 connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });
});
