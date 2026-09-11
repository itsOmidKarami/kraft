import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { Login } from "./Login";

afterEach(() => vi.restoreAllMocks());

describe("Login", () => {
  it("names the address it is asking a password for", () => {
    render(<Login bind="192.168.1.20:8765" onSignedIn={() => {}} />);
    expect(screen.getByText(/192\.168\.1\.20:8765/)).toBeInTheDocument();
  });

  it("signs in and hands control back", async () => {
    const spy = vi.spyOn(api, "login").mockResolvedValue({ ok: true });
    const onSignedIn = vi.fn();
    render(<Login onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(spy).toHaveBeenCalledWith("hunter2", true);
    expect(onSignedIn).toHaveBeenCalled();
  });

  it("says the password was wrong with the new copy", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new Error("Wrong password."));
    render(<Login onSignedIn={() => {}} />);
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Wrong password.")).toHaveClass("form-error");
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("shows the configured session length next to stay signed in", () => {
    render(<Login sessionExpiryDays={7} onSignedIn={() => {}} />);
    expect(screen.getByText(/stay signed in · 7 days/)).toBeInTheDocument();
  });

  it("sends stay_signed_in as false when the switch is off", async () => {
    const spy = vi.spyOn(api, "login").mockResolvedValue({ ok: true });
    render(<Login onSignedIn={() => {}} />);
    await userEvent.click(screen.getByRole("switch", { name: /stay signed in/i }));
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(spy).toHaveBeenCalledWith("hunter2", false);
  });
});
