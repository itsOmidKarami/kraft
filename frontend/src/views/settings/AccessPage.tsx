import { useCallback, useEffect, useState } from "react";
import * as api from "../../api";
import { ago, until } from "../../format";
import { Chip, OverflowMenu, Row, RowText } from "../../components/ui";
import { parseUserAgent } from "../../ua";
import type { Access, AuthSession } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";
import "./access.css";

/* ── 5e access ────────────────────────────────────────────────────────────── */

export function AccessPage() {
  const { value, error, reload } = useResource(() => api.getAccess());
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [hostDraft, setHostDraft] = useState("");
  const access: Access | null = value;

  const [notifyBaseUrl, setNotifyBaseUrl] = useState<string | null>(null);
  useEffect(() => {
    api
      .getNotify()
      .then((n) => setNotifyBaseUrl(n.base_url))
      .catch(() => {});
  }, []);
  const notifyHost = (() => {
    if (!notifyBaseUrl) return null;
    try {
      return new URL(notifyBaseUrl).hostname;
    } catch {
      return null;
    }
  })();

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
            <p className="settings-foot phone-only">
              Changing the bind from a phone locks this phone out until you are on the new
              address.
            </p>
          </section>

          <section className="settings-section">
            <h6>Allowed hosts</h6>
            <p className="settings-foot">the names a browser may reach this instance by</p>
            <div className="host-tags">
              {access.allowed_hosts.map((h) => (
                <Chip
                  key={h}
                  label={h}
                  trailing="×"
                  onClick={() => {
                    // Removing the host this browser is on would 403 every
                    // request the perimeter sees from it, including the one
                    // that just sent this PUT — locking the operator out.
                    if (h === window.location.hostname) {
                      setMessage("can't remove the host you're connected as");
                      return;
                    }
                    put({ allowed_hosts: access.allowed_hosts.filter((x) => x !== h) });
                  }}
                />
              ))}
              <input
                className="input host-add"
                placeholder="add a host or IP"
                value={hostDraft}
                onChange={(e) => setHostDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    const host = hostDraft.trim().replace(/,$/, "");
                    if (host && !access.allowed_hosts.includes(host)) {
                      put({ allowed_hosts: [...access.allowed_hosts, host] });
                    }
                    setHostDraft("");
                  }
                }}
              />
            </div>
            <p className="settings-foot">
              Off loopback, a browser request is refused (403) unless its Host is on this list —
              the DNS-rebinding guard. On 127.0.0.1 the list is ignored and only loopback names
              are accepted.
            </p>
            {access.bind !== "127.0.0.1" && access.allowed_hosts.length === 0 && (
              <p className="settings-foot" data-tone="warn">
                An empty list on a LAN bind refuses every browser.
              </p>
            )}
            {notifyHost && (
              <p className="settings-foot">
                The notification link-back ({notifyHost}) is{" "}
                {access.allowed_hosts.includes(notifyHost)
                  ? "on the list."
                  : "not on the list — that link will be refused."}
              </p>
            )}
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
                      {parseUserAgent(s.label)}
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
