import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { SignIn } from "./SignIn";

type Reply = { status: number; headers?: Record<string, string> } | "network";

let login: Mock<(url: string, init?: RequestInit) => void>;
function serve(reply: Reply | (() => Reply)) {
  login = vi.fn<(url: string, init?: RequestInit) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/health")) return new Response(JSON.stringify({ status: "ok", bind: "0.0.0.0", port: 8765, session_expiry_days: 30 }), { status: 200 });
      login(url, init);
      const r = typeof reply === "function" ? reply() : reply;
      if (r === "network") throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify(r.status === 200 ? { ok: true } : { detail: "x" }), { status: r.status, headers: r.headers });
    }),
  );
}
const sent = () => JSON.parse(String(login.mock.calls[0][1]?.body));

beforeEach(() => serve({ status: 200 }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const field = () => screen.getByLabelText(/^Password/) as HTMLInputElement;

describe("SignIn", () => {
  it("names the instance from /health and offers the session length it reports", async () => {
    render(<SignIn onSignedIn={async () => {}} />);
    expect(await screen.findByText("0.0.0.0:8765")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "30 days" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "This visit only" })).not.toBeChecked();
    expect(field()).toHaveAttribute("autocomplete", "current-password");
    expect(field()).toHaveFocus();
  });

  it("says every browser signs in on a network bind, this machine's too, rather than only off localhost", async () => {
    render(<SignIn onSignedIn={async () => {}} />);
    expect(await screen.findByText("Kraft listens on the network, so every browser signs in, this machine's too.")).toBeInTheDocument();
  });

  it("posts the password and the stay-signed-in choice with the cookie, then runs the probe", async () => {
    const onSignedIn = vi.fn(async () => {});
    render(<SignIn onSignedIn={onSignedIn} />);
    await userEvent.type(field(), "hunter2{Enter}");
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
    expect(login.mock.calls[0][0]).toBe("/api/login");
    expect(login.mock.calls[0][1]?.credentials).toBe("same-origin");
    expect(sent()).toEqual({ password: "hunter2", stay_signed_in: true });
  });

  it("sends stay_signed_in false for This visit only", async () => {
    render(<SignIn onSignedIn={async () => {}} />);
    await userEvent.click(screen.getByRole("radio", { name: "This visit only" }));
    await userEvent.type(field(), "pw{Enter}");
    await waitFor(() => expect(login).toHaveBeenCalled());
    expect(sent().stay_signed_in).toBe(false);
  });

  it("sends nothing for an empty password", async () => {
    render(<SignIn onSignedIn={async () => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.submit(field().closest("form")!);
    expect(login).not.toHaveBeenCalled();
  });

  it("sends once for a double submit and reads Signing in… meanwhile", async () => {
    let release!: () => void;
    const onSignedIn = vi.fn(() => new Promise<void>((r) => (release = r)));
    render(<SignIn onSignedIn={onSignedIn} />);
    await userEvent.type(field(), "pw");
    const form = field().closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(await screen.findByRole("button", { name: "Signing in…" })).toBeDisabled();
    expect(login).toHaveBeenCalledTimes(1);
    await act(async () => release());
  });

  it("answers a 401 with an alert, selects the field, and clears on typing", async () => {
    serve({ status: 401 });
    render(<SignIn onSignedIn={async () => {}} />);
    await userEvent.type(field(), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong password.");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(field().selectionStart).toBe(0);
    expect(field().selectionEnd).toBe(4);
    await userEvent.keyboard("x");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field()).not.toHaveAttribute("aria-invalid", "true");
  });

  it("does not raise kraft:unauthenticated for a wrong password", async () => {
    serve({ status: 401 });
    const lost = vi.fn();
    window.addEventListener("kraft:unauthenticated", lost);
    render(<SignIn onSignedIn={async () => {}} />);
    await userEvent.type(field(), "nope{Enter}");
    await screen.findByRole("alert");
    window.removeEventListener("kraft:unauthenticated", lost);
    expect(lost).not.toHaveBeenCalled();
  });

  it("does not count failures itself: the fifth wrong password is still sent", async () => {
    serve({ status: 401 });
    render(<SignIn onSignedIn={async () => {}} />);
    for (let i = 0; i < 6; i++) {
      await userEvent.type(field(), "a{Enter}");
      await waitFor(() => expect(login).toHaveBeenCalledTimes(i + 1));
      await screen.findByRole("alert");
    }
    expect(field()).not.toBeDisabled();
  });

  it("locks on a 429, counts the server's Retry-After down, and frees the card at zero", async () => {
    serve({ status: 429, headers: { "Retry-After": "125" } });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    render(<SignIn onSignedIn={async () => {}} />);
    fireEvent.change(field(), { target: { value: "pw" } });
    fireEvent.submit(field().closest("form")!);
    await act(async () => {});
    const timer = screen.getByRole("timer");
    expect(timer).toHaveTextContent("Too many failed attempts. Try again in 2:05.");
    expect(field()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    expect(screen.queryByRole("alert")).toBeNull();

    await act(async () => void vi.advanceTimersByTime(5000));
    expect(screen.getByRole("timer")).toHaveTextContent("Try again in 2:00.");
    await act(async () => void vi.advanceTimersByTime(120_000));
    expect(screen.queryByRole("timer")).toBeNull();
    expect(field()).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign in" })).not.toBeDisabled();
  });

  it("says it could not reach the server and stays idle", async () => {
    serve("network");
    render(<SignIn onSignedIn={async () => {}} />);
    await userEvent.type(field(), "pw{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the Kraft server.");
    expect(field()).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign in" })).not.toBeDisabled();
  });
});
