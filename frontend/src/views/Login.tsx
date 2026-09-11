import { useState } from "react";
import * as api from "../api";
import { Switch } from "../components/ui";

/**
 * Login (design 36, m16). Only ever reached when Kraft is bound off localhost
 * -- on this machine there is no password and no login screen.
 */
export function Login({
  bind,
  sessionExpiryDays,
  onSignedIn,
}: {
  bind?: string;
  sessionExpiryDays?: number;
  onSignedIn: () => void;
}) {
  const [password, setPassword] = useState("");
  const [staySignedIn, setStaySignedIn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password, staySignedIn);
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
            {bind ? `bound to ${bind} · ` : ""}this instance asks for a password off localhost
          </span>
        </div>
        <div className="field">
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div className="save-row">
          <Switch
            checked={staySignedIn}
            onChange={setStaySignedIn}
            label="stay signed in"
            disabled={busy}
          />
          <span className="save-hint">
            stay signed in{sessionExpiryDays != null ? ` · ${sessionExpiryDays} days` : ""}
          </span>
        </div>
        {error && <p className="form-error">{error}</p>}
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          Sign in
        </button>
        <span className="login-foot">
          Set or change the password in Settings → Access from the machine Kraft runs on.
          Sessions are listed there and can be revoked.
        </span>
      </form>
    </div>
  );
}
