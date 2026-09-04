import { useState } from "react";
import * as api from "../api";

/**
 * Login (design 1m). Only ever reached when Kraft is bound off localhost —
 * on this machine there is no password and no login screen.
 */
export function Login({ bind, onSignedIn }: { bind?: string; onSignedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-form" onSubmit={submit}>
        <div className="login-head">
          <span className="login-brand">Kraft</span>
          <span className="login-sub">
            {bind ? `Bound to ${bind} — ` : ""}password required off-localhost.
          </span>
        </div>
        <div className="field">
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <p className="form-error">{error}</p>}
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          Sign in
        </button>
        <span className="login-foot">Session cookie · HttpOnly · store survives expiry</span>
      </form>
    </div>
  );
}
