import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { enabledEvents, isEnabled, NOTIFY_EVENTS, notificationText, requestPermission, setEnabled as setBrowserEnabled, setEvents as setBrowserEvents } from "../../../browserNotify";
import { ago } from "../../../format";
import type { Notify } from "../../../types";
import { WEBHOOK_OFF } from "../../ui/reasons";
import { ALLOW, browserActive as browserOn, BROWSER_OFF, BROWSER_WHY, channelState, CHANNELS, CHECK_AGAIN, CLEAR_URL, CLEAR_URL_TEXT, CLEAR_URL_TITLE, eventMeaning, eventWords, evLine, previewAddress, RETRY, PERMISSION_LEAD, PERMISSION_SHORT, permissionNow, PROGRESS_NOTE, toggled, webhookActive, type Channel, type Permission } from "../../settings/notifyWords";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { TabStrip } from "../ui/Rows";
import { SAVED } from "../../settings/savedWords";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, useSaves, type RowSpec } from "./kit";
import { yamlOf } from "./yaml";
import { SAMPLE_INTAKE_TITLE, ALERTS_ON_THIS_DEVICE, TEST_ALERT_SHOWN, ALERTS_IN_BACKGROUND } from "../../settings/sharedWords";

export { permissionNow };
const AREA = "/settings/notifications";

