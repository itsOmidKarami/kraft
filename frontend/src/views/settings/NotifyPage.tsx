import { useEffect, useState } from "react";
import * as api from "../../api";
import { isEnabled as browserNotifyEnabled, NOTIFY_EVENTS, requestPermission, setEnabled as setBrowserNotifyEnabled } from "../../browserNotify";
import { Switch } from "../../components/ui";
import { ago } from "../../format";
import type { Notify } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";
import "./notify.css";

/* ── 5f notifications ─────────────────────────────────────────────────────── */

export function NotifyPage() {
  const { value, error, reload } = useResource(() => api.getNotify());
  const [url, setUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [browserEnabled, setBrowserEnabled] = useState(browserNotifyEnabled);
  const [browserPermission, setBrowserPermission] = useState<NotificationPermission>(
    typeof Notification !== "undefined" ? Notification.permission : "denied",
  );
  // `where` is the control that triggered the save — "channel", "url",
  // "base", or an event id (each event switch is its own control) — so a
  // message only ever renders next to the control that produced it, not
  // wherever `message` happens to also be read.
  const [message, setMessage] = useState<{ where: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const notify: Notify | null = value;

  const [lastTest, setLastTest] = useState(notify?.last_test ?? null);
  const [testBusy, setTestBusy] = useState(false);

  useEffect(() => {
    if (notify) setLastTest(notify.last_test);
  }, [notify]);

  const sendTest = async () => {
    setTestBusy(true);
    try {
      setLastTest(await api.testNotify());
    } catch (e) {
      setMessage({ where: "test", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setTestBusy(false);
    }
  };

  const put = async (body: Parameters<typeof api.putNotify>[0], where: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putNotify(body);
      setUrl("");
      await reload();
      setMessage({ where, text: "saved" });
    } catch (e) {
      setMessage({ where, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const base = baseUrl ?? notify?.base_url ?? "";

  const toggleBrowserNotify = async (next: boolean) => {
    setBrowserNotifyEnabled(next);
    setBrowserEnabled(next);
    if (next) setBrowserPermission(await requestPermission());
  };

  return (
    <>
      <PageHead
        title="Notifications"
        note="one webhook — ntfy, Pushover, Slack, Discord, or your own receiver"
      />
      {error && <p className="form-error">{error}</p>}

      <section className="settings-section">
        <h6>Browser notifications</h6>
        <div className="save-row field-row">
          <Switch checked={browserEnabled} onChange={toggleBrowserNotify} label="Alerts on this device" />
          <span className="save-hint">
            {browserPermission === "denied"
              ? "blocked in browser settings — allow notifications for this site to use it"
              : "this device only, while this tab is in the background"}
          </span>
        </div>
      </section>

      {notify && (
        <>
          <section className="settings-section">
            <h6>Channel</h6>
            <div className="save-row field-row">
              <Switch
                checked={notify.enabled}
                onChange={(v) => put({ enabled: v }, "channel")}
                label="Notifications"
                disabled={busy}
              />
              <span className="save-hint">
                {message?.where === "channel"
                  ? message.text
                  : notify.enabled
                    ? "on — Kraft will POST when a run stops"
                    : "off — no outbound traffic"}
              </span>
            </div>
            <div className="field">
              <label htmlFor="notify-url">Webhook URL</label>
              <input
                id="notify-url"
                className="input mono"
                type="password"
                autoComplete="off"
                placeholder={notify.url_set ? "•••••••• (unchanged)" : "https://ntfy.sh/your-topic"}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <span className="field-hint">
                {notify.url_set
                  ? "a webhook URL is set — it is never shown again, because it usually carries a token"
                  : "usually carries a token in its path, so Kraft stores it 0600 and never displays it"}
              </span>
            </div>
            <SaveRow
              onSave={() => put({ url }, "url")}
              onDiscard={() => setUrl("")}
              dirty={url.length > 0}
              busy={busy}
              message={message?.where === "url" ? message.text : null}
              hint="writes notify.yaml, 0600"
            />
            <div className="save-row">
              <button
                className="btn btn-ghost"
                disabled={testBusy || !notify.url_set}
                onClick={sendTest}
              >
                Send a test
              </button>
              <span className="save-hint">
                {message?.where === "test"
                  ? message.text
                  : lastTest
                    ? lastTest.error
                      ? `last attempt ${ago(lastTest.at)} · failed · ${lastTest.error}`
                      : `last delivered ${ago(lastTest.at)} · ${lastTest.status} ${lastTest.status && lastTest.status < 400 ? "OK" : ""} · ${lastTest.ms} ms`
                    : "never sent"}
              </span>
            </div>
            {notify.url_set && (
              <div className="save-row">
                {/* No `data-danger`: the only rule for it is
                    `.overflow-menu button[data-danger]` (styles.css:159), so
                    outside an overflow menu the attribute styles nothing. The
                    action is also cheap to undo — paste the URL again. */}
                <button
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => put({ url: "" }, "url")}
                >
                  Clear URL
                </button>
                <span className="save-hint">removes the stored webhook and stops all sends</span>
              </div>
            )}
          </section>

          <section className="settings-section">
            <h6>Link back</h6>
            <div className="field">
              <label htmlFor="notify-base-url">Base URL</label>
              <input
                id="notify-base-url"
                className="input mono"
                placeholder="http://192.168.1.20:8765"
                value={base}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
              <span className="field-hint">
                What the notification links to. Kraft only knows its bind address, which is
                0.0.0.0 on the LAN — set the address your phone can actually reach.
              </span>
            </div>
            <SaveRow
              onSave={() => put({ base_url: base }, "base")}
              onDiscard={() => setBaseUrl(null)}
              dirty={baseUrl !== null && baseUrl !== (notify.base_url ?? "")}
              busy={busy}
              message={message?.where === "base" ? message.text : null}
              hint="writes notify.yaml"
            />
          </section>

          <section className="settings-section">
            <h6>What notifies</h6>
            {/* Toggles, not checkboxes: this codebase has no `type="checkbox"`
                anywhere, and `.radio` hides its input to draw a round dot —
                a radio's affordance, which is wrong for a multi-select. The
                `.switch` pattern is already here and already means on/off. */}
            {NOTIFY_EVENTS.map((e) => (
              <div key={e.id} className="save-row field-row">
                <Switch
                  checked={notify.events.includes(e.id)}
                  onChange={() =>
                    put(
                      {
                        events: notify.events.includes(e.id)
                          ? notify.events.filter((x) => x !== e.id)
                          : [...notify.events, e.id],
                      },
                      e.id,
                    )
                  }
                  label={e.id}
                  disabled={busy}
                />
                <span className="save-hint">
                  {message?.where === e.id ? (
                    message.text
                  ) : (
                    <>
                      <code>{e.id}</code> · {e.label}
                    </>
                  )}
                </span>
              </div>
            ))}
            <p className="settings-foot">
              Progress events are deliberately not offered. A notifier that fires on progress
              gets muted, and a muted channel is the same as no channel at all.
            </p>
          </section>

          <section className="settings-section">
            <h6>Preview</h6>
            <div className="notify-preview">
              <span className="notify-preview-icon">K</span>
              <div>
                <div className="notify-preview-title">Kraft · needs you</div>
                <div className="notify-preview-body">
                  Add retry budget to the intake poller — plan_approval is waiting (repo-a, 12
                  min)
                </div>
                <div className="notify-preview-link">
                  {base || "192.168.1.20:8765"}/work-items/wi_01HX3M2
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </>
  );
}
