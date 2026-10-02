import { useCallback, useEffect, useRef, useState } from "react";
import { Laptop, Pencil, Plus, RotateCw, Wifi } from "lucide-react";
import * as api from "../../api";
import { ago, until } from "../../format";
import type { Access, AuthSession, Health } from "../../types";
import { parseUserAgent } from "../../ua";
import { SELF_RESTART, afterRestart, bindHost, useApply } from "../apply/store";
import { Dialog } from "../ui/Dialog";
import { Segmented } from "../ui/Segmented";
import { showToast } from "../ui/Toast";
import { ACCESS_LEDE, EMPTY_HOSTS, hostSuggestion } from "./accessWords";
import { Block, SetRow } from "./parts";
import { YamlFrame } from "./YamlFrame";
import "./settings.css";

const LOOPBACK = "127.0.0.1";
const REMOTE_ACCESS = "https://itsomidkarami.github.io/kraft/guides/remote-access";
const EXPIRIES = [1, 7, 30].map((d) => ({ value: String(d), label: `${d}d` }));
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The port as typed, or why it is not one (1024–65535, the range the server takes). */
export function portProblem(text: string): string | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return "digits only";
  const n = Number(t);
  return n < 1024 || n > 65535 ? "use 1024–65535" : null;
}

/** Settings › Access (UX V2 W16 B). Saved on change: each control sends its own
 *  key, and the page shows what the server answered, so a refused save leaves
 *  the control where it was. Bind and port wait for a restart; the server says
 *  so with an `access.*` apply item. */
