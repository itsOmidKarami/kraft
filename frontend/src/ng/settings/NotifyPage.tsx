import { useEffect, useRef, useState } from "react";
import * as api from "../../api";
import { isEnabled, NOTIFY_EVENTS, notificationText, requestPermission, setEnabled as setBrowserEnabled } from "../../browserNotify";
import { ago } from "../../format";
import type { Notify } from "../../types";
import { Dialog } from "../ui/Dialog";
import { Segmented } from "../ui/Segmented";
import { Switch } from "../ui/Switch";
import { showToast } from "../ui/Toast";
import { Block, SetRow } from "./parts";
import "./settings.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
type Permission = NotificationPermission | "unsupported";
const permissionNow = (): Permission => (typeof Notification === "undefined" ? "unsupported" : Notification.permission);

const PERMISSION_TEXT: Record<Permission, string> = {
  granted: "Allowed in this browser.",
  default: "This browser has not been asked yet.",
  denied: "Blocked in the browser's site settings. Allow notifications for this site there to use it.",
  unsupported: "This browser has no notifications.",
};

/** Settings › Notifications (UX V2 W16 C). Saved on change: each switch sends
 *  its own key; the two text fields send theirs when you leave them or press
 *  Enter. The webhook is the install's; browser alerts are this browser's. */
