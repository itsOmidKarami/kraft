import { useCallback, useEffect, useState } from "react";
import * as api from "../../api";
import { ago, until } from "../../format";
import { OverflowMenu, Row, RowText } from "../../components/ui";
import type { Access, AuthSession } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5e access ────────────────────────────────────────────────────────────── */

export function AccessPage() {
  const { value, error, reload } = useResource(() => api.getAccess());
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const access: Access | null = value;

  const loadSessions = useCallback(() => {
    return api
      .getAuthSessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => setSessions([]));
  }, []);
  // wrapped for the same reason `useResource` wraps `reload`: `useEffect` reads a
  // returned promise as a cleanup function.
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const put = async (body: Parameters<typeof api.putAccess>[0]) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putAccess(body);
      setPassword("");
      // `busy` must mean "settled": clearing it before the re-fetch lands
      // re-enables the controls while the page still renders pre-save state.
      await Promise.all([reload(), loadSessions()]);
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="Access" note="auth is off on localhost and on for anything else" />
      {error && <p className="form-error">{error}</p>}
      {access && (
        <>
          <section className="settings-section">
            <h6>Bind</h6>
            <label className="radio">
              <input
                type="radio"
                name="bind"
                checked={access.bind === "127.0.0.1"}
                onChange={() => put({ bind: "127.0.0.1" })}
              />
              <span className="dot" />
              <span>
                127.0.0.1:{access.port}{" "}
                <span className="row-sub">· this machine only, no password</span>
              </span>
            </label>
            <label className="radio">
              <input
                type="radio"
                name="bind"
                checked={access.bind !== "127.0.0.1"}
                onChange={() => put({ bind: "0.0.0.0" })}
              />
              <span className="dot" />
              <span>
                0.0.0.0:{access.port}{" "}
                <span className="row-sub">· LAN, password required — for the phone view</span>
              </span>
            </label>
            <p className="settings-foot">
              Takes effect on restart. Kraft never binds publicly; use a tunnel if you need
              remote access.
            </p>
          </section>

          <section className="settings-section">
            <h6>Password</h6>
            <div className="access-grid">
              <div className="field">
                <label htmlFor="access-password">
                  {access.password_set ? "New password" : "Set a password"}
                </label>
                <input
                  id="access-password"
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Session expiry</label>
                <div className="seg" role="radiogroup" aria-label="session expiry">
                  {[1, 7, 30].map((d) => (
                    <label key={d} className="seg-opt">
                      <input
                        type="radio"
                        name="expiry"
                        checked={access.session_expiry_days === d}
                        onChange={() => put({ session_expiry_days: d })}
                      />
                      {d}d
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <SaveRow
              onSave={() => put({ password })}
              onDiscard={() => setPassword("")}
              dirty={password.length > 0}
              busy={busy}
              message={message}
              hint="writes access.yaml · a new password signs every session out"
            />
          </section>

          <section className="settings-section">
            <h6>Sessions</h6>
            {sessions.length === 0 && <p className="empty">no sessions — auth is off</p>}
            {sessions.map((s) => (
              <Row key={s.id} columns="1fr 130px 80px auto" data-session={s.id}>
                <RowText
                  title={
                    <>
                      {s.label ?? "unknown"}
                      {s.current && <span className="session-current"> current</span>}
                    </>
                  }
                  sub={s.ip}
                />
                <span className="row-sub">seen {ago(s.last_seen_at)}</span>
                <span className="row-sub">expires {until(s.expires_at)}</span>
                <OverflowMenu
                  label={`session ${s.label ?? s.id}`}
                  items={[
                    {
                      label: "Revoke",
                      danger: true,
                      // Revoking the session you are using signs you out of this
                      // browser on the spot, and there is no undo either way.
                      confirm: s.current
                        ? "Revoke this session? It signs you out here."
                        : `Revoke ${s.label ?? "this session"}? That browser has to sign in again.`,
                      onSelect: () =>
                        api
                          .revokeSession(s.id)
                          .then(loadSessions)
                          .catch((e) => setMessage(e instanceof Error ? e.message : String(e))),
                    },
                  ]}
                />
              </Row>
            ))}
          </section>
        </>
      )}
    </>
  );
}
