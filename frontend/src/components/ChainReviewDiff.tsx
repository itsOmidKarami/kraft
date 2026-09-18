import { DraftDiff } from "./DraftDiff";
import { carryForwardNodeFields, nodesForDiff, parseChainReviewArtifact } from "../chainReviewDiff";
import type { WorkItem } from "../types/work_item";

/** The `chain_finalized` gate's artifact, as a structured diff instead of
 *  raw JSON text (2026-09-13 chain-review design, point 4). */
export function ChainReviewDiff({ item, content }: { item: WorkItem; content: string }) {
  const envelope = parseChainReviewArtifact(content);
  if (!envelope) {
    return (
      <p className="form-error">chain_review: artifact is not the expected JSON envelope</p>
    );
  }
  if (envelope.status === "error") {
    return <p className="form-error">{envelope.rationale ?? "chain_review reported an error"}</p>;
  }

  const nodes = item.chain_definition?.nodes ?? [];
  const tailStart = nodes.findIndex((n) => n.gate_after === "chain_finalized") + 1;
  const before = nodes.slice(tailStart);
  const after = carryForwardNodeFields(before, envelope.revised_chain_nodes ?? []);

  return (
    <div data-testid="chain-review-diff">
      <DraftDiff
        before={nodesForDiff(before as unknown as Record<string, unknown>[])}
        after={nodesForDiff(after)}
      />
      {envelope.rationale && <p className="doc-modal-note">{envelope.rationale}</p>}
      {!!envelope.flags?.length && (
        <div className="chain-review-flags">
          <p className="section-label">Flagged — edit registry.yaml manually</p>
          {envelope.flags.map((f, i) => (
            <p key={i} className="gate-deferred">
              <code>{f.hook_point}</code>.<code>{f.field}</code> ={" "}
              {JSON.stringify(f.current_value)} — {f.concern}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
