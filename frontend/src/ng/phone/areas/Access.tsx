import { useCallback, useEffect, useState } from "react";
import * as api from "../../../api";
import { ago, until } from "../../../format";
import type { Access, AuthSession, Health } from "../../../types";
import { parseUserAgent } from "../../../ua";
import { SELF_RESTART, afterRestart, bindHost, restartNote, useApply } from "../../apply/store";
import { ACCESS_LEDE, EMPTY_HOSTS, hostSuggestion } from "../../settings/accessWords";
import { showToast } from "../../ui/Toast";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, useSaves, type RowSpec } from "./kit";

const LOOPBACK = "127.0.0.1";
const EXPIRIES = [1, 7, 30];

/** The port as typed, or why it is not one (1024–65535, the range the server takes). */
export function portProblem(text: string): string | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return "Digits only.";
  const n = Number(t);
  return n < 1024 || n > 65535 ? "Use a port from 1024 to 65535." : null;
}

/** `/settings/access` (W17 brief O.3). Save on change: each control sends its own key and shows what the server answered; a refusal leaves the control where it was. */
export function AccessScreen() {
  const [access, setAccess] = useState<Access | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<AuthSession | null>(null);
  const { run, mark } = useSaves();
  const { edit, node } = useEditor();
  const sheet = useSheet();
  const { restart, managed, loaded, active } = useApply();
  const askRestart = useApply((s) => s.askRestart);
  const runRestart = useApply((s) => s.runRestart);
  const phase = useApply((s) => s.phase);
  const applyError = useApply((s) => s.error);
  const refreshApply = useApply((s) => s.refresh);

  const loadSessions = useCallback(() => api.getAuthSessions().then((r) => setSessions(r.sessions), () => setSessions([])), []);
  useEffect(() => {
    api.getAccess().then(setAccess, (e) => setLoadError(e instanceof Error ? e.message : String(e)));
    api.getHealth().then(setHealth, () => {});
    void loadSessions();
  }, [loadSessions]);

  const put = (key: string, body: Parameters<typeof api.putAccess>[0]) =>
    run(key, async () => {
      setAccess(await api.putAccess(body));
      void refreshApply();
      if ("password" in body || "session_expiry_days" in body) void loadSessions();
    });

  if (!access) return <AreaScreen title="Access" status={{ label: "saved on change" }} yaml={false}>{loadError ? <p className="ph-error" role="alert">{loadError}</p> : <p className="ph-empty">Loading…</p>}</AreaScreen>;

  const lan = access.bind !== LOOPBACK;
  const items = restart.filter((i) => i.id.startsWith("access."));
  // The server drops its restart item when KRAFT_HOST / KRAFT_PORT wins over the file.
  const overridden = (key: "bind" | "port") => loaded && health?.[key] != null && health[key] !== access[key] && !items.some((i) => i.id === `access.${key}`);
  const envNote = (key: "bind" | "port") => (overridden(key) ? `Set by the environment: running on ${health?.[key]}, which wins over this.` : undefined);
  // Where a restart brings Kraft back: KRAFT_HOST / KRAFT_PORT win over the file.
  const back = afterRestart(access, health, items);
  const address = `${location.protocol}//${bindHost(back.bind)}:${back.port}`;
  const suggested = hostSuggestion(access);
  const undo = () => (health ? put("bind", { bind: health.bind ?? access.bind, port: health.port ?? access.port }) : Promise.resolve(null));

  const hosts = () =>
    edit({
      kind: "menu",
      title: "Allowed hosts",
      help: "Tap a host to remove it.",
      options: [{ value: "+", label: "Add a host…" }, ...access.allowed_hosts.map((h) => ({ value: `rm:${h}`, label: `Remove ${h}`, danger: true }))],
      pick: (v) => {
        if (v === "+") return { kind: "text", title: "Add a host", help: `A host name or IP another device's browser may reach Kraft as.${access.lan_hosts?.length ? ` This machine is ${access.lan_hosts.join(" or ")} on the network.` : ""}`, value: suggested ?? "", placeholder: "host or IP", set: async (t) => { const h = t.trim().replace(/,$/, ""); return !h || access.allowed_hosts.includes(h) ? null : put("host", { allowed_hosts: [...access.allowed_hosts, h] }); } };
        const h = v.slice(3);
        // The host this browser is on: removing it would refuse the very next request.
        if (h === location.hostname) return Promise.resolve("Can't remove the host you're connected as.");
        return put("host", { allowed_hosts: access.allowed_hosts.filter((x) => x !== h) });
      },
    });

  // The server refuses a bind off loopback with no password, so Local network asks for one first and sends both in one save.
  // And, as the desktop's Set and switch does, the host the phone will use
  // when the list is empty: asked for with a password already set, and with
  // none, this machine's LAN address saved with the password (one field per
  // sheet), named in the sheet and changed under Allowed hosts.
  const noHosts = access.allowed_hosts.length === 0;
  const withHost = (h: string | null) => {
    const host = h?.trim().replace(/,$/, "");
    return noHosts && host ? { allowed_hosts: [host] } : {};
  };
  const toLan = () =>
    access.password_set
      ? noHosts
        ? edit({ kind: "text", title: "Phone's host", help: `The name or address your phone will type: it goes on Allowed hosts, and any other is refused (403).${access.lan_hosts?.length ? ` This machine is ${access.lan_hosts.join(" or ")} on the network.` : ""} Leave it empty to add one later.`, value: suggested ?? "", placeholder: "host or IP", submit: "Switch", set: async (v) => put("bind", { bind: "0.0.0.0", ...withHost(v) }) })
        : void put("bind", { bind: "0.0.0.0" })
      : edit({ kind: "text", title: "Set a password", help: `On the network, Kraft asks every browser for a password, this machine's too. Saving it switches to the local network.${noHosts && suggested ? ` ${suggested}, this machine's address, goes on Allowed hosts for the phone; change it there.` : ""}`, value: "", secret: true, set: async (v) => (v ? put("bind", { bind: "0.0.0.0", password: v, ...withHost(suggested) }) : "Enter a password.") });

  const revoke = async (s: AuthSession) => {
    const err = await run("sessions", async () => {
      await api.revokeSession(s.id);
      await loadSessions();
    });
    sheet.close();
    if (!err) showToast(s.current ? "Signed out" : "Session revoked");
  };

  return (
    <AreaScreen title="Access" sub={ACCESS_LEDE} status={items.length ? { label: "restart to apply", tone: "warn" } : { label: "saved on change" }} yaml={false}>
      {items.length > 0 && (
        <Group
          title="Restart needed"
          note={`Kraft comes back at ${address} and this screen follows. Until then it keeps running as it is.${managed ? "" : ` ${SELF_RESTART}`}`}
          rows={[
            ...items.map((i): RowSpec => ({ key: i.id, label: i.text, chips: [{ label: "pending", tone: "warn" }] })),
            { label: "Undo", sub: "Save the running values back.", onClick: () => void undo() },
            ...(managed ? [{ label: "Restart Kraft", disabled: phase === "restarting", onClick: async () => { await askRestart(); sheet.open("restart"); } } as RowSpec] : []),
          ]}
        />
      )}
      {phase === "restarting" && <p className="ph-note" role="status">Restarting… this screen follows when Kraft is back.</p>}
      {applyError && <p className="ph-error" role="alert">{applyError}</p>}
      <Group
        title="Reach"
        foot="Takes effect on restart. 0.0.0.0 listens on every network this machine is on. To reach Kraft from your phone, prefer a Tailscale address: see Remote access in the docs."
        rows={[
          { key: "loopback", label: "This machine only", sub: `${LOOPBACK}:${back.port} · no password`, sw: !lan, onSwitch: () => !(!lan) && void put("bind", { bind: LOOPBACK }), ...mark("bind") },
          { key: "lan", label: "Local network", sub: `0.0.0.0:${back.port} · password required. For the phone view.`, sw: lan, onSwitch: () => lan || toLan() },
        ]}
      />
      {envNote("bind") && <p className="ph-note">{envNote("bind")}</p>}
      <Group
        title="Port"
        foot="Used by both binds. Takes effect on restart."
        rows={[{ label: "port", value: String(access.port), sub: envNote("port"), ...mark("port"), onEdit: () => edit({ kind: "text", title: "Port", help: "1024 to 65535.", value: String(access.port), set: async (v) => { const p = portProblem(v); if (p) return p; return Number(v) === access.port ? null : put("port", { port: Number(v) }); } }) }]}
      />
      {lan ? (
        <>
          <Group
            title="Allowed hosts"
            note="Off loopback, another device's browser is refused (403) unless the Host it sends is on this list: the DNS-rebinding guard."
            foot={access.allowed_hosts.length === 0 ? `${EMPTY_HOSTS}${suggested ? ` Add ${suggested}, this machine's address, for the phone.` : ""}` : undefined}
            rows={[{ label: "hosts", value: access.allowed_hosts.length ? access.allowed_hosts.join(", ") : "none", mono: true, ...mark("host"), onEdit: hosts }]}
          />
          <Group
            title="Password"
            rows={[
              { label: "password", value: access.password_set ? "•••••••• set" : "not set", sub: "A new password signs every session out, this one too.", ...mark("password"), onEdit: () => edit({ kind: "text", title: access.password_set ? "Change password" : "Set a password", help: "Every session is signed out, this one too.", value: "", secret: true, set: async (v) => (v ? put("password", { password: v }) : "Enter a password.") }) },
              { label: "session expiry", value: `${access.session_expiry_days} ${access.session_expiry_days === 1 ? "day" : "days"}`, sub: "How long a sign-in lasts.", ...mark("expiry"), onEdit: () => edit({ kind: "choice", title: "Session expiry", value: String(access.session_expiry_days), options: EXPIRIES.map((d) => ({ value: String(d), label: `${d} ${d === 1 ? "day" : "days"}` })), set: (v) => put("expiry", { session_expiry_days: Number(v) }) }) },
            ]}
          />
          <Group
            title="Sessions"
            note={sessions.length === 0 ? "No sessions: auth is off." : undefined}
            rows={sessions.map((s): RowSpec => ({ key: s.id, label: parseUserAgent(s.label), sub: `${s.ip ?? "unknown address"} · seen ${ago(s.last_seen_at)} · expires ${until(s.expires_at)}`, chips: s.current ? [{ label: "current", tone: "ok" }] : undefined, onClick: () => { setRevoking(s); sheet.open("revoke"); }, ...mark("sessions") }))}
          />
        </>
      ) : (
        <Group title="Not used on 127.0.0.1" rows={[
          { label: "allowed hosts", sub: "Only loopback names are accepted." },
          { label: "password", sub: "Asked for when you pick Local network." },
          { label: "sessions", sub: "None: there is nothing to sign in to." },
        ]} />
      )}
      {node}
      {sheet.is("restart") && (
        <ConfirmSheet title="Restart Kraft?" text={`${restartNote(active)} It comes back at ${address} and this screen follows.`} confirm={{ label: "Restart Kraft", danger: true, run: () => { sheet.close(); void runRestart(); } }} onClose={sheet.close} />
      )}
      {sheet.is("revoke") && revoking && (
        <ConfirmSheet
          title={revoking.current ? "Sign out of this session?" : "Revoke this session?"}
          text={revoking.current ? "You will be asked for the password again." : `${parseUserAgent(revoking.label)} has to sign in again.`}
          confirm={{ label: revoking.current ? "Sign out" : "Revoke", danger: true, run: () => void revoke(revoking) }}
          onClose={sheet.close}
        />
      )}
    </AreaScreen>
  );
}
