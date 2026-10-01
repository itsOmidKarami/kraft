import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PhoneSignIn } from "./PhoneSignIn";

function login(status: number, headers: Record<string, string> = {}) {
  const calls: { path: string; body?: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.endsWith("/health")) return new Response(JSON.stringify({ status: "ok", bind: "0.0.0.0", port: 8765, session_expiry_days: 7 }), { status: 200 });
    return new Response("{}", { status, headers });
  }));
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

describe("PhoneSignIn (G.3)", () => {
  it("signs in with the typed password and the stay choice, then hands over", async () => {
    const calls = login(200);
    const onSignedIn = vi.fn(async () => {});
    render(<PhoneSignIn onSignedIn={onSignedIn} />);
    expect(await screen.findByRole("radio", { name: "7 days", checked: true })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "This visit only" }));
    await userEvent.type(screen.getByLabelText("Password"), "pw{Enter}");
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.path === "/api/login")?.body).toEqual({ password: "pw", stay_signed_in: false });
  });

  it("a wrong password is an answer, not a lost session", async () => {
    login(401);
    const lost = vi.fn();
    window.addEventListener("kraft:unauthenticated", lost);
    render(<PhoneSignIn onSignedIn={async () => {}} />);
    await userEvent.type(screen.getByLabelText("Password"), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong password.");
    expect(lost).not.toHaveBeenCalled();
    window.removeEventListener("kraft:unauthenticated", lost);
  });

  it("shows the server's lockout as a countdown and disables the form", async () => {
    login(429, { "Retry-After": "75" });
    render(<PhoneSignIn onSignedIn={async () => {}} />);
    await userEvent.type(screen.getByLabelText("Password"), "x{Enter}");
    expect(await screen.findByRole("timer")).toHaveTextContent("Try again in 1:15.");
    expect(screen.getByLabelText("Password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    void act;
  });
});
