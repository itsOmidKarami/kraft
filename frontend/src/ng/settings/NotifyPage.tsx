import { useEffect, useState, type ReactNode } from "react";
import { Monitor, Send, Webhook } from "lucide-react";
import * as api from "../../api";
import { enabledEvents, isEnabled, NOTIFY_EVENTS, notificationText, requestPermission, setEnabled as setBrowserEnabled, setEvents as setBrowserEvents } from "../../browserNotify";
import { ago } from "../../format";
import type { Notify } from "../../types";
import { Inspector } from "../graph/Inspector";
import { useResizable, useWidth } from "../graph/useResizable";
import { HeaderActions } from "../shell/HeaderActions";
import { Head, Kv, Note } from "../templates/panes/controls";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Switch } from "../ui/Switch";
import { showToast } from "../ui/Toast";
import "../templates/templates.css";
import "./notify.css";
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
const PERMISSION_SHORT: Record<Permission, string> = { granted: "allowed", default: "not asked yet", denied: "blocked", unsupported: "not supported" };

type Channel = "webhook" | "browser";
type Tab = "overview" | "config" | "yaml";

const evLine = (ids: string[]) => (ids.length ? ids.join(", ") : "nothing");
const toggled = (ids: string[], id: string) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);

/** Settings › Notifications (AreaNotifications): the two channels as cards, the
 *  selected one in the side pane. Switches and event toggles save on change; the
 *  webhook URL and link back are typed in the pane and sent with Save. The
 *  webhook is the install's; browser alerts are this browser's. */
