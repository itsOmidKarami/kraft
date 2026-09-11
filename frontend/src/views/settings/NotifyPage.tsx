import { useState } from "react";
import * as api from "../../api";
import { Switch } from "../../components/ui";
import type { Notify } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5f notifications ─────────────────────────────────────────────────────── */

/** The two states Kraft is blocked on a person. Anything else gets muted
 *  within a week, and a muted channel is the same as no channel. */
const NOTIFY_EVENTS: { id: string; label: string }[] = [
  { id: "gate_requested", label: "a decision is waiting" },
  { id: "work_item_needs_human", label: "stopped — gate wait or cap breach" },
];

export function NotifyPage() {
  const { value, error, reload } = useResource(() => api.getNotify());
  const [url, setUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  // `where` is the control that triggered the save — "channel", "url",
  // "base", or an event id (each event switch is its own control) — so a
  // message only ever renders next to the control that produced it, not
  // wherever `message` happens to also be read.
  const [message, setMessage] = useState<{ where: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const notify: Notify | null = value;

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

  return (
    <>
      <PageHead
        title="Notifications"
        note="one webhook — ntfy, Pushover, Slack, Discord, or your own receiver"
      />
      {error && <p className="form-error">{error}</p>}
      {notify && (
        <>
          <section className="settings-section">
            <h6>Channel</h6>
            <div className="save-row">
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
                `.switch` pattern is already here and already means on/off
                (PluginsPage.tsx). */}
            {NOTIFY_EVENTS.map((e) => (
              <div key={e.id} className="save-row">
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
        </>
      )}
    </>
  );
}
