import { useEffect, useState } from "react";
import { Inspector } from "../../graph/Inspector";
import type { useResizable } from "../../graph/useResizable";
import { request } from "../../http";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import { Head, Note, SelectRow } from "../../templates/panes/controls";
import { CAP_LABEL } from "./types";
import { show } from "./units";

interface Scope {
  path: string;
  kind: "chain" | "node" | "step" | "task";
  level: string;
  caps: Record<string, { value: number | null; source: string; maximum: { value: number; level: string } | null; exceeds: boolean }>;
}

const sourceWord = (s: string) => (s.startsWith("library:") ? `library ${s.split(".").pop()}` : s === "chain" ? "this chain" : s);

/** Preview on a chain (Decisions §12): what each scope of a published chain would run under, given the draft. Read-only. */
export function PreviewPane({ draft, chains, size, onClose }: { draft: ConfigDraft; chains: string[]; size: ReturnType<typeof useResizable>; onClose: () => void }) {
  const [chain, setChain] = useState(chains.includes("default") ? "default" : chains[0] ?? "");
  const [scopes, setScopes] = useState<Scope[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stamp = draft.view!.files["policy.yaml"];
  useEffect(() => {
    if (!chain) return;
    let live = true;
    void request<{ scopes: Scope[]; detail?: string }>(`/drafts/policy/policy/preview?chain=${encodeURIComponent(chain)}`).then((a) => {
      if (!live) return;
      if (a.status === 200) {
        setScopes(a.body.scopes);
        setError(null);
      } else {
        setScopes(null);
        setError(a.body.detail ?? "Could not read the chain.");
      }
    });
    return () => void (live = false);
  }, [chain, stamp]);

  return (
    <Inspector id="policy-preview" open size={size} crumbs={[{ label: "Policy", onClick: onClose }]} icon="workflow" title="On a chain" sub="read-only · edit values on the page" onCollapse={onClose} onExpand={() => {}}>
      <SelectRow label="chain" value={chain} options={chains.map((c) => ({ value: c, label: c }))} onPick={setChain} />
      <Head>Caps at each scope</Head>
      <Note>What each scope would get under this draft. Gates are left out: they wait for a person.</Note>
      {error && <Note bad>{error}</Note>}
      {scopes?.map((s) => {
        const set = Object.entries(s.caps).filter(([, c]) => c.value != null);
        const bad = set.some(([, c]) => c.exceeds);
        return (
          <div key={s.path} className={`pol-pv${bad ? " is-bad" : ""}`}>
            <span className="pol-pv-path">{s.path}</span>
            <span className="pol-pv-kind">{s.kind}</span>
            {set.length === 0 && <span className="pol-pv-cap">no caps</span>}
            {set.map(([cap, c]) => {
              const l = CAP_LABEL[cap] ?? { name: cap, unit: "min" as const };
              return (
                <span key={cap} className={`pol-pv-cap${c.exceeds ? " is-bad" : ""}`}>
                  {l.name} {show(l.unit, c.value)} · {sourceWord(c.source)}{c.exceeds && c.maximum ? ` · exceeds ${show(l.unit, c.maximum.value)}` : ""}
                </span>
              );
            })}
          </div>
        );
      })}
    </Inspector>
  );
}
