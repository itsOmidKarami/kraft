import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** A text field that sends on a pause (Decided 3): the page's `field(…, pause)`
 *  debounces, blur flushes. It follows the server's value while not focused. */
export function PauseText({ label, value, onText, onBlur, long, rows = 3, placeholder, required, autoFocus, sub, bad, mono }: {
  label: string;
  value: string;
  onText: (text: string) => void;
  onBlur?: () => void;
  long?: boolean;
  rows?: number;
  placeholder?: string;
  required?: boolean;
  autoFocus?: boolean;
  sub?: ReactNode;
  bad?: boolean;
  mono?: boolean;
}) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  const id = useId();
  useEffect(() => {
    if (!focused.current) setText(value);
  }, [value]);
  const props = {
    id,
    value: text,
    placeholder,
    autoFocus,
    "aria-required": required || undefined,
    "aria-invalid": bad || undefined,
    className: `tpl-pf-input${long ? " is-long" : ""}${mono ? " is-mono" : ""}${bad ? " is-bad" : ""}`,
    onFocus: () => void (focused.current = true),
    onBlur: () => {
      focused.current = false;
      onBlur?.();
    },
    onChange: (e: { target: { value: string } }) => {
      setText(e.target.value);
      onText(e.target.value);
    },
  };
  return (
    <div className="tpl-pf">
      <label htmlFor={id} className="tpl-pf-label">{label}</label>
      {long ? <textarea rows={rows} {...props} /> : <input spellCheck={false} {...props} />}
      {sub && <p className={`tpl-pf-sub${bad ? " is-bad" : ""}`}>{sub}</p>}
    </div>
  );
}

export type Option = { value: string; label: string; disabled?: boolean };

/** A labelled select; `check` adds the checkbox the gate's document row has. */
export function SelectRow({ label, value, options, onPick, sub, bad, check }: {
  label: string;
  value: string;
  options: Option[];
  onPick: (v: string) => void;
  sub?: ReactNode;
  bad?: boolean;
  check?: { label: string; on: boolean; onToggle: () => void };
}) {
  const id = useId();
  return (
    <div className="tpl-pf">
      <label htmlFor={id} className="tpl-pf-label">{label}</label>
      <div className="tpl-pf-line">
        <select id={id} className={`tpl-pf-select${bad ? " is-bad" : ""}`} value={value} onChange={(e) => onPick(e.target.value)}>
          {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
        </select>
        {check && (
          <label className="tpl-pf-check">
            <input type="checkbox" checked={check.on} onChange={check.onToggle} />
            {check.label}
          </label>
        )}
      </div>
      {sub && <p className={`tpl-pf-sub${bad ? " is-bad" : ""}`}>{sub}</p>}
    </div>
  );
}

/** A key and its value; with `onClick` the value is a button (a link to that level). */
export function Kv({ k, v, onClick, mono, muted }: { k: string; v: ReactNode; onClick?: () => void; mono?: boolean; muted?: boolean }) {
  const cls = `pkv-v${mono ? " is-mono" : ""}${muted ? " is-muted" : ""}`;
  return (
    <div className="pkv">
      <span className="pkv-k">{k}</span>
      {onClick ? <button type="button" className={`${cls} is-link`} onClick={onClick}>{v}</button> : <span className={cls}>{v}</span>}
    </div>
  );
}

export const Head = ({ children }: { children: ReactNode }) => <h3 className="pane-group">{children}</h3>;
export const Note = ({ children, bad }: { children: ReactNode; bad?: boolean }) => <p className={`pane-note${bad ? " is-bad" : ""}`}>{children}</p>;
