import { useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "../ui/Button";
import { gutter } from "./draft/lineDiff";
import type { ConfigDraft } from "./draft/useConfigDraft";
import type { Scope } from "./draft/types";
import { scopeFile } from "./draft/view";
import { ReadOnly } from "./plugin";
import { problemText } from "./problems";
import { tip } from "../ui/Tooltip";

const LINE = 18;

/** Puts the caret at the start of a line and scrolls it into view. */
export function jumpTo(ta: HTMLTextAreaElement | null, scroller: HTMLElement | null, line: number) {
  if (!ta) return;
  const at = ta.value.split("\n").slice(0, line).reduce((n, l) => n + l.length + 1, 0);
  ta.focus();
  ta.setSelectionRange(at, at);
  if (scroller) scroller.scrollTop = Math.max(0, line * LINE - scroller.clientHeight / 3);
}

/** The chain's YAML, a second fully editable view of the same draft (Decisions
 *  §9 YAML): edits apply as you type (one PUT 300 ms after typing stops),
 *  changed lines marked against the published file, problems on their line
 *  and in a docked pane, a syntax error keeping the canvas on the last valid
 *  draft. No autocomplete; Tab inserts two spaces. */
export function YamlView({ draft, scope, published, file: named }: { draft: ConfigDraft; scope: Scope; published: string | null | undefined; /** An area's file (repos, policy, intake); a chain or the library finds its own. */ file?: string }) {
  const view = draft.view!;
  const readOnly = useContext(ReadOnly);
  const file = named ?? scopeFile(view.files, scope);
  const where = named ? "page" : "canvas";
  const r = view.result;
  const stored = view.files[file] ?? "";
  const [text, setText] = useState(stored);
  const typing = useRef(false);
  // The last text that parsed: what "Revert to last valid draft" puts back.
  const lastGood = useRef(stored);
  if (!r.yaml_error) lastGood.current = stored;
  // An answer with no edit pending (a canvas op, undo, another tab) replaces the text.
  useEffect(() => {
    if (!typing.current) setText(stored);
  }, [stored]);
  const [issuesOpen, setIssuesOpen] = useState(true);
  const ta = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const lines = text.split("\n");
  const marks = useMemo(() => (published === undefined ? lines.map(() => "") : gutter(published, text)), [published, text, lines]);
  const err = r.yaml_error?.file === file ? r.yaml_error : undefined;
  const probs = err ? [] : r.problems.filter((p) => p.file === file && p.line);
  const msgs = new Map<number, string[]>();
  if (err) msgs.set(err.line - 1, [err.message]);
  for (const p of probs) msgs.set(p.line! - 1, [...(msgs.get(p.line! - 1) ?? []), problemText(p)]);

  const edit = (t: string) => {
    typing.current = true;
    setText(t);
    draft.text(file, t);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Tab indents, so Escape (or Shift+Tab) is the way out of the editor.
    if (e.key === "Escape") return e.currentTarget.blur();
    if (e.key !== "Tab" || e.shiftKey || readOnly) return;
    e.preventDefault();
    const el = e.currentTarget, a = el.selectionStart, b = el.selectionEnd;
    edit(`${text.slice(0, a)}  ${text.slice(b)}`);
    requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
  };
  const status = readOnly ? "published" : err ? `syntax error · the ${where} keeps the last valid draft` : probs.length || r.problems.length ? `parsed · ${r.problems.length} problem${r.problems.length === 1 ? "" : "s"}` : "parsed · live draft";
  const width = Math.max(40, ...lines.map((l, i) => l.length + (msgs.has(i) ? 60 : 0))) + 4;

  return (
    <section className="yv" aria-label={`${file}, YAML`}>
      <div className="yv-head">
        <span className="yv-file">{file}</span>
        <span className={`yv-status${err || r.problems.length ? " is-bad" : ""}`} role="status">{status}</span>
        <span className="bp-gap" />
        {readOnly ? <span className="yv-legend">read-only · it comes from a plugin</span> : <span className="yv-legend"><span className="yv-plus">+</span> added <span className="yv-tilde">~</span> changed · edits apply to the {where} as you type</span>}
      </div>
      {err && (
        <div className="yv-err" role="alert">
          <span>Line {err.line}: {err.message}</span>
          <Button onClick={() => { typing.current = false; setText(lastGood.current); draft.text(file, lastGood.current); }}>Revert to last valid draft</Button>
        </div>
      )}
      <div className="yv-scroll" ref={scroller}>
        <div className="yv-doc" style={{ minWidth: `${width}ch` }}>
          <div className="yv-gutter" aria-hidden="true">
            {lines.map((_, i) => (
              <div key={i} className={`yv-g${msgs.has(i) ? " is-bad" : ""}`}><span className={`yv-mark is-${marks[i] === "+" ? "add" : marks[i] === "~" ? "change" : "none"}`}>{marks[i]}</span>{i + 1}</div>
            ))}
          </div>
          <div className="yv-body">
            <div className="yv-bg" aria-hidden="true">
              {lines.map((l, i) => (
                <div key={i} className={`yv-bgl${msgs.has(i) ? " is-bad" : marks[i] === "+" ? " is-add" : marks[i] === "~" ? " is-change" : ""}`}>
                  {msgs.has(i) && <span className="yv-msg" style={{ left: `${l.length + 3}ch` }}>{msgs.get(i)!.join(" · ")}</span>}
                </div>
              ))}
            </div>
            <textarea
              ref={ta}
              className="yv-ta"
              aria-label={`${file}, YAML`}
              spellCheck={false}
              autoCapitalize="off"
              autoComplete="off"
              wrap="off"
              readOnly={readOnly}
              value={text}
              style={{ height: lines.length * LINE + 8 }}
              onChange={(e) => edit(e.target.value)}
              onBlur={() => { typing.current = false; draft.flush(); }}
              onKeyDown={onKey}
            />
          </div>
        </div>
      </div>
      {(err || probs.length > 0) && (
        <div className={`yv-issues${issuesOpen ? " is-open" : ""}`}>
          <div className="yv-issues-head">
            <span className="yv-issues-title">{err ? "Syntax error" : `${probs.length} problem${probs.length === 1 ? "" : "s"}`}</span>
            <span className="bp-gap" />
            <button type="button" className="icon-btn" aria-expanded={issuesOpen} {...tip(issuesOpen ? "Collapse the problems" : "Expand the problems")} onClick={() => setIssuesOpen((o) => !o)}>
              {issuesOpen ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
            </button>
          </div>
          {issuesOpen && (
            <ul className="yv-issues-list">
              {err && <li><button type="button" className="yv-issue" onClick={() => jumpTo(ta.current, scroller.current, err.line - 1)}>line {err.line} · {err.message}</button></li>}
              {probs.slice(0, 30).map((p, i) => (
                <li key={i}><button type="button" className="yv-issue" onClick={() => jumpTo(ta.current, scroller.current, p.line! - 1)}>line {p.line} · {p.path} · {problemText(p)}</button></li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