export function AccessPage() {
  const [access, setAccess] = useState<Access | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [notifyHost, setNotifyHost] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [editing, setEditing] = useState<"port" | "password" | "host" | "lan" | null>(null);
  const [draft, setDraft] = useState("");
  /** Local network's second field: the name the phone will use, saved to Allowed hosts with the bind. */
  const [lanHost, setLanHost] = useState("");
  const [revoking, setRevoking] = useState<AuthSession | null>(null);
  const { restart, managed, loaded } = useApply();
  const askRestart = useApply((s) => s.askRestart);
  const refreshApply = useApply((s) => s.refresh);
  const field = useRef<HTMLInputElement>(null);

  const loadSessions = useCallback(() => api.getAuthSessions().then((r) => setSessions(r.sessions), () => setSessions([])), []);
  useEffect(() => {
    api.getAccess().then(setAccess, (e) => setErrors({ page: message(e) }));
    api.getHealth().then(setHealth, () => {});
    void loadSessions();
    api.getNotify().then((n) => {
      try {
        setNotifyHost(n.base_url ? new URL(n.base_url).hostname : null);
      } catch {
        setNotifyHost(null);
      }
    }, () => {});
  }, [loadSessions]);
  useEffect(() => void (editing && field.current?.focus()), [editing]);

  const err = (key: string, text: string | null) => setErrors((e) => ({ ...e, [key]: text }));
  const put = async (key: string, body: Parameters<typeof api.putAccess>[0]): Promise<boolean> => {
    err(key, null);
    try {
      setAccess(await api.putAccess(body));
      void refreshApply();
      if ("password" in body || "session_expiry_days" in body) void loadSessions();
      return true;
    } catch (e) {
      err(key, message(e));
      return false;
    }
  };

  if (!access) return <div className="ng-settings">{errors.page && <p className="set-error" role="alert">{errors.page}</p>}</div>;

  const lan = access.bind !== LOOPBACK;
  const items = restart.filter((i) => i.id.startsWith("access."));
  // The server drops its restart item when KRAFT_HOST / KRAFT_PORT wins over the file.
  const overridden = (key: "bind" | "port") => loaded && health?.[key] != null && health[key] !== access[key] && !items.some((i) => i.id === `access.${key}`);
  // Where a restart brings Kraft back: KRAFT_HOST / KRAFT_PORT win over the file.
  const back = afterRestart(access, health, items);
  const address = `${location.protocol}//${bindHost(back.bind)}:${back.port}`;
  const suggested = hostSuggestion(access);
  // Local network's password prompt reports under the Reach cards, as the bind it saves with.
  const errKey = (what: "port" | "password" | "host" | "lan") => (what === "lan" ? "bind" : what);
  const closeEdit = () => {
    if (editing) err(errKey(editing), null);
    setEditing(null);
    setDraft("");
  };
  const edit = (what: "port" | "password" | "host" | "lan", initial = "") => {
    setEditing(what);
    setDraft(initial);
    err(errKey(what), null);
  };
  const savePort = async () => {
    const problem = portProblem(draft);
    if (problem) return err("port", problem);
    if (Number(draft) === access.port) return closeEdit();
    if (await put("port", { port: Number(draft) })) closeEdit();
  };
  const savePassword = async () => {
    if (!draft) return closeEdit();
    if (await put("password", { password: draft })) closeEdit();
  };
  // The server refuses a bind off loopback with no password, and refuses
  // every other device's browser until its Host is on the list. So Local
  // network asks for what is missing of the two first, the host pre-filled
  // with this machine's LAN address, and sends them with the bind in one save.
  const pickBind = (bind: string) => {
    if (bind === LOOPBACK || (access.password_set && access.allowed_hosts.length)) return void put("bind", { bind });
    setLanHost(suggested ?? "");
    edit("lan");
  };
  const saveLan = async () => {
    if (!access.password_set && !draft) return err("bind", "enter a password");
    const host = lanHost.trim().replace(/,$/, "");
    const hosts = host && !access.allowed_hosts.includes(host) ? { allowed_hosts: [...access.allowed_hosts, host] } : {};
    if (await put("bind", { bind: "0.0.0.0", ...(draft && { password: draft }), ...hosts })) closeEdit();
  };
  const addHost = async () => {
    const h = draft.trim().replace(/,$/, "");
    if (!h || access.allowed_hosts.includes(h)) return closeEdit();
    if (await put("host", { allowed_hosts: [...access.allowed_hosts, h] })) closeEdit();
  };
  const removeHost = (h: string) => {
    // The host this browser is on: removing it would refuse the very next request.
    if (h === location.hostname) return err("host", "can't remove the host you're connected as");
    void put("host", { allowed_hosts: access.allowed_hosts.filter((x) => x !== h) });
  };
  const undo = async () => {
    if (!health) return;
    await put("bind", { bind: health.bind ?? access.bind, port: health.port ?? access.port });
  };
  const revoke = async (s: AuthSession) => {
    setRevoking(null);
    try {
      await api.revokeSession(s.id);
      await loadSessions();
      showToast(s.current ? "Signed out" : "Session revoked");
    } catch (e) {
      err("sessions", message(e));
    }
  };
  const keys = (save: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || (e.key === "," && editing === "host")) {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      e.stopPropagation();
      closeEdit();
    }
  };

  const running = health?.bind != null && health.port != null && (health.bind !== access.bind || health.port !== access.port);
  const yaml = [
    `bind: ${access.bind}`,
    `port: ${access.port}`,
    "allowed_hosts:",
    ...access.allowed_hosts.map((h) => `  - ${h}`),
    access.password_set ? 'password: "********"  # stored hashed, never shown' : "# password: not set",
    `session_expiry_days: ${access.session_expiry_days}`,
    ...(running ? ["", `# running now: bind ${health.bind}, port ${health.port}, until a restart`] : []),
  ].join("\n");

  return (
    <>
    <YamlFrame pageKey="access" file="access.yaml" title="access" icon="shield" status={items.length ? "restart to apply" : "saved on change"} yaml={yaml} yamlNote="The password is stored hashed and never shown. Bind and port are read at startup, so an edit here waits for a restart.">
      <div className="set-page is-cards">
        <div className="set-title"><h1>Access</h1><p className="lede">{ACCESS_LEDE}</p></div>

        {items.length > 0 && (
          <div className="set-pending" role="status">
            <span className="set-pending-icon" aria-hidden><RotateCw size={15} /></span>
            <div className="set-pending-text">
              <strong>Restart to apply</strong>
              {items.map((i) => <span key={i.id}>{i.text}</span>)}
              <span className="set-hint">{`Kraft comes back at ${address} and this page follows. Until then it keeps running as it is.`}</span>
              {!managed && <span className="set-hint">{SELF_RESTART}</span>}
            </div>
            <div className="set-pending-actions">
              <button type="button" className="set-btn" onClick={() => void undo()}>Undo</button>
              {managed && <button type="button" className="set-btn is-primary" onClick={() => void askRestart()}><RotateCw size={12} aria-hidden /> Restart Kraft</button>}
            </div>
          </div>
        )}

        <section aria-labelledby="set-reach">
          <h2 id="set-reach" className="adr-sr">Reach</h2>
          <div className="set-modes is-cards" role="radiogroup" aria-label="Reach">
            {[
              { bind: LOOPBACK, icon: <Laptop size={15} />, title: "This machine only", note: "No password. Only loopback names are accepted." },
              { bind: "0.0.0.0", icon: <Wifi size={15} />, title: "Local network", note: "Password required. For the phone view." },
            ].map((m) => (
              <button key={m.bind} type="button" role="radio" aria-checked={(m.bind === LOOPBACK) === !lan} className="set-mode is-card" onClick={() => pickBind(m.bind)}>
                <span className="set-mode-icon" aria-hidden>{m.icon}</span>
                <span className="set-mode-text">
                  <span className="set-mode-title">{m.title}</span>
                  <span className="set-mode-addr">{m.bind}:{back.port}</span>
                  <span className="set-hint">{m.note}</span>
                </span>
                <span className="set-mode-dot" aria-hidden />
              </button>
            ))}
          </div>
          {editing === "lan" && (
            <>
              {!access.password_set && (
                <SetRow label="password" hint="On the network, Kraft asks every browser for a password, this machine's too. Set one to switch.">
                  <input ref={field} className="set-input" type="password" aria-label="Password for the local network" placeholder="new password" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys(() => void saveLan())} />
                </SetRow>
              )}
              <SetRow label="phone's host" hint={`The name or address your phone will type: it goes on Allowed hosts, and any other is refused (403).${access.lan_hosts?.length ? ` This machine is ${access.lan_hosts.join(" or ")} on the network.` : ""} Leave it empty to add one later.`}>
                <span className="set-inline">
                  <input ref={access.password_set ? field : undefined} className="set-input" aria-label="Host or IP the phone will use" placeholder="host or IP" value={lanHost} onChange={(e) => setLanHost(e.target.value)} onKeyDown={keys(() => void saveLan())} />
                  <button type="button" className="set-btn is-primary" onClick={() => void saveLan()}>{access.password_set ? "Switch" : "Set and switch"}</button>
                  <button type="button" className="set-btn" onClick={closeEdit}>Cancel</button>
                </span>
              </SetRow>
            </>
          )}
          {errors.bind && <span className="set-error" role="alert">{errors.bind}</span>}
          {overridden("bind") && <span className="set-hint">Running on {health?.bind}: the KRAFT_HOST environment setting wins over this.</span>}
          <span className="set-hint">Takes effect on restart. 0.0.0.0 listens on every network this machine is on. To reach Kraft from your phone, prefer a Tailscale address (see <a href={REMOTE_ACCESS} target="_blank" rel="noopener noreferrer">Remote access</a>).</span>
        </section>

        <Block id="set-port" title="Port" card aside={items.some((i) => i.id === "access.port") ? "waits for a restart" : "saved on change"}>
          <SetRow label="port" error={errors.port} hint="Used by both binds. Takes effect on restart. 1024–65535.">
            {editing === "port" ? (
              <input ref={field} className="set-input" aria-label="Port" inputMode="numeric" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys(() => void savePort())} onBlur={() => void savePort()} />
            ) : (
              <button type="button" className="set-value" aria-label={`Port ${access.port}, edit`} onClick={() => edit("port", String(access.port))}>{access.port} <Pencil size={12} aria-hidden /></button>
            )}
          </SetRow>
          {overridden("port") && <span className="set-hint">Running on {health?.port}: the KRAFT_PORT environment setting wins over this.</span>}
        </Block>

        {lan ? (
          <>
            <Block id="set-hosts" title="Allowed hosts" aside="saved on change" card>
              <SetRow label="hosts" error={errors.host} hint="Off loopback, another device's browser is refused (403) unless the Host it sends is on this list: the DNS-rebinding guard. A browser on this machine is let in at 127.0.0.1, localhost or [::1]. On 127.0.0.1 the list is ignored.">
                <ul className="set-chips">
                  {access.allowed_hosts.map((h) => (
                    <li key={h}><button type="button" className="set-chip" aria-label={`Remove ${h}`} title={h === location.hostname ? "You are connected as this host" : `Remove ${h}`} onClick={() => removeHost(h)}>{h} <span aria-hidden>×</span></button></li>
                  ))}
                  <li>
                    {editing === "host" ? (
                      <input ref={field} className="set-input" aria-label="Add a host or IP" placeholder="host or IP" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys(() => void addHost())} onBlur={() => void addHost()} />
                    ) : (
                      <button type="button" className="set-chip is-add" onClick={() => edit("host")}><Plus size={11} aria-hidden /> add</button>
                    )}
                  </li>
                </ul>
              </SetRow>
              {access.allowed_hosts.length === 0 && (
                <span className="set-hint is-warn">
                  {EMPTY_HOSTS}
                  {suggested && <> <button type="button" className="set-chip is-add" onClick={() => void put("host", { allowed_hosts: [suggested] })}><Plus size={11} aria-hidden /> add {suggested}</button></>}
                </span>
              )}
              {notifyHost && <span className="set-hint">The notification link-back ({notifyHost}) is {access.allowed_hosts.includes(notifyHost) ? "on the list." : "not on the list: that link will be refused."}</span>}
            </Block>

            <Block id="set-password" title="Password" aside="writes access.yaml" card>
              <SetRow label="password" error={errors.password} hint="A new password signs every session out.">
                {editing === "password" ? (
                  <span className="set-inline">
                    <input ref={field} className="set-input" type="password" aria-label="New password" placeholder="new password" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys(() => void savePassword())} />
                    <button type="button" className="set-btn is-primary" onClick={() => void savePassword()}>Save</button>
                    <button type="button" className="set-btn" onClick={closeEdit}>Cancel</button>
                  </span>
                ) : (
                  <span className="set-inline">
                    <span className={access.password_set ? "set-mono" : undefined}>{access.password_set ? "•••••••• set" : "not set"}</span>
                    {access.password_set
                      ? <button type="button" className="set-pencil" aria-label="Change" title="Change the password" onClick={() => edit("password")}><Pencil size={12} aria-hidden /></button>
                      : <button type="button" className="set-btn" onClick={() => edit("password")}>Set a password</button>}
                  </span>
                )}
              </SetRow>
              <SetRow label="session expiry" error={errors.expiry} hint="How long a sign-in lasts.">
                <Segmented label="Session expiry" options={EXPIRIES} value={String(access.session_expiry_days)} onChange={(v) => void put("expiry", { session_expiry_days: Number(v) })} />
              </SetRow>
            </Block>

            <Block id="set-sessions" title="Sessions" aside="live" pill card>
              {errors.sessions && <span className="set-error" role="alert">{errors.sessions}</span>}
              {sessions.length === 0 && <p className="set-hint">No sessions: auth is off.</p>}
              <ul className="set-sessions">
                {sessions.map((s) => (
                  <li key={s.id} className="set-session" data-session={s.id}>
                    <span className="set-session-who"><span>{parseUserAgent(s.label)}{s.current && <span className="set-current"> current</span>}</span><span className="set-hint">{s.ip}</span></span>
                    <span className="set-hint">seen {ago(s.last_seen_at)}</span>
                    <span className="set-hint">expires {until(s.expires_at)}</span>
                    <button type="button" className="set-btn is-danger" aria-label={`${s.current ? "Sign out" : "Revoke"} ${parseUserAgent(s.label)}`} onClick={() => setRevoking(s)}>{s.current ? "Sign out here" : "Revoke"}</button>
                  </li>
                ))}
              </ul>
            </Block>
          </>
        ) : (
          <Block id="set-unused" title="Not used on 127.0.0.1" card="dashed">
            {[["allowed hosts", "Only loopback names are accepted."], ["password", "Asked for when you pick Local network."], ["sessions", "None: there is nothing to sign in to."]].map(([k, v]) => (
              <div key={k} className="set-unused"><span className="set-row-label">{k}</span><span className="set-hint">{v}</span></div>
            ))}
          </Block>
        )}
      </div>
    </YamlFrame>
      {revoking && (
        <Dialog
          title={revoking.current ? "Sign out of this session?" : "Revoke this session?"}
          onClose={() => setRevoking(null)}
          footer={<><button type="button" className="set-btn" onClick={() => setRevoking(null)}>Cancel</button><button type="button" className="set-btn is-danger" onClick={() => void revoke(revoking)}>{revoking.current ? "Sign out" : "Revoke"}</button></>}
        >
          <p>{revoking.current ? "It signs you out here." : `${parseUserAgent(revoking.label)} has to sign in again.`}</p>
        </Dialog>
      )}
    </>
  );
}
