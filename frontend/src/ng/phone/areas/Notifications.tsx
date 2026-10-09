import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { isEnabled, NOTIFY_EVENTS, notificationText, requestPermission, setEnabled as setBrowserEnabled } from "../../../browserNotify";
import { ago } from "../../../format";
import type { Notify } from "../../../types";
import { showToast } from "../../ui/Toast";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { TabStrip } from "../ui/Rows";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, useSaves, type RowSpec } from "./kit";
import { yamlOf } from "./yaml";

type Permission = NotificationPermission | "unsupported";
export const permissionNow = (): Permission => (typeof Notification === "undefined" ? "unsupported" : Notification.permission);
const PERMISSION_TEXT: Record<Permission, string> = {
  granted: "Allowed in this browser.",
  default: "This browser has not been asked yet.",
  denied: "Blocked in the browser's site settings. Allow notifications for this site there to use it.",
  unsupported: "This browser has no notifications.",
};
const CHANNELS = { webhook: "Webhook", browser: "Browser alerts" } as const;
type Channel = keyof typeof CHANNELS;
const AREA = "/settings/notifications";

function useNotify() {
  const [notify, setNotify] = useState<Notify | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.getNotify().then(setNotify, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  return { notify, setNotify, error };
}

/** `/settings/notifications`: the two channels and whether each is active (W17 brief O.2). */
export function NotificationsList() {
  const { notify, error } = useNotify();
  const permission = permissionNow();
  const browserActive = isEnabled() && permission === "granted";
  return (
    <AreaScreen title="Notifications" sub="Where Kraft tells you it has stopped for you." status={{ label: "saved on change" }} yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {notify && (
        <Group
          title="Channels"
          foot="Progress events are deliberately not offered. A notifier that fires on progress gets muted, and a muted channel is the same as no channel at all."
          rows={[
            { key: "webhook", label: "Webhook", to: `${AREA}/webhook`, sub: `${notify.events.length ? notify.events.join(", ") : "no events"} · URL ${notify.url_set ? "set" : "not set"}`, chips: [{ label: notify.enabled && notify.url_set ? "active" : "inactive", tone: notify.enabled && notify.url_set ? "ok" : undefined }] },
            { key: "browser", label: "Browser alerts", to: `${AREA}/browser`, sub: `This browser only · ${PERMISSION_TEXT[permission].replace(/\.$/, "").toLowerCase()}`, chips: [{ label: browserActive ? "active" : "inactive", tone: browserActive ? "ok" : undefined }] },
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
  const { notify, setNotify, error } = useNotify();
  const view = (t: "overview" | "config") => setParams(t === "config" ? { tab: "config" } : {}, { replace: true });
  if (!known) return <AreaScreen title="Notifications" status={{ label: "saved on change" }} yaml={false}><p className="ph-empty">There is no channel {channel}.</p></AreaScreen>;
  const name = CHANNELS[channel as Channel];
  return (
    <AreaScreen
      title={name}
      sub={channel === "webhook" ? "One webhook: ntfy, Pushover, Slack, Discord or your own receiver." : "Alerts in this browser while its tab is in the background."}
      status={{ label: "saved on change" }}
      yaml={notify && channel === "webhook" ? yamlOf({ enabled: notify.enabled, url: notify.url_set ? "(set, never shown)" : null, base_url: notify.base_url, events: notify.events }) : false}
    >
      <TabStrip label={name} value={tab} onChange={view} tabs={[{ id: "overview", label: "Overview" }, { id: "config", label: "Config" }]} />
      {error && <p className="ph-error" role="alert">{error}</p>}
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
  const sample = notificationText(event, "Add retry budget to the intake poller");
  return (
    <>
      <Group title="What it looks like" rows={[{ label: "event", value: id, mono: true, onEdit: () => edit({ kind: "choice", title: "Event", value: id, options: NOTIFY_EVENTS.map((e) => ({ value: e.id, label: e.id, hint: e.label })), set: async (v) => { setId(v); return null; } }) }]} />
      <div className="ph-notice" role="img" aria-label={`Preview: ${sample.title}. ${sample.body}`}>
        <span className="ph-notice-icon" aria-hidden="true">K</span>
        <span className="ph-notice-text">
          <span className="ph-notice-title">{sample.title}</span>
          <span className="ph-notice-body">{sample.body}</span>
          <span className="ph-notice-link">{(link || "192.168.1.20:8765").replace(/\/$/, "")}/work-items/07eb05f8c9d14b2e8a6f13d5b7e3042a</span>
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
          { label: "status", value: notify.enabled ? (notify.url_set ? "on" : "on, no URL") : "off", sub: notify.enabled ? "Kraft POSTs to the webhook when a run stops." : "No outbound traffic." },
          { label: "last send", sub: lastSend(notify), value: notify.last_test ? (notify.last_test.error ? "failed" : "delivered") : "never" },
          { label: "notifies", value: notify.events.length ? notify.events.join(", ") : "nothing" },
        ]} />
        <Preview link={notify.base_url ?? ""} />
      </>
    );

  return (
    <>
      <Group rows={[{ label: "enabled", sw: notify.enabled, onSwitch: (on) => void put("enabled", { enabled: on }), ...mark("enabled") }]} />
      <Group title="Notifies" rows={NOTIFY_EVENTS.map((e): RowSpec => ({ key: e.id, label: e.id, mono: true, sub: e.label, sw: notify.events.includes(e.id), onSwitch: () => void put("events", { events: notify.events.includes(e.id) ? notify.events.filter((x) => x !== e.id) : [...notify.events, e.id] }), ...mark("events") }))} />
      <Group
        title="Delivery"
        rows={[
          { label: "webhook URL", value: notify.url_set ? "•••••••• set" : "not set", sub: notify.url_set ? "Never shown again: it usually carries a token." : "Kraft stores it 0600 and never displays it.", ...mark("url"), onEdit: () => edit({ kind: "text", title: "Webhook URL", help: "https://ntfy.sh/your-topic. Stored, never shown again.", value: "", placeholder: "https://ntfy.sh/your-topic", secret: true, set: async (v) => (v.trim() ? run("url", async () => setNotify(await api.putNotify({ url: v.trim() }))) : "Enter a URL, or use Remove URL.") }) },
          ...(notify.url_set ? [{ label: "Remove URL", danger: true, onClick: () => sheet.open("clear") } as RowSpec] : []),
          { label: "link back", value: notify.base_url ?? "not set", mono: true, sub: "What the notification links to: the address your phone can reach.", ...mark("base"), onEdit: () => edit({ kind: "text", title: "Link back", value: notify.base_url ?? "", placeholder: "http://192.168.1.20:8765", set: (v) => run("base", async () => setNotify(await api.putNotify({ base_url: v.trim() }))) }) },
        ]}
      />
      <Group
        title="Test"
        rows={[{ label: "Send a test", onClick: () => void sendTest(), disabled: testing || !canTest, sub: canTest ? lastSend(notify) : "Turn on the webhook and set a URL first.", ...mark("test") }]}
      />
      {node}
      {sheet.is("clear") && (
        <ConfirmSheet title="Remove the webhook URL?" text="Removes the stored webhook and stops all sends." confirm={{ label: "Remove URL", danger: true, run: () => void put("url", { url: "" }).then(() => sheet.close()) }} onClose={sheet.close} />
      )}
    </>
  );
}

function BrowserAlerts({ tab }: { tab: "overview" | "config" }) {
  const [on, setOn] = useState(isEnabled);
  const [permission, setPermission] = useState<Permission>(permissionNow);
  const toggle = (next: boolean) => {
    setBrowserEnabled(next);
    setOn(next);
  };
  const testBrowser = () => {
    try {
      const text = notificationText(NOTIFY_EVENTS[0], "Test alert");
      new Notification(text.title, { body: text.body, icon: "/icon.svg" });
      showToast("Test alert shown on this device");
    } catch {
      showToast("This browser could not show the alert");
    }
  };
  if (tab === "overview")
    return (
      <>
        <Group rows={[
          { label: "status", value: on && permission === "granted" ? "active" : "inactive", sub: "Alerts show only while this tab is in the background." },
          { label: "permission", value: permission === "default" ? "not asked" : permission, sub: PERMISSION_TEXT[permission] },
        ]} />
        <Preview link={location.origin} />
      </>
    );
  return (
    <>
      <Group
        note="Kept in this browser only: another device is its own switch."
        rows={[
          { label: "Alerts on this device", sw: on, disabled: permission === "unsupported", onSwitch: toggle, sub: PERMISSION_TEXT[permission] },
          ...(permission === "default" ? [{ label: "Allow notifications", onClick: () => void requestPermission().then(setPermission), sub: "Asks the browser once." } as RowSpec] : []),
          { label: "Send a test", onClick: testBrowser, disabled: permission !== "granted", sub: permission === "granted" ? "Shows an alert on this device now." : "Allow notifications first." },
        ]}
      />
    </>
  );
}