export function NotifyPage() {
  const [notify, setNotify] = useState<Notify | null>(null);
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [url, setUrl] = useState("");
  const [base, setBase] = useState<string | null>(null);
  const [browserOn, setBrowserOn] = useState(isEnabled);
  const [permission, setPermission] = useState<Permission>(permissionNow);
  const [previewEvent, setPreviewEvent] = useState(NOTIFY_EVENTS[0].id);
  const [testing, setTesting] = useState(false);
  const [clearing, setClearing] = useState(false);
  const urlBox = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getNotify().then(setNotify, (e) => setErrors({ page: message(e) }));
  }, []);
  const err = (key: string, text: string | null) => setErrors((e) => ({ ...e, [key]: text }));

  if (!notify) return <div className="ng-settings">{errors.page && <p className="set-error" role="alert">{errors.page}</p>}</div>;

  const put = async (key: string, body: Parameters<typeof api.putNotify>[0]): Promise<boolean> => {
    err(key, null);
    try {
      setNotify(await api.putNotify(body));
      return true;
    } catch (e) {
      err(key, message(e));
      return false;
    }
  };
  const toggleEvent = (id: string) => put("events", { events: notify.events.includes(id) ? notify.events.filter((x) => x !== id) : [...notify.events, id] });
  const saveUrl = async () => {
    if (!url.trim()) return;
    if (await put("url", { url: url.trim() })) setUrl("");
  };
  const saveBase = async () => {
    if (base === null || base === (notify.base_url ?? "")) return;
    if (await put("base", { base_url: base })) setBase(null);
  };
  const commitOnEnter = (save: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      save();
    }
  };
  const sendTest = async () => {
    setTesting(true);
    err("test", null);
    try {
      const last_test = await api.testNotify();
      setNotify({ ...notify, last_test });
    } catch (e) {
      err("test", message(e));
    } finally {
      setTesting(false);
    }
  };
  const test = notify.last_test;
  const lastTest = test
    ? test.error
      ? `last attempt ${ago(test.at)}, failed: ${test.error}`
      : `last delivered ${ago(test.at)} · ${test.status} · ${test.ms} ms`
    : "never sent";

  const toggleBrowser = (next: boolean) => {
    setBrowserEnabled(next);
    setBrowserOn(next);
  };
  const ask = async () => setPermission(await requestPermission());
  const testBrowser = () => {
    const text = notificationText(NOTIFY_EVENTS[0], "Test alert");
    new Notification(text.title, { body: text.body, icon: "/icon.svg" });
    showToast("Test alert shown on this device");
  };
  const event = NOTIFY_EVENTS.find((e) => e.id === previewEvent) ?? NOTIFY_EVENTS[0];
  const sample = notificationText(event, "Add retry budget to the intake poller");
  const link = base ?? notify.base_url ?? "";

  return (
    <div className="ng-settings">
      <div className="set-page">
        <h1>Notifications</h1>
        <p className="lede">Where Kraft tells you it has stopped for you. One webhook (ntfy, Pushover, Slack, Discord, or your own receiver), and alerts in this browser.</p>

        <Block id="set-webhook" title="Webhook" aside="saved on change">
          <SetRow label="status" error={errors.enabled} hint={notify.enabled ? "On. Kraft POSTs to the webhook when a run stops." : "Off. No outbound traffic."}>
            <Switch label="Webhook notifications" checked={notify.enabled} onChange={(v) => void put("enabled", { enabled: v })} />
          </SetRow>
          <SetRow label="notifies" error={errors.events} hint="Progress events are deliberately not offered. A notifier that fires on progress gets muted, and a muted channel is the same as no channel at all.">
            <ul className="set-events">
              {NOTIFY_EVENTS.map((e) => (
                <li key={e.id} className="set-event">
                  <span className="set-event-text"><code>{e.id}</code><span className="set-hint">{e.label}</span></span>
                  <Switch label={e.id} checked={notify.events.includes(e.id)} onChange={() => void toggleEvent(e.id)} />
                </li>
              ))}
            </ul>
          </SetRow>
          <SetRow label="webhook URL" error={errors.url} hint={notify.url_set ? "A webhook URL is set. It is never shown again, because it usually carries a token." : "Usually carries a token in its path, so Kraft stores it 0600 and never displays it."}>
            <input ref={urlBox} className="set-input is-wide" type="password" autoComplete="off" aria-label="Webhook URL" placeholder={notify.url_set ? "•••••••• (unchanged)" : "https://ntfy.sh/your-topic"} value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={commitOnEnter(() => void saveUrl())} onBlur={() => void saveUrl()} />
            {notify.url_set && <button type="button" className="set-btn is-danger" onClick={() => setClearing(true)}>Clear URL</button>}
          </SetRow>
          <SetRow label="link back" error={errors.base} hint="What the notification links to. Kraft only knows its bind address, 0.0.0.0 on the LAN, so set the address your phone can reach.">
            <input className="set-input is-wide" aria-label="Link back" placeholder="http://192.168.1.20:8765" value={link} onChange={(e) => setBase(e.target.value)} onKeyDown={commitOnEnter(() => void saveBase())} onBlur={() => void saveBase()} />
          </SetRow>
          <SetRow label="test" error={errors.test} hint={lastTest}>
            <button type="button" className="set-btn" disabled={testing || !notify.url_set} onClick={() => void sendTest()}>Send a test</button>
          </SetRow>
        </Block>

        <Block id="set-browser" title="This browser" aside="kept in this browser">
          <SetRow label="alerts" hint={`${PERMISSION_TEXT[permission]} Alerts show only while this tab is in the background.`}>
            <Switch label="Alerts on this device" checked={browserOn} disabled={permission === "unsupported"} onChange={toggleBrowser} />
            {permission === "default" && <button type="button" className="set-btn" onClick={() => void ask()}>Allow notifications</button>}
          </SetRow>
          <SetRow label="test" hint="Shows an alert on this device now.">
            <button type="button" className="set-btn" disabled={permission !== "granted"} onClick={testBrowser}>Send a test</button>
          </SetRow>
        </Block>

        <Block id="set-preview" title="Preview">
          <Segmented label="Event" options={NOTIFY_EVENTS.map((e) => ({ value: e.id, label: e.id }))} value={previewEvent} onChange={setPreviewEvent} />
          <div className="set-notice" role="img" aria-label={`Preview: ${sample.title}. ${sample.body}`}>
            <span className="set-notice-icon" aria-hidden>K</span>
            <div className="set-notice-text">
              <span className="set-notice-title">{sample.title}</span>
              <span className="set-notice-body">{sample.body}</span>
              <span className="set-notice-link">{(link || "192.168.1.20:8765").replace(/\/$/, "")}/work-items/wi_01HX3M2</span>
            </div>
          </div>
        </Block>
      </div>
      {clearing && (
        <Dialog
          title="Clear the webhook URL?"
          onClose={() => setClearing(false)}
          footer={<><button type="button" className="set-btn" onClick={() => setClearing(false)}>Cancel</button><button type="button" className="set-btn is-danger" onClick={() => { setClearing(false); void put("url", { url: "" }); }}>Clear URL</button></>}
        >
          <p>Removes the stored webhook and stops all sends.</p>
        </Dialog>
      )}
    </div>
  );
}
