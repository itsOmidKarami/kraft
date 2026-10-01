import { useEffect, useRef, useState, type FormEvent } from "react";
import * as api from "../../api";
import type { Health } from "../../types/system";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { Segmented } from "../ui/Segmented";
import "./signin.css";

const LOCK_FALLBACK_S = 60;
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** Sign-in card (Decisions §10). Shown when the instance is bound off localhost and this browser has no session. */
export function SignIn({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [password, setPassword] = useState("");
  const [stay, setStay] = useState<"days" | "visit">("days");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [left, setLeft] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const sending = useRef(false);
  const locked = lockedUntil !== null;

  useEffect(() => {
    api.getHealth().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => {
    if (lockedUntil === null) return;
    const tick = () => {
      const s = Math.ceil((lockedUntil - Date.now()) / 1000);
      if (s <= 0) {
        setLockedUntil(null);
        setLeft(0);
      } else setLeft(s);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [lockedUntil]);

  useEffect(() => {
    if (!locked) input.current?.focus();
  }, [locked]);

  useEffect(() => {
    if (error === "Wrong password.") input.current?.select();
  }, [error]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || sending.current || locked) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      // Not api.ts: its req() turns every 401 into `kraft:unauthenticated`, and a wrong password here
      // is an answer, not a lost session. The server owns the lockout; the client counts nothing.
      const res = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password, stay_signed_in: stay === "days" }),
      });
      if (res.ok) {
        setPassword("");
        await onSignedIn();
        return;
      }
      if (res.status === 401) setError("Wrong password.");
      else if (res.status === 429) {
        const retry = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
        setLockedUntil(Date.now() + (retry > 0 ? retry : LOCK_FALLBACK_S) * 1000);
      } else setError(`Sign-in failed (${res.status}).`);
    } catch (err) {
      setError(err instanceof TypeError ? "Could not reach the Kraft server." : err instanceof Error ? err.message : String(err));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  const where = health?.bind ? (health.port ? `${health.bind}:${health.port}` : health.bind) : null;
  const days = health?.session_expiry_days;
  const lockText = `Too many failed attempts. Try again in ${clock(left)}.`;

  return (
    <main className="ng-signin">
      <form className="ng-signin-card" onSubmit={submit}>
        <div className="ng-signin-brand"><span>Kraft</span>{where && <span className="ng-signin-where">{where}</span>}</div>
        <h1>Sign in</h1>
        <p className="ng-signin-lead">This instance asks for a password off localhost.</p>
        <Field label="Password" error={locked ? null : error}>
          <input
            ref={input}
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            disabled={locked}
            aria-invalid={!locked && error === "Wrong password."}
            onChange={(e) => {
              setPassword(e.target.value);
              setError(null);
            }}
          />
        </Field>
        <Segmented
          label="Stay signed in"
          value={stay}
          onChange={setStay}
          disabled={locked || busy}
          options={[
            { value: "days", label: days != null ? `${days} days` : "Stay signed in" },
            { value: "visit", label: "This visit only" },
          ]}
        />
        {locked && (
          <>
            <p className="field-error" role="timer" aria-live="off">{lockText}</p>
            <span className="ng-signin-sr" role="status">{`Too many failed attempts. Try again in ${Math.ceil(left / 60)} min.`}</span>
          </>
        )}
        <Button type="submit" variant="primary" disabled={busy || locked}>{busy ? "Signing in…" : "Sign in"}</Button>
        <p className="ng-signin-foot">
          Set or change the password in Settings → Access from the machine Kraft runs on. Sessions are listed there and can be revoked.
        </p>
      </form>
    </main>
  );
}