function useNotify() {
  const [notify, setNotify] = useState<Notify | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    setError(null);
    api.getNotify().then(setNotify, (e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);
  return { notify, setNotify, error, load };
}

/** `/settings/notifications`: the two channels and whether each is active (W17 brief O.2). */
export function NotificationsList() {
  const { notify, error, load } = useNotify();
  const permission = permissionNow();
  const browserIsActive = browserOn(isEnabled(), permission);
  return (
    <AreaScreen title="Notifications" sub="Where Kraft tells you it has stopped for you." status={{ label: SAVED.change }} yaml={false}>
      {error && <><p className="ph-error" role="alert">{error}</p><Button className="ph-btn" onClick={load}>{RETRY}</Button></>}
      {notify && (
        <Group
          title="Channels"
          foot={PROGRESS_NOTE}
          rows={[
            { key: "webhook", label: CHANNELS.webhook, to: `${AREA}/webhook`, sub: `${notify.events.length ? evLine(notify.events) : "no events"} · URL ${notify.url_set ? "set" : "not set"}`, chips: [{ label: channelState(webhookActive(notify)), tone: webhookActive(notify) ? "ok" : undefined }] },
            { key: "browser", label: CHANNELS.browser, to: `${AREA}/browser`, sub: permission === "granted" ? "This device only" : `This device only · ${PERMISSION_SHORT[permission]}`, chips: [{ label: channelState(browserIsActive), tone: browserIsActive ? "ok" : undefined }] },
          ]}
        />
      )}
    </AreaScreen>
  );
}

/** `/settings/notifications/:channel`: Overview, Config and the effective values as YAML. */
export function NotificationChannel() {
  const { channel } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "config" ? "config" : "overview";
  const known = channel === "webhook" || channel === "browser";
  const { notify, setNotify, error, load } = useNotify();
  const view = (t: "overview" | "config") => setParams(t === "config" ? { tab: "config" } : {}, { replace: true });
  if (!known) return <AreaScreen title="Notifications" status={{ label: SAVED.change }} yaml={false}><p className="ph-empty">There is no channel {channel}.</p></AreaScreen>;
  const name = CHANNELS[channel as Channel];
  return (
    <AreaScreen
      title={name}
      sub={channel === "webhook" ? "One webhook: ntfy, Pushover, Slack, Discord or your own receiver." : "Alerts in this browser while its tab is in the background."}
      status={{ label: SAVED.change }}
      yaml={notify && channel === "webhook" ? yamlOf({ enabled: notify.enabled, url: notify.url_set ? "(set, never shown)" : null, base_url: notify.base_url, events: notify.events }) : false}
    >
      <TabStrip label={name} value={tab} onChange={view} tabs={[{ id: "overview", label: "Overview" }, { id: "config", label: "Config" }]} />
      {error && <><p className="ph-error" role="alert">{error}</p><Button className="ph-btn" onClick={load}>{RETRY}</Button></>}
      {notify && channel === "webhook" && <Webhook notify={notify} setNotify={setNotify} tab={tab} />}
      {notify && channel === "browser" && <BrowserAlerts tab={tab} />}
    </AreaScreen>
  );
}

const lastSend = (n: Notify) => (n.last_test ? (n.last_test.error ? `failed ${ago(n.last_test.at)}: ${n.last_test.error}` : `delivered ${ago(n.last_test.at)} · ${n.last_test.status} · ${n.last_test.ms} ms`) : "never sent");

function Preview({ link }: { link: string }) {
  const [id, setId] = useState<string>(NOTIFY_EVENTS[0].id);
  const { edit, node } = useEditor();
  const event = NOTIFY_EVENTS.find((e) => e.id === id) ?? NOTIFY_EVENTS[0];
  const sample = notificationText(event, SAMPLE_INTAKE_TITLE);
  return (
    <>
      <Group title="What it looks like" rows={[{ label: "event", value: eventWords(id), onEdit: () => edit({ kind: "choice", title: "Event", value: id, options: NOTIFY_EVENTS.map((e) => ({ value: e.id, label: eventWords(e.id), hint: e.label })), set: async (v) => { setId(v); return null; } }) }]} />
      <div className="ph-notice" role="img" aria-label={`Preview: ${sample.title}. ${sample.body}`}>
        <span className="ph-notice-icon" aria-hidden="true">K</span>
        <span className="ph-notice-text">
          <span className="ph-notice-title">{sample.title}</span>
          <span className="ph-notice-body">{sample.body}</span>
          <span className="ph-notice-link">{previewAddress(link)}/work-items/k7m2x9qa</span>
        </span>
      </div>
      {node}
    </>
  );
}

function Webhook({ notify, setNotify, tab }: { notify: Notify; setNotify: (n: Notify) => void; tab: "overview" | "config" }) {
  const { run, mark } = useSaves();
  const { edit, node } = useEditor();
  const sheet = useSheet();
  const [testing, setTesting] = useState(false);
  const put = (key: string, body: Parameters<typeof api.putNotify>[0]) => run(key, async () => setNotify(await api.putNotify(body)));
  const canTest = notify.enabled && notify.url_set;
  const sendTest = async () => {
    setTesting(true);
    const err = await run("test", async () => setNotify({ ...notify, last_test: await api.testNotify() }));
    setTesting(false);
    if (!err) showToast("Test sent. The result is under Last send.");
  };

  if (tab === "overview")
    return (
      <>
        <Group rows={[
          { label: "status", value: channelState(webhookActive(notify)), sub: notify.enabled ? "Kraft POSTs to the webhook when a run stops." : "No outbound traffic." },
          { label: "last send", sub: lastSend(notify), value: notify.last_test ? (notify.last_test.error ? "failed" : "delivered") : "never" },
          { label: "notifies", value: evLine(notify.events) },
        ]} />
        <Preview link={notify.base_url ?? ""} />
      </>
    );

  return (
    <>
      <Group rows={[{ label: "enabled", sw: notify.enabled, onSwitch: (on) => void put("enabled", { enabled: on }), ...mark("enabled") }]} />
          <Group title="Notifies" rows={NOTIFY_EVENTS.map((e): RowSpec => ({ key: e.id, label: eventMeaning(e.label), sub: eventWords(e.id), sw: notify.events.includes(e.id), onSwitch: () => void put("events", { events: notify.events.includes(e.id) ? notify.events.filter((x) => x !== e.id) : [...notify.events, e.id] }), ...mark("events") }))} />
      <Group
        title="Delivery"
        rows={[
          { label: "webhook URL", value: notify.url_set ? "•••••••• set" : "not set", sub: notify.url_set ? "Never shown again: it usually carries a token." : "Kraft stores it 0600 and never displays it.", ...mark("url"), onEdit: () => edit({ kind: "text", title: "Webhook URL", help: "https://ntfy.sh/your-topic. Stored, never shown again.", value: "", placeholder: "https://ntfy.sh/your-topic", secret: true, set: async (v) => (v.trim() ? run("url", async () => setNotify(await api.putNotify({ url: v.trim() }))) : `Enter a URL, or use ${CLEAR_URL}.`) }) },
          ...(notify.url_set ? [{ label: CLEAR_URL, danger: true, onClick: () => sheet.open("clear") } as RowSpec] : []),
          { label: "link back", value: notify.base_url ?? "not set", mono: true, sub: "What the notification links to: the address your phone can reach.", ...mark("base"), onEdit: () => edit({ kind: "text", title: "Link back", value: notify.base_url ?? "", placeholder: "http://192.168.1.20:8765", set: (v) => run("base", async () => setNotify(await api.putNotify({ base_url: v.trim() }))) }) },
        ]}
      />
      <Group
        title="Test"
        rows={[{ label: "Send a test", onClick: () => void sendTest(), disabled: testing || !canTest, sub: canTest ? lastSend(notify) : WEBHOOK_OFF(notify.url_set), ...mark("test") }]}
      />
      {node}
      {sheet.is("clear") && (
        <ConfirmSheet title={CLEAR_URL_TITLE} text={CLEAR_URL_TEXT} confirm={{ label: CLEAR_URL, danger: true, run: () => void put("url", { url: "" }).then(() => sheet.close()) }} onClose={sheet.close} />
      )}
    </>
  );
}

function BrowserAlerts({ tab }: { tab: "overview" | "config" }) {
  const [on, setOn] = useState(isEnabled);
  const [permission, setPermission] = useState<Permission>(permissionNow);
  const [events, setEvents] = useState(enabledEvents);
  const toggle = (next: boolean) => {
    setBrowserEnabled(next);
    setOn(next);
  };
  const toggleEvent = (id: string) => {
    const next = toggled(events, id);
    setBrowserEvents(next);
    setEvents(next);
  };
  const testBrowser = () => {
    try {
      const text = notificationText(NOTIFY_EVENTS[0], "Test alert");
      new Notification(text.title, { body: text.body, icon: "/icon.svg" });
      showToast(TEST_ALERT_SHOWN);
    } catch {
      showToast("This browser could not show the alert", { tone: "error" });
    }
  };
  if (tab === "overview")
    return (
      <>
        <Group rows={[
          { label: "status", value: channelState(browserOn(on, permission)), sub: ALERTS_IN_BACKGROUND },
          ...(permission === "granted" ? [] : [{ label: "permission", value: PERMISSION_SHORT[permission], sub: PERMISSION_LEAD[permission].title } as RowSpec]),
          { label: "notifies", value: evLine(events) },
        ]} />
        <Preview link={location.origin} />
      </>
    );
  const lead = PERMISSION_LEAD[permission];
  const off = permission !== "granted" ? BROWSER_WHY[permission] : !on ? BROWSER_OFF : null;
  return (
    <>
      <Group
        title="Permission"
        rows={[
          permission === "default"
            ? { label: ALLOW, sub: lead.text, onClick: () => void requestPermission().then((p) => { setPermission(p); if (p === "granted") toggle(true); }) }
            : permission === "denied"
              ? { label: lead.title, sub: lead.text }
              : { label: lead.title, sub: lead.text },
          ...(permission === "denied" ? (lead.steps ?? []).map((t, i): RowSpec => ({ label: `${i + 1}. ${t}` })) : []),
          ...(permission === "denied" ? [{ label: CHECK_AGAIN, onClick: () => setPermission(permissionNow()) } as RowSpec] : []),
        ]}
      />
      {permission !== "unsupported" && (
        <>
          <Group
            note="Kept in this browser only: another device is its own switch."
            rows={[
              { label: ALERTS_ON_THIS_DEVICE, sw: on, disabled: permission !== "granted", onSwitch: toggle },
              { label: "Send a test", onClick: testBrowser, disabled: !!off, sub: off ? undefined : "Shows an alert on this device now." },
            ]}
          />
          <Group title="Notifies" rows={NOTIFY_EVENTS.map((e): RowSpec => ({ key: e.id, label: eventMeaning(e.label), sub: eventWords(e.id), sw: events.includes(e.id), onSwitch: () => toggleEvent(e.id) }))} foot={PROGRESS_NOTE} />
        </>
      )}
    </>
  );
}
