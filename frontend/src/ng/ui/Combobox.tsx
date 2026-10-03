import { forwardRef, useEffect, useId, useLayoutEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import "./ui.css";

/** One value a field can take, with a line saying what it is when the server has one. */
export type Choice = { value: string; summary?: string };

/** A comma-separated list's entries, trimmed, empty ones dropped. */
export const entries = (text: string): string[] => text.split(",").map((s) => s.trim()).filter(Boolean);

/** What `text` names that is not one of `choices`: the whole trimmed text, or
 *  with `multiple` each entry of the list. Empty when every value is listed. */
export function unlisted(text: string, choices: Choice[], multiple = false): string[] {
  const known = new Set(choices.map((c) => c.value));
  return (multiple ? entries(text) : [text.trim()].filter(Boolean)).filter((v) => !known.has(v));
}

/** A refusal for values `unlisted` found, naming the first: "“sds” is not an action." */
export const notListed = (bad: string[], noun: string) => (bad.length ? `“${bad[0]}” is not ${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}. Pick one from the list.` : null);

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "role"> & {
  value: string;
  choices: Choice[];
  /** Every keystroke and every pick: the whole text. */
  onChange: (text: string) => void;
  /** A pick from the list, by click or Enter, after `onChange`: the whole text. */
  onPick?: (text: string) => void;
  /** Only a listed value is right: a value that matches none is flagged
   *  (`aria-invalid`) where it is typed. Saving it is the field's call. */
  closed?: boolean;
  /** A comma-separated list: the list filters and completes its last entry and leaves out the ones already in it. */
  multiple?: boolean;
  /** What one value is, for the note when nothing matches ("action"). */
  noun?: string;
  /** Names the list ("Actions"). */
  listLabel?: string;
  /** Flagged for a reason of the field's own. */
  invalid?: boolean;
  /** The list takes room under the field instead of floating over the page (a phone's sheet, which would clip it). */
  inline?: boolean;
};

/** A text field with a list of the values it takes (the ARIA 1.2 combobox
 *  with list autocomplete). The list shows every value on focus, and typing
 *  filters it by value or summary; ↑/↓ move through it, Enter picks, Escape
 *  closes it. With nothing active, Enter and Escape are the field's own. An
 *  open set (a model name) takes any text and the list only suggests; a
 *  `closed` one flags a value it does not list. */
export const Combobox = forwardRef<HTMLInputElement, Props>(function Combobox({ value, choices, onChange, onPick, closed, multiple, noun = "value", listLabel = "Values", invalid, inline, onKeyDown, onFocus, onBlur, className, ...input }, ref) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [focused, setFocused] = useState(false);
  const list = useId();

  // The entry being typed: the whole text, or a list's last entry.
  const cut = multiple ? value.lastIndexOf(",") + 1 : 0;
  const head = multiple ? entries(value.slice(0, cut)) : [];
  const typed = value.slice(cut).trim();
  const q = typed.toLowerCase();
  const left = choices.filter((c) => !head.includes(c.value));
  // A value already picked shows the whole list again, so it can be changed.
  const shown = !q || left.some((c) => c.value === typed) ? left : left.filter((c) => c.value.toLowerCase().includes(q) || !!c.summary?.toLowerCase().includes(q));
  const at = active < shown.length ? active : -1;
  const expanded = open && shown.length > 0;
  const optId = (i: number) => `${list}-${i}`;

  const bad = closed ? unlisted(value, choices, multiple) : [];
  // While typing, only a value no listed one can complete is flagged (an earlier list entry already is).
  const flagged = !!invalid || (bad.length > 0 && (!focused || shown.length === 0 || bad.some((b) => head.includes(b))));

  // Above the field when there is more room there than below (a phone's bottom sheet).
  const box = useRef<HTMLSpanElement>(null);
  const [above, setAbove] = useState(false);
  const floating = expanded || (closed && open && focused && !!typed && !shown.length);
  useLayoutEffect(() => {
    if (!floating || inline || !box.current) return;
    const r = box.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    setAbove(below < 240 && r.top > below);
  }, [floating, inline]);

  useEffect(() => setActive(-1), [value]);
  useEffect(() => {
    if (at >= 0) document.getElementById(optId(at))?.scrollIntoView?.({ block: "nearest" });
  });

  const pick = (c: Choice) => {
    const next = multiple ? [...head, c.value].join(", ") : c.value;
    setOpen(false);
    setActive(-1);
    onChange(next);
    onPick?.(next);
  };

  const keys = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!shown.length) return;
      setOpen(true);
      const down = e.key === "ArrowDown";
      setActive(at < 0 ? (down ? 0 : shown.length - 1) : (at + (down ? 1 : shown.length - 1)) % shown.length);
      return;
    }
    if (e.key === "Enter" && expanded && at >= 0) {
      e.preventDefault();
      return pick(shown[at]);
    }
    if (e.key === "Escape" && expanded) {
      // The list's, not the editor's or the pane's.
      e.preventDefault();
      e.stopPropagation();
      e.nativeEvent.stopImmediatePropagation();
      setOpen(false);
      setActive(-1);
      return;
    }
    // Tab leaves, and an Enter that picks nothing is the field's own (its save): either closes the list.
    if (e.key === "Tab" || e.key === "Enter") setOpen(false);
    onKeyDown?.(e);
  };

  return (
    <span ref={box} className={`cbx${inline ? " is-inline" : above ? " is-above" : ""}`}>
      <input
        {...input}
        ref={ref}
        className={className}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={list}
        aria-activedescendant={expanded && at >= 0 ? optId(at) : undefined}
        aria-invalid={flagged || undefined}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onFocus={(e) => {
          setFocused(true);
          setOpen(true);
          onFocus?.(e);
        }}
        onBlur={(e) => {
          setFocused(false);
          setOpen(false);
          onBlur?.(e);
        }}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setOpen(true);
          onChange(e.target.value);
        }}
        onKeyDown={keys}
      />
      <ul id={list} role="listbox" aria-label={listLabel} className="cbx-list" hidden={!expanded}>
        {expanded && shown.map((c, i) => (
          <li
            key={c.value}
            id={optId(i)}
            role="option"
            aria-selected={i === at}
            className="menu-item cbx-opt"
            // Keep the focus in the field: a click picks, it does not blur.
            onMouseDown={(e) => e.preventDefault()}
            onMouseMove={() => i !== at && setActive(i)}
            onClick={() => pick(c)}
          >
            <span className="menu-text">
              <span className="cbx-value">{c.value}</span>
              {c.summary && <span className="menu-sub">{c.summary}</span>}
            </span>
          </li>
        ))}
      </ul>
      {closed && open && focused && typed && !shown.length && <span className="cbx-none" role="status">No {noun} matches “{typed}”.</span>}
    </span>
  );
});
