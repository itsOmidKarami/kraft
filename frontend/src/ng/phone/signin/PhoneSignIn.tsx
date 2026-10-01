import { useEffect, useRef, useState, type FormEvent } from "react";
import * as api from "../../../api";
import type { Health } from "../../../types/system";
import { Button } from "../../ui/Button";
import "./signin.css";

const LOCK_FALLBACK_S = 60;
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** The sign-in card at phone width (W17 brief G.3): the desktop's card measured at 390 fails the phone rules (a card wider than the screen, targets under 44px, a 14px field), so the phone has its own. The same call and the same answers: the server owns the lockout. */
export function PhoneSignIn({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
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

  useEffect(() => void api.getHealth().then(setHealth).catch(() => {}), []);
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
    if (error === "Wrong password.") input.current?.select();
  }, [error]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || sending.current || locked) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      // Not api.ts: its req() turns every 401 into `kraft:unauthenticated`, and a wrong password is an answer, not a lost session.
      const res = await fetch("/api/login", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify({ password, stay_signed_in: stay === "days" }) });
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
  return (
    <main className="ph-signin">
      <form className="ph-signin-card" onSubmit={submit}>
        <div className="ph-signin-brand"><span>Kraft</span>{where && <span className="ph-signin-where">{where}</span>}</div>
        <h1 className="ph-title">Sign in</h1>
        <p className="ph-help">This instance asks for a password off localhost.</p>
        <label className="ph-field">
          <span>Password</span>
          <input ref={input} className="ph-input" type="password" autoComplete="current-password" autoFocus value={password} disabled={locked} aria-invalid={!locked && error === "Wrong password."} onChange={(e) => { setPassword(e.target.value); setError(null); }} />
        </label>
        {!locked && error && <p className="ph-error" role="alert">{error}</p>}
        <div className="ph-signin-stay" role="radiogroup" aria-label="Stay signed in">
          {([["days", days != null ? `${days} days` : "Stay signed in"], ["visit", "This visit only"]] as const).map(([v, label]) => (
            <button key={v} type="button" role="radio" aria-checked={stay === v} disabled={locked || busy} className={`ph-signin-opt${stay === v ? " ph-is-on" : ""}`} onClick={() => setStay(v)}>{label}</button>
          ))}
        </div>
        {locked && <p className="ph-error" role="timer" aria-live="off">{`Too many failed attempts. Try again in ${clock(left)}.`}</p>}
        <Button type="submit" className="ph-btn ph-btn-primary" variant="primary" disabled={busy || locked}>{busy ? "Signing in…" : "Sign in"}</Button>
        <p className="ph-note">Set or change the password in Settings → Access from the machine Kraft runs on. Sessions are listed there and can be revoked.</p>
      </form>
    </main>
  );
}
