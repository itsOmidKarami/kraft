import { useNavigate } from "react-router-dom";
import type { ReviewThread } from "../../types";
import { ItemHeader } from "../item/header/ItemHeader";
import { roundsOf } from "../item/gateRounds";
import { openLimitEditor } from "../item/RaiseLimit";
import { placeUrl } from "../item/url";
import type { ItemDetail } from "../item/useItem";
import { openBudgetEditor } from "../item/Workspace";
import { Badge } from "../ui/Badge";
import { Empty } from "../ui/Empty";
import { Markdown } from "../ui/Markdown";
import { LABEL_TONE, Quote } from "./Thread";
import { rangeName, threadRange } from "./range";
import { RoundSummary } from "./RoundSummary";
import { LABEL_WORD } from "./sharedWords";
import { useRoundThreads } from "./useReview";
import "../item/item.css";
import "./review.css";

/** One thread, read-only: where it sits, its label and state, the lines it was made on, and its comments. */
export function ThreadReadout({ thread }: { thread: ReviewThread }) {
  const range = threadRange(thread);
  const quote = range && thread.quote ? thread.quote.split("\n") : [];
  return (
    <article className={`rv-thread${thread.state === "resolved" ? " is-resolved" : ""}`} aria-label={`Thread on ${thread.file_path ?? "the item"}`}>
      <div className="rv-thread-head">
        <span className="rv-who rv-mono">{thread.file_path ?? "whole change"}</span>
        {range && <span className="rv-status">· {rangeName(range)}</span>}
        {thread.label && <Badge tone={LABEL_TONE[thread.label]} mono>{LABEL_WORD[thread.label]}</Badge>}
        <span className="rv-status">{thread.state}</span>
      </div>
      {quote.length > 0 && <Quote lines={quote} />}
      {thread.comments.map((c) => (
        <div key={c.id} className="rv-reply">
          <div className="rv-thread-head"><span className="rv-who rv-mono">{c.author === "you" ? "You" : c.author}</span></div>
          <Markdown text={c.body} />
        </div>
      ))}
    </article>
  );
}

/** `/work-items/:id/review?gate=&round=`: one round of a gate as it was. Its review and its threads, read-only, with no
 *  diff and nothing to send, and it works after the gate has passed. */
export function RoundReview({ item, gate, round, version, reload }: { item: ItemDetail; gate: string; round: number; version?: string; reload: () => void }) {
  const navigate = useNavigate();
  const toItem = (p: Parameters<typeof placeUrl>[1]) => navigate(placeUrl(item.id, p));
  const threads = useRoundThreads(item.id, gate, round, version);
  const found = roundsOf(item, gate).find((r) => r.n === round);
  return (
    <div className="review-page rv-round">
      <h1 className="review-visually-hidden">Round {round} of {gate}: {item.title}</h1>
      <ItemHeader
        item={item}
        reload={reload}
        onSettings={() => toItem({ sel: { kind: "chain" }, tab: "config" })}
        onRaise={() => {
          if (item.stop?.limit) return void (openLimitEditor(item.id), toItem({ sel: { kind: "chain" } }));
          if (item.stop?.kind === "budget") openBudgetEditor(item.id);
          toItem({ sel: { kind: "chain" }, tab: "config" });
        }}
        onGate={(g) => toItem({ sel: { kind: "node", node: g }, node: g, pass: g === gate ? round : undefined })}
      />
      <div className="rv-round-body">
        <h2 className="rv-round-title">Round {round} of {gate}</h2>
        {!found && threads.state === "error" && threads.status === 404 ? (
          <Empty variant="block" role="alert">{threads.error}</Empty>
        ) : (
          <>
            {found && <RoundSummary round={found} />}
            {threads.state === "loading" && <div aria-busy="true" />}
            {threads.state === "error" && <Empty variant="block" role="alert">{threads.error}</Empty>}
            {threads.state === "ready" && threads.data.map((t) => <ThreadReadout key={t.id} thread={t} />)}
          </>
        )}
      </div>
    </div>
  );
}
