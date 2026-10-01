import { useEffect, useRef, useState } from "react";
import { Button } from "../../ui/Button";
import { idError } from "../ids";

/** One row: an id field and its button (Decisions §9 Adding a node, Invalid
 *  id). The reason shows only when the id is refused, here or by the server. */
export function IdRow({ label, taken, placeholder, initial = "", go, refused, onGo }: {
  label: string;
  taken: string[];
  placeholder?: string;
  initial?: string;
  go: string;
  /** The server's refusal of the last try. */
  refused?: string | null;
  onGo: (id: string) => void;
}) {
  const [text, setText] = useState(initial);
  const [sent, setSent] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // After the popover is placed (focusSoon), then the text selected.
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.select();
    });
  }, []);
  const err = idError(text, taken) ?? (refused && sent === text ? refused : null);
  const can = !!text && !err;
  const submit = () => {
    if (!can) return;
    setSent(text);
    onGo(text);
  };
  return (
    <div className="idrow">
      <div className="idrow-line">
        <input
          ref={input}
          className={`idrow-input${err ? " is-bad" : ""}`}
          aria-label={label}
          aria-invalid={!!err}
          aria-describedby={err ? "idrow-err" : undefined}
          value={text}
          placeholder={placeholder}
          spellCheck={false}
          onChange={(e) => setText(e.target.value.trim())}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
        />
        <Button variant="primary" disabled={!can} onClick={submit}>{go}</Button>
      </div>
      {err && <p id="idrow-err" className="idrow-err" role="alert">{err}</p>}
    </div>
  );
}
