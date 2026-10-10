import { useContext, useEffect, useRef, useState } from "react";
import { Button } from "../../ui/Button";
import { detailOf } from "../../http";
import * as d from "../draft/draftApi";
import { FIELD_PAUSE_MS, type ConfigDraft } from "../draft/useConfigDraft";
import type { Scope } from "../draft/types";
import { problemsAt } from "../draft/view";
import { problemText } from "../problems";
import { jumpTo } from "../YamlView";
import { ReadOnly } from "../plugin";

/** One component's own YAML (Decisions §9 Item YAML): the server writes it
 *  (W13-A's fragment route, R46), edits send `set_fragment` on a pause; an id
 *  change or a syntax error is refused and the last valid version kept. */
export function ItemYaml({ draft, scope, path, extendsName, fixed }: { draft: ConfigDraft; scope: Scope; path: string; extendsName?: string; /** The text itself, for a part the fragment route does not serve (a plugin's library component). */ fixed?: string }) {
  const view = draft.view!;
  const readOnly = useContext(ReadOnly);
  const [text, setText] = useState<string | null>(fixed ?? null);
  const [refusal, setRefusal] = useState<{ message: string; line?: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const box = useRef<HTMLDivElement>(null);
  // Every answer that changes the draft can change this component's text.
  const stamp = `${view.updated_at}|${view.result.changes.length}|${path}`;

  const load = () =>
    d.fragment(scope.area, scope.key, path).then((a) => {
      if (a.status !== 200) return setFailed(true);
      setFailed(false);
      setText(a.body.text);
    });
  useEffect(() => {
    if (!dirty.current && fixed === undefined) void load();
    // `load` reads only the scope and path, both in `stamp`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const send = async (yaml: string) => {
    const a = await draft.ops([{ op: "set_fragment", path, yaml }], { quiet: true });
    dirty.current = false;
    if (a.status === 200) return setRefusal(null);
    const body = a.body as unknown as { line?: number };
    setRefusal({ message: detailOf(a.body), line: body.line });
  };
  const edit = (t: string) => {
    dirty.current = true;
    setText(t);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void send(t), FIELD_PAUSE_MS);
  };
  const probs = problemsAt(view.result, path, true);
  const lines = (text ?? "").split("\n");

  if (failed) return <p className="pane-note is-bad">Couldn't load this part's YAML.</p>;
  if (text === null) return <p className="pane-note">Loading…</p>;
  return (
    <div className="iy">
      <p className="pane-note">{readOnly ? "Read-only: it comes from a plugin." : extendsName ? `Only what this chain sets. Everything else comes from ${extendsName}.` : "Edits apply to the draft as you type."}</p>
      <div className="iy-box" ref={box}>
        <div className="iy-gutter" aria-hidden="true">{lines.map((_, i) => <div key={i} className={refusal?.line === i + 1 ? "is-bad" : undefined}>{i + 1}</div>)}</div>
        <textarea
          ref={ta}
          className="iy-ta"
          aria-label={`${path}, YAML`}
          spellCheck={false}
          wrap="off"
          readOnly={readOnly}
          value={text}
          style={{ height: lines.length * 17 + 8 }}
          onChange={(e) => edit(e.target.value)}
          onBlur={() => {
            if (!timer.current || !dirty.current) return;
            clearTimeout(timer.current);
            void send(text);
          }}
          onKeyDown={(e) => {
            if (e.key !== "Tab" || e.shiftKey) return;
            e.preventDefault();
            const el = e.currentTarget, a = el.selectionStart, b = el.selectionEnd;
            edit(`${text.slice(0, a)}  ${text.slice(b)}`);
            requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
          }}
        />
      </div>
      {(refusal || probs.length > 0) && (
        <div className="iy-issues" role={refusal ? "alert" : undefined}>
          {refusal && (
            <div className="iy-refused">
              <button type="button" className="yv-issue" onClick={() => refusal.line && jumpTo(ta.current, box.current, refusal.line - 1)}>
                {refusal.line ? `line ${refusal.line} · ` : ""}Not applied · {refusal.message}
              </button>
              <Button onClick={() => { dirty.current = false; setRefusal(null); void load(); }}>Revert</Button>
            </div>
          )}
          {probs.map((p, i) => <p key={i} className="yv-issue">{p.path}{p.field ? ` · ${p.field}` : ""} · {problemText(p)}</p>)}
        </div>
      )}
    </div>
  );
}