export function NotifyPage() {
  const [notify, setNotify] = useState<Notify | null>(null);
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [sel, setSel] = useState<Channel | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [paneOpen, setPaneOpen] = useState(true);
  const [url, setUrl] = useState("");
  const [base, setBase] = useState<string | null>(null);
  const [browserOn, setBrowserOn] = useState(isEnabled);
  const [browserEvents, setBrowserEventsState] = useState(enabledEvents);
  const [permission, setPermission] = useState<Permission>(permissionNow);
  const [browserTest, setBrowserTest] = useState<{ at: string; error: string | null } | null>(null);
  const [testing, setTesting] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [frame, w] = useWidth();
  const size = useResizable("notify", w);

  useEffect(() => {
    api.getNotify().then(setNotify, (e) => setErrors({ page: message(e) }));
  }, []);
  const err = (key: string, text: string | null) => setErrors((e) => ({ ...e, [key]: text }));

  if (!notify) return <div className="tpl-note">{errors.page && <p className="set-error" role="alert">{errors.page}</p>}</div>;

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
  const link = base ?? notify.base_url ?? "";
  const dirty = !!url.trim() || (base !== null && base.trim() !== (notify.base_url ?? ""));
  const save = async () => {
    const body: Parameters<typeof api.putNotify>[0] = {};
    if (url.trim()) body.url = url.trim();
    if (base !== null && base.trim() !== (notify.base_url ?? "")) body.base_url = base.trim();
    if (await put("save", body)) {
      setUrl("");
      setBase(null);
      showToast("Saved · writes notify.yaml");
    }
  };
  const discard = () => {
    setUrl("");
    setBase(null);
    err("save", null);
  };
  const sendTest = async () => {
    setTesting(true);
    err("test", null);
    try {
      const last_test = await api.testNotify();
      setNotify({ ...notify, last_test });
      if (!last_test.error) showToast("Test sent to the webhook");
    } catch (e) {
      err("test", message(e));
    } finally {
      setTesting(false);
    }
  };

  const webhookActive = notify.enabled && notify.url_set;
  const browserActive = browserOn && permission === "granted";
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
  const toggleBrowserEvent = (id: string) => {
    const next = toggled(browserEvents, id);
    setBrowserEvents(next);
    setBrowserEventsState(next);
  };
  const ask = async () => setPermission(await requestPermission());
  const testBrowser = () => {
    try {
      const text = notificationText(NOTIFY_EVENTS[0], "Test alert");
      new Notification(text.title, { body: text.body, icon: "/icon.svg" });
      setBrowserTest({ at: new Date().toISOString(), error: null });
      showToast("Test alert shown on this device");
    } catch (e) {
      setBrowserTest({ at: new Date().toISOString(), error: message(e) });
    }
  };
  const browserTestLine = browserTest ? (browserTest.error ? `last attempt ${ago(browserTest.at)}, failed: ${browserTest.error}` : `last shown ${ago(browserTest.at)}`) : "shows an alert on this device now";

  const pick = (c: Channel | null, t: Tab = c ? "config" : "overview") => {
    setSel(c);
    setTab(t);
    setPaneOpen(true);
  };
  const floor = () => {
    setSel(null);
    setTab("overview");
  };
  const openYaml = () => (!sel && tab === "yaml" && paneOpen ? pick(null) : pick(null, "yaml"));
  const yaml = [
    `enabled: ${notify.enabled}`,
    notify.url_set ? "# webhook_url: 0600, never shown" : "# webhook_url: not set",
    `base_url: ${notify.base_url || '""'}`,
    "events:",
    ...notify.events.map((e) => `  - ${e}`),
  ].join("\n");

  const channels: { id: Channel; icon: ReactNode; name: string; sub: string; active: boolean; events: string[]; extra: string; extraBad?: boolean; testing: boolean; canTest: boolean; test: () => void }[] = [
    {
      id: "webhook", icon: <Webhook size={15} />, name: "Webhook", active: webhookActive, events: notify.events,
      sub: notify.url_set ? `URL stored · links to ${link || "no address"}` : "No URL set",
      extra: notify.url_set ? (test ? (test.error ? `last attempt ${ago(test.at)} failed` : `last sent ${ago(test.at)} · ${test.status} OK`) : "never sent") : "nothing can be sent",
      extraBad: !notify.url_set || !!test?.error, testing, canTest: webhookActive, test: () => void sendTest(),
    },
    {
      id: "browser", icon: <Monitor size={15} />, name: "This browser", active: browserActive, events: browserEvents,
      sub: permission === "granted" ? "Alerts while this tab is in the background. This device only." : `${PERMISSION_TEXT[permission]} This device only.`,
      extra: permission === "granted" ? (browserTest ? browserTestLine.replace(/^last /, "") : "") : PERMISSION_SHORT[permission],
      extraBad: permission === "denied" || !!browserTest?.error, testing: false, canTest: browserActive, test: testBrowser,
    },
  ];

  const sample = (events: string[]) => {
    const event = NOTIFY_EVENTS.find((e) => events.includes(e.id)) ?? NOTIFY_EVENTS[0];
    const text = notificationText(event, "Add retry budget to the intake poller");
    return (
      <div className="set-notice" role="img" aria-label={`Preview: ${text.title}. ${text.body}`}>
        <span className="set-notice-icon" aria-hidden>K</span>
        <div className="set-notice-text">
          <span className="set-notice-title">{text.title}</span>
          <span className="set-notice-body">{text.body}</span>
          <span className="set-notice-link">{(link || "192.168.1.20:8765").replace(/\/$/, "")}/work-items/07eb05f8c9d14b2e8a6f13d5b7e3042a</span>
        </div>
      </div>
    );
  };
  const state = (on: boolean) => <span className={on ? "nt-on" : "nt-off"}>{on ? "active" : "inactive"}</span>;

  const overview = sel === "webhook" ? (
    <>
      <Head>Status</Head>
      <Kv k="status" v={state(webhookActive)} />
      <Kv k="last sent" v={notify.url_set ? lastTest.replace(/^last (delivered|attempt) /, "") : "never"} />
      <Head>Notifies</Head>
      <Kv k="events" v={evLine(notify.events)} mono />
      <Head>A notification looks like</Head>
      {sample(notify.events)}
    </>
  ) : sel === "browser" ? (
    <>
      <Head>Status</Head>
      <Kv k="status" v={state(browserActive)} />
      <Kv k="permission" v={PERMISSION_SHORT[permission]} />
      <Head>Notifies</Head>
      <Kv k="events" v={evLine(browserEvents)} mono />
      <Head>A notification looks like</Head>
      {sample(browserEvents)}
      <Note>Kept in this browser, apart from notify.yaml. Alerts show only while this tab is in the background.</Note>
    </>
  ) : (
    <>
      <Head>Webhook</Head>
      <Kv k="status" v={state(webhookActive)} />
      <Kv k="notifies" v={evLine(notify.events)} mono muted={!webhookActive} />
      <Head>This browser</Head>
      <Kv k="status" v={state(browserActive)} />
      <Kv k="notifies" v={evLine(browserEvents)} mono muted={!browserActive} />
      <Head>A notification looks like</Head>
      {sample(notify.events)}
      <Note>Each channel sends only for its own events. Select a channel to change them.</Note>
    </>
  );

  const eventRows = (on: string[], toggle: (id: string) => void, who: string) => (
    <ul className="set-events nt-events">
      {NOTIFY_EVENTS.map((e) => (
        <li key={e.id} className="set-event">
          <span className="set-event-text"><code>{e.id}</code><span className="set-hint">{e.label}</span></span>
          <Switch label={`${e.id} (${who})`} checked={on.includes(e.id)} onChange={() => toggle(e.id)} />
        </li>
      ))}
    </ul>
  );
  const problem = (key: string) => errors[key] && <p className="set-error" role="alert">{errors[key]}</p>;

  const webhookConfig = (
    <>
      <Head>Status</Head>
      <div className="nt-line">
        <span className="set-event-text"><span>{notify.enabled ? "Active" : "Inactive"}</span><span className="set-hint">Kraft POSTs to the webhook when a run stops</span></span>
        <Switch label="Webhook notifications" checked={notify.enabled} onChange={(v) => void put("enabled", { enabled: v })} />
      </div>
      {problem("enabled")}
      <Head>Notifies</Head>
      {eventRows(notify.events, (id) => void put("events", { events: toggled(notify.events, id) }), "webhook")}
      {problem("events")}
      <Note>Progress events are deliberately not offered. A notifier that fires on progress gets muted, and a muted channel is the same as no channel at all.</Note>
      <Head>Webhook URL</Head>
      <input className="tpl-pf-input is-mono nt-input" type="password" autoComplete="off" aria-label="Webhook URL" placeholder={notify.url_set ? "•••••••• (unchanged)" : "https://ntfy.sh/your-topic"} value={url} onChange={(e) => setUrl(e.target.value)} />
      <Note>{notify.url_set ? "A webhook URL is set. It is never shown again, because it usually carries a token. Kept in notify.yaml, 0600. Typing a new one replaces it." : "Usually carries a token in its path, so Kraft stores it 0600 and never displays it."}</Note>
      <Head>Link back</Head>
      <input className="tpl-pf-input is-mono nt-input" aria-label="Link back" placeholder="http://192.168.1.20:8765" value={link} onChange={(e) => setBase(e.target.value)} />
      <Note>What the notification links to. Kraft only knows its bind address, 0.0.0.0 on the LAN, so set the address your phone can reach.</Note>
      {problem("save")}
      <Head>Test</Head>
      <div className="nt-line">
        <Button disabled={testing || !webhookActive} onClick={() => void sendTest()}><Send size={12} aria-hidden /> Send a test</Button>
        <span className="set-hint">{webhookActive ? lastTest : notify.url_set ? "set the channel active first" : "set a webhook URL first"}</span>
      </div>
      {problem("test")}
      {notify.url_set && (
        <>
          <div className="nt-gap"><Button variant="danger" onClick={() => setClearing(true)}>Clear URL</Button></div>
          <Note>Removes the stored webhook and stops all sends.</Note>
          {problem("url")}
        </>
      )}
    </>
  );
  const browserConfig = (
    <>
      <Head>Status</Head>
      <div className="nt-line">
        <span className="set-event-text"><span>{browserOn ? "Active" : "Inactive"}</span><span className="set-hint">alerts on this device only</span></span>
        <Switch label="Alerts on this device" checked={browserOn} disabled={permission === "unsupported"} onChange={toggleBrowser} />
      </div>
      <Note>{PERMISSION_TEXT[permission]} While this tab is in the background.</Note>
      {permission === "default" && <div className="nt-gap"><Button onClick={() => void ask()}>Allow notifications</Button></div>}
      <Head>Notifies</Head>
      {eventRows(browserEvents, toggleBrowserEvent, "this browser")}
      <Note>Independent of the webhook. Kept in this browser, so it saves at once and has no Save button.</Note>
      <Head>Test</Head>
      <div className="nt-line">
        <Button disabled={!browserActive} onClick={testBrowser}><Send size={12} aria-hidden /> Send a test</Button>
        <span className="set-hint">{browserTestLine}</span>
      </div>
    </>
  );

  const body = tab === "yaml" ? (
    <>
      <pre className="nt-yaml" aria-label="notify.yaml">{yaml}</pre>
      <Note>The webhook URL is stored here but never shown, because it usually carries a token. Browser alerts are kept in this browser, not in this file.</Note>
    </>
  ) : tab === "config" ? (sel === "webhook" ? webhookConfig : browserConfig) : overview;
  const tabs = [{ value: "overview", label: "Overview" }, ...(sel ? [{ value: "config", label: "Config" }] : []), ...(sel !== "browser" ? [{ value: "yaml", label: "YAML" }] : [])];
  const title = sel === "webhook" ? "Webhook" : sel === "browser" ? "This browser" : "notifications";
  const sub = sel === "webhook" ? `notify.yaml · ${webhookActive ? "active" : "inactive"}` : sel === "browser" ? `this device only · ${browserActive ? "active" : "inactive"}` : "notify.yaml · saved on change";

  return (
    <div className="tpl-page" ref={frame}>
      <HeaderActions>
        <span className="saved-note">saved on change</span>
        <Button aria-pressed={!sel && tab === "yaml" && paneOpen} onClick={openYaml}>YAML</Button>
      </HeaderActions>
      <div className="tpl-area">
        <div className="nt-body" style={{ right: size.overlay ? 0 : paneOpen ? size.width : 40 }} onClick={floor}>
          <div className="nt-page">
            <div className="nt-title"><h1>Notifications</h1><p className="lede">Where Kraft tells you it has stopped for you. One webhook (ntfy, Pushover, Slack, Discord, or your own receiver), and alerts in this browser.</p></div>
            <h2 className="nt-h2">Channels</h2>
            {channels.map((c) => (
              <div key={c.id} className={`nt-card${sel === c.id ? " is-sel" : ""}${c.active ? " is-on" : ""}`} onClick={(e) => e.stopPropagation()}>
                <button type="button" className="nt-card-main" aria-pressed={sel === c.id} aria-label={c.name} onClick={() => pick(c.id)}>
                  <span className="nt-icon" aria-hidden>{c.icon}</span>
                  <span className="nt-text">
                    <span className="nt-name">{c.name}</span>
                    <span className="nt-sub">{c.sub}</span>
                    <span className="nt-chips">{c.events.map((id) => <span key={id} className="nt-chip">{id}</span>)}</span>
                  </span>
                  <span className="nt-state">
                    <span className="nt-status"><span className="nt-dot" aria-hidden />{c.active ? "active" : "inactive"}</span>
                    {c.extra && <span className={`nt-extra${c.extraBad ? " is-bad" : ""}`}>{c.extra}</span>}
                  </span>
                </button>
                <Button className="nt-test" aria-label={`Send a test, ${c.name}`} disabled={c.testing || !c.canTest} onClick={c.test}><Send size={12} aria-hidden /> Send a test</Button>
              </div>
            ))}
          </div>
        </div>
        <Inspector
          id="notify-pane"
          open={paneOpen}
          size={size}
          crumbs={sel ? [{ label: "Notifications", onClick: floor }] : []}
          icon={sel === "webhook" ? "webhook" : sel === "browser" ? "monitor" : "bell"}
          title={title}
          sub={sub}
          tabs={tabs}
          tab={tab}
          onTab={(t) => setTab(t as Tab)}
          onCollapse={() => setPaneOpen(false)}
          onExpand={() => setPaneOpen(true)}
          footer={sel === "webhook" && tab === "config" ? <><Button disabled={!dirty} onClick={discard}>Discard</Button><span className="bp-gap" /><Button variant="primary" disabled={!dirty} onClick={() => void save()}>Save</Button></> : undefined}
        >
          {body}
        </Inspector>
      </div>
      {clearing && (
        <Dialog
          title="Clear the webhook URL?"
          onClose={() => setClearing(false)}
          footer={<><Button onClick={() => setClearing(false)}>Cancel</Button><Button variant="danger" onClick={() => { setClearing(false); void put("url", { url: "" }); }}>Clear URL</Button></>}
        >
          <p>Removes the stored webhook and stops all sends.</p>
        </Dialog>
      )}
    </div>
  );
}
