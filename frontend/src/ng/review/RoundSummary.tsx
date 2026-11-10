import { localTime } from "../../format";
import type { GateRound } from "../../types";
import { byWord, outcomeWord, reviewOutcomeWord, threadCountLine } from "../item/gateRounds";
import { Markdown } from "../ui/Markdown";
import "./review.css";

/** A round in a few lines: how it ended and who decided, the review that went with it (its outcome, its summary), and
 *  how many threads it holds. The desktop's round view and the phone's round screen both show it. */
export function RoundSummary({ round }: { round: GateRound }) {
  const who = byWord(round.by);
  const ended = [outcomeWord(round), who, round.ended_at && localTime(round.ended_at)].filter(Boolean).join(" · ");
  return (
    <div className="rv-round-summary">
      <p className="rv-round-ended">{ended}</p>
      {round.review && (
        <>
          <p className="rv-round-outcome">Review: {reviewOutcomeWord(round.review.outcome)}</p>
          {round.review.summary && <Markdown text={round.review.summary} />}
        </>
      )}
      <p className="rv-round-count">{threadCountLine(round)}</p>
    </div>
  );
}
