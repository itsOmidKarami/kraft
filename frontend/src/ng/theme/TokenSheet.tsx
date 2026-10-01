import { useState } from "react";
import { Bell, MoreHorizontal } from "lucide-react";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { IconButton } from "../ui/IconButton";
import { Kbd } from "../ui/Kbd";
import { Menu } from "../ui/Menu";
import { Segmented } from "../ui/Segmented";
import { ShortId } from "../ui/ShortId";
import { Switch } from "../ui/Switch";
import { Tabs } from "../ui/Tabs";
import { HeaderActions } from "../shell/HeaderActions";
import { showToast } from "../ui/Toast";
import { AMOUNTS, paintedMode, SURFACES } from "./looks";
import { ThemeCard } from "./ThemeCard";
import "./tokens.css";

const GROUPS: [string, string[]][] = [
  ["Grounds", ["side", "bg", "surface", "surface-2", "line", "border"]],
  ["Text", ["text", "text-sub", "text-muted", "text-faint", "accent"]],
  ["Status", ["ok", "warn", "bad", "info"]],
  ["Neutral roles", ["selection", "focus"]],
  ["Review diff", ["diff-add-theme", "diff-del-theme", "diff-add-safe", "diff-del-safe", "diff-add-plain", "diff-del-plain"]],
];

/** /ng/_tokens, unlinked: every token and every primitive in the current look,
 *  and a ThemeCard per surface at the current mode and amount. */
export function TokenSheet() {
  const [tab, setTab] = useState("overview");
  const [on, setOn] = useState(true);
  const [seg, setSeg] = useState("subtle");
  const [dialog, setDialog] = useState(false);
  const html = document.documentElement.dataset;
  const amount = (html.amount ?? "subtle") as (typeof AMOUNTS)[number]["value"];

  return (
    <div className="tokens">
      <HeaderActions>
        <Button onClick={() => showToast("Toast from the header")}>Toast</Button>
        <Button variant="primary" onClick={() => setDialog(true)}>Dialog</Button>
      </HeaderActions>
      <h1>Tokens</h1>
      <p className="tokens-look">{`${html.surface} · ${html.accent} accent · ${paintedMode()} · ${amount}`}</p>
      {GROUPS.map(([name, keys]) => (
        <section key={name}>
          <h2>{name}</h2>
          <div className="swatches">
            {keys.map((k) => (
              <div key={k} className="swatch">
                <span className="swatch-chip" style={{ background: `var(--${k})` }} />
                <code>--{k}</code>
              </div>
            ))}
          </div>
        </section>
      ))}

      <section>
        <h2>Text on grounds</h2>
        {/* Each role only where its floor is 4.5: text-faint (3.0) is for
            non-text marks, status colours read as text on cards only. */}
        <div className="type-grid">
          {["side", "bg", "surface"].map((g) => (
            <div key={g} className="type-ground" style={{ background: `var(--${g})` }}>
              <span style={{ color: "var(--text)" }}>text on {g}</span>
              <span style={{ color: "var(--text-sub)" }}>text-sub</span>
              <span style={{ color: "var(--text-muted)" }}>text-muted</span>
              {g !== "side" && <a href="#tokens">accent link</a>}
              {g === "surface" && (
                <span className="status-words">
                  <span style={{ color: "var(--ok)" }}>✓ done</span>
                  <span style={{ color: "var(--warn)" }}>● needs you</span>
                  <span style={{ color: "var(--bad)" }}>✕ failed</span>
                  <span style={{ color: "var(--info)" }}>◐ running</span>
                </span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Buttons</h2>
        <div className="row">
          <Button variant="primary">Approve</Button>
          <Button>Reject…</Button>
          <Button variant="danger">Remove task</Button>
          <Button variant="primary" disabled>Approve</Button>
          <Button disabled>Reject…</Button>
          <Button variant="danger" disabled>Remove task</Button>
        </div>
        <div className="row">
          <IconButton label="Notifications"><Bell size={16} /></IconButton>
          <IconButton label="More" disabled><MoreHorizontal size={16} /></IconButton>
          <Menu
            label="Actions"
            trigger={<MoreHorizontal size={16} />}
            items={[
              { label: "Rename", onSelect: () => {} },
              { label: "Archive", onSelect: () => {}, disabled: true },
              { label: "Cancel item", onSelect: () => {}, danger: true },
            ]}
          />
          <Button onClick={() => setDialog(true)}>Open dialog</Button>
          <Button onClick={() => showToast("Approved — chain continues")}>Show toast</Button>
        </div>
      </section>

      <section>
        <h2>Controls</h2>
        <Tabs id="tok" label="Pane" tabs={[{ value: "overview", label: "Overview" }, { value: "log", label: "Log" }, { value: "yaml", label: "YAML" }]} value={tab} onChange={setTab} />
        <div className="row">
          <Segmented label="Amount" options={AMOUNTS} value={seg} onChange={setSeg} />
          <Segmented label="Locked" options={AMOUNTS} value="mono" onChange={() => {}} disabled />
        </div>
        <div className="row">
          <Switch label="On" checked={on} onChange={setOn} />
          <Switch label="Off" checked={false} onChange={() => {}} />
          <Switch label="Disabled" checked onChange={() => {}} disabled />
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
          <ShortId id="0123456789abcdef0123456789abcdef" />
        </div>
        <div className="fields">
          <Field label="Title" hint="Shown on the board">
            <input defaultValue="Add retry budget" />
          </Field>
          <Field label="Budget (USD)" error="Must be a number">
            <input defaultValue="ten" aria-invalid="true" />
          </Field>
        </div>
      </section>

      <section>
        <h2>Surfaces at this mode and amount</h2>
        <div className="cards">
          {SURFACES.map((s) => (
            <figure key={s.id}>
              <ThemeCard surface={s.id} accent="none" amount={amount} mode={paintedMode()} />
              <figcaption>{s.name}</figcaption>
            </figure>
          ))}
        </div>
      </section>

      {dialog && (
        <Dialog title="Cancel item" onClose={() => setDialog(false)} footer={<><Button onClick={() => setDialog(false)}>Keep</Button><Button variant="danger" onClick={() => setDialog(false)}>Cancel item</Button></>}>
          <Field label="Reason"><input /></Field>
        </Dialog>
      )}
    </div>
  );
}
