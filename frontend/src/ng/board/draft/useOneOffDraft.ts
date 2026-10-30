import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Answer } from "../../http";
import { detailOf, jsonBody, request } from "../../http";
import type { ChainNode } from "../../../types";
import type { CustomPart } from "../../item/oneOff";
import type { DraftStatus } from "../../templates/draft/useConfigDraft";
import { TEXT_DEBOUNCE_MS, type ConfigDraft } from "../../templates/draft/useConfigDraft";
import type { Op, OpsView, Scope } from "../../templates/draft/types";

/** `POST /one-off-chain/ops`'s answer: the editor's draft view, plus the chain it resolves to. */
export type OneOffView = OpsView & { text: string; digest: string | null; custom: CustomPart[]; custom_nodes: string[]; nodes: ChainNode[] };

export const answerValid = (v: OneOffView | null) => !!v && !v.result.yaml_error && v.result.problems.length === 0 && v.digest !== null;

export type OneOffDraft = ConfigDraft & { digest: string | null; valid: boolean; nodes: ChainNode[]; customNodes: string[]; custom: CustomPart[];
  /** The last answer as the hook holds it: after `await flush()` the rendered values may be a render behind. */
  latest: () => { view: OneOffView; valid: boolean } | null };

const done = { status: 204, body: undefined } as Answer<never>;

/** The Templates editor's draft for a chain that exists only in a new item: the
 *  same ops, one at a time and in order, each applied to the text the previous
 *  answer returned. Nothing is stored server side; `onText` hands the page the
 *  text to keep. */
export function useOneOffDraft(text: string, onText: (t: string) => void): OneOffDraft {
  const [view, setView] = useState<OneOffView | null>(null);
  const [status, setStatus] = useState<DraftStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Op[] | null>(null);
  // The last typed text the server refused: the view still holds the text before it, so nothing may be filed until a text is accepted.
  const [rejected, setRejected] = useState(false);
  const rejectedRef = useRef(false);
  const current = useRef(text);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const undoStack = useRef<string[]>([]);
  const typed = useRef<{ timer: ReturnType<typeof setTimeout>; send: () => void } | null>(null);
  const last = useRef<OneOffView | null>(null);
  const live = useRef(true);
  // Bumped when the chain is left or entered: an answer to an older one lands nowhere.
  const gen = useRef(0);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const send = useCallback((list: Op[], opts: { preview?: boolean; quiet?: boolean; text?: string; undo?: boolean } = {}): Promise<Answer<OneOffView>> => {
    const g = gen.current;
    const run = queue.current.then(async () => {
      if (!opts.preview && live.current && g === gen.current) setPending(list);
      const body = { text: opts.text ?? current.current, ops: list, ...(opts.preview ? { preview: true } : {}) };
      const a = await request<OneOffView>("/one-off-chain/ops", jsonBody("POST", body));
      if (!opts.preview && live.current && g === gen.current) setPending(null);
      if (a.status === 200 && !opts.preview && g !== gen.current) return a;
      if (a.status === 200 && !opts.preview) {
        if (!opts.undo && a.body.text !== current.current) undoStack.current.push(current.current);
        current.current = a.body.text;
        last.current = a.body;
        rejectedRef.current = false;
        if (live.current) {
          setView(a.body);
          setRejected(false);
          setStatus("ready");
          setError(null);
          onTextRef.current(a.body.text);
        }
      } else if (a.status !== 200 && !opts.preview && live.current && g === gen.current) {
        if (opts.text !== undefined && !opts.undo) {
          rejectedRef.current = true;
          setRejected(true);
        }
        if (!opts.quiet) setError(detailOf(a.body));
      }
      return a;
    });
    queue.current = run.catch(() => {});
    return run;
  }, []);

  const flushText = useCallback(() => {
    const t = typed.current;
    typed.current = null;
    if (t) {
      clearTimeout(t.timer);
      t.send();
    }
  }, []);

  // Customize, and a page restored from sessionStorage, both arrive with text: one read of it.
  const active = text !== "";
  useEffect(() => {
    gen.current++;
    if (typed.current) clearTimeout(typed.current.timer);
    typed.current = null;
    undoStack.current = [];
    rejectedRef.current = false;
    setRejected(false);
    if (!active) {
      setView(null);
      last.current = null;
      setStatus("loading");
      return;
    }
    live.current = true;
    current.current = text;
    void send([]);
    // The text changes on every answer; only arriving or leaving starts a read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const ops = useCallback((list: Op[], opts: { preview?: boolean; quiet?: boolean } = {}) => {
    if (!opts.preview) flushText();
    return send(list, opts);
  }, [send, flushText]);

  const field = useCallback((path: string, name: string, value: unknown) => void ops([{ op: "set_field", path, field: name, value }]), [ops]);

  const typedText = useCallback((_file: string, body: string) => {
    if (typed.current) clearTimeout(typed.current.timer);
    const fire = () => void send([], { text: body });
    typed.current = { send: fire, timer: setTimeout(() => { typed.current = null; fire(); }, TEXT_DEBOUNCE_MS) };
  }, [send]);

  const flush = useCallback(async () => {
    flushText();
    await queue.current;
  }, [flushText]);

  const undo = useCallback(async () => {
    flushText();
    await queue.current;
    const prev = undoStack.current.pop();
    if (prev === undefined) return { status: 409, body: { detail: "Nothing to undo" } } as unknown as Answer<never>;
    return send([], { text: prev, undo: true }) as unknown as Promise<Answer<never>>;
  }, [send, flushText]);

  useEffect(() => () => { live.current = false; flushText(); }, [flushText]);

  const valid = answerValid(view) && !rejected;
  const scope: Scope = useMemo(() => ({ area: "chains", key: view?.key ?? "one-off" }), [view?.key]);
  return useMemo(() => ({
    scope, view, status, error, pending, stale: null,
    clearError: () => setError(null),
    ops, field, text: typedText, flush, undo,
    publish: async () => done, keepMine: async () => done, discard: async () => done, reload: async () => done,
    resolvedNode: (id: string) => (view?.result.resolved?.chain?.nodes as never as { id: string }[] | undefined)?.find((n) => n.id === id) ?? null,
    digest: view?.digest ?? null, valid, nodes: view?.nodes ?? [], customNodes: view?.custom_nodes ?? [], custom: view?.custom ?? [],
    latest: () => (last.current ? { view: last.current, valid: answerValid(last.current) && !rejectedRef.current } : null),
  }) as unknown as OneOffDraft, [scope, view, status, error, pending, ops, field, typedText, flush, undo, valid]);
}
