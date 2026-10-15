import { useContext, useEffect, useId, useState, type ReactNode } from "react";
import { Combobox, notListed, unlisted, type Choice } from "../../ui/Combobox";
import { Switch } from "../../ui/Switch";
import { ReadOnly } from "../addon";
import { ValueMenu, type Option } from "../draft/ValueMenu";

/** A text field that sends on a pause (Decided 3): the page's `field(…, pause)`
 *  debounces, blur flushes. It follows the server's value while not focused.
 *  With `choices` it lists the values it takes as you type; a `closed` set
 *  sends only a listed value (or an empty one, unless it is required), and
 *  flags any other where it is typed instead of saving it. */
export function PauseText({ label, value, onText, onBlur, long, rows = 3, placeholder, required, autoFocus, sub, bad, mono, choices, closed, noun = label, listLabel }: {
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
  choices?: Choice[];
  closed?: boolean;
  noun?: string;
  listLabel?: string;
}) {
  const [text, setText] = useState(value);
  const [focused, setFocused] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!focused) setText(value);
  }, [value]);
  const readOnly = useContext(ReadOnly);
  if (readOnly) return <Kv k={label} v={value || "not set"} mono={mono} muted={!value} />;
  // A closed set's typed value that is not listed is kept here, never sent.
  const listed = choices?.length ? choices : null;
  // A required one is not sent empty either: clearing it on the way to
  // typing a new value would save it unset and break the draft.
  const empty = (t: string) => !!required && !t.trim();
  const stray = closed && listed ? (empty(text) ? `Pick ${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun} from the list.` : notListed(unlisted(text, listed), noun)) : null;
  const send = (t: string) => {
    if (!closed || !listed || (!empty(t) && !unlisted(t, listed).length)) onText(t);
  };
  const note = stray && !focused ? stray : sub;
  // The note under the field is its description, so a refusal is read with the
  // field and not only seen under it: "invalid" alone gave no reason (R10b-08).
  const noteId = `${id}-note`;
  const props = {
    id,
    "aria-describedby": note ? noteId : undefined,
    value: text,
    placeholder,
    autoFocus,
    "aria-required": required || undefined,
    "aria-invalid": bad || undefined,
    className: `tpl-pf-input${long ? " is-long" : ""}${mono ? " is-mono" : ""}${bad || (stray && !focused) ? " is-bad" : ""}`,
    onFocus: () => setFocused(true),
    onBlur: () => {
      setFocused(false);
      onBlur?.();
    },
    onChange: (e: { target: { value: string } }) => {
      setText(e.target.value);
      send(e.target.value);
    },
  };
  return (
    <div className="tpl-pf">
      <label htmlFor={id} className="tpl-pf-label">{label}</label>
      {long ? <textarea rows={rows} {...props} /> : listed ? (
        <Combobox
          {...props}
          choices={listed}
          closed={closed}
          noun={noun}
          listLabel={listLabel}
          invalid={bad}
          onChange={(t) => {
            setText(t);
            send(t);
          }}
          // A pick is a decision: it goes now, not after the pause.
          onPick={() => onBlur?.()}
        />
      ) : <input spellCheck={false} {...props} />}
      {note && <p id={noteId} className={`tpl-pf-sub${bad || (stray && !focused) ? " is-bad" : ""}`}>{note}</p>}
    </div>
  );
}

export type { Option };

/** A labelled pick, edited in place like a ValueCell: the chosen option and a pencil, then the list on a click.
 *  `check` adds the switch the gate's document row has; `plain` is a picker that is no edit. */
export function SelectRow({ label, value, options, onPick, sub, bad, check, plain }: {
  label: string;
  value: string;
  options: Option[];
  onPick: (v: string) => void;
  sub?: ReactNode;
  bad?: boolean;
  check?: { label: string; on: boolean; onToggle: () => void };
  plain?: boolean;
}) {
  const readOnly = useContext(ReadOnly);
  const shown = options.find((o) => o.value === value)?.label ?? value;
  if (readOnly) return <Kv k={label} v={`${shown}${check?.on ? ` · ${check.label}` : ""}`} />;
  return (
    <div className="tpl-pf">
      <span className="tpl-pf-label">{label}</span>
      <div className="tpl-pf-line">
        <ValueMenu label={label} value={value} options={options} onPick={onPick} bad={bad} plain={plain} />
        {check && (
          <label className="tpl-pf-check">
            <Switch checked={check.on} onChange={check.onToggle} label={check.label} />
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

/** The rows of a group that are at their default, folded into one line ("3 defaults · show"); opening it lists them as they are. */
export function Fold({ count, noun = "default", lead, children }: { count: number; noun?: string; /** What the line starts with, when it also names the group. */ lead?: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!count) return null;
  return (
    <>
      <button type="button" className="pane-fold" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        {lead}{count} {count === 1 ? noun : `${noun}s`} · {open ? "hide" : "show"}
      </button>
      <div id={id} hidden={!open} className="pane-fold-body">{open && children}</div>
    </>
  );
}
