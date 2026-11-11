import { localTime } from "../../format";
import type { GateRound } from "../../types";
import { byWord, outcomeWord, ownOutcome, threadCountLine } from "../item/gateRounds";
import { Markdown } from "../ui/Markdown";
import "./review.css";

/** A round in a few lines: how it ended and who decided, the review that went with it (its outcome, its summary), and
 *  how many threads it holds. The desktop's round view and the phone's round screen both show it. */
export function RoundSummary({ round }: { round: GateRound }) {
  const who = byWord(round.by);
  // The review's own outcome only when the round's header has not said it already.
  const own = ownOutcome(round);
  const ended = [outcomeWord(round), who, round.ended_at && localTime(round.ended_at)].filter(Boolean).join(" · ");
  return (
    <div className="rv-round-summary">
      <p className="rv-round-ended">{ended}</p>
      {own && <p className="rv-round-outcome">Review: {own}</p>}
      {round.review?.summary && <Markdown text={round.review.summary} />}
      <p className="rv-round-count">{threadCountLine(round)}</p>
    </div>
  );
}
